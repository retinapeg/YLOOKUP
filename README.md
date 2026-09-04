# FundOps Copilot

**Turn messy fund documents into auditable exceptions.**

FundOps Copilot is a hackathon MVP for private-markets operations teams. It extracts a focused set of capital-call fields, deterministically reconciles them with a fund record, explains every exception with source evidence, and records human review decisions in an append-only audit trail.

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

## Two-minute demo flow

1. Click **Load Demo Case** in the hero area.
2. Confirm the existing Northstar fund record and the extracted document summary.
3. Show the GBP 25,000 capital-call variance and two-day due-date variance in Reconciliation Results.
4. Open each exception to show page, confidence, and exact source evidence.
5. Mark an exception **Approved**, **Needs investigation**, or **Rejected** and add a note.
6. Show the persisted decision in the Audit Log.
7. Optionally load the matching case or upload a text-based PDF to show live deterministic extraction.

## Architecture

The app is intentionally a single local Streamlit process with small, testable modules:

- `app/models.py` — Pydantic workflow contracts.
- `app/extraction.py` — PDF/text extraction, deterministic field parsing, and an optional OpenAI-compatible structured extractor.
- `app/reconciliation.py` — deterministic money, date, text, and missing-value rules.
- `app/storage.py` — append-only SQLite audit history.
- `app/review.py` — independent, evidence-only validation of extracted values.
- `app/file_handling.py` — bounded upload validation and temporary-file cleanup.
- `app/sample_data.py` — bundled, realistic offline cases.
- `streamlit_app.py` — UI and orchestration only.

The SQLite file is created at `data/fundops.db` and intentionally ignored by Git because it is machine-local demo state.

## Demo data

- `data/fund_record.json` — Northstar Growth Fund II record.
- `data/sample_documents/matching_capital_call.pdf` — clean control case.
- `data/sample_documents/discrepancy_capital_call.pdf` — deliberate amount and due-date exceptions.
- Companion `.txt` fixtures keep the sample flow reliable even if PDF parsing is unavailable.

The optional `data/gold/` corpus contains 27 synthetic edge cases for repeatable quality checks. It is separate from the one-click Albion demo, so it cannot change the presentation path.

## Optional AI extraction

Deterministic extraction is the default and handles the included cases. To enable the optional OpenAI-compatible provider, set:

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o-mini"
# Optional for another compatible endpoint:
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

Then enable **Use OpenAI-compatible extraction** in the sidebar before processing an upload. The model is used only to structure unstructured document text. All comparisons, severities, variances, and exception statuses remain deterministic Python logic. If the provider is unavailable or returns invalid data, the extractor falls back to the deterministic result and records a visible extraction note.

## Tests

```bash
pytest -q
```

The high-value suite covers exact matches, numeric/date discrepancies, missing fields, demo extraction, and persisted audit decisions.

## Optional evaluation benchmark

Run the versioned synthetic benchmark without an API key:

```bash
python -m app.evals --mode fixture --skip-reviewer
```

The output is labelled as a deterministic synthetic baseline, includes explicit numerators and denominators, and is written to the ignored `eval_results.json` file. It is not a production-accuracy claim.
