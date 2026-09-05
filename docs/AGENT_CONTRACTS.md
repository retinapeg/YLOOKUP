# Agent contracts

This file defines the integration contracts for FundOps Control Room. New work must extend these interfaces or migrate them deliberately; it must not introduce a second record model, reconciliation engine, reviewer outcome, or audit event type.

## Canonical imports

```python
from app.models import (
    AuditEvent,
    DocumentType,
    ExtractedDocument,
    ExtractedField,
    ExtractionMethod,
    FundRecord,
    ReconciliationItem,
    ReconciliationReport,
    ReconciliationStatus,
    ReviewDecision,
    Severity,
)
from app.normalization import normalize_currency_code, normalize_monetary_value
from app.extraction import extract_document
from app.reconciliation import reconcile_document
from app.review import review_reconciliation
from app.storage import AuditStore
```

`ReconciliationResult` is an alias of `ReconciliationItem`, and `reconcile_records` is an alias of `reconcile_document`. They exist only for caller compatibility and must not diverge.

## Domain model contract

All canonical domain models live in `app/models.py`. They reject unknown fields, trim string inputs, and validate assignments.

### `FundRecord`

The authoritative record contains:

- identity: `case_id`, `fund_name`, `investor_name`;
- money: `commitment_amount`, `capital_call_amount`, optional `management_fee` as `Decimal`;
- dates: `call_date`, `due_date`;
- control context: three-letter uppercase `currency`, optional `bank_account_reference`, and `document_type`.

`FundRecord.reconciliation_values()` is the stable field order. Add a new reconciled field there only with its normalization rule, severity, UI display, gold-label migration, and tests.

### `ExtractedField`

An extracted value is never just a scalar. It carries:

- `value` as `Decimal`, `date`, `str`, or `None`;
- `source` and optional `source_type`;
- either PDF/TXT `page` provenance or workbook `sheet` plus `cell` provenance;
- exact `evidence` and bounded `confidence`;
- `method` and optional exact `extractor` identifier;
- optional timezone-aware `timestamp`;
- optional `abstention_reason` when no trustworthy value is selected.

Callers must preserve field-level method provenance in hybrid extraction. A deterministic fill-in must not be relabelled as model output. A workbook cell requires its sheet, and page provenance must not be combined with a workbook locator.

### `ExtractedDocument`

`ExtractedDocument` binds `case_id`, original `source_document`, `document_type`, the canonical field map, top-level `extraction_method`, and user-visible `warnings`. `value_for(name)` is the supported scalar accessor.

Warnings disclose fallback or partial processing. They are not a substitute for per-field provenance.

### Reconciliation

`ReconciliationItem` contains `field`, typed `expected` and `observed`, `status`, `severity`, optional signed `difference`, plain-language `explanation`, and copied field `provenance`. Numeric items may also carry expected/observed currency context so displays do not mislabel cross-currency values.

`ReconciliationStatus` semantics are:

| Status | Meaning |
| --- | --- |
| `PASS` | The supported deterministic comparison passed. |
| `MISMATCH` | Trustworthy typed values differ under the configured rule. |
| `MISSING` | A value expected by the canonical record was not extracted. |
| `REVIEW` | The comparison must abstain because the value, confidence, syntax, or context is ambiguous/unsupported. |

`Severity.NONE` is reserved for `PASS`; control exceptions use `LOW`, `MEDIUM`, or `HIGH` according to the fixed policy in `app/reconciliation.py`.

`ReconciliationReport` contains the stable `results` list, counts for every status, overall status, source metadata, and UTC generation time. `exceptions` means every non-`PASS` item. Overall precedence is `MISMATCH`, then `MISSING`, then `REVIEW`, then `PASS`.

## Deterministic normalization contract

`app/normalization.py` is the canonical financial normalization boundary. `normalize_monetary_value(value)` returns a finite `Decimal` plus any explicit currency. It supports declared US/EU separators, common currency codes/symbols, signs, and balanced accounting parentheses. It raises a typed normalization error when syntax has multiple defensible interpretations or is unsupported.

