"""Compare a download run with its source catalogue and classify every gap.

    swisstip-gaps --run releases/<pack>

Reads ``plan.json``, every attempt manifest under ``pages/`` and the summaries
of plugin document folders, then writes ``gap-report.json`` and
``gap-report.md`` into the run directory. Every target gets one gap class and
a verdict on whether a plain retry can close it. No request is made.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Sequence
from urllib.parse import urlsplit

from .acquisition import failure_detail, read_json, review_flags, write_json
from .review_decisions import apply_review_decisions


REPORT_SCHEMA = "swisstip.download-gap-report/v1"

# Saved pages that need no acquisition action but deserve a reader's attention.
INFORMATIONAL = {"javascript-shell-resolved", "rendered-dom"}

# Most specific cause first. A target that failed several times is classified by
# the most specific cause any attempt recorded.
GAP_CLASSES: list[tuple[str, bool, str]] = [
    ("mailto-redirect", False, "the link redirects to an e-mail address; it is a contact, not a document"),
    ("access-denied", False,
     "robots.txt or HTTP 403 denies this client; needs an access review, an alternative official host or a manual capture"),
    ("dns-failure", False, "the host does not resolve; the catalogue needs the successor URL"),
    ("tls-certificate", True, "certificate error; retry with --transport curl or correct the host name in the catalogue"),
    ("not-found", False, "HTTP 404/410; rediscover the page and update the catalogue"),
    ("redirect-out-of-scope", False, "the redirect leaves the host allowlist; review the target host before adding it"),
    ("oversized", True, "larger than the 25 MB response cap; fetch deliberately with a larger limit"),
    ("rate-limited", True, "HTTP 429; retry later with a longer delay"),
    ("server-error", True, "HTTP 5xx or server unavailable; retry later"),
    ("transient-network", True, "connection or timeout failure; retry with --retry-failed"),
    ("robots-unavailable", True, "robots.txt could not be fetched, so the crawler failed closed; retry, possibly with --transport curl"),
    ("unclassified", True, "cause not recognised; inspect the attempt manifests"),
    ("not-attempted", True, "no attempt recorded; run the downloader with --download"),
]
GAP_RANK = {name: rank for rank, (name, _, _) in enumerate(GAP_CLASSES)}
GAP_INFO = {name: (retriable, action) for name, retriable, action in GAP_CLASSES}


def classify_failure(manifest: dict) -> str:
    detail = failure_detail(manifest).lower()
    codes = set(re.findall(r"http (?:error )?(\d{3})", detail))
    if "mailto:" in detail:
        return "mailto-redirect"
    if "robots-disallowed" in detail or "403" in codes or "forbidden" in detail:
        return "access-denied"
    if "getaddrinfo" in detail or "name or service not known" in detail or "nodename nor servname" in detail:
        return "dns-failure"
    if "certificate" in detail or "ssl" in detail:
        return "tls-certificate"
    if codes & {"404", "410"}:
        return "not-found"
    if "redirect-out-of-scope" in detail or "redirect limit" in detail or "redirect to unreviewed" in detail:
        return "redirect-out-of-scope"
    if "response-too-large" in detail or "response-limit" in detail or "exceeds" in detail:
        return "oversized"
    if "429" in codes or "rate-limit" in detail:
        return "rate-limited"
    if any(code.startswith("5") for code in codes) or "server-unavailable" in detail:
        return "server-error"
    if any(term in detail for term in ("timed out", "timeout", "request-failed", "connection", "urlerror", "oserror", "10060", "10061")):
        return "transient-network"
    if "robots-unavailable" in detail:
        return "robots-unavailable"
    if detail == "pending":
        return "not-attempted"
    return "unclassified"


def attempt_manifests(page_folder: Path) -> list[dict]:
    manifests = []
    for folder in sorted(page_folder.glob("attempt-*")):
        path = folder / "manifest.json"
        if path.is_file():
            manifests.append({"attempt": folder.name, **read_json(path)})
    return manifests


def plugin_coverage(run: Path) -> dict[str, list[dict]]:
    """Saved plugin documents by the catalogue URL they were resolved from."""
    covered: dict[str, list[dict]] = {}
    for path in sorted(run.glob("*-documents/summary.json")):
        for result in read_json(path)["results"]:
            if result["status"] == "saved" and result.get("source_page_url"):
                covered.setdefault(result["source_page_url"], []).append({
                    "folder": path.parent.name, "url": result["url"], "version_uri": result.get("version_uri"),
                    "relative_path": [s["relative_path"] for s in result["snapshots"]]})
    return covered


def assess_target(target: dict, run: Path, covered: dict[str, list[dict]]) -> dict:
    folder = run / "pages" / target["url_id"]
    latest_path = folder / "latest.json"
    latest = read_json(latest_path) if latest_path.exists() else {**target, "status": "pending", "snapshots": []}
    attempts = attempt_manifests(folder) if folder.is_dir() else []
    entries = target.get("registry_entries", [])
    attribution = target.get("attribution")
    row = {
        "url": target["url"], "url_id": target["url_id"],
        "label": target["references"][0]["label"] if target.get("references") else target["url"],
        "kind": attribution["kind"] if attribution else "catalogue",
        "source_ids": [entry["definition"]["source_id"] for entry in entries] or (attribution or {}).get("source_ids", []),
        "scan_statuses": sorted({entry["scan_status"] for entry in entries}),
        "status": latest["status"],
        "attempts": len(attempts),
        "saved_attempts": sum(1 for item in attempts if item["status"] == "saved"),
        "attempt_outcomes": [{"attempt": item["attempt"], "status": item["status"],
                              "retrieved_at": next((s["retrieved_at"] for s in item.get("snapshots", [])), None),
                              "detail": None if item["status"] == "saved" else failure_detail(item),
                              "review_flags": sorted({f for s in item.get("snapshots", []) for f in s.get("review_flags", [])})}
                             for item in attempts],
        "latest_snapshots": [{"relative_path": s["relative_path"], "sha256": s["sha256"],
                              "retrieved_at": s["retrieved_at"], "content_type": s["content_type"],
                              "review_flags": s.get("review_flags", [])} for s in latest.get("snapshots", [])],
        "plugin_documents": covered.get(target["url"], []),
    }
    flags = {f for s in latest.get("snapshots", []) for f in s.get("review_flags", [])}
    # Copies made by other tools carry no flags; re-check the saved bytes themselves.
    for item in latest.get("snapshots", []):
        path = run / item["relative_path"]
        if path.is_file():
            flags.update(review_flags(path.read_bytes(), path.suffix.lower()))
    for item in row["latest_snapshots"]:
        item["review_flags"] = sorted(flags) if latest["status"] == "saved" else item["review_flags"]
    failed = [classify_failure(item) for item in attempts if item["status"] != "saved"]
    if latest["status"] == "saved":
        if "error_page_title" in flags:
            gap, retriable, action = "soft-error-page", True, \
                "the server answered HTTP 200 with an error page; retry with --retry-failed, and if it stays an error page, check the URL in a browser and rediscover the page or update the catalogue"
        elif "javascript_application_shell" in flags:
            if row["plugin_documents"]:
                gap, retriable, action = "javascript-shell-resolved", False, \
                    "the page itself is an application shell; the dated documents resolved through the source plugin carry the content"
            else:
                gap, retriable, action = "javascript-shell", False, \
                    "the saved HTML is an application shell without content; a plain retry returns the same shell, so it needs a source adapter or a browser-rendered capture"
        elif "possible_access_challenge" in flags:
            gap, retriable, action = "access-challenge", True, "the saved HTML looks like a bot challenge page; retry or capture manually"
        elif "unexpected_media_type" in flags:
            gap, retriable, action = "unexpected-media-type", True, "the response type differs from the expected document type; inspect before use"
        elif "browser-rendered-dom-not-raw-http-response" in flags:
            gap, retriable, action = "rendered-dom", False, "the latest saved bytes are a browser-rendered DOM, not a raw HTTP response; earlier raw attempts stay available"
        else:
            gap, retriable, action = "none", False, ""
        if failed and gap == "none":
            first_saved = next(item["attempt"] for item in row["attempt_outcomes"] if item["status"] == "saved")
            action = f"recovered on {first_saved} after: " + ", ".join(sorted(set(failed)))
    else:
        gap = min(failed, key=GAP_RANK.get) if failed else classify_failure(latest)
        retriable, action = GAP_INFO[gap]
        row["detail"] = failure_detail(latest)
    if "needs_access_review" in row["scan_statuses"] and gap != "none":
        retriable = False
        action += "; the catalogue marks this source needs_access_review, so a retry needs an operator decision first"
    row.update({"gap": gap, "retriable": retriable, "action": action})
    return row


def build_report(run: Path) -> dict:
    plan = read_json(run / "plan.json")
    covered = plugin_coverage(run)
    rows = [assess_target(target, run, covered) for target in plan["targets"]]
    catalogue_sources = {entry["definition"]["source_id"]
                         for target in plan["targets"] for entry in target.get("registry_entries", [])}
    catalogue_path = Path(plan["catalogue"])
    unplanned = []
    if catalogue_path.is_file():
        data = read_json(catalogue_path)
        unplanned = [entry["definition"]["source_id"] for entry in data["sources"]
                     if entry["definition"]["source_id"] not in catalogue_sources]
    gaps = [row for row in rows if row["gap"] != "none" and row["gap"] not in INFORMATIONAL]
    report = {
        "schema_version": REPORT_SCHEMA, "run": run.name, "catalogue": plan["catalogue"],
        "catalogue_sha256": plan["catalogue_sha256"], "markdown": plan.get("markdown"),
        "target_count": len(rows),
        "counts": {"status": dict(Counter(row["status"] for row in rows)),
                   "kind": dict(Counter(row["kind"] for row in rows)),
                   "gap": dict(Counter(row["gap"] for row in rows)),
                   "gap_by_kind": {kind: dict(Counter(row["gap"] for row in rows if row["kind"] == kind))
                                   for kind in sorted({row["kind"] for row in rows})},
                   "retriable": sum(1 for row in gaps if row["retriable"]),
                   "not_retriable": sum(1 for row in gaps if not row["retriable"]),
                   "informational": sum(1 for row in rows if row["gap"] in INFORMATIONAL),
                   "registry_sources_planned": len(catalogue_sources),
                   "registry_sources_not_in_plan": len(unplanned)},
        "registry_sources_not_in_plan": unplanned,
        "retriable": [row["url"] for row in gaps if row["retriable"]],
        "not_retriable": [row["url"] for row in gaps if not row["retriable"]],
        "informational": [row["url"] for row in rows if row["gap"] in INFORMATIONAL],
        "targets": rows,
    }
    return apply_review_decisions(report, run)


def render_markdown(report: dict) -> str:
    counts = report["counts"]
    has_review = bool(report.get("review_decisions_file"))
    title = "Acquisition review" if has_review else "Download gaps"
    lines = [f"# {title}: {report['run']}", "",
             f"Catalogue: `{Path(report['catalogue']).name}` (sha256 `{report['catalogue_sha256'][:12]}`)."
             + (f" Markdown links from `{Path(report['markdown']).name}`." if report.get("markdown") else ""), "",
             f"Targets: {report['target_count']}. Status counts: {counts['status']}. By kind: {counts['kind']}.",
             (f"Human-reviewed and approved: {counts.get('approved', 0)}. Outstanding review items: {counts.get('outstanding', 0)}."
              if has_review else f"Gap classes: {counts['gap']}."),
             ("Accepted items require no further acquisition work. " if has_review else
              f"Gaps a plain retry can close: {counts['retriable']}. Gaps that need a catalogue change, an access review or a manual capture: {counts['not_retriable']}. ") +
             f"Saved pages with a note (application shell resolved through a plugin, browser-rendered capture): {counts['informational']}.",
             ""]
    if report["registry_sources_not_in_plan"]:
        lines.extend([f"Registry sources not in this plan: {', '.join(report['registry_sources_not_in_plan'])}.", ""])
    if has_review:
        lines.extend([f"Decisions and original observations are recorded in [{report['review_decisions_file']}]({report['review_decisions_file']}). "
                      "Approval accepts the recorded scope; download outcomes retain their original meaning.", ""])
    gaps = [row for row in report["targets"] if (row.get("outstanding", False) if has_review else row["gap"] != "none")]
    if gaps:
        lines.extend(["## Outstanding items" if has_review else "## Gaps", "", "| Source | Kind | URL | Status | Gap | Retriable | What to do |", "| --- | --- | --- | --- | --- | --- | --- |"])
        for row in gaps:
            source = ", ".join(f"`{s}`" for s in row["source_ids"]) or ("(catalogue link)" if row["kind"] == "catalogue" else "-")
            statuses = f" ({', '.join(row['scan_statuses'])})" if row["scan_statuses"] and row["scan_statuses"] != ["ready"] else ""
            lines.append(f"| {source}{statuses} | {row['kind']} | [{row['label']}]({row['url']}) | {row['status']} | {row['gap']} | "
                         f"{'yes' if row['retriable'] else 'no'} | {row['action'].replace('|', '/')} |")
        lines.append("")
    recovered = [row for row in report["targets"] if row["gap"] == "none" and row["action"]]
    if recovered:
        lines.extend(["## Recovered by a later attempt", ""])
        lines.extend(f"- [{row['label']}]({row['url']}): {row['action']}" for row in recovered)
        lines.append("")
    lines.extend(["## All catalogue targets", "", f"| Source | URL | Status | Attempts (saved) | Latest snapshot | {'Review' if has_review else 'Gap'} |", "| --- | --- | --- | --- | --- | --- |"])
    for row in report["targets"]:
        if row["kind"] != "catalogue":
            continue
        source = ", ".join(f"`{s}`" for s in row["source_ids"]) or "-"
        latest = "; ".join(f"[{Path(s['relative_path']).parent.name}]({s['relative_path']}) {s['retrieved_at'][:10]}"
                           + (" " + ", ".join(s["review_flags"]) if s["review_flags"] else "")
                           for s in row["latest_snapshots"]) or "-"
        review = "human-reviewed / approved" if row.get("disposition") == "approved" else row["gap"]
        lines.append(f"| {source} | [{row['label']}]({row['url']}) | {row['status']} | {row['attempts']} ({row['saved_attempts']}) | {latest} | {review} |")
    discovered = [row for row in report["targets"] if row["kind"] != "catalogue"]
    if discovered:
        lines.extend(["", "## Discovered pages", "",
                      "Pages found by a link-following crawl and attributed to catalogue sources by host and path allowlist "
                      "(in-scope), by a published language link from such a page (language-variant), or neither (out-of-scope). "
                      "Per-page rows are in gap-report.json.", "",
                      ("| Host | Kind | Pages | Saved | Outstanding | Open classes |" if has_review else
                       "| Host | Kind | Pages | Saved | Gaps | Gap classes |"), "| --- | --- | ---: | ---: | ---: | --- |"])
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in discovered:
            groups.setdefault((urlsplit(row["url"]).hostname or "", row["kind"]), []).append(row)
        for (host, kind), items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
            gap_counts = Counter(r["gap"] for r in items if (r.get("outstanding", False) if has_review else r["gap"] != "none"))
            lines.append(f"| {host} | {kind} | {len(items)} | {sum(1 for r in items if r['status'] == 'saved')} | "
                         f"{sum(gap_counts.values())} | {', '.join(f'{k} {v}' for k, v in gap_counts.most_common()) or '-'} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True, help="download run directory with a plan.json")
    args = parser.parse_args(argv)
    if not (args.run / "plan.json").is_file():
        parser.error(f"{args.run} has no plan.json")
    report = build_report(args.run)
    write_json(args.run / "gap-report.json", report)
    (args.run / "gap-report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"run": report["run"], "targets": report["target_count"], **report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
