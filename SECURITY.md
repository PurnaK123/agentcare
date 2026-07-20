# Security Policy

AgentCare is a synthetic-data challenge demonstration, not a production healthcare system.

## Reporting

Do not open a public issue containing credentials, patient information, or exploit details.
Contact the repository owner privately through their GitHub profile and include only synthetic
reproduction data.

## Security Boundaries

- Do not deploy with real patient information.
- Do not commit `.env`, API keys, submission tokens, database exports, or uploaded documents.
- Production startup rejects a weak session secret, insecure cookies, or a missing OpenAI key.
- All mutating browser forms require a signed session and CSRF token.
- Patient ownership and patient/staff roles are enforced by backend dependencies and queries.
- Uploaded files are allowlisted, size-limited, signature-checked, renamed, checksummed, and
  stored outside the public static directory.
- LLM output is schema-validated and never grants authorization or direct infrastructure access.
- Prompts and audit records exclude document bodies and unnecessary personal information.

See `docs/threat-model.md` for trust boundaries and residual risks.
