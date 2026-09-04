# Synthetic private-markets reconciliation dataset

This corpus contains entirely fictional institutional investors, funds, notices, references, and amounts. It includes no personal data and no usable payment coordinates. Every source document is marked `FICTIONAL - DEMO ONLY - DO NOT PAY`.

## Dataset layout

- `data/fund_record.json` and `data/sample_documents/` support the one-click Northstar/Albion MVP flow.
- `data/demo/northstar_growth_fund_ii/` is the polished two-page Northstar package with its LP register, canonical record, notice, and case-level gold label.
- `data/evals/investor_register.csv` and `.xlsx` hold canonical register rows for the evaluation cases.
- `data/evals/notices/` contains text fixtures. Form-feed characters delimit pages.
- `data/gold/capital_call_reconciliation.json` is the authoritative machine-readable manifest; its companion schema documents the stable envelope.

## Gold-label contract

Money values are lossless decimal strings, dates are ISO `YYYY-MM-DD`, currencies are ISO 4217 codes, and missing or unresolved extracted values are explicit JSON `null`. An omitted key is unlabelled; a present key with `null` is labelled missing, ambiguous, or conflicting, with the reason in `expected_extraction.null_reasons`.

Each case contains all ten canonical reconciliation fields, per-field expected/observed values, status, severity, signed difference, exception code, and evidence IDs. Evidence records point to an exact file, page, and line. `additional_results` carries batch or cross-document controls such as duplicate notice IDs and payment receipts without changing the application's core record model.

`replayable_against_current_reconciler` means the current deterministic reconciler can reproduce the labelled field results from the canonical extracted values. Ambiguity, duplicate detection, cross-field capacity controls, and payment matching remain explicit evaluation targets even when the current application does not yet implement them.

## Scenario rationale

