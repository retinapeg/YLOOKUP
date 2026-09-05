# FundOps Control Room agent guide

This repository is the canonical FundOps Control Room project. Read
`docs/ARCHITECTURE.md` and `docs/AGENT_CONTRACTS.md` before changing a domain
boundary.

## Working policy

- `main` is the stable integration branch after the initial sync recorded in
  `docs/handoffs/initial-main-sync.md`.
- Do feature work on a focused branch, normally prefixed with `feature/` or
  `codex/`. Do not force-push or rewrite shared history.
- Fetch before starting, preserve concurrent work, and integrate semantically.
  Do not resolve conflicts by choosing an entire side without inspecting it.
- Update a file under `docs/handoffs/` when handing work to another machine or
  agent. Include branch, commit, tests, failures, and changed interfaces.

## Canonical boundaries

- Domain models: `app/models.py`
- Financial normalization: `app/normalization.py`
- Document extraction: `app/extraction.py`
- Deterministic controls: `app/reconciliation.py`
- Independent evidence review: `app/review.py`
- Append-only audit persistence: `app/storage.py`
- Workflow/UI orchestration: `streamlit_app.py`
- Evaluation runner and schemas: `app/evals/`

Extend these modules instead of adding parallel `*_v2`, `final_*`, or duplicate
model/storage implementations. A model may interpret source text, but it must
not perform financial arithmetic, clear a deterministic control break, or make
a human decision. Preserve typed values, field-level provenance, explicit
abstention, and document-scoped audit history across every adapter.

## Local verification

Use the checked-in dependency set and run the smallest relevant checks while
developing. Before an integration handoff, run:

```bash
python -m pytest -q
python -m app.evals --mode fixture --fail-on-regression
python -m scripts.smoke_demo
```

The frontend is Streamlit, not a separate JavaScript application. Start it with
`streamlit run streamlit_app.py` and use **Load Northstar Demo** for the prepared
offline path.

## Data and safety

All checked-in cases are synthetic. Do not commit real investor documents,
credentials, `.env`, Streamlit secrets, local SQLite files, virtualenvs, caches,
generated evaluation output, or build artifacts. Keep fixture mode offline and
do not describe synthetic regression metrics as production or model accuracy.
