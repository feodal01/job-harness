#!/usr/bin/env python3
"""Offline evaluation harness for the deterministic experience engine."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from job_harness.v1.experience_engine import assess_listing_experience
from job_harness.v1.models import JobListing
from job_harness.v1.types import FilterSupport


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", help="Path(s) to job-harness results.json")
    parser.add_argument("--labels", help="Optional CSV with url,label columns")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    labels = _read_labels(Path(args.labels)) if args.labels else {}
    rows = []
    for path in [Path(item) for item in args.results]:
        rows.extend(_evaluate_file(path, labels))

    report = _build_report(rows, bool(labels))
    if args.format == "markdown":
        print(_markdown_report(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _evaluate_file(path: Path, labels: dict[str, str]) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = payload.get("sources") or {}
    rows = []
    for raw in payload.get("listings") or []:
        listing = JobListing(
            **{k: v for k, v in raw.items() if k in JobListing.__dataclass_fields__}
        )
        support = _source_experience_support(sources, listing.source)
        assessment = assess_listing_experience(listing, listing.source, support)
        predicted = assessment.levels[0] if len(assessment.levels) == 1 else "multi" if assessment.levels else "unknown"
        rows.append(
            {
                "source": listing.source,
                "url": listing.url,
                "title": listing.title,
                "predicted": predicted,
                "levels": list(assessment.levels),
                "origin": assessment.origin,
                "confidence": assessment.confidence,
                "evidence": list(assessment.evidence),
                "label": labels.get(listing.url),
            }
        )
    return rows


def _source_experience_support(sources: dict[str, Any], source: str) -> FilterSupport:
    raw = (
        sources.get(source, {})
        .get("flag_enforcement", {})
        .get("experience", FilterSupport.UNSUPPORTED.value)
    )
    return FilterSupport(str(raw))


def _read_labels(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "url" not in (reader.fieldnames or ()) or "label" not in (reader.fieldnames or ()):
            raise ValueError("labels CSV must contain url,label columns")
        return {
            row["url"]: row["label"].strip().lower()
            for row in reader
            if row.get("url") and row.get("label")
        }


def _build_report(rows: list[dict[str, Any]], has_labels: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "total": len(rows),
        "by_origin": dict(Counter(row["origin"] for row in rows)),
        "by_confidence": dict(Counter(row["confidence"] for row in rows)),
        "by_prediction": dict(Counter(row["predicted"] for row in rows)),
        "unknown_examples": [
            _example(row) for row in rows if row["predicted"] == "unknown"
        ][:10],
    }
    if has_labels:
        labeled = [row for row in rows if row.get("label")]
        report["labeled_total"] = len(labeled)
        report["metrics"] = _metrics(labeled)
        report["false_positive_examples"] = [
            _example(row)
            for row in labeled
            if row["predicted"] != "unknown" and row["predicted"] != row["label"]
        ][:10]
    return report


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "accuracy": None,
            "macro_f1": None,
            "middle_precision": None,
            "unknown_rate": None,
        }
    labels = ("junior", "middle", "senior")
    correct = sum(1 for row in rows if row["predicted"] == row["label"])
    f1_values = []
    for label in labels:
        tp = sum(1 for row in rows if row["predicted"] == label and row["label"] == label)
        fp = sum(1 for row in rows if row["predicted"] == label and row["label"] != label)
        fn = sum(1 for row in rows if row["predicted"] != label and row["label"] == label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1_values.append(
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    middle_tp = sum(1 for row in rows if row["predicted"] == "middle" and row["label"] == "middle")
    middle_fp = sum(1 for row in rows if row["predicted"] == "middle" and row["label"] != "middle")
    return {
        "accuracy": correct / len(rows),
        "macro_f1": sum(f1_values) / len(f1_values),
        "middle_precision": (
            middle_tp / (middle_tp + middle_fp)
            if middle_tp + middle_fp
            else None
        ),
        "unknown_rate": sum(1 for row in rows if row["predicted"] == "unknown") / len(rows),
    }


def _example(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": row["source"],
        "title": row["title"],
        "url": row["url"],
        "predicted": row["predicted"],
        "label": row.get("label"),
        "evidence": row["evidence"],
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Experience Engine Evaluation",
        "",
        f"- Total: {report['total']}",
        f"- By origin: {report['by_origin']}",
        f"- By confidence: {report['by_confidence']}",
        f"- By prediction: {report['by_prediction']}",
    ]
    metrics = report.get("metrics")
    if metrics:
        lines.append(f"- Metrics: {metrics}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
