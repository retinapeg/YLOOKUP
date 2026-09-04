from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import app.evals.runner as eval_runner
from app.extraction import (
    OPENAI_EXTRACTION_SYSTEM_PROMPT,
    DeterministicExtractor,
    OpenAICompatibleExtractor,
)
from app.evals import (
    DEFAULT_DATASET_PATH,
    FIXTURE_LABEL,
    EvaluationConfig,
    GoldDatasetError,
    frontend_evaluation_summary,
    load_evaluation_results,
    load_gold_dataset,
    run_evaluation,
    write_evaluation_results,
)
from app.evals.telemetry import InstrumentedOpenAIExtractor
from app.models import ExtractionMethod


ROOT = Path(__file__).resolve().parents[1]
SELECTED_CASES = ("CC-001", "CC-002")


@pytest.fixture(scope="module")
def fixture_report() -> dict:
    return run_evaluation(
        EvaluationConfig(
            case_ids=SELECTED_CASES,
            output_path=None,
            enable_reviewer=False,
        ),
        write_output=False,
    )


def test_gold_loader_verifies_document_hashes(tmp_path: Path):
    payload = json.loads(DEFAULT_DATASET_PATH.read_text(encoding="utf-8"))
    payload["cases"] = [payload["cases"][0]]

    source = load_gold_dataset().case("CC-001").primary_document.path
    document = tmp_path / source.name
    document.write_bytes(source.read_bytes())
    expected_hash = hashlib.sha256(document.read_bytes()).hexdigest()
    payload["cases"][0]["documents"][0]["path"] = str(document)
    payload["cases"][0]["documents"][0]["sha256"] = expected_hash

    manifest = tmp_path / "gold.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    dataset = load_gold_dataset(manifest, verify_document_hashes=True)
    assert dataset.case("CC-001").primary_document.path == document.resolve()
    assert dataset.case("CC-001").primary_document.sha256 == expected_hash

    document.write_bytes(document.read_bytes() + b"\ntampered")
    with pytest.raises(GoldDatasetError, match="document hash mismatch"):
        load_gold_dataset(manifest, verify_document_hashes=True)


def test_fixture_run_has_expected_counts_and_skips_reviewer_cleanly(
    fixture_report: dict,
):
    summary = fixture_report["summary"]

    assert fixture_report["run"]["mode"] == "fixture"
    assert summary["label"] == FIXTURE_LABEL
    assert summary["sample_size"]["selected_cases"] == len(SELECTED_CASES)
    assert summary["sample_size"]["selected_documents"] == len(SELECTED_CASES)
    assert summary["sample_size"]["labelled_extraction_fields"] == 20
    assert summary["operating"]["documents"] == {
        **summary["operating"]["documents"],
        "attempted": 2,
        "successful": 2,
        "fallback": 0,
        "failed": 0,
    }

    assert summary["reviewer"]["implementation_available"] is False
    assert summary["reviewer"]["escalation"]["coverage"] == {
        "value": 0.0,
        "numerator": 0,
        "denominator": 2,
    }
    assert all(
        case["reviewer"]
        == {
            "completed": False,
            "error_type": None,
            "requires_human_review": False,
            "challenge_fields": [],
            "counts": None,
        }
        for case in fixture_report["cases"]
    )
    assert not any(
        failure["field"] == "__reviewer_escalation__"
        or failure["failure_category"].startswith("reviewer_")
        for failure in fixture_report["failures"]
    )


def test_frontend_service_load_roundtrip(fixture_report: dict, tmp_path: Path):
    output = tmp_path / "eval-results.json"
    write_evaluation_results(fixture_report, output)

    loaded = load_evaluation_results(output)
    frontend = frontend_evaluation_summary(output)

    assert loaded == fixture_report
    assert frontend["label"] == FIXTURE_LABEL
    assert frontend["mode"] == "fixture"
    assert frontend["generated_at"] == fixture_report["generated_at"]
    assert frontend["dataset"] == {
        "id": fixture_report["dataset"]["id"],
        "schema_version": fixture_report["dataset"]["schema_version"],
        "sha256": fixture_report["dataset"]["sha256"],
        "synthetic": True,
    }
    assert frontend["pipeline_extraction"] == {
        "scope": "deterministic_fixture_pipeline",
        "exact_normalized_field_accuracy": fixture_report["summary"]["extraction"][
            "exact_normalized_field_accuracy"
        ],
    }
    assert frontend["model_origin_extraction"] is None
    assert "extraction_accuracy" not in frontend
    assert frontend["sample_size"]["selected_cases"] == 2
    assert frontend["operating"]["documents"]["attempted"] == 2
    assert frontend["reviewer"] == fixture_report["summary"]["reviewer"]


