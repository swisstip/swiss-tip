"""Validate retained proposals and package them as unconfirmed curation entries."""

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from pathlib import Path

import yaml

from swisstip.build.curation import Curation
from swisstip.build.release_build import TextDataset, build_release
from swisstip.extraction.anchors import make_anchor


class PackagingError(ValueError):
    """Packaging cannot preserve the curation or its exact citations."""


def _atomic_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _slug(label: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40].rstrip("-") or "concept"


def _source_ids(record: dict, entry: dict) -> set[str]:
    return set(entry.get("source_ids", [])) | {
        item.get("definition", {}).get("source_id") for item in record.get("source_registry", [])
        if item.get("definition", {}).get("source_id")
    }


def _existing_candidates(curation: dict) -> set[str]:
    return {
        candidate_id
        for concept in curation.get("concepts", [])
        for fact in concept.get("facts", [])
        for candidate_id in re.findall(r"(?<![\w-])candidate-[A-Za-z0-9_-]+(?![\w-])",
                                       fact.get("provenance", {}).get("source") or "")
    }


def candidate_citations(candidate: dict, record: dict) -> list[dict]:
    """Recheck every span against the current record before widening to blocks."""
    content = record["content_text"]
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != record["content_sha256"]:
        raise PackagingError(f"{record['document_id']}: content hash does not match record text")
    evidence = candidate.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PackagingError("candidate has no evidence")
    numbers = set()
    for span in evidence:
        number = span.get("block_number")
        if type(number) is not int or not 1 <= number <= len(record["blocks"]):
            raise PackagingError(f"invalid evidence block number: {number!r}")
        block = record["blocks"][number - 1]
        start, end = span.get("start"), span.get("end")
        if (type(start) is not int or type(end) is not int
                or not block["start"] <= start < end <= block["end"]):
            raise PackagingError(f"{span.get('evidence_id')}: span is outside block {number}")
        expected_id = f"b{number:05d}:{start - block['start']}:{end - block['start']}"
        if span.get("evidence_id") != expected_id:
            raise PackagingError(f"{span.get('evidence_id')}: evidence ID does not match its offsets")
        if (span.get("block_id") != block["block_id"] or span.get("text_sha256") != block["text_sha256"]
                or span.get("quote") != content[start:end]
                or content[block["start"]:block["end"]] != block["text"]
                or hashlib.sha256(block["text"].encode("utf-8")).hexdigest() != block["text_sha256"]):
            raise PackagingError(f"{span.get('evidence_id')}: evidence no longer matches its block")
        numbers.add(number)
    groups = []
    for number in sorted(numbers):
        if groups and number == groups[-1][1] + 1:
            groups[-1][1] = number
        else:
            groups.append([number, number])
    return [dict(document_id=record["document_id"], first_block=first, last_block=last,
                 anchor=make_anchor(record, first, last)) for first, last in groups]


