"""A single self-contained `report.html` for a run, for a quick visual read of `summary.json`
and `comparison.json` without a notebook or `deepeval inspect`."""

from html import escape
from pathlib import Path

from .models import CaseScore
from .report import HIGHER_IS_WORSE, LOWER_IS_WORSE

_METRIC_ORDER = LOWER_IS_WORSE + HIGHER_IS_WORSE

_STYLE = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem;
       background: #f7f7f9; color: #1c1c1e; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 0.5rem; }
.meta { color: #666; margin-bottom: 1.5rem; }
table { border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
th, td { padding: 0.4rem 0.7rem; border-bottom: 1px solid #e5e5e7; text-align: right; font-size: 0.85rem; }
th { background: #eee; text-align: right; position: sticky; top: 0; }
th:first-child, td:first-child { text-align: left; }
tr:hover { background: #f0f7ff; }
.card { background: #fff; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.pass { color: #1a7f37; }
.fail { color: #c62828; font-weight: 600; }
.reasons { font-size: 0.8rem; color: #666; text-align: left; max-width: 22rem; }
.case-link { color: inherit; text-decoration: none; border-bottom: 1px dotted #999; }
.case-link:hover { border-bottom-style: solid; }
"""


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _cell_class(metric: str, value: float | None, threshold: float) -> str:
    if value is None:
        return ""
    bad = value > (1 - threshold) if metric in HIGHER_IS_WORSE else value < threshold
    return "fail" if bad else "pass"


def _summary_table(configuration: str, config_report: dict) -> str:
    rows = []
    for metric, entry in config_report["metrics"].items():
        if entry["evaluated"] == 0:
            continue
        status = "fail" if entry["failing"] else "pass"
        rows.append(
            f"<tr><td>{escape(metric)}</td><td>{_fmt(entry['mean'])}</td>"
            f"<td>{entry['evaluated']}</td><td class=\"{status}\">{entry['failing']}</td></tr>"
        )
    return (
        f"<div class=\"card\"><h2>{escape(configuration)} (n={config_report['n']})</h2>"
        "<table><thead><tr><th>metric</th><th>mean</th><th>evaluated</th><th>failing</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _case_row(score: CaseScore, threshold: float, metrics: list[str]) -> str:
    case_link = f"cases/{score.case_id}/{score.configuration}/trial-001/answer.txt"
    cells = "".join(
        f"<td class=\"{_cell_class(metric, getattr(score, metric, None), threshold)}\">"
        f"{_fmt(getattr(score, metric, None))}</td>"
        for metric in metrics
    )
    reasons = escape("; ".join(score.reasons)) if score.reasons else ""
    return (
        "<tr><td>"
        f"<a class=\"case-link\" href=\"{escape(case_link)}\">{escape(score.case_id)}</a></td>"
        f"<td>{escape(score.configuration)}</td>{cells}"
        f"<td class=\"reasons\">{reasons}</td></tr>"
    )


def _cases_table(scores: list[CaseScore], threshold: float) -> str:
    metrics = [metric for metric in _METRIC_ORDER if any(getattr(score, metric, None) is not None for score in scores)]
    header = "".join(f"<th>{escape(metric)}</th>" for metric in metrics)
    rows = "".join(_case_row(score, threshold, metrics) for score in scores)
    return (
        "<div class=\"card\"><h2>Cases</h2>"
        f"<table><thead><tr><th>case</th><th>configuration</th>{header}<th>reasons</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def generate_html(run_id: str, report: dict, scores: list[CaseScore]) -> str:
    threshold = report["threshold"]
    summaries = "".join(_summary_table(name, config_report) for name, config_report in report["configurations"].items())
    cases = _cases_table(scores, threshold) if scores else ""
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>Swiss TIP eval report - {escape(run_id)}</title><style>{_STYLE}</style></head>"
        f"<body><h1>Swiss TIP eval report</h1><p class=\"meta\">{escape(run_id)} &middot; "
        f"threshold {threshold}</p>{summaries}{cases}</body></html>"
    )


def write_html_report(run_dir: Path, report: dict, scores: list[CaseScore]) -> Path:
    path = run_dir / "report.html"
    path.write_text(generate_html(run_dir.name, report, scores), encoding="utf-8")
    return path
