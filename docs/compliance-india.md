# India Privacy and Regulatory Posture

This document is an engineering assessment, not legal advice or a certification.

## Demonstration Scope

AgentCare is intentionally limited to synthetic data and administrative coordination. It does not
diagnose, recommend treatment, prescribe, change dosage, or interpret clinical findings. The public
repository contains no real patient information, credentials, or production tokens.

Truly synthetic information that cannot identify a person is materially different from digital
personal data. However, a deployed form can become subject to privacy obligations if someone enters
real information despite warnings. The application therefore treats all inputs as sensitive even in
demo mode.

## Indian Frameworks to Reassess Before Real Use

- Digital Personal Data Protection Act, 2023 and rules/commencement requirements applicable at the
  time of deployment: notice, lawful processing/consent where applicable, processor governance,
  data-principal rights, retention/deletion, security safeguards, and breach response.
- Information Technology Act and applicable SPDI/security rules for real health information.
- CERT-In directions and incident/log obligations applicable to the deploying organization.
- ABDM Health Data Management Policy and consent-manager requirements if integrating with ABDM.
- Medical Device Rules and clinical safety regulation if functionality expands into diagnosis,
  treatment recommendations, triage, or clinical decision support.

Applicability depends on the organization, data, users, integrations, and current law. Obtain Indian
privacy, healthcare, and cybersecurity counsel before a pilot with real people or a hospital.

## Controls Implemented for the Challenge

- Synthetic-only banners, acknowledgement fields, sample records, and `.test` email domains.
- No public signup; deployment owner controls synthetic patient and staff credentials.
- Backend RBAC, patient ownership checks, signed HTTP-only session cookie, CSRF, and secure headers.
- Minimum-necessary LLM context and deterministic redaction of common email, phone, and identifier
  patterns from document previews.
- Schema validation and allowlisted tools; the LLM cannot authorize or mutate data directly.
- Private upload directory, signature/size checks, random storage names, checksums, and authenticated
  downloads.
- Correlated audit history without document bodies, request bodies, credentials, or model secrets.
- Hard medical-scope boundary and deterministic emergency escalation before any operational action.

## Gaps Before Any Real Deployment

A real deployment would additionally require a formal data inventory and DPIA-equivalent risk
assessment, approved privacy notice and consent/legal basis, retention automation, data-subject
request handling, tested backup/restore and disaster recovery, key management, MFA/SSO, rate limits,
malware scanning and OCR controls, security testing, vendor contracts, OpenAI data-processing terms
appropriate to the use, data-location assessment, incident response, monitoring, and independent
legal/clinical/security review.

Do not describe this challenge application as "DPDP compliant", "HIPAA compliant", medically
approved, or production ready.
