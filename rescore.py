"""Re-score a saved eval run's predictions against corrected ground-truth labels.

Used when data/eval_set.csv's expected_category values change after an eval already ran against
the service. Re-scoring re-reads the saved per-row predictions from the eval_results/*.json file
and just swaps in the corrected expected_category/ambiguous, so it costs zero HTTP/LLM calls.

Also reports a "restricted" accuracy limited to rows whose expected_category is one of the
categories app/models.py's Category enum currently implements -- this separates real model error
from the mechanical failures caused by the taxonomy/code gap (see data/eval_set.csv's header).

Usage (PowerShell):
    python rescore.py --baseline eval_results/eval_20260924T150807Z_baseline.json --label baseline-rescored
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.models import Category
from evaluate import DEFAULT_DATASET, DEFAULT_RESULTS_DIR, Prediction, Row, load_dataset, summarize

REGRESSION_CHECK_IDS = ["78", "81", "84", "85", "86", "87"]


def load_saved_predictions(path: Path) -> tuple[dict, list[dict]]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data, data["rows"]


def build_predictions(saved_rows: list[dict], corrected_rows: dict[str, Row]) -> list[Prediction]:
    predictions = []
    for saved in saved_rows:
        row_id = saved["id"]
        corrected = corrected_rows.get(row_id)
        if corrected is None:
            raise KeyError(f"row id {row_id} from saved results not found in corrected dataset")
        predictions.append(
            Prediction(
                row=corrected,
                http_status=200 if saved["valid_json"] else None,
                predicted_category=saved["predicted_category"],
                predicted_severity=saved["predicted_severity"],
                analysis_source=saved["analysis_source"],
                valid_json=saved["valid_json"],
                error=saved["error"],
                latency_seconds=saved["latency_seconds"],
                attempts=saved["attempts"],
            )
        )
    return predictions


def restricted_summary(predictions: list[Prediction]) -> dict:
    implemented = sorted(c.value for c in Category)
    restricted = [p for p in predictions if p.row.expected_category in implemented]
    return {
        "implemented_categories": implemented,
        "row_count": len(restricted),
        "summary": summarize(restricted) if restricted else None,
    }


def regression_check(predictions: list[Prediction]) -> list[dict]:
    by_id = {p.row.id: p for p in predictions}
    results = []
    for row_id in REGRESSION_CHECK_IDS:
        p = by_id.get(row_id)
        if p is None:
            continue
        results.append(
            {
                "id": row_id,
                "text": p.row.text,
                "expected_category": p.row.expected_category,
                "baseline_predicted_category": p.predicted_category,
                "still_misrouted_to_customer_absent": p.predicted_category == "CUSTOMER_ABSENT",
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score a saved eval run against corrected labels.")
    parser.add_argument("--baseline", type=Path, required=True, help="Path to a previously saved eval_*.json")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="Corrected dataset CSV")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--label", default=None, help="Recorded in the output filename")
    args = parser.parse_args()

    corrected_rows = {row.id: row for row in load_dataset(args.dataset)}
    saved_data, saved_rows = load_saved_predictions(args.baseline)
    predictions = build_predictions(saved_rows, corrected_rows)

    overall = summarize(predictions)
    restricted = restricted_summary(predictions)
    regression = regression_check(predictions)

    print("=== Re-scored against corrected labels ===")
    print(f"Source baseline: {args.baseline} (recorded {saved_data.get('timestamp')})")
    print(f"Corrected dataset: {args.dataset}")
    print(f"Overall category accuracy: {overall['category_accuracy']:.1%} ({overall['total_rows']} rows)")
    if restricted["summary"] is not None:
        print(
            f"Restricted accuracy ({', '.join(restricted['implemented_categories'])} only): "
            f"{restricted['summary']['category_accuracy']:.1%} ({restricted['row_count']} rows)"
        )
    else:
        print("Restricted accuracy: n/a (no rows match implemented categories)")
    if overall["critical_severity_recall"] is not None:
        print(f"CRITICAL severity recall: {overall['critical_severity_recall']:.1%}")

    print("\n=== Regression check: former ADDRESS_ISSUE rows the baseline sent to CUSTOMER_ABSENT ===")
    for r in regression:
        flag = "STILL MISROUTED" if r["still_misrouted_to_customer_absent"] else "ok"
        print(f"  id={r['id']} predicted={r['baseline_predicted_category']} expected={r['expected_category']} [{flag}]")

    label_part = f"_{args.label}" if args.label else "_rescored"
    out_path = args.results_dir / (args.baseline.stem + label_part + ".json")
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "source_baseline": str(args.baseline),
                "source_baseline_timestamp": saved_data.get("timestamp"),
                "corrected_dataset": str(args.dataset),
                "overall_summary": overall,
                "restricted_summary": restricted,
                "regression_check": regression,
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
