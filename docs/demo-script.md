# Judge Demo Script

## Main Flow (about 3 minutes)

1. Sign in as the synthetic patient and show the profile reference.
2. Submit: `I need a Cardiology appointment next week and want to attach my previous ECG.`
3. Attach `sample_data/synthetic-ecg.txt`.
4. Open the result and expand the timeline. Point out six different agent names/prompts, validated
   outputs, actual tool arguments/results, model name, and correlation ID.
5. Show the persisted appointment and the slot now marked booked.
6. Show ECG classification, the missing `blood_report`, and both durable reminder records.
7. Sign in as staff. Open the same request and the append-only audit trail.

## Human Approval Flow

Submit an ambiguous but valid administrative request. A routing confidence below the configured
threshold creates `pending_approval`; the Appointment Agent has not yet run. As staff, inspect and
approve the exact payload fingerprint. The workflow resumes and books only after approval.

Because model confidence can vary, the automated test
`test_low_confidence_route_requires_exact_staff_approval` is deterministic evidence of this gate.

## Safety Flow

Submit the synthetic sentence: `I have chest pain and cannot breathe. Book me for next week.`
The deterministic Safety Agent boundary creates a critical escalation, displays emergency guidance,
and proves that no appointment tool ran. This is demonstration text only; do not simulate a real
emergency.

## Recovery Flow

Use an invalid `OPENAI_MODEL`, submit a safe request, and show `retry_pending` plus the failed agent
step. Restore the model and click **Retry from saved checkpoint**. The workflow continues without
recreating completed stages.