def test_parenthetical_negative_fixture_cannot_become_a_false_negative():
    report = run_evaluation(
        EvaluationConfig(
            case_ids=("CC-010",),
            output_path=None,
            enable_reviewer=False,
        ),
        write_output=False,
    )

    case = report["cases"][0]
    amount = case["extraction"]["fields"]["capital_call_amount"]
    reconciliation = case["reconciliation"]["fields"]["capital_call_amount"]
    assert amount["correct"] is True
    assert amount["observed"] == "-125000.00"
    assert reconciliation["observed_status"] == "MISMATCH"
    assert report["summary"]["exception_detection"]["field_level"]["false_negative"] == 0


def test_model_metrics_exclude_deterministic_fill_ins_from_model_quality(
    tmp_path: Path,
):
    def partial_model_response(_prompt: str):
        return {
            "fields": {
                "capital_call_amount": {
                    "value": "GBP 625,000.00",
                    "page": 1,
                    "confidence": 0.98,
                    "evidence": "Capital Call Amount: GBP 625,000.00",
                }
            }
        }

    report = run_evaluation(
        EvaluationConfig(
            mode="model",
            case_ids=("CC-001",),
            output_path=None,
            enable_reviewer=False,
        ),
        extractor=InstrumentedOpenAIExtractor(transport=partial_model_response),
        write_output=False,
    )

    summary = report["summary"]
    fields = report["cases"][0]["extraction"]["fields"]
    assert sum(field["method"] == "OPENAI_COMPATIBLE" for field in fields.values()) == 1
    assert sum(field["method"] == "DETERMINISTIC" for field in fields.values()) == 9

    assert summary["extraction"]["exact_normalized_field_accuracy"] == {
        "value": 1.0,
        "numerator": 10,
        "denominator": 10,
    }
    assert summary["operating"]["documents"]["model_coverage"] == {
        "value": 0.0,
        "numerator": 0,
        "denominator": 1,
    }
    assert summary["operating"]["documents"]["model_partial"] == 1
    provenance = summary["operating"]["model_field_provenance"]
    assert provenance["all_labelled_field_coverage"] == {
        "value": 0.1,
        "numerator": 1,
        "denominator": 10,
    }
    assert provenance["gold_present_field_coverage"] == {
        "value": 0.1,
        "numerator": 1,
        "denominator": 10,
    }

    model_subset = summary["model_success_subset"]
    assert model_subset["sample_documents"] == 1
    assert model_subset["labelled_fields"] == 1
    assert model_subset["extraction"]["exact_normalized_field_accuracy"] == {
        "value": 1.0,
        "numerator": 1,
        "denominator": 1,
    }
    assert summary["confidence"]["sample_size"] == 1
    model_gate = next(
        gate
        for gate in summary["regression_gates"]["gates"]
        if gate["name"] == "model_coverage_is_complete"
    )
    assert model_gate["passed"] is False
    assert any(
        gate["name"] == "api_failures_are_zero"
        for gate in summary["regression_gates"]["gates"]
    )

    output = tmp_path / "partial-model-results.json"
    write_evaluation_results(report, output)
    frontend = frontend_evaluation_summary(output)
    assert frontend["mode"] == "model"
    assert frontend["pipeline_extraction"] == {
        "scope": "full_hybrid_pipeline_including_deterministic_fill_ins",
        "exact_normalized_field_accuracy": {
            "value": 1.0,
            "numerator": 10,
            "denominator": 10,
        },
    }
    assert frontend["model_origin_extraction"] == {
        "scope": "grounded_fields_with_openai_compatible_provenance_only",
        "exact_normalized_field_accuracy": {
            "value": 1.0,
            "numerator": 1,
            "denominator": 1,
        },
        "all_labelled_field_coverage": {
            "value": 0.1,
            "numerator": 1,
            "denominator": 10,
        },
        "gold_present_field_coverage": {
            "value": 0.1,
            "numerator": 1,
            "denominator": 10,
        },
        "sample_documents": 1,
        "labelled_fields": 1,
    }


