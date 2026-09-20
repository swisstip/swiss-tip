"""Build an optional local embedding index and measure concept retrieval.

The benchmark measures search, not legal correctness or caller answers. All
provider access is explicit in the CLI; metric helpers accept fake searchers.
"""

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from swisstip.core.contracts import SearchRequest
from swisstip.core.release import Release, load_release
from swisstip.core.validation import assert_valid
from swisstip.runtime.service import ReleaseService


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_cases(path: Path, release: Release, *, allow_release_change: bool = False) -> dict:
    """Validate a frozen case file against the release it is run on.

    A dataset names the release it was authored for. A later release is refused unless the caller opts in, and
    even then every expected concept and cited fact must still be published: the dataset file itself is never
    rewritten, because measurement records cite its hash.
    """
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("release_id") != release.manifest.release_id and not allow_release_change:
        raise ValueError("Benchmark dataset release_id does not match the loaded release; pass "
                         "--allow-release-change to run it on a later release with the same identifiers.")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError("Benchmark dataset must contain between 1 and 200 cases.")
    concepts = {c.concept_id for c in release.concepts}
    facts = {f.fact_id: f for f in release.facts}
    seen = set()
    for case in cases:
        identity = case.get("case_id")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Every benchmark case needs a unique nonempty case_id.")
        seen.add(identity)
        if not isinstance(case.get("language"), str) or not case["language"]:
            raise ValueError(f"{identity}: language is required.")
        SearchRequest(query=case.get("query", ""), limit=5)
        expected = case.get("expected_concept_ids")
        citations = case.get("evidence_fact_ids")
        if not isinstance(expected, list) or not all(isinstance(x, str) for x in expected):
            raise ValueError(f"{identity}: expected_concept_ids must be a list of strings.")
        if len(expected) != len(set(expected)) or not set(expected) <= concepts:
            raise ValueError(f"{identity}: expected concepts are duplicated or not published.")
        if not isinstance(citations, list) or not all(isinstance(x, str) and x in facts for x in citations):
            raise ValueError(f"{identity}: evidence_fact_ids must identify published facts.")
        if {facts[fid].concept_id for fid in citations} != set(expected):
            raise ValueError(f"{identity}: evidence facts must support every expected concept, and no others.")
        if not expected and not case.get("unsupported_reason"):
            raise ValueError(f"{identity}: unsupported questions require a reason.")
    return dataset


