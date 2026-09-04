# FundOps Control Room

**Turn messy fund documents into auditable exceptions.**

FundOps Control Room is a hackathon MVP for private-markets operations teams. It extracts a focused set of capital-call fields, deterministically reconciles them with a fund record, independently checks source support, and records human review decisions in an append-only audit trail.

This is a workflow tool, not a PDF chatbot.

## Run locally

Python 3.9+ is supported.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open the local URL printed by Streamlit. Click **Load Demo Case** for the strongest demo path; no API key or internet connection is required after dependencies are installed.

## Demo flow

1. Click **Load Demo Case** in the hero area.
2. Pause on the control-break card: expected **GBP 625,000**, incoming **GBP 650,000**, deterministic variance **GBP +25,000**, **HIGH** severity.
3. In **Reconciliation Results**, point out that the amount came from the incoming two-page PDF while the expected value is mapped to `investor_register.xlsx` → `LP Register!I2`.
4. Open **Exception Queue** to show the exact PDF sentence, page locator, extraction method, and independent evidence-review finding.
5. Choose **Needs investigation**, enter a reason, then open **Audit Log** to show the appended decision and its key reconciliation/evidence context.
6. Open **Evals** to show results calculated by the executable synthetic benchmark, including denominators, gates, model-call count, and visible failures.

Prepared talk tracks are in [`DEMO.md`](DEMO.md). Technically precise judge answers and implementation boundaries are in [`docs/JUDGE_QUESTIONS.md`](docs/JUDGE_QUESTIONS.md).

## Architecture

The app is intentionally a single local Streamlit process with small, testable modules:

- `app/models.py` — Pydantic workflow contracts.
- `app/extraction.py` — PDF/text extraction, deterministic field parsing, and an optional OpenAI-compatible structured extractor.
- `app/reconciliation.py` — deterministic money, date, text, and missing-value rules.
- `app/storage.py` — append-only SQLite audit history.
- `app/review.py` — independent, evidence-only validation of extracted values.
- `app/evals/` — versioned corpus runner, metrics, provenance-aware model measurement, and frontend result contract.
- `app/file_handling.py` — bounded upload validation and temporary-file cleanup.
- `app/sample_data.py` — bundled, realistic offline cases.
- `streamlit_app.py` — UI and orchestration only.

The SQLite file is created at `data/fundops.db` and intentionally ignored by Git because it is machine-local demo state.

## Demo data

- `data/demo/northstar_growth_fund_ii/investor_register.xlsx` — formatted synthetic LP register; flagship expected amount is `LP Register!I2`.
- `data/demo/northstar_growth_fund_ii/capital_call_notice.pdf` — two-page flagship notice containing the GBP 650,000 current call plus stale historical amounts.
- `data/fund_record.json` and `data/sample_documents/matching_capital_call.pdf` — clean-match control case.
- Companion `.txt` fixtures keep the sample flow reliable even if PDF parsing is unavailable.

The optional `data/gold/` corpus contains 27 synthetic edge cases for repeatable quality checks. The flagship is also gold case `CC-002`.

## Optional AI extraction

Deterministic extraction is the default and handles the included cases. To enable the optional OpenAI-compatible provider, set:

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o-mini"
# Optional for another compatible endpoint:
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

Then enable **Use OpenAI-compatible extraction** in the sidebar before loading a demo case or processing an upload. The model is used only to structure unstructured document text. All comparisons, severities, variances, and exception statuses remain deterministic Python logic. Model-returned fields must cite text found on the claimed source page; ungrounded fields are discarded. If the provider is unavailable or invalid, the extractor falls back to the deterministic result and records a visible extraction note.

Without the key, the bundled demo runs entirely offline. That default path is deliberately labelled deterministic; it is not presented as an LLM run.

## Tests

```bash
python -m pytest -q
```

The high-value suite covers exact matches, numeric/date discrepancies, missing fields, demo extraction, and persisted audit decisions.

## Optional evaluation benchmark

Run the versioned synthetic benchmark without an API key:

```bash
python -m app.evals --mode fixture --fail-on-regression
```

The same current-code run is exposed in the **Evals** tab. It is labelled as a deterministic synthetic baseline, includes explicit numerators and denominators, distinguishes end-to-end and isolated-rule results, records model-origin provenance, and writes a downloadable JSON artifact. It is not an LLM-accuracy or production-performance claim.
