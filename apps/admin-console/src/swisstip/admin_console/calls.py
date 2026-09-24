"""The server's log lines, parsed into the operations screen (section 4.9).

The server writes one line per call:

    2026-09-13 10:00:00,123 swiss-tip INFO tool=search status=OK bytes=812 ms=1.4 release=<pack>-2026-09-13-v1

Over stdio that line goes to stderr inside the client and is not collected,
so the screen stays empty until the streamable HTTP transport writes a log
file. The parser already accepts the two additive keys the gap feed needs,
`gap=` on a call whose status is not SUPPORTED and `hits=` on a search, so
the format can grow without this module changing. Request payloads are never
logged; a query is logged only when it produced no hit.
"""

import json
import re
from collections import Counter
from pathlib import Path

KEY_VALUE = re.compile(r"(?P<key>[a-z_]+)=(?P<value>\"[^\"]*\"|\S+)")
TIMESTAMP = re.compile(r"^(?P<at>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})[.,]?(?P<millis>\d+)?")
NUMERIC = {"bytes": int, "ms": float, "hits": int}
CALL_KEYS = ("tool", "status", "bytes", "ms", "release", "gap", "hits", "query")


def parse_line(line: str) -> dict | None:
    """One call as a record, or None for a line that is not a call."""
    values = {match["key"]: match["value"].strip('"') for match in KEY_VALUE.finditer(line)}
    if "tool" not in values:
        return None
    entry: dict = {}
    stamp = TIMESTAMP.match(line.strip())
    if stamp:
        entry["at"] = stamp["at"].replace(" ", "T")
    for key in CALL_KEYS:
        if key not in values:
            continue
        try:
            entry[key] = NUMERIC[key](values[key]) if key in NUMERIC else values[key]
        except ValueError:
            entry[key] = values[key]
    return entry


def parse_log(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return [entry for entry in (parse_line(line) for line in text.splitlines()) if entry]


def append_calls(path: Path, entries: list[dict]) -> int:
    """`calls.jsonl` is append only and holds exactly what was parsed."""
    if not entries:
        return 0
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(entries)


def read_calls(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile; no interpolation, so a single call reports itself."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), -(-int(round(fraction * len(ordered) * 100)) // 100)))
    return round(ordered[rank - 1], 1)


def bucket(entry: dict, by: str) -> str:
    at = entry.get("at") or ""
    return at[:13] if by == "hour" else at[:10]


def aggregate(entries: list[dict], *, by: str = "hour") -> dict:
    """Traffic, latency and size per tool, plus the gap feed (section 4.9)."""
    tools = sorted({entry["tool"] for entry in entries})
    per_tool = []
    for tool in tools:
        rows = [entry for entry in entries if entry["tool"] == tool]
        latencies = [row["ms"] for row in rows if isinstance(row.get("ms"), (int, float))]
        sizes = [row["bytes"] for row in rows if isinstance(row.get("bytes"), int)]
        per_tool.append(dict(
            tool=tool, calls=len(rows), statuses=dict(Counter(row.get("status", "?") for row in rows)),
            errors=sum(1 for row in rows if str(row.get("status", "")).endswith("_ERROR")
                       or row.get("status") in ("INVALID_ARGUMENT", "RELEASE_UNAVAILABLE")),
            ms=dict(p50=percentile(latencies, 0.5), p95=percentile(latencies, 0.95), p99=percentile(latencies, 0.99)),
            bytes=dict(p50=percentile(sizes, 0.5), p95=percentile(sizes, 0.95), p99=percentile(sizes, 0.99))))
    traffic = {}
    for entry in entries:
        traffic.setdefault(bucket(entry, by), Counter())[entry["tool"]] += 1
    gaps = Counter(entry["gap"] for entry in entries
                   if entry.get("gap") and entry.get("status") == "OUT_OF_COVERAGE")
    needs_context = Counter(entry["gap"] for entry in entries
                            if entry.get("gap") and entry.get("status") == "NEEDS_CONTEXT")
    empty_searches = Counter(entry["query"] for entry in entries
                             if entry.get("tool") == "search" and entry.get("hits") == 0 and entry.get("query"))
    return dict(
        calls=len(entries), releases=sorted({entry["release"] for entry in entries if entry.get("release")}),
        per_tool=per_tool, by=by,
        traffic=[dict(bucket=key, counts=dict(value), total=sum(value.values()))
                 for key, value in sorted(traffic.items())],
        error_codes=dict(Counter(entry.get("status") for entry in entries
                                 if entry.get("status") in ("INVALID_ARGUMENT", "RELEASE_UNAVAILABLE",
                                                            "OPERATIONAL_ERROR"))),
        gap_feed=dict(out_of_coverage=gaps.most_common(20), needs_context=needs_context.most_common(20),
                      searches_without_hit=empty_searches.most_common(20)))
