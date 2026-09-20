"""Candidate dataset reuse, indexes and conservative consolidation views."""

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import REPORT_SCHEMA
from .checkpoints import atomic_write_json, digest, read_json

ATTRIBUTED_KINDS = {"catalogue", "in-scope", "language-variant", "plugin-document"}
IDENTITY_FIELDS = ("preferred_label", "scope", "concept_type", "granularity", "description")
STOP_WORDS = {"der", "die", "das", "den", "des", "dem", "ein", "eine", "einer", "eines",
              "und", "oder", "für", "durch", "von", "im", "in", "mit", "zum", "zur",
              "the", "of", "for", "and", "a"}


def select_entries(entries: list[dict], *, scope="attributed", kinds=(), sources=(), document_ids=(),
                   languages=(), max_pages=None) -> list[dict]:
    selected = {}
    for entry in entries:
        if (not entry.get("eligible_for_processing") or not entry.get("preferred_representation")
                or entry.get("superseded")):
            continue
        kind = entry.get("attribution_kind")
        if scope == "attributed" and kind not in ATTRIBUTED_KINDS:
            continue
        if kinds and kind not in kinds:
            continue
        if sources and not set(sources).intersection(entry.get("source_ids", [])):
            continue
        if document_ids and entry["document_id"] not in document_ids:
            continue
        language = entry.get("language_declared") or entry.get("language_hint") or ""
        if languages and language.lower() not in {v.lower() for v in languages} and language.lower().split("-")[0] not in {v.lower() for v in languages}:
            continue
        selected[entry["document_id"]] = entry
    ordered = sorted(selected.values(), key=lambda e: (e.get("source_url") or "", e["document_id"]))
    return ordered[:max_pages] if max_pages is not None else ordered


