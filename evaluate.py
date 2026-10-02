"""Evaluate ai-service classification quality against data/eval_set.csv.

Calls the running service for every dataset row and reports category accuracy,
macro-F1, a confusion matrix, per-language accuracy, CRITICAL-severity recall,
valid-JSON rate, analysisSource (LLM vs KEYWORD_FALLBACK) rate, and latency.

Throttling and retry-with-backoff are permanent, not a one-off flag: this script
is re-run many times (baseline, after each step, final report numbers) against a
free-tier Groq key, so every run must survive rate limiting on its own.

Usage (PowerShell):
    python evaluate.py --base-url http://127.0.0.1:8000 --label baseline
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DATASET = Path(__file__).parent / "data" / "eval_set.csv"
DEFAULT_RESULTS_DIR = Path(__file__).parent / "eval_results"
FALLBACK_RATE_WARNING_THRESHOLD = 0.15


@dataclass
class Row:
    id: str
    text: str
    language: str
    expected_category: str
    expected_severity: str
    ambiguous: bool
    notes: str


@dataclass
class Prediction:
    row: Row
    http_status: int | None
    predicted_category: str | None
    predicted_severity: str | None
    analysis_source: str | None
    valid_json: bool
    error: str | None
    latency_seconds: float
    attempts: int


def load_dataset(path: Path) -> list[Row]:
    with path.open(encoding="utf-8") as fh:
        data_lines = [line for line in fh if not line.lstrip().startswith("#")]
    reader = csv.DictReader(data_lines)
    rows: list[Row] = []
    for raw in reader:
        rows.append(
            Row(
                id=raw["id"],
                text=raw["text"],
                language=raw["language"].strip(),
                expected_category=raw["expected_category"].strip().upper(),
                expected_severity=raw["expected_severity"].strip().upper(),
                ambiguous=raw["ambiguous"].strip().lower() in {"true", "1", "yes"},
                notes=(raw.get("notes") or "").strip(),
            )
        )
    return rows


def call_service(
    base_url: str,
    row: Row,
    api_key: str | None,
    timeout: float,
    max_retries: int,
    backoff_base: float,
) -> Prediction:
    """POST one row to /v1/exceptions:analyze, retrying 429/5xx/network errors
    with exponential backoff (honoring Retry-After when the server sends one)."""
    url = base_url.rstrip("/") + "/v1/exceptions:analyze"
    payload = json.dumps(
        {"text": row.text, "shipmentId": f"EVAL-{row.id}", "requestId": str(uuid.uuid4())}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    attempt = 0
    last_error: str | None = None
    while attempt <= max_retries:
        attempt += 1
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                elapsed = time.monotonic() - start
                body = json.loads(response.read().decode("utf-8"))
                structured = body.get("structuredRecord", {})
                return Prediction(
                    row=row,
                    http_status=response.status,
                    predicted_category=structured.get("category"),
                    predicted_severity=structured.get("severity"),
                    analysis_source=body.get("analysisSource"),
                    valid_json=True,
                    error=None,
                    latency_seconds=elapsed,
                    attempts=attempt,
                )
        except urllib.error.HTTPError as exc:
            elapsed = time.monotonic() - start
            last_error = f"HTTP {exc.code}"
            if (exc.code == 429 or exc.code >= 500) and attempt <= max_retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after else backoff_base * (2 ** (attempt - 1))
                time.sleep(delay + random.uniform(0, 0.25))
                continue
            try:
                error_body = json.loads(exc.read().decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                error_body = None
            return Prediction(
                row=row, http_status=exc.code, predicted_category=None,
                predicted_severity=None, analysis_source=None,
                valid_json=error_body is not None, error=last_error,
                latency_seconds=elapsed, attempts=attempt,
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            elapsed = time.monotonic() - start
            last_error = str(exc)
            if attempt <= max_retries:
                time.sleep(backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.25))
                continue
            return Prediction(
                row=row, http_status=None, predicted_category=None,
                predicted_severity=None, analysis_source=None,
                valid_json=False, error=last_error,
                latency_seconds=elapsed, attempts=attempt,
            )
        except json.JSONDecodeError as exc:
            elapsed = time.monotonic() - start
            return Prediction(
                row=row, http_status=200, predicted_category=None,
                predicted_severity=None, analysis_source=None,
                valid_json=False, error=f"invalid JSON body: {exc}",
                latency_seconds=elapsed, attempts=attempt,
            )
    return Prediction(
        row=row, http_status=None, predicted_category=None, predicted_severity=None,
        analysis_source=None, valid_json=False, error=last_error or "unknown error",
        latency_seconds=0.0, attempts=attempt,
    )


def macro_f1(predictions: list[Prediction]) -> tuple[float, dict[str, dict[str, float]]]:
    labels = sorted(
        {p.row.expected_category for p in predictions}
        | {p.predicted_category for p in predictions if p.predicted_category}
    )
    per_label: dict[str, dict[str, float]] = {}
    f1_scores = []
    for label in labels:
        tp = sum(1 for p in predictions if p.predicted_category == label and p.row.expected_category == label)
        fp = sum(1 for p in predictions if p.predicted_category == label and p.row.expected_category != label)
        fn = sum(1 for p in predictions if p.predicted_category != label and p.row.expected_category == label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_label[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
        f1_scores.append(f1)
    macro = statistics.mean(f1_scores) if f1_scores else 0.0
    return macro, per_label


def confusion_matrix(predictions: list[Prediction]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in predictions:
        matrix[p.row.expected_category][p.predicted_category or "NO_RESPONSE"] += 1
    return {expected: dict(preds) for expected, preds in matrix.items()}


def per_language_accuracy(predictions: list[Prediction]) -> dict[str, float]:
    by_lang: dict[str, list[Prediction]] = defaultdict(list)
    for p in predictions:
        by_lang[p.row.language].append(p)
    return {
        lang: sum(1 for p in preds if p.predicted_category == p.row.expected_category) / len(preds)
        for lang, preds in by_lang.items()
    }


def critical_severity_recall(predictions: list[Prediction]) -> float | None:
    critical_rows = [p for p in predictions if p.row.expected_severity == "CRITICAL"]
    if not critical_rows:
        return None
    correct = sum(1 for p in critical_rows if p.predicted_severity == "CRITICAL")
    return correct / len(critical_rows)


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[index]


def summarize(predictions: list[Prediction]) -> dict:
    total = len(predictions)
    correct = sum(1 for p in predictions if p.predicted_category == p.row.expected_category)
    macro, per_label = macro_f1(predictions)
    valid_json_rate = sum(1 for p in predictions if p.valid_json) / total if total else 0.0
    source_counts = {
        "LLM": sum(1 for p in predictions if p.analysis_source == "LLM"),
        "KEYWORD_FALLBACK": sum(1 for p in predictions if p.analysis_source == "KEYWORD_FALLBACK"),
        "NO_RESPONSE": sum(1 for p in predictions if p.analysis_source is None),
    }
    latencies = sorted(p.latency_seconds for p in predictions if p.valid_json)

    return {
        "total_rows": total,
        "category_accuracy": correct / total if total else 0.0,
        "category_macro_f1": macro,
        "per_category": per_label,
        "confusion_matrix": confusion_matrix(predictions),
        "per_language_accuracy": per_language_accuracy(predictions),
        "critical_severity_recall": critical_severity_recall(predictions),
        "valid_json_rate": valid_json_rate,
        "analysis_source_rates": {k: (v / total if total else 0.0) for k, v in source_counts.items()},
        "latency_seconds": {
            "mean": statistics.mean(latencies) if latencies else 0.0,
            "p95": percentile(latencies, 0.95),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate ai-service classification quality.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--api-key", default=None, help="Bearer token, only needed if AI_SERVICE_API_KEY is set on the service.")
    parser.add_argument("--rps", type=float, default=1.0, help="Max requests per second; keep low for Groq's free tier.")
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--backoff-base", type=float, default=2.0, help="Base seconds for exponential backoff on 429/5xx.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--label", default=None, help="Recorded in the results filename, e.g. 'baseline' or 'after-step1'.")
    args = parser.parse_args()

    rows = load_dataset(args.dataset)
    print(f"Loaded {len(rows)} rows from {args.dataset}")

    delay = 1.0 / args.rps if args.rps > 0 else 0.0
    predictions: list[Prediction] = []
    for i, row in enumerate(rows, start=1):
        prediction = call_service(args.base_url, row, args.api_key, args.timeout, args.max_retries, args.backoff_base)
        predictions.append(prediction)
        status = prediction.analysis_source or prediction.error or "?"
        print(f"[{i}/{len(rows)}] id={row.id} expected={row.expected_category} predicted={prediction.predicted_category} source={status}")
        if delay and i < len(rows):
            time.sleep(delay)

    summary = summarize(predictions)

    print("\n=== Summary ===")
    print(f"Category accuracy: {summary['category_accuracy']:.1%}")
    print(f"Category macro-F1: {summary['category_macro_f1']:.3f}")
    print(f"Valid JSON rate: {summary['valid_json_rate']:.1%}")
    rates = summary["analysis_source_rates"]
    print(f"analysisSource: LLM={rates['LLM']:.1%} KEYWORD_FALLBACK={rates['KEYWORD_FALLBACK']:.1%} NO_RESPONSE={rates['NO_RESPONSE']:.1%}")
    if rates["KEYWORD_FALLBACK"] > FALLBACK_RATE_WARNING_THRESHOLD:
        print(
            f"WARNING: fallback rate is {rates['KEYWORD_FALLBACK']:.1%} (>{FALLBACK_RATE_WARNING_THRESHOLD:.0%}). "
            "This usually means the LLM path was rate-limited or failing mid-run (Groq free tier), not that "
            "the model is genuinely this unreliable. Category accuracy/F1 from this run should NOT be reported "
            "as the LLM path's real quality -- lower --rps, raise --max-retries/--backoff-base, or re-run later."
        )
    critical_recall = summary["critical_severity_recall"]
    if critical_recall is not None:
        print(f"CRITICAL severity recall: {critical_recall:.1%}")
    print(f"Latency mean={summary['latency_seconds']['mean']:.2f}s p95={summary['latency_seconds']['p95']:.2f}s")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label_part = f"_{args.label}" if args.label else ""
    args.results_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.results_dir / f"eval_{timestamp}{label_part}.json"
    with results_path.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "timestamp": timestamp,
                "base_url": args.base_url,
                "dataset": str(args.dataset),
                "label": args.label,
                "summary": summary,
                "rows": [
                    {
                        "id": p.row.id,
                        "text": p.row.text,
                        "language": p.row.language,
                        "expected_category": p.row.expected_category,
                        "expected_severity": p.row.expected_severity,
                        "ambiguous": p.row.ambiguous,
                        "predicted_category": p.predicted_category,
                        "predicted_severity": p.predicted_severity,
                        "analysis_source": p.analysis_source,
                        "valid_json": p.valid_json,
                        "error": p.error,
                        "latency_seconds": p.latency_seconds,
                        "attempts": p.attempts,
                    }
                    for p in predictions
                ],
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nWrote results to {results_path}")


if __name__ == "__main__":
    main()
