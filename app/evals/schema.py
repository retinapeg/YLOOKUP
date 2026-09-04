"""Validated access to the versioned FundOps gold evaluation corpus.

The corpus intentionally stays independent of production Pydantic models.  That
keeps malformed or incomplete labels from being silently coerced into whatever
the application currently happens to emit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = (
    PROJECT_ROOT / "data" / "gold" / "capital_call_reconciliation.json"
)


class GoldDatasetError(ValueError):
    """Raised when a gold manifest is unsafe or ambiguous to evaluate."""


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GoldDatasetError(f"{context} must be an object")
    return value


def _sequence(value: Any, context: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise GoldDatasetError(f"{context} must be an array")
    return value


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldDatasetError(f"{context} must be a non-empty string")
    return value.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_reference(manifest_path: Path, reference: str) -> Path:
    raw_path = Path(reference)
    if raw_path.is_absolute():
        candidates = [raw_path]
    else:
        candidates = [parent / raw_path for parent in manifest_path.parents]

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    tried = ", ".join(str(candidate) for candidate in candidates)
    raise GoldDatasetError(
        f"document reference {reference!r} does not resolve to a file; tried: {tried}"
    )


@dataclass(frozen=True)
class GoldDocument:
    document_id: str
    reference: str
    path: Path
    disposition: str
    sha256: Optional[str]


@dataclass(frozen=True)
class GoldCase:
    case_id: str
    title: str
    description: str
    scenario_types: Tuple[str, ...]
    replayable: bool
    documents: Tuple[GoldDocument, ...]
    canonical_record: Mapping[str, Any]
    expected_values: Mapping[str, Any]
    field_evidence: Mapping[str, Any]
    expected_field_results: Mapping[str, Mapping[str, Any]]
    expected_additional_results: Mapping[str, Mapping[str, Any]]
    expected_overall_status: str
    reviewer_label: Mapping[str, Any]

    @property
    def primary_document(self) -> GoldDocument:
        """Return the authoritative single-document input for this workflow.

        Amended cases are deliberately ordered with the superseded notice first,
        so choosing the first document would silently score the wrong source.
        """

        current = [
            document
            for document in self.documents
            if document.disposition.casefold() == "current"
        ]
        if len(current) == 1:
            return current[0]
        if len(current) > 1:
            raise GoldDatasetError(
                f"case {self.case_id} has multiple CURRENT documents"
            )
        if len(self.documents) == 1:
            return self.documents[0]
        raise GoldDatasetError(
            f"case {self.case_id} has multiple documents but no unique CURRENT input"
        )


@dataclass(frozen=True)
class GoldDataset:
    schema_version: str
    dataset_id: str
    synthetic: bool
    fields: Tuple[str, ...]
    statuses: Tuple[str, ...]
    severities: Tuple[str, ...]
    cases: Tuple[GoldCase, ...]
    path: Path
    sha256: str

    def case(self, case_id: str) -> GoldCase:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(case_id)


def load_gold_dataset(
    path: Path = DEFAULT_DATASET_PATH,
    *,
    verify_document_hashes: bool = True,
) -> GoldDataset:
    """Load and defensively validate a versioned gold manifest.

    A JSON Schema is shipped next to the dataset for other tooling.  The runner
    uses these dependency-free checks as well so evaluation still works in the
    minimal offline demo environment.
    """

    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise GoldDatasetError(f"gold dataset not found: {manifest_path}")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldDatasetError(
            f"could not read gold dataset {manifest_path}: {type(exc).__name__}"
        ) from exc

    root = _mapping(payload, "dataset")
    schema_version = _required_text(root.get("schema_version"), "schema_version")
    if schema_version.split(".", 1)[0] != "1":
        raise GoldDatasetError(
            f"unsupported gold schema version {schema_version!r}; expected major version 1"
        )
    dataset_id = _required_text(root.get("dataset_id"), "dataset_id")
    synthetic = root.get("synthetic")
    if not isinstance(synthetic, bool):
        raise GoldDatasetError("synthetic must be a boolean")

    raw_fields = _sequence(root.get("reconciliation_fields"), "reconciliation_fields")
    fields = tuple(
        _required_text(field, f"reconciliation_fields[{index}]")
        for index, field in enumerate(raw_fields)
    )
    if not fields or len(set(fields)) != len(fields):
        raise GoldDatasetError("reconciliation_fields must be non-empty and unique")

    statuses = tuple(
        _required_text(status, "status_values entry")
        for status in _sequence(root.get("status_values"), "status_values")
    )
    severities = tuple(
        _required_text(severity, "severity_values entry")
        for severity in _sequence(root.get("severity_values"), "severity_values")
    )
    if "PASS" not in statuses:
        raise GoldDatasetError("status_values must include PASS")

    raw_cases = _sequence(root.get("cases"), "cases")
    if not raw_cases:
        raise GoldDatasetError("gold dataset contains no cases")

    cases = []
    seen_case_ids = set()
    for case_index, raw_case in enumerate(raw_cases):
        case_data = _mapping(raw_case, f"cases[{case_index}]")
        case_id = _required_text(case_data.get("case_id"), f"cases[{case_index}].case_id")
        if case_id in seen_case_ids:
            raise GoldDatasetError(f"duplicate case_id: {case_id}")
        seen_case_ids.add(case_id)

        raw_documents = _sequence(case_data.get("documents"), f"{case_id}.documents")
        if not raw_documents:
            raise GoldDatasetError(f"{case_id}.documents must not be empty")
        documents = []
        for document_index, raw_document in enumerate(raw_documents):
            document_data = _mapping(
                raw_document, f"{case_id}.documents[{document_index}]"
            )
            reference = _required_text(
                document_data.get("path"),
                f"{case_id}.documents[{document_index}].path",
            )
            document_path = _resolve_reference(manifest_path, reference)
            expected_hash = document_data.get("sha256")
            if expected_hash is not None:
                expected_hash = _required_text(
                    expected_hash,
                    f"{case_id}.documents[{document_index}].sha256",
                ).casefold()
                if verify_document_hashes:
                    actual_hash = _sha256(document_path)
                    if actual_hash != expected_hash:
                        raise GoldDatasetError(
                            f"{case_id} document hash mismatch for {reference}: "
                            f"expected {expected_hash}, got {actual_hash}"
                        )
            documents.append(
                GoldDocument(
                    document_id=_required_text(
                        document_data.get("document_id"),
                        f"{case_id}.documents[{document_index}].document_id",
                    ),
                    reference=reference,
                    path=document_path,
                    disposition=str(document_data.get("disposition") or "").strip(),
                    sha256=expected_hash,
                )
            )

        canonical_record = _mapping(
            case_data.get("canonical_record"), f"{case_id}.canonical_record"
        )
        if canonical_record.get("case_id") != case_id:
            raise GoldDatasetError(
                f"{case_id}.canonical_record.case_id must equal the case id"
            )

        expected_extraction = _mapping(
            case_data.get("expected_extraction"), f"{case_id}.expected_extraction"
        )
        expected_values = _mapping(
            expected_extraction.get("canonical_values"),
            f"{case_id}.expected_extraction.canonical_values",
        )
        unknown_value_fields = set(expected_values) - set(fields)
        if unknown_value_fields:
            raise GoldDatasetError(
                f"{case_id} labels unknown extraction fields: "
                + ", ".join(sorted(unknown_value_fields))
            )

        expected_reconciliation = _mapping(
            case_data.get("expected_reconciliation"),
            f"{case_id}.expected_reconciliation",
        )
        field_results_raw = _mapping(
            expected_reconciliation.get("field_results"),
            f"{case_id}.expected_reconciliation.field_results",
        )
        field_results: Dict[str, Mapping[str, Any]] = {}
        for field_name, raw_result in field_results_raw.items():
            if field_name not in fields:
                raise GoldDatasetError(
                    f"{case_id} reconciliation labels unknown field {field_name!r}"
                )
            result = _mapping(raw_result, f"{case_id}.field_results.{field_name}")
            status = _required_text(
                result.get("status"), f"{case_id}.field_results.{field_name}.status"
            )
            severity = _required_text(
                result.get("severity"),
                f"{case_id}.field_results.{field_name}.severity",
            )
            if status not in statuses:
                raise GoldDatasetError(
                    f"{case_id}.{field_name} uses unknown status {status!r}"
                )
            if severity not in severities:
                raise GoldDatasetError(
                    f"{case_id}.{field_name} uses unknown severity {severity!r}"
                )
            field_results[field_name] = result

        overall_status = _required_text(
            expected_reconciliation.get("overall_status"),
            f"{case_id}.expected_reconciliation.overall_status",
        )
        if overall_status not in statuses:
            raise GoldDatasetError(
                f"{case_id} uses unknown overall status {overall_status!r}"
            )

        scenario_types = tuple(
            _required_text(value, f"{case_id}.scenario_types entry")
            for value in _sequence(case_data.get("scenario_types", []), f"{case_id}.scenario_types")
        )
        replayable = case_data.get("replayable_against_current_reconciler")
        if not isinstance(replayable, bool):
            raise GoldDatasetError(
                f"{case_id}.replayable_against_current_reconciler must be a boolean"
            )

        additional_raw = _mapping(
            expected_reconciliation.get("additional_results", {}),
            f"{case_id}.expected_reconciliation.additional_results",
        )
        additional_results = {
            str(name): _mapping(result, f"{case_id}.additional_results.{name}")
            for name, result in additional_raw.items()
        }

        case = GoldCase(
            case_id=case_id,
            title=str(case_data.get("title") or case_id),
            description=str(case_data.get("description") or ""),
            scenario_types=scenario_types,
            replayable=replayable,
            documents=tuple(documents),
            canonical_record=canonical_record,
            expected_values=expected_values,
            field_evidence=_mapping(
                expected_extraction.get("field_evidence", {}),
                f"{case_id}.expected_extraction.field_evidence",
            ),
            expected_field_results=field_results,
            expected_additional_results=additional_results,
            expected_overall_status=overall_status,
            reviewer_label=_mapping(
                case_data.get("reviewer_label", {}), f"{case_id}.reviewer_label"
            ),
        )
        # Resolve now so ambiguity is caught before any model/API calls occur.
        case.primary_document
        cases.append(case)

    return GoldDataset(
        schema_version=schema_version,
        dataset_id=dataset_id,
        synthetic=synthetic,
        fields=fields,
        statuses=statuses,
        severities=severities,
        cases=tuple(sorted(cases, key=lambda item: item.case_id)),
        path=manifest_path,
        sha256=_sha256(manifest_path),
    )


__all__ = [
    "DEFAULT_DATASET_PATH",
    "PROJECT_ROOT",
    "GoldCase",
    "GoldDataset",
    "GoldDatasetError",
    "GoldDocument",
    "load_gold_dataset",
]
