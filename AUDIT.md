# ylookup audit

## Summary

- This checkout is **FundOps Control Room**, a Python/Streamlit capital-call extraction and reconciliation prototype, not the described bulk-document RAG app.
- The offline path runs: **150 tests passed**, both demo cases loaded through the main script, the smoke passed, and the Streamlit server responded successfully.
- The 27-case synthetic evaluation passed **4/4 regression gates**, but missed **4/17 expected human escalations**; passing gates do not mean every scenario works.
- Biggest gap relative to RAG: **no retrieval chunker, embeddings, vector index, similarity search, top-k retrieval or question-answer generation**. Live model mode remains untested.

## Pipeline table

“Works” means exercised successfully within the stated local/synthetic scope, not production validation. “Missing” refers to the audited checkout, not unexamined branches or history.

| stage | file:function | status (works / broken / missing / untested) | notes |
| --- | --- | --- | --- |
| a. Document ingestion | `streamlit_app.py:_process_upload` (721); `app/file_handling.py:validate_upload` (232), `temporary_upload` (300); `app/extraction.py:_read_pages` (238) | works | Single local browser upload or bundled local fixture. UI accepts PDF/TXT, with a 10 MiB upload limit. TXT is UTF-8; PDF text uses `pypdf.PdfReader`. Extraction API accepts a local path. PDF parsing can use a same-stem TXT fallback if available; uploaded files generally have no sidecar. |
| a. Bulk ingestion / OCR / connectors | No implementation | missing | No folder-ingestion job, crawler, cloud connector, OCR, ingestion queue or bulk-document index. The eval loop is a fixed regression corpus. |
| a. Live investor-register ingestion | `app/sample_data.py:load_fund_record` (38); `streamlit_app.py:_set_case` (671) | missing | Demo loads checked-in JSON. CSV/XLSX fixtures exist, but the live workflow does not parse them into records. Lower-level validation accepts XLSX; extraction and UI only support PDF/TXT. |
| b. Retrieval chunking | No implementation; page handling in `app/extraction.py:_read_pages` (244–264), `extract_text` (490–502) | missing | TXT splits on form-feed `\f`; PDF splits by physical page. No token/character chunk size or overlap setting. These are variable-sized page segments with no deliberate overlap, not retrieval chunks. |
| c. Embedding | No implementation | missing | No embedding model/API, document vectors or query vectors. |
| d. Vector storage/index | No implementation | missing | No vector library/database or in-memory embedding matrix/index. |
| d. Actual persistence | `app/storage.py:AuditStore._initialize` (35), `append` (166), `list_events` (274) | works | SQLite stores human decision events and field/evidence snapshots. SQL index at 159–162 is on case/document/field/time, not vectors. Current workflow and source bytes live in Streamlit session state; no searchable source archive. |
| e. Retrieval | No implementation | missing | No similarity measure, retrieval top-k, reranker, query API or search UI. Reading recent audit events is not passage retrieval. |
| f. Answer generation | No implementation | missing | No user-question + retrieved-context prompt or generated answers. Optional chat completions instead extract/review structured fields. |
| Actual field extraction | `app/extraction.py:DeterministicExtractor.extract` (513), `_extract_pages` (282) | works | Regexes recognize ten field labels. Typed values retain page/evidence/confidence. Conflicting or unsupported labelled values trigger abstention. Domain-specific parsing, not general question answering. |
| Optional model extraction | `app/extraction.py:OpenAICompatibleExtractor.extract` (657), `_request` (570) | untested | Live provider not called. Default `gpt-4.1-mini`, temperature 0, JSON output. System prompt at 533–538 requests fields only, each with value/page/confidence/evidence. All page text is joined into one prompt at 673–675; no retrieval or token-budget chunking. Mocked responses and no-key fallback have tests. |
| Deterministic controls | `app/normalization.py:normalize_monetary_value` (173); `app/reconciliation.py:reconcile_field` (176), `reconcile_document` (483) | works | Decimal amounts, dates, currencies, names/references, missingness and confidence rules. Default numeric tolerance zero; confidence threshold 0.80. |
| Independent evidence review | `app/review.py:DeterministicEvidenceReviewer.assess` (641), `review_reconciliation` (1102) | works | Separate field-local checks; cannot clear a deterministic mismatch. Limited scope: observed end-to-end escalation recall 13/17. |
| Optional model review | `app/review.py:OpenAICompatibleEvidenceReviewer._request_model` (805), `assess` (855) | untested | Same default model; strict structured response. Prompt at 754–770 asks whether one cited field is supported, challenged or insufficient. No live request made. |
| g. Orchestration | `streamlit_app.py:main` (2042), `_set_case` (671), `_process_upload` (721), `_commit_workflow_result` (632) | works | Synchronous validation → extraction → reconciliation → review → human queue → audit append. Both demo cases exercised through the main script. |
| g. Autonomous agents | No agent runtime | missing | Extractor/reviewer are adapters behind protocols. `AGENT_CONTRACTS.md` defines contributor/module interfaces; no planner, tool loop, agent framework or background worker. |
| h. Tests / existing evaluation | `tests/test_*.py`; `app/evals/__main__.py:main` (248), `app/evals/runner.py:run_evaluation` (1040); `scripts/smoke_demo.py:main` (24) | works | 150 tests passed. Eval executes 27 primary documents from a 30-document synthetic corpus, scores 270 fields, and replays reconciliation on 21 cases. Known failures remain visible. |
| h. Retrieval evaluation | No implementation | missing | No questions/gold relevant chunks, recall@5, MRR or ranking benchmark. Existing recall measures exceptions/escalations. |