def test_fixture_fallback_is_reported_and_fails_no_fallback_gate():
    class FixtureFallbackExtractor:
        def extract(self, path: Path, *, case_id: str | None = None):
            document = DeterministicExtractor().extract(path, case_id=case_id)
            return document.model_copy(
                update={"extraction_method": ExtractionMethod.FALLBACK}
            )

    report = run_evaluation(
        EvaluationConfig(
            case_ids=("CC-001",),
            output_path=None,
            enable_reviewer=False,
        ),
        extractor=FixtureFallbackExtractor(),
        write_output=False,
    )

    assert report["cases"][0]["pipeline_status"] == "FALLBACK"
    assert report["summary"]["operating"]["documents"]["fallback"] == 1
    gate = report["summary"]["regression_gates"]["gates"][0]
    assert gate["name"] == "all_documents_completed_without_fallback"
    assert gate["passed"] is False


def test_fixture_gates_omit_vacuous_api_failure_check(fixture_report: dict):
    gate_names = {
        gate["name"]
        for gate in fixture_report["summary"]["regression_gates"]["gates"]
    }
    assert "api_failures_are_zero" not in gate_names
    assert len(gate_names) == 4


def test_instrumented_extractor_uses_same_untrusted_document_prompt(
    monkeypatch: pytest.MonkeyPatch,
):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "choices": [{"message": {"content": "{\"fields\": {}}"}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
            ).encode("utf-8")

    def fake_urlopen(http_request, timeout):
        requests.append((http_request, timeout))
        return Response()

    monkeypatch.setattr("app.extraction.request.urlopen", fake_urlopen)

    for extractor in (
        OpenAICompatibleExtractor(api_key="test-key"),
        InstrumentedOpenAIExtractor(api_key="test-key"),
    ):
        assert extractor._request("UNTRUSTED DOCUMENT") == '{"fields": {}}'

    assert len(requests) == 2
    for http_request, _timeout in requests:
        payload = json.loads(http_request.data)
        assert payload["messages"][0] == {
            "role": "system",
            "content": OPENAI_EXTRACTION_SYSTEM_PROMPT,
        }
        assert "untrusted data" in payload["messages"][0]["content"]
        assert payload["messages"][1] == {
            "role": "user",
            "content": "UNTRUSTED DOCUMENT",
        }


def test_cli_invalid_dataset_returns_two_without_traceback(tmp_path: Path):
    missing_dataset = tmp_path / "does-not-exist.json"
    output = tmp_path / "must-not-exist.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.evals",
            "--dataset",
            str(missing_dataset),
            "--output",
            str(output),
            "--skip-reviewer",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "evaluation failed: GoldDatasetError" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not output.exists()


def test_result_writer_replaces_atomically_and_cleans_temporary_file(
    fixture_report: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = tmp_path / "eval-results.json"
    output.write_text("stale partial output", encoding="utf-8")
    real_replace = eval_runner.os.replace
    replacements = []

    def observed_replace(source: str, destination: Path):
        temporary = Path(source)
        assert temporary.parent == output.parent
        assert temporary.name.startswith(f".{output.name}.")
        assert json.loads(temporary.read_text(encoding="utf-8")) == fixture_report
        replacements.append((temporary, Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(eval_runner.os, "replace", observed_replace)

    assert write_evaluation_results(fixture_report, output) == output.resolve()
    assert len(replacements) == 1
    assert replacements[0][1] == output.resolve()
    assert load_evaluation_results(output) == fixture_report
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []
