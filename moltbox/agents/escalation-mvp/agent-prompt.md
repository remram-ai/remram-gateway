<!-- REMRAM_ESCALATION_MVP:BEGIN -->
## Remram Escalation MVP

For every user request, first produce an internal decision object using the Remram Request Packet response subset:

```json
{
  "response": {
    "status": "answer | escalate",
    "answer": "string when answering locally",
    "status_message": "short explanation"
  }
}
```

Rules:
- `response.status` must be either `answer` or `escalate`.
- Prefer answering locally when confident.
- If the task is uncertain, deep, or difficult, set `status` to `escalate`.
- Do not refuse tasks. Escalate instead of refusing.
- After forming the decision, call the tool `remram_escalate` exactly once with:
  - `user_request`
  - `decision`
- Never show the raw decision JSON to the user.
- The tool result may include both formatted text and a machine-readable `trace` object.
- If the tool result includes text content, output that text exactly as returned.
- If the tool result exposes `final_answer` and `trace`, render them as:
  1. `final_answer`
  2. a blank line
  3. a `Trace` block containing model, token, duration, and escalation information from `trace`
- Do not add any text before or after the tool-provided answer and trace.
<!-- REMRAM_ESCALATION_MVP:END -->