## How to run it (the exact commands that worked)

**Repository map**

- Audited **2026-11-22**. Working directory and Git root: `/Users/leo/Projects/ylookup`.
- Origin: `https://github.com/retinapeg/ylookup.git`. Branch: `main`. Initial status: `## main...origin/main`, with no uncommitted changes.
- Last local commit: `d462d094805a94b6664fddde367efdb78c3b566f`, committed **2026-09-27T20:29:47+01:00**.
- Exact commit message: `README: 10-second summary (question, result, status, three bullets); previous README moved verbatim to docs/GUIDE.md`.
- Local HEAD only was audited. No fetch, checkout, branch change, commit, push or history rewrite.
- Primary language: Python, with HTML/CSS embedded in Streamlit. One optional JavaScript utility, `scripts/generate_investor_workbooks.mjs`, imports `@oai/artifact-tool`. No checked-in Node package manifest/lockfile or separate JavaScript frontend. Node was not installed or invoked.
- Entry points: `streamlit_app.py` (UI); `python -m app.evals` (eval CLI); `python -m scripts.smoke_demo` (offline smoke). Fixture-generation scripts were not run.
- `README.md` summarizes the project; `docs/GUIDE.md` contains setup/demo/model/eval instructions. `docs/ARCHITECTURE.md` and `docs/AGENT_CONTRACTS.md` define module boundaries. Dataset, failure-mode and FAQ docs also exist.
- `.python-version` and GitHub Actions select Python **3.12**. Available `python3` was **3.13.15** at `/Users/leo/.venvs/modal/bin/python3`; it successfully created an isolated repository-local `.venv`. All installs went into that new environment.