| Case | Scenario | Why it exists | Expected outcome |
| --- | --- | --- | --- |
| CC-001 | Exact match control | The notice matches the Northstar administrator record across all ten canonical fields. | PASS / NONE; exceptions: none |
| CC-002 | Northstar flagship multi-field exception | A polished two-page notice overstates Call 04 by GBP 25,000 and advances the due date by two days; a settled prior-call amount is retained as stale context. | MISMATCH / HIGH; exceptions: capital_call_amount, due_date |
| CC-003 | Currency mismatch | The numeric amount matches, but the notice denominates the call in USD while the fund record requires GBP. | MISMATCH / HIGH; exceptions: currency |
| CC-004 | Due-date mismatch | The notice date is correct, but settlement is requested seven days later than the administrator record. | MISMATCH / HIGH; exceptions: due_date |
| CC-005 | Investor naming variation | A shortened investor name omits punctuation and expands to the same legal entity after entity-name normalization. | PASS / NONE; exceptions: none |
| CC-006 | Commitment omitted from notice | The administrator register contains the commitment, but the capital-call notice never states it. | MISSING / HIGH; exceptions: commitment_amount |
| CC-007 | Duplicate investor register match | Two active LP rows share the same legal name but carry different LP IDs and commitments; the notice omits the LP ID needed to choose one. | REVIEW / HIGH; exceptions: investor_name |
| CC-008 | Percentage stated instead of amount | The notice states only 2.50% of commitment; GBP 300,000 is derivable but no explicit call amount appears. | MISSING / HIGH; exceptions: capital_call_amount |
| CC-009 | European comma-decimal formatting | Amounts use full stops as thousands separators and commas as decimal marks, testing locale-aware normalization. | PASS / NONE; exceptions: none |
| CC-010 | Parenthetical negative amount | The notice presents the amount in accounting parentheses, which denotes a negative value rather than a positive capital call. | MISMATCH / HIGH; exceptions: capital_call_amount |
| CC-011 | OCR-like corrupted amount | The digit 2 and zeros are corrupted as letters; an amount-in-words line corroborates the normalized value. | PASS / NONE; exceptions: none |
| CC-012 | Conflicting amounts across pages | Two pages independently label different values as the current capital-call amount, so no single canonical amount is selected. | REVIEW / HIGH; exceptions: capital_call_amount |
| CC-013 | Stale previous-call amount | A history schedule contains Call 04 at GBP 500,000, while the clearly labelled current Call 05 amount is GBP 625,000. | PASS / NONE; exceptions: none |
| CC-014 | Ambiguous fund name | The notice says only Northstar Growth Fund, and the LP appears in both Fund I and Fund II without a fund identifier. | REVIEW / HIGH; exceptions: fund_name |
| CC-015 | Mixed date formats | The notice uses written and slash-form dates that normalize to the ISO dates held by the administrator. | PASS / NONE; exceptions: none |
| CC-016 | Total versus remaining commitment confusion | The notice labels the GBP 12.5m remaining commitment as total commitment; the legal commitment is GBP 20m. | MISMATCH / HIGH; exceptions: commitment_amount |
| CC-017 | Wrong payment reference | The notice carries the prior call's payment reference even though the amount and dates match. | MISMATCH / MEDIUM; exceptions: bank_account_reference |
| CC-018 | Due date missing | The notice asks the LP to pay promptly but contains no calendar due date. | MISSING / HIGH; exceptions: due_date |
| CC-019 | Notice belongs to the wrong investor | Fund and amount happen to agree, but the notice is addressed to a different institutional LP. | MISMATCH / HIGH; exceptions: investor_name |
| CC-020 | Duplicated capital-call notice | Two files carry the same notice ID and identical capital-call content; each document passes alone, but the batch must be held as a duplicate. | REVIEW / HIGH; exceptions: document_id |
| CC-021 | Fractional-cent precision variance | The administrator amount differs by half a penny before presentation rounding; the current zero-tolerance control retains the variance rather than silently clearing it. | MISMATCH / HIGH; exceptions: capital_call_amount |
| CC-022 | Capital component versus total due | The notice separates a GBP 600,000 capital contribution from a GBP 25,000 management fee and a GBP 625,000 total due. | PASS / NONE; exceptions: none |
| CC-023 | Dual-currency settlement equivalent | The legal call is USD 800,000; GBP 625,000 is clearly labelled as a settlement equivalent at a locked rate. | PASS / NONE; exceptions: none |
| CC-024 | Same investor across two sleeves | Two register rows share the same legal entity, but the notice includes the LP account ID that selects Sleeve B. | PASS / NONE; exceptions: none |
| CC-025 | Call exceeds remaining commitment | The notice matches the expected call record, but GBP 700,000 exceeds the GBP 625,000 uncalled balance before the drawdown. | REVIEW / HIGH; exceptions: capital_call_amount |
| CC-026 | Amended notice supersedes original | A later notice explicitly supersedes the GBP 600,000 original and correctly states the current GBP 625,000 call. | PASS / NONE; exceptions: none |
| CC-027 | Payment receipt shortfall | The notice and register agree at GBP 625,000, but the associated receipt records GBP 624,980, leaving GBP 20 unresolved. | MISMATCH / HIGH; exceptions: payment_amount |

## Northstar demo package

The extended benchmark package reconciles Alderstone Civic Pension Partnership against Northstar Growth Fund II Call 04. It is separate from the primary Albion one-click demo and exists only for evaluator stress testing. The register expects GBP 625,000 due 30 September 2026. The notice states GBP 650,000 due 28 September 2026, while all other canonical fields agree. Page 2 includes a settled GBP 500,000 prior call as a deliberately stale distractor.

The separate one-click MVP case uses Albion Capital Partners and follows the primary product brief: GBP 5,000,000 commitment, GBP 625,000 expected call, and 18 September 2026 due date. Its discrepancy notice changes only the call amount to GBP 650,000 and due date to 20 September 2026.

## Regeneration and validation

Run `python scripts/generate_gold_dataset.py`, then `node scripts/generate_investor_workbooks.mjs`, then `python scripts/generate_fixture_pdfs.py` using environments that provide the documented artifact dependencies. Run `pytest -q` to validate schema shape, cross-file references, evidence locators, register parity, duplicate declarations, financial invariants, and replayable reconciliation labels.

Do not use these fixtures for payment, onboarding, sanctions screening, tax reporting, or any real investor workflow.