def record_path(directory: Path, document_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
        raise ValueError(f"Invalid document ID: {document_id!r}")
    return Path(directory) / "documents" / f"{document_id}.json"


def reusable(report: dict, record: dict, prompts: dict, profile: str, model: str, settings: dict) -> bool:
    kinds = ("extraction", "review", "basis") if settings.get("classify_basis") else ("extraction", "review")
    return (report.get("schema_version") == REPORT_SCHEMA
            and report.get("content_sha256") == record.get("content_sha256")
            and all(report.get("prompts", {}).get(kind, {}).get("sha256") == prompts[kind]["sha256"] for kind in kinds)
            and report.get("profile") == profile and report.get("model") == model
            and report.get("settings") == settings and not report.get("failures")
            and not any(c.get("status") == "awaiting_response" for c in report.get("chunks", [])))


def write_report(output: Path, report: dict) -> None:
    target = record_path(output, report["document_id"])
    if target.exists():
        old = read_json(target)
        if old.get("content_sha256") != report.get("content_sha256"):
            history = Path(output) / "history" / report["document_id"] / f"{digest(old)}.json"
            atomic_write_json(history, old)
    atomic_write_json(target, report)


def load_reports(output: Path) -> list[dict]:
    return [read_json(path) for path in sorted((Path(output) / "documents").glob("*.json"))]


def _key(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join((value or "").split())).lower()


def consolidate(reports: list[dict]) -> dict:
    groups = {}
    for report in reports:
        for candidate in report.get("candidates", []):
            identity = tuple(_key(v) for v in [report.get("language"), *[candidate[f] for f in IDENTITY_FIELDS]])
            if identity not in groups:
                groups[identity] = dict(group_id=f"proposal-group-{digest(identity)[:16]}",
                                        language=report.get("language"), validation_state="CANDIDATE",
                                        **{f: candidate[f] for f in IDENTITY_FIELDS}, members=[])
            groups[identity]["members"].append(dict(document_id=report["document_id"],
                                                    source_url=report.get("source_url"), candidate=candidate))
    ordered = list(groups.values())
    labels = [{_key(label) for member in group["members"] for label in
               [member["candidate"]["preferred_label"], *member["candidate"].get("alternative_labels", [])]}
              for group in ordered]
    tokens = [set(re.findall(r"\w+", _key(g["preferred_label"]))) - STOP_WORDS for g in ordered]
    pairs, count = [], 0
    for right, second in enumerate(ordered):
        for left in range(right):
            first = ordered[left]
            if _key(first["language"]) != _key(second["language"]):
                continue
            overlap, union = tokens[left] & tokens[right], tokens[left] | tokens[right]
            similarity = len(overlap) / len(union) if union else 0
            aliases = bool(labels[left] & labels[right])
            if not aliases and not (len(overlap) >= 2 and similarity >= .6):
                continue
            count += 1
            if len(pairs) < 200:
                pairs.append(dict(group_ids=[first["group_id"], second["group_id"]],
                                  labels=[first["preferred_label"], second["preferred_label"]],
                                  reason="shared_label_or_alias" if aliases else "similar_label_tokens",
                                  label_similarity=round(similarity, 3),
                                  same_scope=_key(first["scope"]) == _key(second["scope"]),
                                  different_fields=[f for f in IDENTITY_FIELDS[1:] if _key(first[f]) != _key(second[f])],
                                  action="review_equivalence_and_applicability_before_merging"))
    return dict(consolidation_policy="exact_normalized_label_scope_type_granularity_description_language",
                consolidated_concepts=ordered, duplicate_review_pairs=pairs,
                duplicate_review_pair_count=count, duplicate_review_pairs_truncated=count > len(pairs))


def summarize_reports(reports: list[dict]) -> dict:
    def total(field):
        values = [r.get(field) for r in reports]
        return None if any(v is None for v in values) else sum(values)

    return dict(reports=len(reports), candidates=sum(len(r.get("candidates", [])) for r in reports),
                rejected=sum(len(r.get("rejected", [])) for r in reports),
                failed_reports=sum(bool(r.get("failures")) for r in reports),
                request_count=sum(r.get("request_count", 0) for r in reports),
                prompt_tokens=total("prompt_tokens"), output_tokens=total("output_tokens"),
                models=dict(Counter(f"{r.get('provider')}:{r.get('model')}" for r in reports)),
                prompts=sorted({(r.get("prompts", {}).get("extraction", {}).get("sha256", ""),
                                r.get("prompts", {}).get("review", {}).get("sha256", "")) for r in reports}),
                semantic_quality="requires_human_review; counts_do_not_measure_accuracy_or_recall")


def rebuild_dataset(output: Path) -> dict:
    reports = load_reports(output)
    entries = [dict(document_id=r["document_id"], source_url=r.get("source_url"), title=r.get("title"),
                    language=r.get("language"), source_ids=r.get("source_ids", []), job_id=r.get("job_id"),
                    content_sha256=r.get("content_sha256"), candidates=len(r.get("candidates", [])),
                    failures=len(r.get("failures", [])), file=f"documents/{r['document_id']}.json") for r in reports]
    summary = dict(schema_version="swisstip.concept-candidate-dataset/v1", generated_at=datetime.now(UTC).isoformat(),
                   **summarize_reports(reports))
    atomic_write_json(Path(output) / "index.json", entries)
    atomic_write_json(Path(output) / "summary.json", summary)
    by_source = defaultdict(list)
    for entry in entries:
        by_source[", ".join(entry["source_ids"]) or entry.get("source_url") or "unattributed"].append(entry)
    lines = ["# Concept candidates", "", f"{len(reports)} reports; {summary['candidates']} retained candidates.", ""]
    for source, group in sorted(by_source.items()):
        lines.extend([f"## {source}", "", "| Document | Language | Candidates | Failures |",
                      "| --- | --- | ---: | ---: |"])
        for e in group:
            title = (e["title"] or e["document_id"]).replace("|", "/").replace("\n", " ")
            lines.append(f"| [{title}]({e['file']}) | {e['language'] or ''} | {e['candidates']} | {e['failures']} |")
        lines.append("")
    (Path(output) / "index.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return summary


def prune(output: Path, live_ids: set[str]) -> list[str]:
    """Remove only obsolete report files and the explicitly requested saved history."""
    removed = []
    root = Path(output).resolve()
    paths = list((root / "documents").glob("*.json")) + list((root / "history").glob("*/*.json"))
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Report path escapes candidate dataset: {path}")
        if path.parent.name == "documents" and path.stem in live_ids:
            continue
        path.unlink()
        removed.append(str(path.relative_to(root)))
    return removed