```text
ylookup/
  streamlit_app.py       UI, session state and orchestration
  app/
    models.py           Typed contracts
    extraction.py       TXT/PDF reading and extraction adapters
    normalization.py    Money/currency parsing
    reconciliation.py   Deterministic controls
    review.py           Independent evidence review
    storage.py          SQLite human-decision audit
    file_handling.py    Upload validation and temporary staging
    errors.py           Public error contracts
    observability.py    Structured stage logs
    sample_data.py      Demo loading
    evals/              Schema, runner, metrics, telemetry, CLI
  data/
    sample_documents/   Two small TXT notices and PDF equivalents
    demo/               Northstar notice/register/canonical-record package
    evals/              Synthetic notices and register fixtures
    gold/               Gold manifest and schema
    fund_record.json    Default canonical record
  tests/                12 Python test modules
  scripts/              Smoke and fixture generators
  docs/                 Setup, architecture, limitations, diagrams
  .github/workflows/    Python 3.12 pytest + fixture-eval CI
  requirements.txt
```

| Declared dependency | Purpose | Installed in audit |
| --- | --- | --- |
| `streamlit>=1.32,<1.41` | UI and AppTest | 1.40.2 |
| `pandas>=2.1,<2.3` | Tables | 2.2.3 |
| `pydantic>=2.7,<3` | Typed contracts | 2.14.4 |
| `pypdf>=4,<6` | PDF text | 5.9.0 |
| `reportlab>=4,<5` | Synthetic PDFs/tests | 4.5.1 |
| `pytest>=8,<9` | Tests | 8.4.2 |

These are version ranges, not a fully locked environment. `sqlite3`, `decimal` and the model adapters' `urllib.request` are standard-library modules; no OpenAI SDK is required.

**Commands and results**

All commands ran from `/Users/leo/Projects/ylookup`. Existing TXT samples are 860 and 816 bytes, so no `sample_docs/` folder was created. UI checks used bundled PDF/demo fixtures. `env -u OPENAI_API_KEY` removes the key only from each child process.

1. Create the environment, install requirements and check isolation/dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir -r requirements.txt
.venv/bin/python -c 'from pathlib import Path; import sys; print(sys.version); print(sys.prefix); print("isolated:", sys.prefix != sys.base_prefix); Path(".venv/audit-tmp").mkdir(exist_ok=True)'
.venv/bin/python -m pip check
```

All succeeded. Isolation output included `/Users/leo/Projects/ylookup/.venv` and `isolated: True`. Dependency check returned `No broken requirements found.` with the non-fatal cache warning below.

2. Run the existing tests:

```bash
env -u OPENAI_API_KEY PYTHONDONTWRITEBYTECODE=1 TMPDIR=/Users/leo/Projects/ylookup/.venv/audit-tmp .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=.venv/audit-pytest
```

Exact result: **`150 passed in 14.31s`**, exit 0. Flags confine temporary files to `.venv` and disable pytest's repository cache. Coverage includes extraction, controls, review, storage, file handling, fixture consistency, errors/telemetry, evals and Streamlit behavior.

3. Run the fixture evaluation:

```bash
env -u OPENAI_API_KEY PYTHONDONTWRITEBYTECODE=1 TMPDIR=/Users/leo/Projects/ylookup/.venv/audit-tmp .venv/bin/python -m app.evals --mode fixture --fail-on-regression --output .venv/audit-eval-results.json
```

Exit 0. Evidence artifact: `.venv/audit-eval-results.json`.

| Measure | Observed result | Scope |
| --- | --- | --- |
| Exact normalized extraction | 267/270 | Synthetic labelled fields |
| Numeric / date extraction | 53/54; 53/53 | Respective field types |
| Correct abstention | 28/29 | Gold-missing fields |
| Exception precision / recall | 12/14; 12/12 | 21 reconciliation-replayable cases |
| Isolated rule correctness | 210/210 | Gold extraction injected into controls |
| Human escalation precision / recall | 13/16; 13/17 | Case-level labels |
| Regression gates | 4/4 pass | Existing gate policy |
| Model calls / API failures | 0 / 0 | Offline fixture path |

These are synthetic regression measurements, not retrieval metrics, live-model accuracy or production accuracy. Independent field-level reviewer gold labels are absent; case-level escalation is a different measure.

4. Run the existing smoke:

```bash
env -u OPENAI_API_KEY PYTHONDONTWRITEBYTECODE=1 TMPDIR=/Users/leo/Projects/ylookup/.venv/audit-tmp .venv/bin/python -m scripts.smoke_demo
```

Exit 0. Clean case: `PASS`, 0 exceptions. Northstar: `MISMATCH`, 2 exceptions, amount difference `25000.00`, due-date difference `-2`, 10 review findings, `audit_append: PASS`. No-key model mode disclosed `FALLBACK` and preserved the mismatch.

5. Exercise the main script and its buttons using Streamlit AppTest; no new test file was added:

```bash
env -u OPENAI_API_KEY PYTHONDONTWRITEBYTECODE=1 TMPDIR=/Users/leo/Projects/ylookup/.venv/audit-tmp FUNDOPS_DB_PATH=/Users/leo/Projects/ylookup/.venv/audit-ui.db .venv/bin/python - <<'PY'
import json
from streamlit.testing.v1 import AppTest