def _entry(candidate: dict, report: dict, record: dict, concept_id: str, topic: str,
           jurisdiction: str) -> dict:
    review = candidate.get("review", {})
    if (candidate.get("validation_state") != "CANDIDATE" or review.get("decision") != "supported"
            or review.get("issue") != "none" or not str(review.get("reason") or "").strip()):
        raise PackagingError("candidate has no supported separate review")
    confidence = candidate.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise PackagingError("candidate confidence must be a number from 0 to 1")
    label, description = candidate.get("preferred_label"), candidate.get("description")
    if not isinstance(label, str) or not label.strip() or not isinstance(description, str) or not description.strip():
        raise PackagingError("candidate label and description must be nonempty strings")
    provider, model = report.get("provider"), report.get("model")
    if not provider or not model or not report.get("job_id"):
        raise PackagingError("report must identify provider, model and job")
    language = (report.get("language") or "").replace("_", "-").split("-")[0].lower()
    if not re.fullmatch(r"[a-z]{2}", language):
        raise PackagingError(f"candidate has no two-letter language: {report.get('language')!r}")
    prompts = report.get("prompts", {})
    prompt_hashes = [prompts.get(kind, {}).get("sha256", "") for kind in ("extraction", "review")]
    if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in prompt_hashes):
        raise PackagingError("report must identify both prompt hashes")
    author = f"{provider}:{model}"
    source = (f"concepts job {report['job_id']}, document {record['document_id']}, "
              f"candidate {candidate['candidate_id']}, prompts {prompt_hashes[0][:8]}/{prompt_hashes[1][:8]}")
    if report.get("legacy_batch"):
        source += f", legacy batch {report['legacy_batch']}, report {report['legacy_report_id']}"
    citations = candidate_citations(candidate, record)
    notes = [f"scope: {candidate.get('scope', '')}", f"confidence: {confidence} (uncalibrated)",
             f"review: {review['reason']}",
             "relations: " + json.dumps(candidate.get("relations", []), ensure_ascii=False, sort_keys=True)]
    basis = candidate.get("basis")
    if basis:
        # The model's classification of what the evidence is becomes the citation's basis, a proposal like the
        # statement itself: the build labels it, the reviewer confirms or corrects it.
        spec = {key: basis[key] for key in ("kind", "level", "norm", "refers_to") if basis.get(key)}
        for citation in citations:
            citation["basis"] = spec
        notes.append(f"basis: {basis['kind']} ({basis.get('reason') or 'no reason given'})")
    notes.append(f"proposed by {author} on {str(report.get('generated_at', ''))[:10]}, job {report['job_id']}")
    return dict(
        concept_id=concept_id, topic_id=topic, label=label, description=description,
        aliases=candidate.get("alternative_labels", []), questions=candidate.get("user_questions", []),
        source_terms=[], required_context=[], required_user_facts=[], decision_rule=None, notes=notes,
        facts=[dict(fact_id=f"{concept_id}-1", statement=description, language=language, jurisdiction=jurisdiction,
                    provenance=dict(kind="model-candidate", review_status="model-candidate-automated-review",
                                    author=author, source=source, notes=[review["reason"]]),
                    evidence=citations)])


def load_reports(concepts_dir: Path) -> list[dict]:
    """Read the current report dataset, not archived jobs or checkpoint responses."""
    directory = Path(concepts_dir) / "documents"
    if not directory.is_dir():
        raise PackagingError(f"Not a candidate dataset (no documents/): {concepts_dir}")
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]