def ranking_metrics(expected: list[str], actual: list[str]) -> dict:
    """Unsupported cases are separate from recall, never zero-denominator passes."""
    wanted = set(expected)
    if not wanted:
        return {"recall_at_3": None, "recall_at_5": None, "all_required_at_3": None,
                "all_required_at_5": None, "top1_correct": None,
                "unsupported_empty": not actual, "unsupported_hit_count": len(actual)}
    return {"recall_at_3": len(wanted & set(actual[:3])) / len(wanted),
            "recall_at_5": len(wanted & set(actual[:5])) / len(wanted),
            "all_required_at_3": wanted <= set(actual[:3]),
            "all_required_at_5": wanted <= set(actual[:5]),
            "top1_correct": bool(actual and actual[0] in wanted) if len(wanted) == 1 else None,
            "unsupported_empty": None, "unsupported_hit_count": None}


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile; explicit and reproducible for small samples."""
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)]


def summarize(samples: list[dict]) -> dict:
    successful = [sample for sample in samples if sample["outcome"] == "success"]
    metrics = [sample["metrics"] for sample in successful]

    def mean(field):
        values = [metric[field] for metric in metrics if metric[field] is not None]
        return statistics.mean(values) if values else None

    latencies = [sample["latency_ms"] for sample in successful]
    return {"attempted_runs": len(samples), "successful_runs": len(successful),
            "provider_failure_runs": sum(sample["outcome"] == "provider-failure" for sample in samples),
            "fallback_runs": sum(sample.get("retrieval_mode") == "lexical-fallback" for sample in samples),
            "error_runs": sum(sample["outcome"] == "error" for sample in samples),
            "supported_runs": sum(metric["recall_at_5"] is not None for metric in metrics),
            "unsupported_runs": sum(metric["unsupported_empty"] is not None for metric in metrics),
            "recall_at_3": mean("recall_at_3"), "recall_at_5": mean("recall_at_5"),
            "all_required_at_3": mean("all_required_at_3"), "all_required_at_5": mean("all_required_at_5"),
            "top1_accuracy_single_expected": mean("top1_correct"),
            "unsupported_empty_rate": mean("unsupported_empty"),
            "unsupported_mean_returned_hits": mean("unsupported_hit_count"),
            "latency_ms_p50": percentile(latencies, 0.5), "latency_ms_p95": percentile(latencies, 0.95),
            "all_attempt_latency_ms_p50": percentile([sample["latency_ms"] for sample in samples], 0.5),
            "all_attempt_latency_ms_p95": percentile([sample["latency_ms"] for sample in samples], 0.95)}


def run_benchmark(cases: list[dict], searchers: dict, repeats: int = 3, before_call=None) -> dict:
    """Searchers return JSON dictionaries. No provider is created by this function.

    A hybrid fallback is measured but excluded from hybrid quality summaries.
    Otherwise a dead provider could appear to pass as successful hybrid search.
    """
    if not 1 <= repeats <= 10:
        raise ValueError("repeats must be between 1 and 10.")
    if not searchers:
        raise ValueError("At least one search mode is required.")
    samples = []
    mode_names = list(searchers)
    for repeat in range(repeats):
        # Rotate the mode order across repeats; case order is frozen.
        offset = repeat % len(mode_names)
        for case in cases:
            for mode in mode_names[offset:] + mode_names[:offset]:
                sample = {"case_id": case["case_id"], "language": case["language"], "mode": mode,
                          "repeat": repeat + 1, "outcome": "error", "retrieval_mode": None,
                          "fallback_reason": None, "hits": [], "metrics": None}
                started = time.perf_counter()
                try:
                    if before_call is not None:
                        before_call(mode)
                    # Provider unloading is outside the timed search, but a failure is recorded.
                    started = time.perf_counter()
                    result = searchers[mode](case["query"], 5)
                    sample["latency_ms"] = (time.perf_counter() - started) * 1000
                    sample["retrieval_mode"] = result.get("retrieval_mode", mode)
                    sample["fallback_reason"] = result.get("fallback_reason")
                    sample["hits"] = result["results"]
                    sample["matched_count"] = result.get("matched_count", len(sample["hits"]))
                    sample["truncated"] = result.get("truncated", False)
                    actual = [hit["concept_id"] for hit in sample["hits"]]
                    if len(actual) != len(set(actual)):
                        raise ValueError("Searcher returned duplicate concept IDs.")
                    sample["metrics"] = ranking_metrics(case["expected_concept_ids"], actual)
                    sample["outcome"] = ("provider-failure" if sample["retrieval_mode"] == "lexical-fallback"
                                         else "success")
                    if sample["retrieval_mode"] not in (mode, "lexical-fallback"):
                        raise ValueError(f"Requested {mode} but got {sample['retrieval_mode']}.")
                except Exception as exc:
                    sample["latency_ms"] = (time.perf_counter() - started) * 1000
                    sample["error"] = f"{type(exc).__name__}: {exc}"
                    sample["outcome"] = ("provider-failure" if isinstance(exc, ProviderFailure) else "error")
                samples.append(sample)
    aggregates = {}
    for mode in mode_names:
        mode_samples = [sample for sample in samples if sample["mode"] == mode]
        aggregates[mode] = summarize(mode_samples)
        aggregates[mode]["by_language"] = {
            language: summarize([sample for sample in mode_samples if sample["language"] == language])
            for language in sorted({case["language"] for case in cases})}
    details = []
    for case in cases:
        detail = dict(case)
        detail["modes"] = {mode: summarize([sample for sample in samples
                                           if sample["mode"] == mode and sample["case_id"] == case["case_id"]])
                           for mode in mode_names}
        details.append(detail)
    return {"aggregates": aggregates, "cases": details, "samples": samples}


class ProviderFailure(ValueError):
    """The benchmark could not execute the requested semantic provider call."""


def service_searcher(service: ReleaseService):
    def search(query: str, limit: int) -> dict:
        return service.search(SearchRequest(query=query, limit=limit)).model_dump(mode="json")
    return search


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("index", "benchmark"):
        command = commands.add_parser(name)
        command.add_argument("--release", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--base-url", default="http://127.0.0.1:11434")
        command.add_argument("--timeout", type=positive_float, default=10.0)
        if name == "index":
            command.add_argument("--model", default="qwen3-embedding:0.6b")
        else:
            command.add_argument("--index", type=Path, required=True)
            command.add_argument("--cases", type=Path, required=True)
            command.add_argument("--repeats", type=int, choices=range(1, 11), default=3)
            command.add_argument("--min-score", type=float, default=0.5)
            command.add_argument("--candidate-limit", type=int, choices=range(5, 101), default=10)
            command.add_argument("--semantic-only", action="store_true", help="also measure the semantic ranker alone")
            command.add_argument("--cold", action="store_true", help="unload the model before each semantic query")
            command.add_argument("--allow-release-change", action="store_true",
                                 help="run a dataset authored for another release; its concept and fact IDs are "
                                      "still validated, and the report names both releases")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # Optional index/provider imports occur only when this explicit developer CLI runs.
    from swisstip.runtime.semantic import (OllamaEmbedder, SemanticError, SemanticSearch,
                                           build_index, load_index, save_index)

    try:
        release = load_release(args.release)
        assert_valid(release)
        base_url = "http://127.0.0.1:11434" if args.base_url == "local" else args.base_url
        if args.command == "index":
            embedder = OllamaEmbedder(model=args.model, base_url=base_url, timeout_seconds=args.timeout)
            started = time.perf_counter()
            index = build_index(release, embedder)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            save_index(index, args.output)
            print(json.dumps({"output": str(args.output), "concepts": len(index.concepts),
                              "dimension": index.dimension, "model": index.model,
                              "model_digest": index.model_digest, "index_sha256": file_hash(args.output),
                              "wall_seconds": time.perf_counter() - started}))
            return 0

        dataset = load_cases(args.cases, release, allow_release_change=args.allow_release_change)
        index = load_index(args.index, release)
        embedder = OllamaEmbedder(model=index.model, base_url=base_url, timeout_seconds=args.timeout)
        semantic = SemanticSearch(index, embedder, min_score=args.min_score, candidate_limit=args.candidate_limit)
        lexical_service = ReleaseService(release)
        hybrid_service = ReleaseService(release, semantic_search=semantic)
        searchers = {"lexical": service_searcher(lexical_service), "hybrid": service_searcher(hybrid_service)}

        def semantic_search(query: str, limit: int) -> dict:
            try:
                ranked = semantic.rank(query)
            except SemanticError as exc:
                raise ProviderFailure(str(exc)) from exc
            return {"retrieval_mode": "semantic", "matched_count": len(ranked), "truncated": len(ranked) > limit,
                    "results": [{"concept_id": concept_id, "score": score} for score, concept_id in ranked[:limit]]}

        if args.semantic_only:
            searchers["semantic"] = semantic_search

        def before_call(mode: str) -> None:
            if args.cold and mode != "lexical":
                try:
                    embedder.unload()
                except SemanticError as exc:
                    raise ProviderFailure(f"Cold model unload failed: {exc}") from exc

        warmup = {"performed": not args.cold, "query": "Swiss residence", "error": None}
        if not args.cold:
            try:
                semantic.rank(warmup["query"])
            except SemanticError as exc:
                warmup["error"] = str(exc)
        started_at = datetime.now(timezone.utc).isoformat()
        results = run_benchmark(dataset["cases"], searchers, repeats=args.repeats, before_call=before_call)
        report = {"benchmark_version": "swisstip-concept-search/v1", "started_at": started_at,
                  "finished_at": datetime.now(timezone.utc).isoformat(),
                  "dataset_id": dataset.get("dataset_id"), "dataset_authorship": dataset.get("authorship"),
                  "dataset_release_id": dataset.get("release_id"),
                  "release_id": release.manifest.release_id, "release_sha256": file_hash(args.release),
                  "release_content_sha256": release.manifest.content_sha256,
                  "dataset_sha256": file_hash(args.cases), "index_sha256": file_hash(args.index),
                  "index_content_sha256": index.content_sha256, "model": index.model,
                  "model_digest": index.model_digest, "dimension": index.dimension,
                  "runtime_code_sha256": {name: file_hash(Path(__file__).with_name(name))
                                           for name in ("service.py", "semantic.py", "search_cli.py")},
                  "settings": {"repeats": args.repeats, "limit": 5, "min_score": args.min_score,
                               "candidate_limit": args.candidate_limit, "base_url": base_url,
                               "timeout_seconds": args.timeout, "cold": args.cold,
                               "modes": list(searchers), "warmup": warmup,
                               "timing": "wall search latency; cold unload excluded; nearest-rank percentiles",
                               "cold_definition": "Ollama model unloaded before each query, OS page cache untouched",
                               "quality_denominator": "successful runs of requested mode; fallbacks excluded",
                               "scope": "Concept retrieval only. No resolve, answer-generation or legal-quality evaluation."},
                  **results}
        write_json(args.output, report)
        print(json.dumps({"output": str(args.output), "aggregates": {
            mode: {key: value for key, value in aggregate.items() if key != "by_language"}
            for mode, aggregate in report["aggregates"].items()}}, ensure_ascii=False))
        return 2 if any(sample["outcome"] != "success" for sample in results["samples"]) else 0
    except (OSError, ValueError) as exc:
        print(f"search_cli: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