app = AppTest.from_file('streamlit_app.py', default_timeout=30).run()
assert not app.exception, [error.message for error in app.exception]
for label, key in [('Load Demo Case', 'load_northstar'), ('Load Clean Match', None)]:
    button = next(b for b in app.button if b.label == label and (key is None or b.key == key))
    button.click().run()
    assert not app.exception, [error.message for error in app.exception]
    document = app.session_state['document']
    report = app.session_state['report']
    print(json.dumps({
        'action': label,
        'source': document.source_document,
        'method': document.extraction_method.value,
        'fields': len(document.fields),
        'status': report.overall_status.value,
        'exceptions': [{'field': item.field, 'difference': str(item.difference)} for item in report.exceptions],
        'warnings': document.warnings,
        'tabs': [tab.label for tab in app.tabs],
    }, sort_keys=True))
    assert len(report.exceptions) == (2 if label == 'Load Demo Case' else 0)
PY
```

Exit 0. Both cases produced 10 deterministic fields, no extraction warnings and no AppTest exceptions. Northstar showed `Exception Queue (2)`, +25,000 and -2 days; Clean Match showed `Exception Queue (0)`. This tests UI logic, not visual browser layout.

6. Start the real entry point in the foreground:

```bash
env -u OPENAI_API_KEY PYTHONDONTWRITEBYTECODE=1 TMPDIR=/Users/leo/Projects/ylookup/.venv/audit-tmp FUNDOPS_DB_PATH=/Users/leo/Projects/ylookup/.venv/audit-server.db .venv/bin/python -m streamlit run streamlit_app.py --server.headless=true --server.address=127.0.0.1 --server.port=18501 --browser.gatherUsageStats=false --server.fileWatcherType=none
```

It printed `URL: http://127.0.0.1:18501`. While running, this local probe succeeded:

```bash
.venv/bin/python - <<'PY'
from urllib.request import urlopen
for path in ('/_stcore/health', '/'):
    with urlopen('http://127.0.0.1:18501' + path, timeout=5) as response:
        body = response.read()
        print(path, response.status, body.decode() if path.endswith('health') else 'Streamlit HTML: ' + str(b'Streamlit' in body))
PY
```

Exact response:

```text
/_stcore/health 200 ok
/ 200 Streamlit HTML: True
```

Stopped the foreground server with **Ctrl-C**; it printed `Stopping...` and exited 0. Nothing was left serving. To rerun manually, start the same command and click **Load Demo Case**. HTTP success establishes startup; the separate AppTest run establishes the workflow.

Runtime outputs are under ignored `.venv/`. Existing source, fixtures, dependencies and tracked configuration were unchanged. `AUDIT.md` is the only new deliverable.

## What broke (exact errors)

**No dependency-install failure, pytest failure, fixture regression-gate failure, smoke failure or main-entry-point exception was observed.** No step approached ten minutes. The following functional gaps remain despite the successful offline demo.

The eval artifact reports **10 failed case/field entries**, not ten crashed documents:

