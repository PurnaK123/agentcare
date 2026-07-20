import hashlib
import re
import uuid
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DocumentStatus, PatientDocument
from app.schemas import DocumentClassification
from app.security import redact_sensitive_text
from app.services.audit import record_audit


class DocumentError(ValueError):
    pass


ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".txt"}
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
}
INJECTION_PATTERNS = (
    r"ignore (?:all |the )?(?:previous|prior) instructions",
    r"reveal (?:the )?(?:system prompt|secrets|api key)",
    r"(?:call|invoke|execute) (?:a |the )?tool",
    r"you are now (?:the |an )?system",
    r"<script\b",
)


def _validate_signature(extension: str, content: bytes) -> None:
    valid = {
        ".pdf": content.startswith(b"%PDF"),
        ".png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": content.startswith(b"\xff\xd8\xff"),
        ".jpeg": content.startswith(b"\xff\xd8\xff"),
        ".txt": b"\x00" not in content[:2048],
    }
    if not valid.get(extension, False):
        raise DocumentError("The file content does not match its extension")


def stage_document(
    db: Session,
    *,
    patient_id: int,
    request_id: int,
    original_name: str,
    content: bytes,
    correlation_id: str,
) -> PatientDocument:
    settings = get_settings()
    safe_name = Path(original_name or "upload").name.replace("\x00", "")[:255]
    extension = Path(safe_name).suffix.casefold()
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentError("Only PDF, PNG, JPEG, and text documents are accepted")
    if not content:
        raise DocumentError("The uploaded file is empty")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise DocumentError(f"Documents must be smaller than {settings.max_upload_mb} MB")
    _validate_signature(extension, content)
    checksum = hashlib.sha256(content).hexdigest()
    duplicate = db.scalar(
        select(PatientDocument)
        .where(
            PatientDocument.patient_id == patient_id,
            PatientDocument.checksum_sha256 == checksum,
            PatientDocument.status != DocumentStatus.REJECTED,
        )
        .order_by(PatientDocument.id)
    )
    if duplicate:
        document = PatientDocument(
            patient_id=patient_id,
            request_id=request_id,
            document_type=duplicate.document_type,
            status=DocumentStatus.DUPLICATE,
            original_name=safe_name,
            storage_reference=duplicate.storage_reference,
            mime_type=MIME_TYPES[extension],
            size_bytes=len(content),
            checksum_sha256=checksum,
            classification_confidence=duplicate.classification_confidence,
            classification_reason="Exact SHA-256 duplicate of an existing document.",
            duplicate_of_id=duplicate.id,
        )
        db.add(document)
        db.flush()
        record_audit(
            db,
            action="document.duplicate_detected",
            entity_type="patient_document",
            entity_id=document.id,
            actor_role="document_agent",
            correlation_id=correlation_id,
            metadata={"duplicate_of_id": duplicate.id, "checksum_prefix": checksum[:12]},
        )
        return document

    settings.resolve_data_paths()
    patient_directory = settings.upload_dir.resolve() / str(patient_id)
    patient_directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = patient_directory / stored_name
    temporary = patient_directory / f".{stored_name}.tmp"
    temporary.write_bytes(content)
    temporary.replace(destination)
    storage_reference = destination.relative_to(settings.upload_dir.resolve()).as_posix()
    document = PatientDocument(
        patient_id=patient_id,
        request_id=request_id,
        document_type="unknown",
        status=DocumentStatus.PENDING,
        original_name=safe_name,
        storage_reference=storage_reference,
        mime_type=MIME_TYPES[extension],
        size_bytes=len(content),
        checksum_sha256=checksum,
    )
    db.add(document)
    db.flush()
    record_audit(
        db,
        action="document.staged",
        entity_type="patient_document",
        entity_id=document.id,
        actor_role="document_agent",
        correlation_id=correlation_id,
        metadata={
            "mime_type": document.mime_type,
            "size_bytes": document.size_bytes,
            "checksum_prefix": checksum[:12],
        },
    )
    return document


def resolve_document_path(document: PatientDocument) -> Path:
    upload_root = get_settings().upload_dir.resolve()
    candidate = (upload_root / document.storage_reference).resolve()
    if upload_root not in candidate.parents:
        raise DocumentError("Invalid document storage reference")
    if not candidate.is_file():
        raise DocumentError("Stored document is unavailable")
    return candidate


def extract_document_preview(document: PatientDocument, max_characters: int = 6000) -> str:
    if document.status == DocumentStatus.DUPLICATE:
        return "Exact duplicate; classification inherited from the original document."
    path = resolve_document_path(document)
    content = path.read_bytes()
    try:
        if document.mime_type == "application/pdf":
            reader = PdfReader(BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:5])
        elif document.mime_type == "text/plain":
            text = content.decode("utf-8", errors="replace")
        else:
            text = f"Image document. Filename: {document.original_name}"
    except Exception as exc:
        raise DocumentError("Text could not be extracted from this document") from exc
    text = re.sub(r"\s+", " ", text).strip()[:max_characters]
    return redact_sensitive_text(text)


def has_prompt_injection(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in INJECTION_PATTERNS)


def apply_classification(
    db: Session,
    *,
    document_id: int,
    classification: DocumentClassification,
    correlation_id: str,
) -> PatientDocument:
    document = db.get(PatientDocument, document_id)
    if not document:
        raise DocumentError("Document not found")
    if document.status == DocumentStatus.DUPLICATE:
        return document
    document.document_type = classification.document_type
    document.document_date = classification.document_date
    document.classification_confidence = classification.confidence
    document.classification_reason = classification.reason
    document.prompt_injection_detected = classification.prompt_injection_detected
    if classification.prompt_injection_detected or classification.confidence < 0.65:
        document.status = DocumentStatus.NEEDS_REVIEW
    else:
        document.status = DocumentStatus.CLASSIFIED
    db.flush()
    record_audit(
        db,
        action="document.classified",
        entity_type="patient_document",
        entity_id=document.id,
        actor_role="document_agent",
        correlation_id=correlation_id,
        metadata={
            "document_type": document.document_type,
            "confidence": document.classification_confidence,
            "status": document.status.value,
            "prompt_injection_detected": document.prompt_injection_detected,
        },
    )
    return document


def missing_document_types(
    db: Session, *, patient_id: int, required_types: list[str]
) -> list[str]:
    if not required_types:
        return []
    available = set(
        db.scalars(
            select(PatientDocument.document_type).where(
                PatientDocument.patient_id == patient_id,
                PatientDocument.status == DocumentStatus.CLASSIFIED,
            )
        )
    )
    return sorted(set(required_types) - available)
