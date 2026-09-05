# Judge questions

These answers describe the repository as implemented. The bundled data is entirely fictional, and the default benchmark is a deterministic synthetic regression run—not evidence of production or LLM performance.

## Why use an LLM at all?

An LLM is useful at the fuzzy perception boundary. Real notices express the same fact with different wording, layout, ordering, and surrounding context. The optional OpenAI-compatible extractor maps document text into a narrow typed schema: field name, normalized value, page, confidence, evidence text, and extraction method.

The model is not needed for the known-format bundled fixture, so the reliable default demo uses a deterministic parser. The LLM path is implemented and can be enabled for demo cases or uploads with a real API key. A field returned by the model is accepted only if its evidence text occurs on the cited source page. Partial model output is explicitly hybrid: grounded model fields retain model provenance, deterministic fill-ins retain deterministic provenance, and evaluation measures only model-origin fields as model output.

## Why not use an LLM for reconciliation?

Reconciliation is financial control logic and should be reproducible, inspectable, and unit-testable. The implementation uses exact `Decimal` arithmetic, date differences, normalized identifiers/text, explicit missing states, a fixed field-severity policy, zero numeric tolerance by default, and a confidence threshold. The same expected **625,000** and observed **650,000** values always produce a signed **+25,000** difference and **HIGH** severity.

The separation is deliberate: probabilistic tooling may structure evidence; deterministic software decides whether values agree. Neither stage can make the human decision.

## How do you evaluate extraction?

`python -m app.evals --mode fixture --fail-on-regression` runs the real extraction, reconciliation, and reviewer code over a versioned corpus. The manifest includes document SHA-256 hashes, normalized gold values, explicit null/abstention labels, evidence locators, expected statuses/severities/differences, and case-level human-escalation labels.

The current deterministic fixture run reports:

| Measure | Result | Scope |
| --- | ---: | --- |
| Exact normalized extraction | 267/270 (98.9%) | All labelled fields |
| Value accuracy where gold is present | 239/241 (99.2%) | Present fields only |
| Numeric extraction | 53/54 (98.1%) | Numeric fields |
| Date extraction | 53/53 (100%) | Date fields |
| Correct abstention | 28/29 (96.6%) | Gold-missing fields |
| Field exception precision | 12/14 (85.7%) | 21 replayable cases |
| Field exception recall | 12/12 (100%) | 21 replayable cases |
| High-severity exception recall | 11/11 (100%) | 21 replayable cases |
| Isolated rule correctness | 210/210 (100%) | Gold extraction injected into deterministic rules |
| Reviewer escalation precision | 13/16 (81.2%) | Case-level labels |
| Reviewer escalation recall | 13/17 (76.5%) | Case-level labels |

The four reviewer misses are not hidden: those cases need context beyond the field-specific snippet—register ambiguity, a batch duplicate, a cross-field remaining-commitment check, or a multi-document payment check. The repeated-label cross-page conflict is now detected upstream and safely held. Field-level reviewer `CHALLENGE` precision/recall is unavailable because the corpus does not yet label challenge fields. Confidence is a deterministic heuristic and its displayed calibration statistic is descriptive only. The fixture run makes **zero model calls**.

## How do you deal with hallucination?

The model cannot create a trusted field merely by returning valid JSON. The extractor restricts output to recognized fields, requires an explicit valid page and confidence, coerces values into domain types, and requires a case-insensitive, whitespace-normalized occurrence of the evidence on that page. A model field with absent or invalid provenance is discarded. A partial response is visibly hybrid, and provider failure becomes a disclosed deterministic fallback.

A separate reviewer receives one field at a time and independently checks its supplied evidence. It can return `SUPPORTED`, `CHALLENGE`, `INSUFFICIENT_EVIDENCE`, or `NOT_REVIEWED`; it cannot change extraction, reconciliation, or audit decisions. Low-confidence matches, missing data, parsing failures, reviewer failures, and non-pass controls route to a human.

This reduces hallucination risk; it does not make the system hallucination-proof. Verbatim occurrence proves that a citation exists, not that the chosen passage is contextually authoritative. Register ambiguity and entity aliases remain visible examples of that limitation.

## How do you handle ambiguous documents?

The system represents uncertainty rather than inventing a value. An absent observation becomes `MISSING`; a labelled value that is malformed, unsupported, or conflicts with another labelled occurrence becomes an explicit extraction abstention and reconciles as `REVIEW`; low-confidence observations also become `REVIEW`; whole-document parse failures stop the upload; reviewer failure becomes `NOT_REVIEWED`. Those states require human review.

