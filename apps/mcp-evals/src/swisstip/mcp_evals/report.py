"""Per-metric threshold summary: which cases scored badly, not just the mean."""

from .models import CaseScore

# Quality metrics where a lower score is worse.
LOWER_IS_WORSE = (
    "citation_precision",
    "citation_recall",
    "tool_selection",
    "fact_accuracy",
    "answer_relevancy",
    "faithfulness",
    "contextual_precision",
    "contextual_recall",
)
# Rate metrics where a higher score is worse; flagged against (1 - threshold).
HIGHER_IS_WORSE = ("unsupported_claim_rate",)


def summarize_scores(scores: list[CaseScore], threshold: float = 0.5) -> dict:
    by_config: dict[str, list[CaseScore]] = {}
    for score in scores:
        by_config.setdefault(score.configuration, []).append(score)

    report: dict = {"threshold": threshold, "configurations": {}}
    for configuration, rows in by_config.items():
        metrics_report = {}
        for metric in LOWER_IS_WORSE:
            values = [(row.case_id, getattr(row, metric)) for row in rows if getattr(row, metric) is not None]
            failing = [case_id for case_id, value in values if value < threshold]
            metrics_report[metric] = _metric_entry(values, failing)
        for metric in HIGHER_IS_WORSE:
            bad_threshold = 1 - threshold
            values = [(row.case_id, getattr(row, metric)) for row in rows if getattr(row, metric) is not None]
            failing = [case_id for case_id, value in values if value > bad_threshold]
            metrics_report[metric] = _metric_entry(values, failing)
        report["configurations"][configuration] = {"n": len(rows), "metrics": metrics_report}
    return report


def _metric_entry(values: list[tuple[str, float]], failing: list[str]) -> dict:
    mean = sum(value for _, value in values) / len(values) if values else None
    return {
        "mean": mean,
        "evaluated": len(values),
        "failing": len(failing),
        "failing_cases": failing,
    }


def format_means(report: dict) -> str:
    """One line per metric, mean only, e.g. for a quick before/after glance."""
    lines = []
    for configuration, config_report in report["configurations"].items():
        lines.append(f"\n{configuration} (n={config_report['n']})")
        for metric, entry in config_report["metrics"].items():
            if entry["mean"] is None:
                continue
            lines.append(f"  {metric:24s} {entry['mean']:.4f}")
    return "\n".join(lines)


def format_summary(report: dict) -> str:
    lines = []
    threshold = report["threshold"]
    for configuration, config_report in report["configurations"].items():
        lines.append(f"\n{configuration} (n={config_report['n']}, threshold={threshold})")
        for metric, entry in config_report["metrics"].items():
            if entry["evaluated"] == 0:
                continue
            lines.append(f"  {metric:24s} {entry['failing']}/{entry['evaluated']} below threshold"
                         + (f"  -> {', '.join(entry['failing_cases'])}" if entry["failing_cases"] else ""))
    return "\n".join(lines)
