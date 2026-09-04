"""Command-line entry point for ``python -m app.evals``."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .runner import (
    DEFAULT_OUTPUT_PATH,
    EvaluationConfig,
    run_evaluation,
)
from .schema import DEFAULT_DATASET_PATH, GoldDatasetError


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


def _probability(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evals",
        description=(
            "Run the FundOps extraction, exception, reconciliation, reviewer, "
            "confidence, and operating benchmark over the versioned gold corpus."
        ),
    )
    parser.add_argument(
        "--dataset",
        "--gold",
        dest="dataset_path",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="gold manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="JSON result path (default: %(default)s)",
    )
    parser.add_argument(
        "--mode",
        choices=("fixture", "model"),
        default="fixture",
        help="offline deterministic fixture baseline or live model extraction",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="evaluate only this case id; repeat for multiple cases",
    )
    parser.add_argument(
        "--numeric-tolerance",
        type=_decimal,
        default=Decimal("0"),
        help="absolute reconciliation tolerance (default: exact/zero)",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=_probability,
        default=0.80,
        help="below this confidence, matching values route to REVIEW",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=12,
        help="maximum failures in the concise terminal summary",
    )
    parser.add_argument("--model", help="OPENAI_MODEL override for model mode")
    parser.add_argument("--base-url", help="OPENAI_BASE_URL override for model mode")
    parser.add_argument(
        "--model-timeout-seconds", type=float, default=12.0
    )
    parser.add_argument(
        "--input-cost-per-million",
        type=_decimal,
        help="optional USD input-token rate used only for an explicit estimate",
    )
    parser.add_argument(
        "--output-cost-per-million",
        type=_decimal,
        help="optional USD output-token rate used only for an explicit estimate",
    )
    parser.add_argument(
        "--baseline",
        dest="baseline_path",
        type=Path,
        help="optional prior result from the identical dataset/mode",
    )
    parser.add_argument(
        "--skip-document-hash-check",
        action="store_true",
        help="skip source SHA-256 verification (not recommended)",
    )
    parser.add_argument(
        "--skip-reviewer",
        action="store_true",
        help="do not run the optional independent reviewer stage",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 1 when any transparent regression gate fails",
    )
    return parser


def _metric(metric: Mapping[str, Any]) -> str:
    value = metric.get("value")
    numerator = metric.get("numerator")
    denominator = metric.get("denominator")
    rendered = "n/a" if value is None else f"{float(value):.1%}"
    return f"{rendered} ({numerator}/{denominator})"


def _compact(value: Any, limit: int = 38) -> str:
    rendered = "null" if value is None else str(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def print_summary(report: Mapping[str, Any], output_path: Path) -> None:
    summary = report["summary"]
    sample = summary["sample_size"]
    extraction = summary["extraction"]
    exception = summary["exception_detection"]["field_level"]
    rule = summary["reconciliation"]["isolated_rule_correctness"][
        "rule_correctness"
    ]
    operations = summary["operating"]
    total_latency = operations["latency_ms"]["total"]
    print(f"FundOps evaluation — {summary['label']}")
    print(
        f"Sample: n={sample['selected_cases']} documents, "
        f"{sample['labelled_extraction_fields']} labelled fields; "
        f"{sample['replayable_reconciliation_cases']} reconciliation-replayable cases"
    )
    extraction_label = (
        "Hybrid pipeline extraction"
        if report["run"]["mode"] == "model"
        else "Extraction"
    )
    print(
        f"{extraction_label}: exact normalized "
        + _metric(extraction["exact_normalized_field_accuracy"])
        + "; numeric "
        + _metric(extraction["numeric_accuracy"])
        + "; date "
        + _metric(extraction["date_accuracy"])
        + "; abstention "
        + _metric(extraction["missing_abstention"]["correct_abstention_rate"])
    )
    if report["run"]["mode"] == "model":
        model_subset = summary["model_success_subset"]
        print(
            "Model-origin fields: coverage "
            + _metric(model_subset["all_labelled_field_coverage"])
            + "; conditional exact normalized "
            + _metric(model_subset["extraction"]["exact_normalized_field_accuracy"])
        )
    print(
        "Exceptions: precision "
        + _metric(exception["precision"])
        + "; recall "
        + _metric(exception["recall"])
        + "; F1 "
        + _metric(exception["f1"])
        + f"; FN={exception['false_negative']}"
    )
    print("Deterministic reconciliation rules: " + _metric(rule))

    challenge = summary["reviewer"]["challenge_detection"]
    if challenge.get("available"):
        print("Reviewer challenges: " + _metric(challenge["metrics"]["f1"]))
    else:
        print("Reviewer challenges: n/a — no independent field-level gold labels")

    p95 = total_latency["p95_ms"]
    p95_text = "n/a" if p95 is None else f"{p95:.1f} ms"
    print(
        f"Operating: mean {total_latency['mean_ms']:.1f} ms/document; "
        f"p95 {p95_text}; model calls "
        f"{operations['model_calls'].get('count')}; "
        f"API failures {operations['model_calls'].get('api_failures')}"
    )
    calibration = summary["confidence"]
    if calibration.get("available"):
        print(
            f"Confidence: descriptive ECE {calibration['expected_calibration_error']:.3f} "
            f"on n={calibration['sample_size']} fields ({calibration['confidence_kind']})"
        )
    else:
        print("Confidence: n/a")

    gates = summary["regression_gates"]
    passed = sum(gate["passed"] for gate in gates["gates"])
    print(
        f"Regression gates: {'PASS' if gates['passed'] else 'FAIL'} "
        f"({passed}/{len(gates['gates'])})"
    )
    failures = summary["failure_analysis"]["worst_failed_cases"]
    if failures:
        print("Worst failed cases:")
        for failure in failures:
            print(
                f"  {failure['case_id']} · {failure['field']} · "
                f"{failure['failure_category']} · expected={_compact(failure['expected'])} "
                f"observed={_compact(failure['observed'])}"
            )
    print(f"Wrote {Path(output_path).expanduser().resolve()}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_failures < 0:
        parser.error("--max-failures must be non-negative")
    try:
        config = EvaluationConfig(
            dataset_path=args.dataset_path,
            output_path=args.output,
            mode=args.mode,
            case_ids=tuple(args.case_id),
            numeric_tolerance=args.numeric_tolerance,
            confidence_threshold=args.confidence_threshold,
            max_failures=args.max_failures,
            verify_document_hashes=not args.skip_document_hash_check,
            enable_reviewer=not args.skip_reviewer,
            model=args.model,
            base_url=args.base_url,
            model_timeout_seconds=args.model_timeout_seconds,
            input_cost_per_million=args.input_cost_per_million,
            output_cost_per_million=args.output_cost_per_million,
            baseline_path=args.baseline_path,
        )
        report = run_evaluation(config)
    except (GoldDatasetError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print_summary(report, args.output)
    if args.mode == "model":
        coverage = report["summary"]["operating"]["model_field_provenance"][
            "all_labelled_field_coverage"
        ]
        if coverage["numerator"] == 0:
            print(
                "model evaluation produced no grounded fields with model provenance; "
                "deterministic fallbacks and fill-ins were excluded from model quality",
                file=sys.stderr,
            )
            return 2
    if args.fail_on_regression and not report["summary"]["regression_gates"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
