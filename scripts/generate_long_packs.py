"""Rebuild only the checked-in synthetic long-pack fixtures from existing gold notices."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/evals/long_packs"
CASES = (1, 2, 3, 4, 6, 9, 10, 13, 15, 17, 18, 19)
BANNER = "SYNTHETIC TRAINING PACK — FICTIONAL — DO NOT PAY"
COMPANIES = ("Fallowmere Glass", "Plover Rail", "Ashwater Textiles", "Fenlight Stores")


def filler(pack, page):
    company = COMPANIES[(pack + page) % len(COMPANIES)]
    quarter = (page - 1) % 4 + 1
    sections = (
        f"Quarterly partner letter | Q{quarter}\nThe operating team visited {company} to review order books, "
        "maintenance backlogs and supplier lead times. Procurement savings arrived later than budget. "
        "The next review will track cancellations separately from orders that moved into the following quarter.",
        f"Unaudited portfolio schedule | Q{quarter}\nCompany | Sales index | Margin index | Prior margin\n"
        + "\n".join(f"{name} | {93+i+page} | {105-i+pack} | {101+i}" for i, name in enumerate(COMPANIES))
        + "\nIndices use the prior year as a base of 100. These figures aggregate operating companies.",
        "Legal notices | Distribution restrictions\nThe quarterly letter is supplied for discussion with existing "
        "partners. Estimates and illustrations depend on the assumptions recorded in the schedules. "
        "No subscription, payment instruction or amendment takes effect through this exhibit.",
        f"Valuation committee minutes | {company}\nThe committee compared the proposed multiple with the prior "
        "valuation and recorded reasons for differences. Follow-up work concerns customer concentration and "
        "stock ageing. The draft remains subject to review at the next scheduled meeting.",
        "Glossary | Investor operations\nA capital call is a request under a commitment. A notice normally "
        "identifies a due date, currency and payment reference. This glossary explains the terms without "
        "specifying an amount due, an investor name or the details of a current transaction.",
    )
    return f"{BANNER}\nReporting packet {pack:02} · folio {page}\n\n{sections[(page + pack) % len(sections)]}\n"


def build():
    cases = json.loads((ROOT / "data/gold/capital_call_reconciliation.json").read_text())["cases"]
    by_id = {case["case_id"]: case for case in cases}
    decoy_path = "data/sample_documents/discrepancy_capital_call.txt"
    decoy_text = (ROOT / decoy_path).read_text()
    manifest = []
    for i, case_no in enumerate(CASES):
        case = by_id[f"CC-{case_no:03}"]
        original = case["documents"][0]["path"]
        source_pages = (ROOT / original).read_text().split("\f")
        # Repaginate selected notices between full lines so some fields of
        # the same current notice have different gold pages.
        split_line = 10 if i % 3 == 0 else None
        parts = source_pages[:]
        if split_line:
            lines = parts.pop(0).splitlines(keepends=True)
            parts[:0] = ["".join(lines[:split_line]), "".join(lines[split_line:])]
        length = 20 + (i * 13) % 21
        position = 2 + (7 * i + 3) % (length - len(parts) - 3)  # one-based
        pages = [filler(i + 1, p) for p in range(1, length + 1)]
        pages[position-1:position-1+len(parts)] = [BANNER + "\n" + part for part in parts]
        decoys = []
        if i % 2:
            page = position - 1 if i % 4 == 1 else position + len(parts) + 1
            pages[page-1] = BANNER + "\nArchived example — superseded notice for comparison only.\n" + decoy_text
            decoys = [page]
        labels = case["expected_extraction"]
        gold = {}
        for field, value in labels["canonical_values"].items():
            loc = None
            if value is not None:
                item = next(e for e in labels["field_evidence"][field] if e["source_file"] == original)
                ref = item["locator"]
                loc = position + ref["page"] - 1
                if split_line and (ref["page"] > 1 or ref["line_start"] > split_line):
                    loc += 1
                assert item["raw_text"] in pages[loc - 1], (case["case_id"], field)
            gold[field] = {"value": value, "page": loc}
        name = f"pack-{i+1:02}.txt"
        content = "\f".join(pages).encode("utf-8")
        (DEST / name).write_bytes(content)
        manifest.append(dict(file=name, source_case=case["case_id"], source=original, pages=length,
                             decoy_pages=decoys, gold=gold, sha256=hashlib.sha256(content).hexdigest()))
    (DEST / "manifest.json").write_text(json.dumps({"synthetic": True, "packs": manifest},
                                                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    DEST.mkdir(parents=True, exist_ok=True)
    build()
