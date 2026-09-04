# Failure modes

FundOps Control Room fails closed at input and persistence boundaries, and surfaces uncertain extraction as reviewable workflow state. A failed or partial operation must never be presented as completed.

| Failure | Detection | User behaviour | System behaviour | Recovery |
|---|---|---|---|---|
| Unsupported file type | Extension and content/signature validation disagree with the allowlist | Show the accepted formats; do not start the workflow | Reject before writing a temporary file; `415 unsupported_file_type` | Upload a supported PDF or text file |
| File exceeds the configured limit | Check declared and actual byte count before parsing | Show the maximum size without losing the active case | Reject and clean up any partial temporary file; `413 file_too_large` | Upload a smaller file or split the source |
| PDF parsing fails | Parser raises, returns no usable pages, or produces no text | Explain that the document could not be read; do not show invented fields | Stop that parse, remove temporary files, and return `422 malformed_pdf` | Retry with a text-based PDF or use the bundled deterministic demo |
| XLSX is malformed | The reusable validator rejects a malformed workbook | Identify the workbook problem without a stack trace | Do not persist partial rows; return `422 malformed_xlsx` | XLSX import is not exposed in the MVP UI; use the JSON fund record |
| Required field is missing | Typed input validation or reconciliation finds an absent required value | Highlight the field and route the case to review | Represent extracted omissions as `MISSING`/uncertain; reject an unusable request with `422 missing_required_field` | Correct the source/record or resolve the exception with a reason |
| AI service is unavailable | Credentials are absent, connection fails, or provider returns an availability error | Show a warning that deterministic extraction was used | Fall back to deterministic extraction and attach a warning; if no safe fallback exists, return `503 ai_unavailable` | Continue with the fallback or retry the provider later |
| AI response is invalid | JSON parsing and typed schema validation fail | Show that AI output was not trusted | Discard the response, retain no fabricated fields, and fall back; otherwise return `502 ai_invalid_response` | Continue with deterministic results or retry |
| AI request times out | The configured provider deadline expires | Show a timeout warning while preserving the current case | Cancel/abandon the provider call and fall back; otherwise return `504 ai_timeout` | Continue with deterministic results or retry later |
| Duplicate record or repeated submission | **Not implemented in the current single-document UI**; the synthetic corpus contains a labelled duplicate case | Do not claim automatic duplicate protection in the MVP | Production design: compare stable business keys and document hashes, preserve both sources, and return `409 duplicate_record` rather than overwrite | Add batch context and an idempotency store before production use |
| Audit/database write fails | SQLite transaction, constraint, lock, or disk error | State clearly that the decision was **not saved** | Roll back atomically, leave prior audit state unchanged, and return `503 audit_write_failed` | Retry after storage is available; verify the event appears once |

## Error and health contract

The domain errors carry status codes and a small stable envelope so a future HTTP API can expose them consistently:

```json
{
  "error": {
    "code": "malformed_xlsx",
    "status": 422,
    "message": "The spreadsheet could not be read. Check the file and try another copy.",
    "request_id": "request identifier",
    "stage": "file_validation",
    "retryable": false
  }
}
```

The current Streamlit UI does not return this envelope over an application API; it presents a safe message and reference ID rather than a traceback. Streamlit's lightweight liveness endpoint is `/_stcore/health`; `200` means the Streamlit process is responding, not that the AI provider or every uploaded document is healthy. Dependency failures are reported on the affected request instead of making liveness depend on an optional provider.

## Logging and audit

The optional observability helper can emit structured JSON stage logs with `request_id`, `workflow_stage`, `duration_ms`, and `success`/`failure`, plus a safe error code on failure. It accepts only a fixed metadata schema so document text, prompts, credentials, and provider bodies cannot be logged accidentally. The current local Streamlit workflow does not enable stage logging by default.

Human decisions are append-only in normal application flow. Every new audit event contains an ID, timezone-aware timestamp, case and package identifiers, source document and location, field, expected/observed/difference snapshots, evidence-review status, actor, decision, and reason. The package identifier is a SHA-256 digest over source bytes, the canonical record, and the completed extraction. A new decision appends an event; it never edits the extracted source or an earlier event. SQLite update/delete/replace guards provide a second line of enforcement, and a failed audit write is not reflected as saved in the UI. This is not a claim of a cryptographically immutable external ledger.
