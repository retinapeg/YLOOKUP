"""Generate deterministic, text-searchable PDFs for the synthetic demo notices."""

from __future__ import annotations

import textwrap
from pathlib import Path

from reportlab import rl_config
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas


rl_config.invariant = 1

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    (
        ROOT / "data/sample_documents/matching_capital_call.txt",
        ROOT / "data/sample_documents/matching_capital_call.pdf",
        "MATCHING CONTROL",
    ),
    (
        ROOT / "data/sample_documents/discrepancy_capital_call.txt",
        ROOT / "data/sample_documents/discrepancy_capital_call.pdf",
        "EXCEPTION CONTROL",
    ),
    (
        ROOT / "data/demo/northstar_growth_fund_ii/capital_call_notice.txt",
        ROOT / "data/demo/northstar_growth_fund_ii/capital_call_notice.pdf",
        "NORTHSTAR DEMO",
    ),
)

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT = 54
RIGHT = PAGE_WIDTH - 54


def _fit_text(text: str, font: str, size: float, width: float) -> list[str]:
    if not text:
        return [""]
    approximate = max(18, int(width / max(size * 0.54, 1)))
    candidates = textwrap.wrap(text, width=approximate, break_long_words=False) or [text]
    lines: list[str] = []
    for candidate in candidates:
        while stringWidth(candidate, font, size) > width and len(candidate) > 1:
            split_at = candidate.rfind(" ", 0, max(1, len(candidate) - 4))
            if split_at <= 0:
                split_at = len(candidate) - 1
            lines.append(candidate[:split_at].rstrip())
            candidate = candidate[split_at:].lstrip()
        lines.append(candidate)
    return lines


def _draw_chrome(canvas: Canvas, label: str, page_number: int) -> float:
    canvas.setFillColor(HexColor("#13233A"))
    canvas.rect(0, PAGE_HEIGHT - 88, PAGE_WIDTH, 88, stroke=0, fill=1)
    canvas.setFillColor(HexColor("#FFFFFF"))
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(LEFT, PAGE_HEIGHT - 39, "NORTHSTAR PRIVATE MARKETS ADMINISTRATION")
    canvas.setFont("Helvetica", 8)
    canvas.drawString(LEFT, PAGE_HEIGHT - 57, "Synthetic operations fixture | Capital-call reconciliation")

    canvas.setFillColor(HexColor("#B42318"))
    canvas.roundRect(RIGHT - 170, PAGE_HEIGHT - 63, 170, 28, 4, stroke=0, fill=1)
    canvas.setFillColor(HexColor("#FFFFFF"))
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.drawCentredString(RIGHT - 85, PAGE_HEIGHT - 47, "FICTIONAL - DEMO ONLY - DO NOT PAY")
    canvas.setFont("Helvetica", 6.5)
    canvas.drawCentredString(RIGHT - 85, PAGE_HEIGHT - 57, label)

    canvas.setStrokeColor(HexColor("#CBD5E1"))
    canvas.line(LEFT, 42, RIGHT, 42)
    canvas.setFillColor(HexColor("#64748B"))
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(LEFT, 28, "FundOps Copilot synthetic corpus | No bank coordinates are included")
    canvas.drawRightString(RIGHT, 28, f"Page {page_number}")
    return PAGE_HEIGHT - 116


def _draw_page(canvas: Canvas, lines: list[str], label: str, page_number: int) -> None:
    y = _draw_chrome(canvas, label, page_number)
    previous_blank = False
    field_prefixes = (
        "Document Type:",
        "Fund Name:",
        "Investor Name:",
        "Commitment Amount:",
        "Capital Call Amount:",
        "Management Fee:",
        "Call Date:",
        "Due Date:",
        "Currency:",
        "Payment Reference:",
    )

    for raw in lines:
        line = raw.rstrip()
        if not line or line == "FICTIONAL - DEMO ONLY - DO NOT PAY":
            if not previous_blank:
                y -= 8
            previous_blank = True
            continue
        previous_blank = False

        if line == line.upper() and len(line) > 5:
            font, size, color, leading = "Helvetica-Bold", 15, "#13233A", 21
        elif line in {"Purpose of call", "Payment control", "Current-call allocation", "Historical calls - context only"}:
            font, size, color, leading = "Helvetica-Bold", 10.5, "#1D4ED8", 16
        elif line.startswith(field_prefixes):
            font, size, color, leading = "Helvetica", 9.5, "#111827", 14
        else:
            font, size, color, leading = "Helvetica", 9, "#334155", 13

        wrapped = _fit_text(line, font, size, RIGHT - LEFT)
        required = leading * len(wrapped)
        if y - required < 56:
            canvas.showPage()
            page_number += 1
            y = _draw_chrome(canvas, label, page_number)
        canvas.setFillColor(HexColor(color))
        canvas.setFont(font, size)
        for rendered in wrapped:
            canvas.drawString(LEFT, y, rendered)
            y -= leading


def generate_pdf(source: Path, destination: Path, label: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(
        str(destination),
        pagesize=A4,
        pageCompression=1,
        invariant=1,
    )
    canvas.setAuthor("FundOps Copilot Synthetic Corpus")
    canvas.setCreator("FundOps Copilot fixture generator")
    canvas.setTitle(f"{label.title()} Capital Call Notice")
    canvas.setSubject("Entirely fictional private-markets reconciliation fixture")

    pages = source.read_text(encoding="utf-8").split("\f")
    for index, page in enumerate(pages, start=1):
        if index > 1:
            canvas.showPage()
        _draw_page(canvas, page.splitlines(), label, index)
    canvas.save()


def main() -> None:
    for source, destination, label in FIXTURES:
        generate_pdf(source, destination, label)
        print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