- Four missed human escalations: **CC-007** (ambiguous/duplicate register match), **CC-020** (batch duplicate notice), **CC-025** (call exceeds remaining commitment), **CC-027** (payment-receipt shortfall). The runner attributes these to context/controls absent from the field-local workflow.
- **CC-014** extracts `Northstar Growth Fund` when the gold label expects abstention on an ambiguous name.
- **CC-005** treats `Merevale Teachers Pension Tr. Ltd` as different from `Merevale Teachers' Pension Trust Limited`, creating an exception false positive.
- **CC-011** abstains on OCR-like `GBP 6Z5,OOO.OO` instead of recovering the gold `625000.00`. This is a false abstention under fixture labels, not a parser crash.
- Three escalation false positives: **CC-005**, **CC-009** (comma-decimal formatting), **CC-011**. Two follow upstream discrepancies; CC-009 is a field-local reviewer/gold-label disagreement.

The four regression gates do not cover all these expectations. Live providers, real documents, scale/load and visual browser layout were untested. OCR, live register parsing and cross-document controls are missing.

**Non-fatal warnings, verbatim**

`.venv/bin/python -m pip check`:

```text
WARNING: The directory '/Users/leo/Library/Caches/pip' or its parent directory is not owned or is not writable by the current user. The cache has been disabled. Check the permissions and owner of that directory. If executing pip with sudo, you should use sudo's -H flag.
No broken requirements found.
```

Installation already used `--no-cache-dir`. No global installation, permission or configuration change was made.

AppTest command:

```text
2026-11-22 19:33:32.936 WARNING streamlit.runtime.scriptrunner_utils.script_run_context: Thread 'MainThread': missing ScriptRunContext! This warning can be ignored when running in bare mode.
```

**Auditor errors, not application defects**

This one-off result-inspection command assumed the wrong report key:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
r=json.loads(Path('.venv/audit-eval-results.json').read_text())
print('failure_cases:', json.dumps(r['failure_cases'], indent=2))
print('env template variable names:', [s.split('=',1)[0] for s in Path('.env.example').read_text().splitlines() if '=' in s and not s.startswith('#')])
print('samples:', [(str(p), p.stat().st_size) for p in Path('data/sample_documents').glob('*.txt')])
PY
```

Exact error:

```text
Traceback (most recent call last):
  File "<stdin>", line 4, in <module>
KeyError: 'failure_cases'
```

Reading the actual `report["summary"]["failure_analysis"]` schema succeeded and supplied the findings above.

The first in-memory report-assembly attempt also stopped before writing a file:

```text
Script error:
Error: Missing captured command
    at exec_main.mjs:296:41
```

Cause: an overly broad guard searched the report for the English word “undefined” and mistook explanatory prose for a missing command. Checking each captured command individually succeeded. Both mistakes were confined to audit tooling; application code was untouched.

## Keys and external services needed

| Item | Where read / used | Requirement and audit outcome |
| --- | --- | --- |
| `OPENAI_API_KEY` | `os.getenv` at `app/extraction.py:559` and `app/review.py:787`; UI check at `streamlit_app.py:860`. Constructors also accept `api_key`. | Only for optional live model extraction/review. No key requested, printed, invented or configured. Runtime checks unset it in child processes. |
| `OPENAI_MODEL` | `app/extraction.py:561`; `app/review.py:791` | Optional override; default `gpt-4.1-mini`. This is the code's default, not a verified claim about current provider availability. |
| `OPENAI_BASE_URL` | `app/extraction.py:560`; `app/review.py:788–790` | Optional compatible service; default `https://api.openai.com/v1`. Both POST to `/chat/completions` with 12-second default timeout. Review requires strict JSON-schema response support. |
| `FUNDOPS_DB_PATH` | `streamlit_app.py:38` | Local database path, not a key/service. Default `data/fundops.db`; audit UI writes redirected into `.venv`. |
| Python package index | `python -m pip install ...` | Network required to obtain dependencies; installation succeeded. Exercised fixture/demo path works offline afterward. |
| Node / `@oai/artifact-tool` | `scripts/generate_investor_workbooks.mjs:3–5` | Optional workbook-regeneration utility; untested here. Node was not installed or invoked; bundled workbook fixtures suffice. |

