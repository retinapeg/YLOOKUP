"""Evaluate ranked chunk pages and deterministic extraction on synthetic long packs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from app import retrieval
from app.evals.metrics import values_equal
from app.extraction import DeterministicExtractor

PACKS = Path(__file__).resolve().parents[2] / "data/evals/long_packs"
CUTOFFS = (1, 3, 5)


def _ratio(numerator, denominator):
    return {"hits": numerator, "total": denominator, "value": numerator / denominator if denominator else None}


def _recalls(rows):
    ranked = {idx: row["pages"] for idx, row in enumerate(rows)}
    gold = {idx: row["gold_page"] for idx, row in enumerate(rows)}
    n = sum(page is not None for page in gold.values())
    result = {}
    for k in CUTOFFS:
        rate = retrieval.recall_at_k(ranked, gold, k)
        result[k] = {"hits": round(rate * n), "total": n, "value": rate if n else None}
    return result


def _summarize(rows):
    recalls = {field: _recalls([row for row in rows if row["field"] == field])
               for field, _ in retrieval.FIELD_QUERIES}
    recalls["overall"] = _recalls(rows)
    groups = {}
    for name, selected in (("all packs", rows),
                           ("decoy packs", [r for r in rows if r["decoys"]]),
                           ("clean packs", [r for r in rows if not r["decoys"]])):
        eligible = [r for r in selected if r["gold_page"] is not None and r["decoys"]]
        groups[name] = {
            "retrieval_exact": _ratio(sum(r["retrieval_exact"] for r in selected), len(selected)),
            "full_exact": _ratio(sum(r["full_exact"] for r in selected), len(selected)),
            "top1_decoy": _ratio(sum(bool(r["pages"]) and r["pages"][0] in r["decoys"]
                                     for r in eligible), len(eligible)),
        }
    return {"recall": recalls, "accuracy": groups, "field_results": rows}


def run_report(*, pack_dir=PACKS, offline=False, maths="production"):
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True:
        raise ValueError("Expected synthetic long-pack manifest")
    baselines = []
    for pack in manifest["packs"]:
        path = pack_dir / pack["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != pack["sha256"]:
            raise ValueError(f"Hash mismatch: {pack['file']}")
        full = DeterministicExtractor(retrieval_mode=False).extract(path)
        baselines.append((pack, path, full))
    report = {"maths": maths, "pack_count": len(baselines), "window_words": 80,
              "overlap_words": 20, "extraction_k": 5, "backends": {}}
    backends = {"tfidf": retrieval.TfidfRetriever()}
    if not offline and os.getenv("OPENAI_API_KEY"):
        backends["embeddings"] = retrieval.EmbeddingRetriever()
    else:
        report["backends"]["embeddings"] = {"status": "not run", "reason":
            "explicit offline mode" if offline else "OPENAI_API_KEY absent"}
    for name, backend in backends.items():
        extractor = DeterministicExtractor(retrieval_mode=True, retriever=backend,
                                           chunk_size=80, overlap=20, top_k=5)
        rows = []
        for pack, path, full in baselines:
            scoped = extractor.extract(path)
            if scoped.retrieval.backend != name:
                raise ValueError(f"Cannot attribute fallback results to {name}")
            for field, label in pack["gold"].items():
                hits = scoped.retrieval.fields[field]
                rows.append({
                    "pack": pack["file"], "field": field, "gold_page": label["page"],
                    "pages": [hit.chunk.page for hit in hits], "decoys": pack["decoy_pages"],
                    "retrieval_exact": values_equal(field, label["value"], scoped.value_for(field)),
                    "full_exact": values_equal(field, label["value"], full.value_for(field)),
                })
        report["backends"][name] = {"status": "completed", **_summarize(rows)}
    return report


def _fraction(metric):
    return f"{metric['hits']}/{metric['total']}" if metric["total"] else "—"


def format_report(report):
    lines = [f"Maths: {report['maths']}. Synthetic packs: {report['pack_count']}.",
             "Extraction uses the first five ranked chunks. Repeated pages retain their chunk ranks."]
    for name, result in report["backends"].items():
        if result["status"] != "completed":
            lines += ["", f"{name}: {result['status']} ({result['reason']})."]
            continue
        lines += ["", f"**{name} page recall**", "",
                  "| Field | Recall@1 | Recall@3 | Recall@5 |", "| --- | ---: | ---: | ---: |"]
        for field, rates in result["recall"].items():
            lines.append("| " + " | ".join([field] + [_fraction(rates[k]) for k in CUTOFFS]) + " |")
        lines += ["", "| Group | Exact fields, retrieval | Exact fields, full document | Top-1 decoy / eligible fields |",
                  "| --- | ---: | ---: | ---: |"]
        for group, rates in result["accuracy"].items():
            lines.append("| " + " | ".join([group] + [_fraction(rates[key]) for key in
                         ("retrieval_exact", "full_exact", "top1_decoy")]) + " |")
    return "\n".join(lines)


def main(argv=None, *, maths="production"):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="skip embeddings even with credentials")
    parser.add_argument("--output", type=Path, help="optional detailed JSON including ranked pages per field")
    args = parser.parse_args(argv)
    try:
        report = run_report(offline=args.offline, maths=maths)
    except NotImplementedError:
        parser.exit(2, "Retrieval maths is unfinished. For the test-only reference run see docs/handoffs/rag-ingestion.md.\n")
    print(format_report(report))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
