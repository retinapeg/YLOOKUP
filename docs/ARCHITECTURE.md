# Architecture

FundOps Control Room is an offline-first Streamlit application that demonstrates an evidence-preserving capital-call control workflow. It runs as one local Python process so the demo is dependable, while keeping domain boundaries explicit enough to extract into services later.

## Implemented flow

```mermaid
flowchart TD
    subgraph Inputs
        P[PDF or TXT capital-call notice]
        X[XLSX investor register]
        J[Checked-in canonical JSON row]
    end

    P --> V[Validate size, suffix, MIME, and structure]
    V --> T[Random temporary file]
    T --> PAGES[Numbered source pages]
    PAGES -->|Full document| E[Structured extraction]
    PAGES -->|Retrieval mode| CH[Page-local word windows]
    CH --> RET[Rank chunks separately for each field]
    RET -->|Field-specific evidence only| E
    E --> PR[Typed value plus provenance]
    PR --> N[Deterministic normalization]
    X -. displayed and downloadable fixture .-> J
    J --> FR[FundRecord]
    N --> R[Deterministic reconciliation]
    FR --> R
    R --> IR[Independent evidence review]
    IR --> Q[Exception queue]
    Q --> HD[Human decision with reason]
    HD --> A[(Append-only SQLite audit history)]

    subgraph Evaluation
        G[Versioned synthetic gold corpus]
        ER[Evaluation runner]
        M[Generated metrics, gates, and failed cases]
        G --> ER --> M
    end
    ER --> E
    ER --> R
    ER --> IR
```

The XLSX file is a genuine synthetic workbook and its relevant cell addresses are displayed in the demo. The current application does not parse that workbook into the live record: it loads a checked-in JSON snapshot that matches the selected row. This boundary is intentional and visible rather than presented as a connector that does not exist.

## Canonical module ownership

| Concern | Canonical implementation | Responsibility |
| --- | --- | --- |
| Domain types | `app/models.py` | Pydantic records, extracted fields, reconciliation results, and human audit events |
| Deterministic normalization | `app/normalization.py` | Fail-closed monetary and currency normalization used by financial controls |
| Retrieval | `app/retrieval.py` | Page-local chunks, field queries, offline TF-IDF, optional embeddings, and ranked evidence |
| Extraction | `app/extraction.py` | PDF/TXT reading, labelled deterministic extraction, and optional grounded model extraction |
| Reconciliation | `app/reconciliation.py` | Exact expected-versus-observed controls, status, severity, and difference calculation |
| Independent review | `app/review.py` | Evidence-only findings that cannot mutate or clear reconciliation |
| Audit persistence | `app/storage.py` | Append-only document-scoped SQLite events and read APIs |
| Upload safety | `app/file_handling.py` | Bounded structural validation, random staging paths, and cleanup |
| Public failures | `app/errors.py` | Allowlisted error codes, stages, safe messages, and request IDs |
| Observability | `app/observability.py` | Redacted per-stage JSON telemetry with no free-form document metadata |
| Evaluation | `app/evals/` | Gold loading, current-code execution, metrics, gates, artifacts, and UI adapter |
| Orchestration/UI | `streamlit_app.py` | Session state, workflow invocation, exception decisions, and generated eval display |

There is one implementation for each concern. Compatibility names such as `ReconciliationResult` and `reconcile_records` are aliases to the canonical implementation, not competing models or engines.

## Trust boundaries

### Input and temporary storage

Uploads are checked before parsing. The application accepts only allowlisted formats, applies a 10 MiB limit, checks signatures/package structure, writes to a random temporary location, and deletes that copy after processing. Original bytes remain only in active Streamlit session state so the user can download the source during the demo.

### Retrieval scope and evidence

Full-document mode remains the startup setting while the four maths functions await hand implementation. In retrieval mode, the configured retriever ranks chunks from **one document** for each of ten field queries. The default is TF-IDF. Both chunk and query vectors use the term weights fitted to that document. An optional embeddings adapter batches chunks and queries at the configured OpenAI-compatible endpoint. Selecting embeddings without a key uses TF-IDF and reports the substitution.

Windows default to 80 whitespace-delimited words with 20 words of overlap. Each window keeps its exact page substring, half-open character offsets, one-based page, zero-based chunk ID and source document digest. The two extractors consume complete source lines within the selected windows to avoid treating clipped labels or amounts as complete evidence. Each proposed field and any fallback stay within the same selection. The extraction ledger displays the raw ranked windows, including clipped edges, and their scores. The current session retains this trace. Human audit events continue to store the field snapshot and citation.

Production calls the maths functions directly. They deliberately raise `NotImplementedError` pending hand implementation. The test-only switch and its reference functions live in `tests/_reference_retrieval.py`. See the [implementation handoff](handoffs/rag-ingestion.md) for commands and measured results.

### Optional model extraction

The model may propose structured fields, but every accepted field must use a known name, coerce into the domain type, include a finite confidence, cite a valid page, and quote evidence present on that page. Invalid or partial model output is discarded or filled by the deterministic extractor with per-field method provenance. Provider failure produces a disclosed fallback; it does not bypass reconciliation.

### Deterministic normalization and reconciliation

Financial values are normalized with fail-closed locale and accounting rules. Ambiguous or unsupported syntax becomes a review state instead of a guessed amount. Reconciliation uses `Decimal`, exact dates, explicit missing handling, currency controls, stable severities, and zero monetary tolerance by default. No network call or model response participates in comparison arithmetic.

### Independent evidence review

The reviewer receives one allowlisted field request containing the extracted value, its citation, and that field's reconciliation snapshot. It returns a separate finding. A `SUPPORTED` citation means the source supports the extracted value; it never means the value agrees with the fund record. Reviewer failure or insufficient evidence requires human review.

### Human decision and audit

The exception UI requires a reason before recording `APPROVED`, `REJECTED`, or `NEEDS_INVESTIGATION`. The event snapshots the relevant values, variance, reviewer result, source locator, actor, UTC timestamp, and a document-scope digest. Storage exposes append and read operations only, and SQLite triggers reject update, delete, and replacement attempts. This is local append-only audit history, not a cryptographic ledger.

## Evaluation path

`python -m app.evals --mode fixture --fail-on-regression` verifies fixture hashes and runs the same extraction, reconciliation, and reviewer implementations used by the demo. The result artifact contains its dataset digest, environment metadata, explicit supports, provider telemetry, regression-gate policy, and ranked failed cases. Isolated rule correctness injects gold extracted values to distinguish reconciliation defects from upstream extraction defects.

The separate long-pack benchmark (`python -m app.evals.retrieval`) runs deterministic extraction on both paths. It scores page recall at chunk ranks 1, 3 and 5, exact normalized field values, and top-ranked decoy pages. It evaluates embeddings when a key is present unless `--offline` is set. Gold metadata is used only for scoring. The existing fixture command retains its full-document baseline.

Fixture mode is a deterministic regression baseline and makes no model calls. Model mode keeps grounded model-origin fields separate from deterministic fallback and fill-ins. Neither mode establishes production accuracy without a permissioned representative dataset and an appropriate statistical design.

## Deployment boundary

The implemented deployment is a single-user local Streamlit process with SQLite. A production system would need immutable source storage, an idempotent job queue, Postgres or another managed audit database, enterprise identity and authorization, tenant isolation, malware scanning, encryption/key management, retention controls, provider governance, monitoring, and batch/cross-document controls. Those capabilities are not represented as present in this repository.
