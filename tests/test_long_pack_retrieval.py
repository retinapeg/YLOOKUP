import hashlib
import json
from pathlib import Path

import pytest

from app import retrieval
from app.evals.retrieval import PACKS, run_report
from tests._reference_retrieval import test_only_reference_maths as use_reference


def test_long_pack_gold_independent_of_extractor():
    root = Path(__file__).resolve().parents[1]
    source = json.loads((root / "data/gold/capital_call_reconciliation.json").read_text())
    cases = {c["case_id"]: c for c in source["cases"]}
    manifest = json.loads((PACKS / "manifest.json").read_text())
    assert manifest["synthetic"] and 10 <= len(manifest["packs"]) <= 15
    assert any(p["decoy_pages"] for p in manifest["packs"])
    assert any(not p["decoy_pages"] for p in manifest["packs"])
    for pack in manifest["packs"]:
        blob = (PACKS / pack["file"]).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == pack["sha256"]
        pages = blob.decode().split("\f")
        assert 20 <= len(pages) == pack["pages"] <= 40
        assert all("SYNTHETIC" in p for p in pages)
        gold = cases[pack["source_case"]]["expected_extraction"]
        assert set(pack["gold"]) == dict(retrieval.FIELD_QUERIES).keys()
        for field, label in pack["gold"].items():
            assert label["value"] == gold["canonical_values"][field]
            if label["value"] is None:
                assert label["page"] is None
                continue
            assert label["page"] not in pack["decoy_pages"]
            assert any(item["raw_text"] in pages[label["page"]-1]
                       for item in gold["field_evidence"][field])


def test_report_measures_ranked_pages_and_decoy_subset(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with use_reference():
        report = run_report(maths="test-only reference")
    assert report["backends"]["embeddings"]["status"] == "not run"
    result = report["backends"]["tfidf"]
    rows = result["field_results"]
    assert len(rows) == report["pack_count"] * len(retrieval.FIELD_QUERIES)
    for name, metrics in result["recall"].items():
        eligible = [r for r in rows if r["gold_page"] is not None and name in ("overall", r["field"])]
        for k, rate in metrics.items():
            assert rate["total"] == len(eligible)
            assert rate["hits"] == sum(r["gold_page"] in r["pages"][:k] for r in eligible)
    decoy = result["accuracy"]["decoy packs"]
    eligible = [r for r in rows if r["decoys"] and r["gold_page"] is not None]
    assert decoy["top1_decoy"]["total"] == len(eligible)
    assert decoy["top1_decoy"]["hits"] == sum(r["pages"][0] in r["decoys"] for r in eligible)
    assert result["accuracy"]["all packs"]["full_exact"]["total"] == len(rows)


def test_offline_report_never_builds_embedding_backend(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    def unexpected():
        pytest.fail("offline evaluation constructed an embedding backend")
    monkeypatch.setattr(retrieval, "EmbeddingRetriever", unexpected)
    with use_reference():
        report = run_report(offline=True, maths="test-only reference")
    assert report["backends"]["embeddings"]["reason"] == "explicit offline mode"