`.env.example` names the three OpenAI variables. Application source does **not** load `.env` via dotenv or read `st.secrets` for them: adapters read process environment or explicit constructor arguments. The guide documents environment-variable configuration.

Live extraction sends complete page text; live review sends one field's value, evidence/provenance and reconciliation snapshot. No live provider request was made. Missing-key extraction discloses `AI extraction unavailable; used deterministic extraction` (`app/extraction.py:667`). Missing-key model review returns `NOT_REVIEWED` (`app/review.py:863–869`). Successful fallback does not establish that live model mode works.

## Maths core (file:line, what it computes)

**The requested RAG maths is absent.** No repository lines compute document/query embedding similarity, rank retrieved chunks, select retrieval top-k, or apply softmax/L2 normalization to retrieval scores.

Inspection and source searches across `app/`, `streamlit_app.py`, `scripts/` and `requirements.txt` found page splitting at `app/extraction.py:246–248, 500–501` and SHA-256 byte blocks at `app/evals/schema.py:48–49`. Neither is retrieval chunking.

**Actual selection, comparison and normalization**

Let `i` index fields, `o_i` be an observed value, `e_i` its expected value, and `p_i` its extraction confidence.

| Exact location | Plain words and index notation |
| --- | --- |
| `app/extraction.py:209–224, 355–382`, `_candidates_agree` / `_extract_pages` | Require labelled candidates to agree, then use the first in source order; otherwise abstain. For ordered candidates `c_(i,j)`, require a single distinct normalized value `card{N(c_(i,j)):j=1,...,m}=1` and no conflicting explicit money currency, then choose `v_i=c_(i,1)`. No relevance score or ranked top-1. |
| `app/extraction.py:380–382, 412, 474` | Fixed confidences: 0.99 for labelled money/dates, 0.97 for other labelled fields, 0.96 for inferred document type, 0.93 for inferred currency. `p_i` is a rule-assigned constant; the values do not sum to one. No softmax. |
| `app/extraction.py:624, 630–637`, `_ground_evidence` | Normalize case/whitespace and check that the quote appears on its cited page. `g_i=1[N(quote_i) is a substring of N(page_(r_i))]`. This is Boolean evidence grounding, not semantic similarity. |
| `app/reconciliation.py:248–263, 487–488` | Route a low-confidence field to review: `p_i<0.80 ⇒ REVIEW`, after earlier missing/abstention checks. |
| `app/reconciliation.py:295–296`, `reconcile_field` | Signed money difference `Δ_i=o_i-e_i`. Numeric match iff `abs(Δ_i)≤τ`; default `τ=0`. Uses Decimal; currency is controlled separately. |
| `app/reconciliation.py:360–361` | Signed date difference `Δ_i^date=days(o_i-e_i)`; match iff zero. |
| `app/reconciliation.py:60–67, 456–459` | Unicode/whitespace/case normalization followed by exact equality: `m_i=1[N(o_i)=N(e_i)]`. Payment references additionally drop non-alphanumeric characters. No fuzzy similarity. |
| `app/normalization.py:198–225` | Parse signs, currency, decimal/group separators into money. With integer digits `I_i`, fraction `F_i` of `d_i` digits and sign `σ_i`, `a_i=σ_i(I_i+F_i/10^{d_i})` after syntax validation. This normalizes monetary syntax, not scores. |
| `streamlit_app.py:1107–1114`, `_table_items` | Stable ascending sort puts human-review rows first, then HIGH severity within each group. Key `K_i=(1-h_i,1-H_i)` for review flag `h_i` and HIGH flag `H_i`. No truncation. |
| `app/evals/runner.py:439–466, 594, 1400` | Sort evaluation failures by fixed category priority, then descending confidence, then case ID/field; display first `max_failures` (CLI default 12). Key `K_i=(a_i,-p_i,case_i,field_i)`, using zero for missing confidence. For ascending lexicographic order `π`, take `π_1,...,π_min(M,n)`. These are failed cases, not retrieved chunks. |
| `app/storage.py:296, 324` | SQL orders audit events by timestamp/ID descending, with limit 200 by default or 1 for latest matching decision. Take a prefix of events ordered by `(t_i,id_i)`, not similarity. |

