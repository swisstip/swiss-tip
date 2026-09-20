"""Plan and run resumable concept extraction over a run's text index."""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from . import JOB_SCHEMA
from .budget import Budget, BudgetExceeded, check_plan
from .checkpoints import CheckpointProvider, atomic_write_json, digest, read_json
from .chunks import pack_chunks
from .extract import extract_record
from .prompts import load_prompts
from .providers.base import ProviderError
from .providers.config import create_provider, default_config_path, load_config, validate_config
from .results import (consolidate, load_reports, prune, rebuild_dataset, record_path, reusable,
                      select_entries, summarize_reports, write_report)
from .sections import build_sections, section_summary


def default_output(run: Path) -> Path:
    return default_config_path().parent.parent / ".local" / run.resolve().name / "concepts"


def check_output_location(run: Path, output: Path) -> None:
    run, output = Path(run).resolve(), Path(output).resolve()
    if output == run or any(part.lower() in {"pages", "text"} or part.lower().endswith("-documents")
                            for part in output.parts):
        raise ValueError("Concept output must be outside saved responses and text datasets")
    if any(part.lower() == "releases" for part in run.parts) and output.is_relative_to(run):
        raise ValueError("Concept output must be outside the committed pack; use .local/<pack>/concepts")
    repository_releases = default_config_path().parent.parent / "releases"
    if output.is_relative_to(repository_releases):
        raise ValueError("Concept output must be outside committed packs; use .local/<pack>/concepts")


def report_settings(config: dict) -> dict:
    limits = config["extraction"]
    return {**{key: limits[key] for key in ("chunk_content_characters", "chunk_overlap_characters",
                                           "max_concepts_per_chunk", "max_review_input_characters",
                                           "review_fallback_batch_size", "classify_basis")}, "generation": config["generation"]}


def make_plan(run: Path, output: Path, config: dict, prompts, *, profile=None, scope="attributed",
              kinds=(), sources=(), document_ids=(), languages=(), max_pages=None, allow_large=False,
              force=False, retry_failed=False) -> dict:
    """Read only the index and selected records; estimate two requests per runnable chunk, three when the basis
    of the retained proposals is classified."""
    entries = read_json(Path(run) / "text" / "index.json")
    if not isinstance(entries, list):
        raise ValueError("Text index must be a list of entries")
    limits, settings = config["extraction"], report_settings(config)
    selected = select_entries(entries, scope=scope, kinds=kinds, sources=sources, document_ids=document_ids,
                              languages=languages, max_pages=max_pages or limits.get("max_pages_per_run"))
    name = profile or config["active_profile"]
    if name not in config["profiles"]:
        raise ValueError(f"Unknown model profile: {name}")
    records = []
    for entry in selected:
        item = dict(document_id=entry["document_id"], source_url=entry.get("source_url"),
                    source_ids=entry.get("source_ids", []), content_sha256=entry.get("content_sha256"),
                    status="selected", sections=[], chunks=[], request_count=0, characters=0)
        records.append(item)
        if entry.get("text_characters", 0) > limits["max_characters_per_document"] and not allow_large:
            item.update(status="skipped", reason="max_characters_per_document")
            continue
        record = read_json(record_path(Path(run) / "text", entry["document_id"]))
        if record.get("document_id") != entry["document_id"] or record.get("content_sha256") != entry.get("content_sha256"):
            raise ValueError(f"Text index disagrees with record {entry['document_id']}; rebuild text index")
        existing_path = record_path(output, entry["document_id"])
        existing = read_json(existing_path) if existing_path.exists() else None
        if retry_failed and (existing is None or not existing.get("failures")):
            item.update(status="skipped", reason="retry_failed_only")
            continue
        sections = build_sections(record)
        chunks = pack_chunks(record, sections, chunk_content_characters=limits["chunk_content_characters"],
                             chunk_overlap_characters=limits["chunk_overlap_characters"])
        per_chunk = 3 if limits.get("classify_basis", True) else 2
        item.update(sections=[section_summary(s) for s in sections],
                    chunks=[dict(chunk_index=c["chunk_index"], section_ids=c["section_ids"],
                                 characters=c["characters"], status=c["status"],
                                 evidence_ids=[s["evidence_id"] for s in c["evidence_spans"]],
                                 request_count=0 if c["status"] == "skipped_link_only" else per_chunk) for c in chunks],
                    characters=sum(s["characters"] for s in sections if s["kept"]))
        item["estimated_requests"] = sum(c["request_count"] for c in item["chunks"])
        if existing is not None and not force and reusable(existing, record, prompts.to_dict(), name,
                                                         config["profiles"][name]["model"], settings):
            item.update(status="reused")
        elif item["estimated_requests"] > limits["max_model_requests_per_page"]:
            item.update(status="skipped", reason="max_model_requests_per_page")
        else:
            item["request_count"] = item["estimated_requests"]
    plan = dict(schema_version=JOB_SCHEMA, run=str(Path(run).resolve()), profile=name,
                model=config["profiles"][name]["model"], prompts=prompts.to_dict(), settings=settings,
                selection=dict(scope=scope, kinds=list(kinds or ()), sources=list(sources or ()),
                               document_ids=list(document_ids or ()), languages=list(languages or ()),
                               max_pages=max_pages, allow_large=allow_large),
                ceilings=limits, records=records, selected=len(records),
                request_count=sum(r["request_count"] for r in records),
                characters=sum(r["characters"] for r in records if r["status"] == "selected"))
    # The request identity is independent of whether a previous invocation finished.
    identity = dict(run=plan["run"], profile=name, model=plan["model"], prompts=plan["prompts"],
                    settings=settings, selection=plan["selection"], ceilings=limits,
                    records=[(r["document_id"], r["content_sha256"]) for r in records])
    plan["plan_sha256"] = digest(identity)
    return plan


