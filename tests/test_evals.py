from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import app.evals.runner as eval_runner
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
    assert frontend["generated_at"] == fixture_report["generated_at"]
    assert frontend["dataset"] == {
        "id": fixture_report["dataset"]["id"],
        "schema_version": fixture_report["dataset"]["schema_version"],
        "synthetic": True,
    }
    assert frontend["sample_size"]["selected_cases"] == 2
    assert frontend["operating"]["documents"]["attempted"] == 2
    assert frontend["reviewer"] == fixture_report["summary"]["reviewer"]


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