The deterministic extractor evaluates every recognized labelled occurrence, accepts repeated values only when their normalized forms agree, and abstains on conflicts. The reviewer still sees the field’s cited snippet rather than the whole case package, so entity resolution and batch/cross-record context remain known gaps. The production next step is layout-aware candidate extraction and explicit case-context rules—not asking a model to guess.

## How would this scale?

Today this is a single-process Streamlit application with a local SQLite audit store. The code is separated into ingestion, extraction, reconciliation, evidence review, storage, and evaluation modules so each boundary can move behind a queue or service without changing the financial rule semantics.

A production design would store immutable originals in object storage, enqueue idempotent per-document jobs, keep workflow state and audit events in Postgres, version prompts/models/rules, horizontally scale stateless extraction and reconciliation workers, and add OCR/layout processing, retry/dead-letter handling, deduplication, monitoring, and load tests. Batch and cross-document controls would receive explicit case context. None of that infrastructure is claimed as implemented here.

## How would you secure customer data?

Implemented safeguards and building blocks include bounded uploads; extension, MIME, and structural validation; PDF and XLSX package checks; random temporary paths and cleanup; sanitized public errors; allowlisted stage logging that rejects document text and secrets; prompt-injection language in the model instruction; per-field data minimization for model review; and a fully local/offline default path. The Streamlit workflow emits correlated stage logs for validation, extraction, reconciliation, independent review, and audit append.

The MVP does not include SSO, RBAC, tenant isolation, encrypted application storage, malware scanning, managed key rotation, regional routing, retention enforcement, or a production deployment. Optional AI extraction sends extracted document text to the configured provider. Production use would require those missing controls, vendor no-training/retention terms, customer-specific data residency, access logs, and deletion policies.

## How would you integrate with Excel?

The repository contains a real, formatted synthetic XLSX register. In the flagship workbook the expected amount is **`LP Register!I2`** and the expected due date is **`LP Register!M2`**. The UI exposes those exact references and lets the judge download the workbook.

The MVP does not ingest a live workbook: it loads a checked-in canonical JSON snapshot matching the synthetic row. A production connector would read a named Excel table through Microsoft Graph with least-privilege permissions and retain workbook ID, eTag/version, sheet, row key, and cell address as provenance. Any writeback would occur only after a human decision and would use optimistic concurrency. Reconciliation would remain deterministic Python code.

## What would you build next?

The eval gives a defensible order:

1. Add layout-aware/OCR corroboration and entity alias resolution beyond the implemented labelled-candidate and US/EU monetary rules.
2. Add batch, duplicate, remaining-commitment, and payment-receipt controls so the four reviewer-escalation misses have the context they require.
3. Label field-level reviewer challenges and run a permissioned, de-identified real-document evaluation in both deterministic and model modes.
4. Add the production Excel connector and immutable source retention.
5. Add enterprise identity, tenant isolation, encryption/key management, observability, and retention controls.

Autonomous payment execution is deliberately not next; the human decision gate should remain.

## What is genuinely implemented versus mocked?

Genuinely implemented:

- Typed extraction, reconciliation, reviewer, and audit contracts.
- PDF/TXT parsing and deterministic extraction.
- Optional OpenAI-compatible extraction for opt-in demo cases and uploads.
- Page-level verification of model-returned evidence and field-level provenance.
- Deterministic money/date/reference/missing/confidence controls.
- An independent evidence-review stage with deterministic and optional model-backed implementations.
- Human approve/reject/investigate actions with a required reason.
- Document-scoped package digests and append-only SQLite events protected by update/delete/replace triggers.
- Key reconciliation and evidence-review context in each new audit decision: expected and observed values, difference, reviewer status/method, and source locator.
- Defensive upload validation and temporary-file cleanup.
- A versioned synthetic corpus, executable evaluator, regression gates, and visible failure analysis.
- Real synthetic CSV/XLSX and text-searchable PDF artifacts.

Synthetic, mocked, or not implemented:

- Every fund, investor, notice, amount, and result is fictional.
- The default one-click extractor and reviewer are deterministic offline implementations.
- No live-model evaluation result is claimed; the displayed fixture benchmark makes zero model calls.
- Confidence values in fixture mode are heuristics, not production-calibrated probabilities.
- The workbook is a downloadable demo artifact, not a live Excel integration.
- No email, administrator, payment, or approval-system connector exists.
- A human “approval” records a decision only; it does not move money.
- No enterprise auth, tenancy, encrypted persistence, immutable source archive, or production deployment exists.
- The randomized temporary disk copy is deleted after processing. Original bytes remain in active Streamlit session state for source download; the audit retains a binding digest and source locator, not the uploaded file itself.
- Reviewer challenge metrics are not available without field-level challenge labels.
- Cross-record, OCR-corrupted, and entity-resolution cases remain visible failures; repeated labelled cross-page conflicts now fail closed.
