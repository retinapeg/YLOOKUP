# Initial canonical main sync

STATUS: COMPLETE

BRANCH: `main`

LATEST COMMIT: This document's containing commit; resolve it with `git rev-parse HEAD`.

CANONICAL MAIN COMMIT: This document's containing commit. The integrated code checkpoint immediately before this handoff is `8afda4f` (`sync: establish canonical hackathon baseline`).

ORIGIN/MAIN COMMIT: The same commit as local `main`; verify with `git rev-parse main origin/main` after fetching.

REPO PATH: `/Users/leo/Documents/ChatGPT/ylookup`

REMOTE: `git@github.com:retinapeg/YLOOKUP.git` (`https://github.com/retinapeg/YLOOKUP`, public canonical repository)

WORKING TREE: Clean after this handoff commit and push.

FEATURES PRESENT:

- Offline PDF/TXT capital-call extraction with typed field provenance and explicit abstention.
- Deterministic US/EU/accounting monetary normalization and currency-aware reconciliation.
- Exact amount, date, currency, text, missing-value, and confidence controls with stable severity.
- Independent evidence review that cannot clear a deterministic mismatch.
- Document-scoped, append-only SQLite human decision history.
- Streamlit control room with the Northstar mismatch demo, clean-match case, exception queue, audit log, and evaluation view.
- Versioned synthetic corpus, executable evaluation runner, regression gates, failure attribution, and reproducibility metadata.

BACKEND STATUS: Operational for the local single-process demo. Extraction, normalization, reconciliation, review, audit storage, observability, and evaluation share the canonical contracts documented under `docs/`.

FRONTEND STATUS: Operational Streamlit UI. There is no separate JavaScript frontend or frontend build step; UI contracts are covered by Python tests and the offline smoke path.

TEST STATUS: PASS — 150 tests passed; offline smoke passed; fixture evaluation passed all 4 applicable regression gates.

TESTS RUN:

- `.venv/bin/python -m pytest -q` — `150 passed in 26.70s` on the final pre-push rerun (the integration checkpoint also passed in 15.47s).
- `env -u OPENAI_API_KEY .venv/bin/python -m scripts.smoke_demo` — Northstar `MISMATCH` with two exceptions, clean case `PASS`, no-key model mode disclosed `FALLBACK`, and audit append `PASS`.
- `.venv/bin/python -m app.evals --mode fixture --output /tmp/fundops-checkpoint-eval.json --fail-on-regression` — 4/4 gates passed, 267/270 exact extracted fields, 12/12 exception recall at 12/14 precision, and 210/210 isolated reconciliation rules.

KNOWN FAILURES:

- The synthetic evaluation deliberately retains ten ranked failed rows: four context-dependent reviewer false negatives, one spurious fund-name extraction, two upstream exception false positives, and three reviewer false positives.
- Model-backed quality has not been established; the verified fixture run made zero model calls.

KNOWN ISSUES:

- Live UI register ingestion uses the checked-in canonical JSON row; the XLSX is a genuine downloadable fixture but is not parsed into the live record.
- OCR/layout-aware parsing, entity resolution, batch and cross-document controls, immutable source storage, enterprise identity/authorization, and production deployment controls are not implemented.
- SQLite is local, single-process demo storage rather than a managed multi-user audit service.

IMPORTANT INTERFACES:

- `extract_document(path, *, extractor=None, case_id=None) -> ExtractedDocument`
- `reconcile_document(fund_record, document, *, numeric_tolerance=Decimal("0"), confidence_threshold=0.80) -> ReconciliationReport`
- `review_reconciliation(report, reviewer=None) -> ReviewReport`
- `AuditStore.append(event)`, `list_events(...)`, and `latest_decision(...)`
- `run_evaluation(EvaluationConfig(...)) -> dict`

INTERFACES CHANGED:

- Extracted fields now carry source type, workbook/page locator, extractor ID, timezone-aware timestamp, and abstention reason.
- Reconciliation items carry expected/observed currency context.
- Audit events persist expected/observed currencies and a validated audit request ID, with additive legacy-database migration.
- Review methods include explicit local-policy and unavailable modes; failures serialize fail closed.
- Evaluation artifacts report Git/worktree provenance and separate context-dependent reviewer attribution.

WHAT WORKS: The bundled Northstar and clean-match demo paths, deterministic controls, exception review, append-only decisions, synthetic evaluation, and no-key fallback all execute locally without network access after dependencies are installed.

WHAT IS PARTIALLY IMPLEMENTED: Optional OpenAI-compatible extraction/review adapters exist but were not exercised in this checkpoint; the production capabilities listed under known issues remain future work.

FILES CHANGED: The integrated checkpoint `8afda4f` changes the canonical modules under `app/`, `streamlit_app.py`, the evaluation and smoke runners, tests, README/demo material, architecture/contracts, and the integration checkpoint. This final handoff adds `AGENTS.md`, `docs/CONTRACTS.md`, and this file, and aligns `.env.example` with the documented default model.

NEXT PARALLEL WORKSTREAMS:

- Primary machine: create `feature/core-engine` only when new core work is ready to begin.
- Small laptop: continue product work on `feature/small-laptop-product` after updating it from `origin/main`.
- Add narrower feature branches only for concrete work; do not pre-create every suggested branch.

WHAT THE INTEGRATION AGENT MUST KNOW:

- `main` is now the stable shared baseline. Fetch first and develop on feature branches.
- Read `AGENTS.md`, `docs/ARCHITECTURE.md`, and `docs/AGENT_CONTRACTS.md` before changing boundaries.
- Preserve the authority split between model interpretation, deterministic controls, independent review, and human decisions.
- Never force-push, discard useful work, commit private data, or claim synthetic metrics as production accuracy.

NEXT STEP FOR LAPTOP 1:

```bash
git clone https://github.com/retinapeg/fundops-control-room.git
cd fundops-control-room
git switch main
```

If the repository is already cloned, run `git fetch origin`, `git switch main`, and `git pull --ff-only origin main`.