Consumers must turn normalization errors into `REVIEW` or an explicit extraction abstention. They must never delete punctuation blindly, coerce percentages to money, use binary floating-point for control arithmetic, or guess through malformed grouping.

`normalize_currency_code(value)` produces the three-letter uppercase comparison form. Entity-name and evidence parsing performed by the independent reviewer remain intentionally separate checks; reviewer agreement cannot define the production value.

## Workflow interfaces

### Extraction

```python
extract_document(path, *, extractor=None, case_id=None) -> ExtractedDocument
```

The default extractor is deterministic and offline. The optional OpenAI-compatible extractor uses the same result contract. Model fields are accepted only when their page, evidence occurrence, confidence, field name, and typed value validate. Provider absence or invalid output returns a disclosed deterministic fallback when one is available.

### Reconciliation

```python
reconcile_document(
    fund_record,
    document,
    *,
    numeric_tolerance=Decimal("0"),
    confidence_threshold=0.80,
) -> ReconciliationReport
```

This function is pure with respect to persistence and external services. It validates matching `case_id` values and does not call a model. Given the same inputs and configuration, it must return the same field outcomes and differences apart from `generated_at`.

### Independent evidence review

```python
review_reconciliation(report, reviewer=None) -> ReviewReport
```

Review types live in `app/review.py`: `SUPPORTED`, `CHALLENGE`, `INSUFFICIENT_EVIDENCE`, and `NOT_REVIEWED`. The default reviewer is an offline independent parser. Model review is optional and receives one minimized field request at a time.

The reviewer must not mutate extraction or reconciliation. `requires_human_review` is true whenever reconciliation is non-`PASS` or review is non-`SUPPORTED`. Therefore `SUPPORTED` can never clear a deterministic mismatch.

### Human audit

```python
store = AuditStore(path)
saved = store.append(event)
history = store.list_events(case_id=..., document_id=...)
latest = store.latest_decision(case_id, field, document_id=...)
```

`ReviewDecision` is strictly `APPROVED`, `NEEDS_INVESTIGATION`, or `REJECTED`. `AuditEvent` snapshots the case/document scope, field, expected and observed values plus their currency context, difference, reviewer status, source locator, audit request ID, actor, reason (`note`), decision, and timezone-aware creation time.

An audit event is append-only. Do not add update/delete/upsert behavior or reuse a decision from a different document digest. A UI success state may be shown only after `append` returns.

### Evaluation

```python
run_evaluation(EvaluationConfig(...)) -> dict
```

The versioned gold schema and generated report schema live under `app/evals/`. The runner must execute current production modules, verify document hashes by default, retain explicit supports, and write complete artifacts atomically. Fixture, hybrid-pipeline, and grounded model-origin metrics remain separately labelled. Missing reviewer context remains a visible failed case, not an inferred success.

## Failure and observability contract

Public failures use only `WorkflowErrorCode`, `WorkflowStage`, safe catalogue messages, and UUID request IDs from `app/errors.py`. Unexpected exceptions are sanitized before display. Structured stage logs accept only request ID, allowlisted stage, duration, outcome, and safe error code—never document text, evidence, filenames, prompts, provider responses, or credentials.

A user-triggered document workflow has one request ID across validation, extraction, normalization/reconciliation, and independent review. A later human decision has its own request ID for the audit append, and that ID is stored with the event. Work-in-progress results are committed to Streamlit session state only after all required stages complete, preserving the last good case on failure.

## Integration rules

1. Read this file and `docs/ARCHITECTURE.md` before changing a boundary.
2. Extend the canonical module; do not add names such as `models_v2.py`, `reconciliation2.py`, or a second audit table.
3. Keep AI interpretation, deterministic controls, evidence review, and human decisions as separate authorities.
4. Preserve provenance through every adapter. Never replace a source-backed missing value with an invented scalar.
5. Treat schema/status changes as migrations: update consumers, fixtures, evaluation labels, documentation, and tests together.
6. Keep fixture mode offline and deterministic. A missing key is a supported operating mode, not an exceptional crash path.
7. Report limitations and denominators. Do not turn synthetic regression results into production or model-accuracy claims.