def run_extraction(run: Path, output: Path | None = None, *, config=None, profile=None, dry_run=False,
                   scope="attributed", kinds=(), sources=(), document_ids=(), languages=(), max_pages=None,
                   allow_large=False, workers=1, force=False, retry_failed=False, prune_superseded=False,
                   fresh_inference=False, extraction_prompt=None, review_prompt=None, basis_prompt=None,
                   max_minutes=None, on_missing="capture", verbose=False, provider_factory=None) -> tuple[int, dict]:
    run = Path(run).resolve()
    output = Path(output).resolve() if output is not None else default_output(run)
    check_output_location(run, output)
    if not dry_run and profile is None:
        raise ValueError("A named --profile is required unless --dry-run is set")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if force and retry_failed:
        raise ValueError("--force and --retry-failed cannot be combined")
    config = validate_config(config) if config is not None else load_config()
    prompts = load_prompts(extraction_prompt_file=extraction_prompt, review_prompt_file=review_prompt,
                           basis_prompt_file=basis_prompt)
    plan = make_plan(run, output, config, prompts, profile=profile, scope=scope, kinds=kinds,
                     sources=sources, document_ids=document_ids, languages=languages, max_pages=max_pages,
                     allow_large=allow_large, force=force or fresh_inference, retry_failed=retry_failed)
    name = plan["profile"]
    suffix = f"-{name}-{plan['plan_sha256'][:6]}"
    existing_jobs = sorted((output / "jobs").glob(f"*{suffix}"))
    job_id = existing_jobs[0].name if existing_jobs else datetime.now(UTC).date().isoformat() + suffix
    job_dir = output / "jobs" / job_id
    plan["job_id"] = job_id
    plan["generated_at"] = datetime.now(UTC).isoformat()
    atomic_write_json(job_dir / "plan.json", plan)
    try:
        check_plan(plan, config["extraction"])
    except BudgetExceeded as exc:
        plan["refused"] = str(exc)
        atomic_write_json(job_dir / "plan.json", plan)
        return 1, plan
    if dry_run:
        return 0, plan
    selected_profile = config["profiles"][name]
    budget = Budget(config["extraction"], max_minutes=max_minutes or config["extraction"].get("max_minutes"))
    log_lock = threading.Lock()

    def log(message):
        with log_lock:
            with (job_dir / "job.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{datetime.now(UTC).isoformat()} {message}\n")
            if verbose:
                print(message, file=sys.stderr, flush=True)

    factory = provider_factory or create_provider
    checkpoint_dirs = list((output / "jobs").glob("*/checkpoints"))
    work = [r for r in plan["records"] if r["status"] == "selected"]

    def extract_one(item):
        record = read_json(record_path(run / "text", item["document_id"]))
        if record.get("content_sha256") != item["content_sha256"]:
            raise ValueError(f"Record {item['document_id']} changed after planning; rerun")
        provider = factory(selected_profile, config["generation"], exchange_dir=job_dir / "exchange", on_missing=on_missing)
        wrapper = CheckpointProvider(provider, job_dir / "checkpoints",
                                     dict(document_id=record["document_id"], content_sha256=record["content_sha256"],
                                          profile=name, model=selected_profile["model"], generation_settings=config["generation"]),
                                     budget, retries=config["retries"], fresh_inference=fresh_inference,
                                     profile_config=selected_profile, log=log, search_dirs=checkpoint_dirs)
        report = extract_record(record, wrapper, prompts=prompts, settings=plan["settings"], job_id=job_id,
                                profile=name, model=selected_profile["model"])
        report["source_ids"] = item["source_ids"]
        report["provider"] = report.get("provider") or selected_profile.get("provider", selected_profile["adapter"])
        write_report(output, report)
        for chunk in report["chunks"]:
            log(f"chunk {record['document_id']} {chunk['chunk_index']} {chunk['status']}")
        log(f"record {record['document_id']} candidates {len(report['candidates'])} failures {len(report['failures'])}")
        return report

    reports = []
    try:
        if workers > 1 and len(work) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                reports = list(pool.map(extract_one, work))
        else:
            reports = [extract_one(item) for item in work]
    finally:
        # Rebuild from disk even after a stopped provider or failed checkpoint write.
        dataset_summary = rebuild_dataset(output)
        on_disk = load_reports(output)
        job_document_ids = {r["document_id"] for r in plan["records"] if r["status"] in {"selected", "reused"}}
        job_reports = [r for r in on_disk if r["document_id"] in job_document_ids]
        summary = dict(schema_version=JOB_SCHEMA, job_id=job_id, plan=plan, ceilings=plan["ceilings"],
                       statistics=budget.snapshot(), **summarize_reports(job_reports), **consolidate(job_reports))
        atomic_write_json(job_dir / "summary.json", summary)
    if prune_superseded:
        live = {e["document_id"] for e in read_json(run / "text" / "index.json") if not e.get("superseded")}
        summary["pruned"] = prune(output, live)
        dataset_summary = rebuild_dataset(output)
        atomic_write_json(job_dir / "summary.json", summary)
    summary["dataset"] = dataset_summary
    return (1 if budget.stopped_reason or any(r.get("failures") for r in reports) else 0), summary


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "check":
        from .providers.exchange import check_exchange
        parser = argparse.ArgumentParser(description="Validate captured exchange responses")
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args(arguments[1:])
        directories = sorted(args.output.glob("jobs/*/exchange"))
        if (args.output / "requests").is_dir():
            directories = [args.output]
        results = [check_exchange(directory) for directory in directories]
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return int(any(r.get("invalid") for r in results))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--scope", choices=("attributed", "all"), default="attributed")
    for field in ("kind", "source", "document-id", "language"):
        parser.add_argument(f"--{field}", action="append")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--allow-large", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    reuse = parser.add_mutually_exclusive_group()
    reuse.add_argument("--force", action="store_true")
    reuse.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--fresh-inference", action="store_true")
    parser.add_argument("--extraction-prompt", type=Path)
    parser.add_argument("--review-prompt", type=Path)
    parser.add_argument("--basis-prompt", type=Path)
    parser.add_argument("--no-classify-basis", action="store_true",
                        help="skip the third call per chunk that says what each retained proposal's evidence is")
    parser.add_argument("--on-missing", choices=("capture", "fail"), default="capture")
    parser.add_argument("--verbose", action="store_true")
    overrides = {"max-requests": "max_model_requests_per_run", "max-requests-per-page": "max_model_requests_per_page",
                 "max-total-input-characters": "max_total_input_characters",
                 "max-characters-per-document": "max_characters_per_document",
                 "max-prompt-tokens": "max_prompt_tokens_per_run", "max-review-input-characters": "max_review_input_characters",
                 "chunk-content-characters": "chunk_content_characters", "chunk-overlap-characters": "chunk_overlap_characters",
                 "max-concepts-per-chunk": "max_concepts_per_chunk", "review-fallback-batch-size": "review_fallback_batch_size"}
    for option in overrides:
        parser.add_argument(f"--{option}", type=int)
    parser.add_argument("--max-minutes", type=float)
    for option in ("max-retries", "backoff-seconds", "max-backoff-seconds", "max-retry-after-seconds"):
        parser.add_argument(f"--{option}", type=int if option == "max-retries" else float)
    args = parser.parse_args(arguments)
    try:
        config = load_config(args.config)
        for option, key in overrides.items():
            value = getattr(args, option.replace("-", "_"))
            if value is not None:
                config["extraction"][key] = value
        if args.max_minutes is not None:
            config["extraction"]["max_minutes"] = args.max_minutes
        if args.no_classify_basis:
            config["extraction"]["classify_basis"] = False
        for key in ("max_retries", "backoff_seconds", "max_backoff_seconds", "max_retry_after_seconds"):
            if getattr(args, key) is not None:
                config["retries"][key] = getattr(args, key)
        config = validate_config(config)
        if args.max_pages is not None and args.max_pages < 1:
            raise ValueError("--max-pages must be at least 1")
        code, result = run_extraction(args.run, args.output, config=config, profile=args.profile,
                                     dry_run=args.dry_run, scope=args.scope, kinds=args.kind, sources=args.source,
                                     document_ids=args.document_id, languages=args.language, max_pages=args.max_pages,
                                     allow_large=args.allow_large, workers=args.workers, force=args.force,
                                     retry_failed=args.retry_failed, prune_superseded=args.prune,
                                     fresh_inference=args.fresh_inference, extraction_prompt=args.extraction_prompt,
                                     review_prompt=args.review_prompt, basis_prompt=args.basis_prompt, max_minutes=args.max_minutes,
                                     on_missing=args.on_missing, verbose=args.verbose)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        printed = {key: result[key] for key in ("job_id", "selected", "request_count", "characters", "ceilings")}
        if "refused" in result:
            printed["refused"] = result["refused"]
    else:
        printed = {key: result[key] for key in ("job_id", "statistics", "reports", "candidates", "rejected", "failed_reports") if key in result}
        if "refused" in result:
            printed["refused"] = result["refused"]
    print(json.dumps(printed, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
