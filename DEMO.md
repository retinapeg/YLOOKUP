# FundOps Control Room demo

The flagship is a fictional Northstar Growth Fund II capital call for Alderstone Civic Pension Partnership. The administrator register expects **GBP 625,000** at **`LP Register!I2`**. The incoming two-page PDF states **GBP 650,000** and also contains stale historical amounts. The control engine returns a **HIGH** severity **GBP +25,000** variance.

## Before presenting

Start with a fresh local audit database so the human-decision moment is clean:

```bash
source .venv/bin/activate
DEMO_RUN_DIR="$(mktemp -d)"
FUNDOPS_DB_PATH="$DEMO_RUN_DIR/audit.db" streamlit run streamlit_app.py
```

Use the default offline mode for the most reliable demo. It runs the deterministic fixture extractor and reviewer, and it is labelled that way in the UI. If a real `OPENAI_API_KEY` is configured, you may enable **Use OpenAI-compatible extraction** before loading the case; only call it an AI run if the Extraction Ledger actually says **OpenAI Compatible** and there is no fallback warning.

Do not describe the synthetic benchmark as production performance. Do not imply that **Approved** moves money; it only records a review decision.

## 30-second version

1. Click **Load Demo Case**.
2. Point to the red control-break card: “The register expects **GBP 625,000**. The incoming notice says **GBP 650,000**. Exact decimal logic—not a model—produces a **GBP 25,000 HIGH-severity variance**.”
3. Click **Exception Queue (2)**: “Every extracted value carries the exact PDF sentence and page. A separate evidence reviewer confirms that the source supports 650,000; that does not approve the notice.”
4. Click **Evals**: “This is an executable 27-document synthetic regression run: **264/270** exact fields, **12/12** gold field exceptions across the 21 replayable cases, and **210/210** isolated deterministic rule outcomes. It made **zero model calls**, so these are not LLM or production claims.”

Close with: “AI handles the fuzzy document boundary when enabled; deterministic software owns financial controls; humans own consequential decisions.”

## 90-second version

1. Start on the six-step strip. “Fund operations starts with different evidence: an Excel register and a messy multi-page PDF. We normalize the facts, run deterministic controls, independently verify evidence, ask a human to decide, and measure the system.”
2. Click **Load Demo Case**. Pause on the control-break card. Read the three values: **GBP 625,000 expected**, **GBP 650,000 observed**, **GBP +25,000 variance**, **HIGH severity**.
3. Click **Fund Record**. Show `investor_register.xlsx` and `LP Register!I2`. Say: “This is a real synthetic workbook artifact and an exact cell reference. The MVP loads a checked-in JSON snapshot of that row; it does not pretend to have a live Excel connector.”
4. Click **Reconciliation Results**. “The notice contains several amounts, including historical calls. Extraction selects typed current-call fields and retains source provenance. In offline demo mode that parser is deterministic; the implemented optional LLM path uses the same schema and rejects model fields whose cited text is not found on the claimed page.”
5. Click **Exception Queue (2)**. Show the expected/observed/difference cards, the exact sentence `Capital Call Amount: GBP 650,000.00`, **PDF page 1**, and the **SUPPORTED** independent finding. Say: “Supported means grounded in the document—not financially correct.”
6. Enter `Hold pending administrator confirmation` and choose **Needs investigation**. Click **Audit Log**. Show the event ID, human action, expected and observed values, evidence-review result, reason, source locator, and package digest.
7. Click **Evals**. Read the denominators, then point to **Known failure cases**. “The regression gates pass, but every failure remains visible. The reviewer still misses five cases that need context beyond a field-specific snippet: ambiguity, cross-page, batch, cross-field, and multi-document controls. This is a regression suite, not a victory-lap percentage.”

Close with: “The model never gets authority to decide whether money reconciles, and the system never converts uncertainty into approval.”

## 3-minute version

### 0:00–0:30 — Set the control problem

“A fund-operations analyst receives an administrator register and a capital-call PDF. The PDF is two pages and includes the current call, a fee, an adjustment, and three old settled calls. The hard problem is not chatting with the PDF; it is turning messy evidence into a reviewable financial control without losing the source.”

Use the six-step strip to establish the architecture: multi-source inputs → semantic extraction → deterministic reconciliation → independent evidence review → human decision → evaluation.

### 0:30–1:05 — Reveal the break

Click **Load Demo Case**.

“The authoritative register snapshot expects **GBP 625,000**. The incoming notice states **GBP 650,000**. The reconciliation layer uses Python `Decimal` values and zero tolerance, so it deterministically calculates **GBP +25,000** and assigns **HIGH** severity. The due date is also two days early, proving this is a field-level rules engine rather than a single hard-coded comparison.”

Click **Fund Record** and show `investor_register.xlsx` → `LP Register!I2`, plus the download controls for both source artifacts. State the MVP boundary: the JSON record is the checked-in canonical snapshot of the synthetic workbook; live Excel ingestion is future work.

### 1:05–1:45 — Prove evidence and separation of duties

Click **Reconciliation Results**, select **Capital call**, and point to the evidence drawer: filename, **PDF page 1**, extractor, confidence, and verbatim sentence.

“A model is useful where language and layout vary. The optional OpenAI-compatible adapter is genuinely implemented for that extraction boundary. It must return a known field, typed value, page, confidence, and evidence text. The application verifies that the evidence occurs on that page; unsupported fields are discarded and the fallback is disclosed. This particular run is the offline deterministic mode, which is why the UI says Deterministic.”

Click **Exception Queue (2)**.

“The reviewer is a separate stage. The default offline reviewer independently re-parses the field-specific evidence and says **SUPPORTED**. That only means the PDF supports the extracted 650,000. It does not clear the 25,000 discrepancy. When the current pipeline detects missing, low-confidence, conflicting, unsupported, or unreviewed evidence, it keeps the case escalated. The visible cross-page-conflict miss shows where detection still needs work.”

### 1:45–2:20 — Put the human in control

Enter `Hold pending administrator confirmation` and choose **Needs investigation**. Open **Audit Log**.

“The human owns the consequential action. This event is append-only and includes the decision, reason, actor, UTC time, expected value, observed value, variance, evidence-review result, exact source locator, and a digest that binds the source bytes, canonical record, and extraction. This is decision auditability, not a claim of cryptographic ledger immutability. No button here moves money.”

### 2:20–3:00 — Show measurement, including failure

Click **Evals**.

“These figures were just computed from the current code over 27 fictional documents and 270 labelled fields. Exact normalized extraction is **264/270 (97.8%)**. Field-level exception recall is **12/12 (100%)** and precision is **12/16 (75.0%)**. When extraction is held constant, reconciliation rule correctness is **210/210 (100%)**. Correct abstention is **27/29 (93.1%)**. All four applicable count-based regression gates pass.”

Then point to **Known failure cases**:

“We keep failures in the demo. Locale-formatted amounts, OCR-like corruption, entity aliases, cross-page conflicts, and cross-document controls still need work. Reviewer escalation is **12/17 recall**, because five labelled cases require context beyond the field-specific snippet: ambiguity, cross-page, batch, cross-field, and multi-document checks. There are no field-level reviewer challenge labels yet. The run made **zero model calls**, so nothing here is a production or LLM-performance claim.”

Close with: “The engineering choice is the product: probabilistic extraction at the fuzzy edge, deterministic reconciliation at the financial core, an independent evidence check, and a human decision backed by an executable audit and eval trail.”
