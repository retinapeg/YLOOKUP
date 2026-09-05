# Small laptop product checkpoint

STATUS: CHECKPOINTED

BRANCH: `feature/small-laptop-product`

LATEST COMMIT: `HEAD` — this handoff is part of the checkpoint commit; resolve the immutable SHA with `git log -1 --format=%H`.

WHAT WORKS:

- The branch contains the verified canonical FundOps baseline from `origin/main`.
- The bundled Northstar and clean-match cases run offline through PDF/TXT validation, typed extraction, deterministic normalization and reconciliation, independent evidence review, and document-scoped append-only audit decisions.
- The existing Streamlit product surface includes the Northstar demo, reconciliation results, exception queue, evidence details, audit log, fund record, extraction ledger, and real fixture-evaluation results.
- Optional OpenAI-compatible extraction and evidence-review adapters fail closed and disclose fallback or unavailable states.
- The versioned synthetic evaluation suite reports explicit denominators, regression gates, model-call provenance, and visible failure cases.

WHAT IS PARTIALLY IMPLEMENTED:

- The requested new one-screen, three-column product control room was deliberately not started because the urgent cross-laptop checkpoint superseded feature development.
- The current UI remains the verified tabbed Streamlit experience inherited from the canonical baseline.
- Live register ingestion still uses a checked-in canonical JSON snapshot alongside the real downloadable XLSX fixture.
- OCR/layout-aware parsing, entity resolution, batch and cross-document controls, immutable source storage, enterprise identity/authorization, and production deployment controls remain future work.

FILES CHANGED:

- `docs/handoffs/small-laptop-product.md` — this branch-specific checkpoint.
- The branch was fast-forwarded from its prior `45fa185` baseline to the complete canonical integration at `origin/main`; the inherited files are documented in `docs/handoffs/integration-checkpoint.md` and `docs/handoffs/initial-main-sync.md`.

TESTS RUN:

- Canonical pre-push verification inherited at `9836dc6`: 150 tests passed, the offline smoke path passed, and all four applicable fixture-evaluation gates passed.
- `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_streamlit_app.py tests/test_smoke_demo.py` — `12 passed in 21.02s`.
- `env -u OPENAI_API_KEY -u OPENAI_BASE_URL -u OPENAI_MODEL .venv/bin/python -m scripts.smoke_demo` — clean case `PASS`, Northstar case `MISMATCH` with two exceptions, no-key model mode disclosed `FALLBACK`, and audit append `PASS`.

KNOWN FAILURES:

- No focused checkpoint test failures are known at handoff time.
- The synthetic evaluation intentionally retains ranked extraction and context-dependent reviewer failures; these are documented in the generated evaluation result and are not hidden.
- Model-backed quality has not been established; fixture evaluation makes zero model calls.

INTERFACES CHANGED:

- This checkpoint introduces no new interface changes beyond the canonical baseline.
- The inherited canonical interface changes are documented in `docs/AGENT_CONTRACTS.md` and `docs/handoffs/integration-checkpoint.md`.

WHAT THE INTEGRATION AGENT MUST KNOW:

- This branch is a clean, fast-forwarded product-work starting point and must not be treated as completed implementation of the newer three-column UI brief.
- Use the single canonical repository at `https://github.com/retinapeg/YLOOKUP.git`; the former `fundops-control-room` URL redirects to the same repository object.
- Fetch before integrating, preserve the authority split between model interpretation, deterministic controls, independent review, and human decisions, and never force-push.
- Synthetic fixture metrics are regression signals, not production or model-accuracy claims.
