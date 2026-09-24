"""Download the exact URLs of a source catalogue and resolve enabled source plugins.

Plan first (no requests), then download into the same directory:

    swisstip-download --catalogue releases/<pack>/sources.json --output releases/<pack>
    swisstip-download --catalogue releases/<pack>/sources.json --output releases/<pack> --download

A run directory is bound to the catalogue bytes it was planned from; a changed
catalogue needs a new directory. Saved responses are reused after a hash check;
``--retry-failed`` makes a new attempt for every unsaved target and for every
saved response that is an error page served with HTTP 200.
"""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import time
from typing import Sequence
from urllib.parse import urlsplit

from .acquisition import (
    PLAN_SCHEMA, PRINT_LOCK, NetworkBudgetLock, catalogue_targets, complete_network_attempt,
    error_page, now, read_json, reserve_network_attempt, saved_and_intact, snapshot, summary, write_json,
)
from .catalog import load_source_catalog, select_sources
from .plugins import load_source_plugins


MAX_RESPONSE_BYTES = 25_000_000


def target_is_ready(target: dict) -> bool:
    """Registry seeds follow their scan status; plain catalogue links are always eligible."""
    entries = target.get("registry_entries", [])
    return not entries or any(entry["scan_status"] == "ready" for entry in entries)


def build_plan(catalogue: Path, markdown: Path | None, *, scan_set: str | None, source_ids: list[str] | None,
               workers: int, respect_robots: bool = True) -> dict:
    data = load_source_catalog(catalogue)
    entries = select_sources(data, scan_set=scan_set, source_ids=source_ids)
    targets = catalogue_targets(catalogue, entries, markdown)
    for target in targets:
        hosts = {urlsplit(target["url"]).hostname}
        prefixes = set()
        for entry in target.get("registry_entries", []):
            hosts.update(entry["definition"]["allowed_hosts"])
            prefixes.update(entry["definition"]["allowed_path_prefixes"])
        # Explicit www aliases allow ordinary official-site redirects. No link crawling.
        hosts |= {host[4:] if host.startswith("www.") else "www." + host for host in list(hosts) if host}
        target["allowed_redirect_hosts"] = sorted(hosts)
        target["allowed_path_prefixes"] = sorted(prefixes) or [urlsplit(target["url"]).path or "/"]
    return {"schema_version": PLAN_SCHEMA, "created_at": now(),
            "catalogue": str(catalogue.resolve()), "catalogue_sha256": hashlib.sha256(catalogue.read_bytes()).hexdigest(),
            "markdown": str(markdown.resolve()) if markdown else None,
            "markdown_sha256": hashlib.sha256(markdown.read_bytes()).hexdigest() if markdown else None,
            "catalog_ref": {"artifact_id": data["artifact_id"], "version": data["version"]},
            "selection": {"scan_set": scan_set if source_ids is None else None, "source_ids": source_ids,
                          "selected_source_count": len(entries)},
            "targets": targets,
            "scope": "Exact catalogue URL inventory, depth zero; no recursive crawling",
            "workers": workers,
            "robots_policy": "required; fail closed" if respect_robots else "overridden by operator (--no-obey-robots)",
            "max_response_bytes": MAX_RESPONSE_BYTES}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalogue", type=Path, required=True, help="source registry, a sources.json file")
    parser.add_argument("--markdown", type=Path, help="optional sources.md whose explicit links are added as targets")
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--set", dest="scan_set", help="named scan set of the registry; default: every source")
    selectors.add_argument("--source", action="append", help="explicit source ID; repeatable")
    parser.add_argument("--output", type=Path, required=True, help="run directory; created when absent")
    parser.add_argument("--download", action="store_true", help="make requests; otherwise only save the plan")
    parser.add_argument("--retry-failed", action="store_true", help="retry unsuccessful URLs and saved error pages in an existing run")
    parser.add_argument("--include-not-ready", action="store_true",
                        help="also download registry seeds whose scan_status is not 'ready'")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4, help="host groups in parallel")
    parser.add_argument("--transport", choices=("urllib", "curl"), default="urllib")
    parser.add_argument("--obey-robots", action=argparse.BooleanOptionalAction, default=True,
                        help="respect robots.txt and its crawl delays, failing closed when the policy cannot be read "
                             "(default); --no-obey-robots overrides it for a host you are authorised to access, and "
                             "records robots_status 'overridden' in the run")
    plugins = parser.add_mutually_exclusive_group()
    plugins.add_argument("--source-plugin", action="append", help="enabled source plugin; repeatable; default: fedlex")
    plugins.add_argument("--no-source-plugins", action="store_true", help="only snapshot listed URLs")
    args = parser.parse_args(argv)

    registry = load_source_plugins([] if args.no_source_plugins else args.source_plugin)
    args.output.mkdir(parents=True, exist_ok=True)
    plan_path = args.output / "plan.json"
    if plan_path.exists():
        plan = read_json(plan_path)
        if plan["catalogue_sha256"] != hashlib.sha256(args.catalogue.read_bytes()).hexdigest():
            parser.error("Catalogue changed; use a new output directory to preserve the original plan")
        if args.markdown and plan.get("markdown_sha256") != hashlib.sha256(args.markdown.read_bytes()).hexdigest():
            parser.error("Markdown catalogue changed; use a new output directory to preserve the original plan")
    else:
        try:
            plan = build_plan(args.catalogue, args.markdown, scan_set=args.scan_set, source_ids=args.source,
                              workers=args.workers, respect_robots=args.obey_robots)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            parser.error(str(exc))
        write_json(plan_path, plan)
        (args.output / "catalogue.json").write_bytes(args.catalogue.read_bytes())
        if args.markdown:
            (args.output / "catalogue.md").write_bytes(args.markdown.read_bytes())
    plugin_plan = {"enabled": registry.describe(), "sources": [
        {"url": t["url"], "plugin_id": p.plugin_id}
        for t in plan["targets"] if (p := registry.select(t["url"])) is not None]}
    write_json(args.output / "plugin-plan.json", plugin_plan)
    print(f"Planned {len(plan['targets'])} distinct URLs in {args.output}", flush=True)
    if not args.download:
        summary(args.output, plan)
        return 0
    if (args.output / "autopilot" / "workflow.json").is_file() and not plan.get("network_budget"):
        parser.error("governed download requires human confirmation of the plan and network budget")
    if plan.get("network_budget") and plugin_plan["sources"]:
        parser.error("a confirmed governed plan cannot enable unbudgeted source plugins")

    groups: dict[str, list[dict]] = defaultdict(list)
    for target in plan["targets"]:
        latest = args.output / "pages" / target["url_id"] / "latest.json"
        if latest.exists():
            prior = read_json(latest)
            if saved_and_intact(prior, args.output) and not (args.retry_failed and error_page(prior, args.output)):
                continue
            if prior["status"] != "saved" and not args.retry_failed:
                continue
        if not (args.include_not_ready or target_is_ready(target)):
            statuses = sorted({entry["scan_status"] for entry in target["registry_entries"]})
            print(f"skipped ({', '.join(statuses)}): {target['url']}", flush=True)
            continue
        host = urlsplit(target["url"]).hostname.removeprefix("www.")
        groups[host].append(target)

    def download_group(targets: list[dict]) -> None:
        result: dict = {}
        for index, target in enumerate(targets):
            if index:
                time.sleep(max(2, result.get("report", {}).get("effective_delay_seconds", 2)))
            if plan.get("network_budget"):
                reservation_id, limits = reserve_network_attempt(args.output, plan, target)
                result = snapshot(target, args.output, transport=args.transport, limits=limits,
                                  respect_robots=args.obey_robots)
                complete_network_attempt(args.output, plan, reservation_id, result)
            else:
                result = snapshot(target, args.output, transport=args.transport, respect_robots=args.obey_robots)
            with PRINT_LOCK:
                print(f"{result['status']}: {target['url']}", flush=True)

    budget_lock = NetworkBudgetLock(args.output) if plan.get("network_budget") else nullcontext()
    with budget_lock:
        if plan.get("network_budget"):
            for targets in groups.values():
                download_group(targets)
                summary(args.output, plan)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(download_group, targets) for targets in groups.values()]
                for future in as_completed(futures):
                    future.result()
                    summary(args.output, plan)
        from .plugin_downloads import run_source_plugins

        plugin_reports = run_source_plugins(args.output, registry, transport=args.transport,
                                            retry_failed=args.retry_failed, respect_robots=args.obey_robots)
        result = summary(args.output, plan)
    print(json.dumps({key: result[key] for key in ("target_count", "counts", "saved_bytes")}), flush=True)
    return int(result["counts"].get("saved", 0) != result["target_count"] or any(
        r["counts"].get("saved", 0) != r["target_count"] or r.get("resolution_errors") for r in plugin_reports))


if __name__ == "__main__":
    raise SystemExit(main())
