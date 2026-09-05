# Integration contracts

The normative, detailed interface specification is
[AGENT_CONTRACTS.md](AGENT_CONTRACTS.md). This stable path is the entry point
requested for cross-machine integration and intentionally does not duplicate
that specification.

The canonical workflow is:

```text
extract_document -> ExtractedDocument
reconcile_document(FundRecord, ExtractedDocument) -> ReconciliationReport
review_reconciliation(ReconciliationReport) -> ReviewReport
AuditStore.append(AuditEvent) -> persisted append-only decision
```

Core rules:

- canonical domain types live in `app/models.py`;
- financial parsing and currency normalization live in `app/normalization.py`;
- reconciliation is deterministic and independent of model output;
- evidence review cannot mutate extraction or clear a control break;
- human decisions are document-scoped, reasoned, and append-only;
- fixture evaluation must execute the current production modules and retain
  denominators, provenance, and failed cases.

Any interface migration must update the normative contract, architecture,
callers, persistence compatibility, fixtures, evaluation labels, and tests in
the same branch.