def package_candidates(reports: list[dict], *, curation_path: Path, text_dir: Path, topic: str,
                       write: bool = False, report_path: Path | None = None, jurisdiction: str | None = None,
                       document_ids=(), sources=(), min_confidence: float = 0.0, concept_types=(),
                       granularities=(), job: str | None = None) -> dict:
    """Preview or atomically append proposals after a complete, successful dry build.

    Existing authored entries are kept byte-for-byte when appending to a normal
    block-style YAML concepts list. Empty lists and other valid YAML layouts are
    serialized without changing their data. The build receives a separate model
    copy because it may add anchors to unanchored existing citations.
    """
    if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
        raise PackagingError("min-confidence must be between 0 and 1")
    curation_path = Path(curation_path)
    destination = Path(report_path) if report_path else curation_path.parent / "packaging-report.json"
    if destination.resolve() == curation_path.resolve():
        raise PackagingError("Packaging report must not overwrite the curation file")
    protected_text = Path(text_dir).resolve()
    if any(path.resolve().is_relative_to(protected_text) for path in (curation_path, destination)):
        raise PackagingError("Packaging outputs must not write inside the source text dataset")
    original_bytes = curation_path.read_bytes()
    original = original_bytes.decode("utf-8-sig")
    data = yaml.safe_load(original)
    if not isinstance(data, dict):
        raise PackagingError("curation must be a mapping")
    if topic not in {item.get("topic_id") for item in data.get("topics", [])}:
        raise PackagingError(f"Topic {topic!r} does not exist in {curation_path}")
    dataset = TextDataset(text_dir)
    existing = _existing_candidates(data)
    used_ids = {item["concept_id"] for item in data.get("concepts", [])}
    entries, packaged, skipped = [], [], []
    document_ids, sources, concept_types = set(document_ids), set(sources), set(concept_types)
    granularities = set(granularities) or {"TOPIC", "ANSWERABLE", "DETAIL"}
    for report in reports:
        if report.get("schema_version") != "swisstip.concept-candidates/v1":
            raise PackagingError(f"Unsupported candidate report: {report.get('schema_version')!r}")
        document_id = report.get("document_id")
        record = dataset.record(document_id) if document_id in dataset.index else None
        index_entry = dataset.index.get(document_id, {})
        for candidate in report.get("candidates", []):
            identifier = candidate.get("candidate_id")
            item = dict(document_id=document_id, candidate_id=identifier,
                        preferred_label=candidate.get("preferred_label"), job_id=report.get("job_id"))
            reason = None
            if not isinstance(identifier, str) or not re.fullmatch(r"candidate-[A-Za-z0-9_-]+", identifier):
                raise PackagingError(f"{document_id}: invalid candidate ID {identifier!r}")
            if identifier in existing:
                reason = "already_present"
            elif document_ids and document_id not in document_ids:
                reason = "document_filter"
            elif job and report.get("job_id") != job:
                reason = "job_filter"
            elif candidate.get("granularity") not in granularities:
                reason = "granularity_filter"
            elif concept_types and candidate.get("concept_type") not in concept_types:
                reason = "concept_type_filter"
            elif not record or not dataset.is_current(document_id):
                reason = "stale"
            elif (not index_entry.get("eligible_for_processing") or not index_entry.get("preferred_representation")
                  or not record.get("eligible_for_processing")):
                reason = "ineligible"
            elif sources and not sources.intersection(_source_ids(record, index_entry)):
                reason = "source_filter"
            elif (report.get("content_sha256") != record["content_sha256"]
                  or report.get("raw_sha256") != record["acquisition"]["raw_sha256"]
                  or report.get("extractor_version") != record["extractor_version"]):
                reason = "stale"
            else:
                confidence = candidate.get("confidence")
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
                    raise PackagingError(f"{identifier}: candidate confidence must be numeric")
                if confidence < min_confidence:
                    reason = "confidence_filter"
            if reason:
                skipped.append(dict(item, reason=reason))
                continue
            registry = record.get("source_registry") or []
            assigned = jurisdiction or (registry[0].get("definition", {}).get("jurisdiction") if registry else None)
            if not assigned:
                skipped.append(dict(item, reason="missing_jurisdiction"))
                continue
            base_id = f"mc-{document_id.removeprefix('doc-')[:8]}-{_slug(candidate['preferred_label'])}"
            concept_id, suffix = base_id, 1
            while concept_id in used_ids:
                suffix += 1
                concept_id = f"{base_id}-{suffix}"
            try:
                entry = _entry(candidate, report, record, concept_id, topic, assigned)
            except (ValueError, KeyError, TypeError) as exc:
                raise PackagingError(f"{document_id}, {identifier}: {exc}") from exc
            entries.append(entry)
            packaged.append(dict(item, concept_id=concept_id, jurisdiction=assigned))
            existing.add(identifier)
            used_ids.add(concept_id)
    combined = copy.deepcopy(data)
    combined.setdefault("concepts", []).extend(entries)
    validated = Curation.model_validate(combined)
    _, build = build_release(validated, Path(text_dir), f"{validated.pack}-concepts-packaging-preview")
    unresolved = [f"{fact.get('fact_id')}: {citation.get('document_id')} ({citation.get('outcome')})"
                  for fact in build["facts_resolved"] + build["dropped"]
                  for citation in fact.get("citations", []) if citation.get("outcome") != "same-snapshot"]
    if unresolved or build["dropped"]:
        raise PackagingError("Dry build must resolve every citation same-snapshot: " + "; ".join(unresolved))
    result = dict(schema_version="swisstip.concept-packaging/v1", curation=str(curation_path),
                  text_dataset=str(Path(text_dir)), written=False, entries=entries, packaged=packaged, skipped=skipped,
                  summary=dict(packaged=len(entries), skipped=len(skipped)), build=build)
    if write and entries:
        if curation_path.read_bytes() != original_bytes:
            raise PackagingError("Curation changed while packaging; rerun against the new file")
        # Appending preserves comments and every existing human-authored entry.
        addition = yaml.safe_dump(entries, allow_unicode=True, sort_keys=False, width=110)
        updated = original.rstrip() + "\n" + "\n".join("  " + line for line in addition.splitlines()) + "\n"
        try:
            matches = yaml.safe_load(updated) == combined
        except yaml.YAMLError:
            matches = False
        if not matches:
            updated = yaml.safe_dump(combined, allow_unicode=True, sort_keys=False, width=110)
        _atomic_text(curation_path, updated)
        result["written"] = True
    _atomic_text(destination, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
