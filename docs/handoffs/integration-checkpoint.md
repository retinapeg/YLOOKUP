# FundOps integration checkpoint

STATUS: CHECKPOINTED

BRANCH: `main`

LATEST COMMIT: `HEAD` — resolve the immutable checkpoint SHA with `git log -1 --format=%H` after this file is committed.

WHAT WORKS:

- Offline PDF/TXT extraction produces typed fields with page evidence, source type, extractor identity, and explicit abstention reasons.
- Repeated labelled values are compared as a candidate set; conflicts fail closed and optional model output cannot overwrite that abstention.
- Monetary parsing uses one deterministic US/EU/accounting normalization boundary, including currency context.
- Deterministic reconciliation, independent evidence review, exception routing, human decisions, and append-only SQLite history form one coherent workflow.
- Streamlit correlates validation, extraction, reconciliation, and review stages; audit writes use and persist a separate request ID.
- The real synthetic evaluation runner exposes generated denominators, regression gates, artifact/code provenance, and failed-case attribution.
- The offline demo smoke path and no-key optional-model behavior are covered by tests.

WHAT IS PARTIALLY IMPLEMENTED:

- The checked-in XLSX is a real fixture, but live UI ingestion uses its canonical JSON snapshot.
- OCR/layout-aware parsing, entity resolution, batch/cross-record controls, immutable source storage, enterprise identity, and deployment controls are not implemented.
- The requested remote branch review and GitHub push cannot be completed until the existing repository URL is supplied; this clone has no configured remotes and the named agent refs are absent locally.

FILES CHANGED:

- Contracts and workflow: `app/models.py`, `app/normalization.py`, `app/extraction.py`, `app/reconciliation.py`, `app/review.py`, `app/storage.py`, `app/errors.py`.
- Evaluation: `app/evals/__main__.py`, `app/evals/runner.py`, `app/evals/service.py`.
- Product surface: `streamlit_app.py`, `scripts/smoke_demo.py`.
- Documentation: `README.md`, `DEMO.md`, `docs/ARCHITECTURE.md`, `docs/AGENT_CONTRACTS.md`, `docs/FAILURE_MODES.md`, `docs/JUDGE_QUESTIONS.md`, this handoff.
- Tests: extraction, reconciliation, review, storage, errors, observability, eval, Streamlit, and demo-smoke suites under `tests/`.

TESTS RUN:

- Focused extraction: 24 passed.
- Focused reconciliation plus evaluation: 40 passed.
- Focused storage: 13 passed.
- Focused evaluation: 13 passed.
- Focused Streamlit: 11 passed.
- Full suite: 150 passed in 15.47 seconds.
- Offline/no-key smoke: passed; clean case `PASS`, Northstar `MISMATCH` with two exceptions and an append-only audit event, optional model path `FALLBACK` without a crash.
- Fixture evaluation: 267/270 extraction, 12/12 exception recall, 210/210 isolated reconciliation, 13/17 human-escalation recall, and 4/4 gates passed.

KNOWN FAILURES:

- No `origin` remote exists, so a push attempt will fail until the correct existing GitHub URL is configured. Do not create or guess a repository.
- Synthetic fixture evaluation retains ten failure rows. Four are case-level reviewer false negatives requiring register, batch, cross-field, or multi-document context; the other rows expose extraction/reviewer precision limitations.
- Model-backed quality has not been established; fixture mode makes zero model calls.

INTERFACES CHANGED:

- `ExtractedField` now supports source type, workbook locator, extractor ID, timezone-aware timestamp, and abstention reason.
- `ReconciliationItem` now carries expected/observed currency and currency status.
- `AuditEvent` and persistence APIs now carry expected/observed currency and an optional validated request ID, with additive legacy-database migration.
- `ReviewMethod` includes explicit local-policy and unavailable modes; reviewer failure is serialized fail closed.
- Evaluation artifacts include Git commit/worktree provenance and separated reviewer context subsets/failure attribution.
- `WorkflowStage` includes independent review and audit append stages.

WHAT THE INTEGRATION AGENT MUST KNOW:

- Canonical contracts are `docs/AGENT_CONTRACTS.md`; do not add parallel models, normalizers, reconciliation engines, reviewers, or audit tables.
- At checkout time there was no `origin`, no remote-tracking refs, no unreachable branch commits, and no historical copies of the requested architecture/contract documents. Earlier agent work was already combined into the shared working tree/main history, so no named branch diff could be truthfully reviewed.
- Current fixture metrics are 267/270 exact extracted fields, 12/12 exception recall at 12/14 precision, 210/210 isolated rule correctness, 13/17 reviewer escalation recall at 13/16 precision, and 4/4 gates passing. These are synthetic regression results, not production or LLM accuracy.
- Preserve the fail-closed distinction: model interpretation structures evidence; deterministic code normalizes/reconciles; the independent reviewer cannot clear a deterministic break; only a human appends a decision.
- Before any push, verify the user-provided existing remote with `git ls-remote`; never force push or invent a repository.