**Existing evaluation arithmetic**

At `app/evals/metrics.py:274–285`, let `y_i∈{0,1}` be a gold exception/escalation label and `ŷ_i∈{0,1}` the prediction:

- `TP=Σ_i y_iŷ_i`, `FP=Σ_i(1-y_i)ŷ_i`, `FN=Σ_i y_i(1-ŷ_i)`.
- `precision=TP/(TP+FP)` and `recall=TP/(TP+FN)`. `rate` at lines 166–170 returns `None` for a zero denominator.
- F1 arithmetic at lines 293–296 is `2PR/(P+R)` when defined, with preceding branches for undefined/zero cases. This is classification recall, not passage recall@k.

`app/evals/metrics.py:408–416, 443–444` computes descriptive confidence statistics. For correctness `z_i∈{0,1}` over `n` usable fields: `Brier=(1/n)Σ_i(p_i-z_i)^2`. For confidence bins `B_b`: `ECE=Σ_b (|B_b|/n)|mean_(i∈B_b)(p_i)-mean_(i∈B_b)(z_i)|`. These evaluate existing confidences; they do not normalize scores.

**Reference notation for the requested RAG operations — not implemented here**

Let `x_(i,j)` be coordinate `j` of chunk vector `i` and `q_j` the query coordinate, over `d` dimensions and `n` chunks.

- **Cosine:** alignment after removing vector lengths: `s_i=(Σ_(j=1)^d q_j x_(i,j))/(sqrt(Σ_j q_j²) sqrt(Σ_j x_(i,j)²))` for nonzero vectors.
- **Top-k:** choose `π` such that `s_(π_1)≥...≥s_(π_n)`; return `π_1,...,π_min(k,n)` with an explicit tie rule.
- **Optional stable softmax:** `w_i=exp(s_i-m)/Σ_(r=1)^n exp(s_r-m)`, `m=max_r s_r`. Produces weights summing to one, preserves ranking, and is unnecessary just to select top-k. It does not automatically yield calibrated relevance probabilities.

There are no file/line targets for these three operations in this checkout; these equations explain the absent operations without adding code.

## Suggested next steps to make it a portfolio project by Tuesday: max 5 items, smallest first

1. **Resolve the project identity:** present this checkout as FundOps; if RAG is intended, locate its actual code before planning a rewrite.
2. **Preserve a reproducible run recipe:** record tested Python/dependency versions and the 150-test/27-case commands alongside the known misses.
3. **Prepare a short demo:** clean vs discrepant notice, field evidence, two control breaks, and a human audit decision. Label all data synthetic.
4. **Add a retrieval eval once the intended retriever is located/exists:** hand-write 10–20 questions with relevant document/chunk IDs and measure mean recall@5: `(1/Q)Σ_q |R_q∩T_(q,5)|/|R_q|` for nonempty gold sets `R_q`. Define how chunks map to relevant documents. This checkout cannot produce that retrieval baseline yet.
5. **Choose one bounded implementation gap later:** for FundOps, investigate CC-009's comma-decimal reviewer false positive; if the chosen portfolio is RAG instead, scope a tiny offline retrieval path before bulk ingestion or agents.

**Stopping condition:** audit complete; no fixes, refactors, features or suggested next steps were started.

