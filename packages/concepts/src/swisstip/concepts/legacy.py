"""Anchor predecessor V3 batch quotes to the current text dataset, conservatively."""

import copy
import json
import re
from collections import Counter
from pathlib import Path

from swisstip.build.release_build import TextDataset

LEGACY_SCHEMA = "swisstip.concept-proposal-batch/v1"


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _heading_key(value: str) -> str:
    # The predecessor inserted spaces around inline links before punctuation.
    # This comparison is only for the discarded prefix, never the cited quote.
    return re.sub(r"\s+([.,;:!?])", r"\1", _normalize(value))


def _normalized_coordinates(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace while retaining each character's original offset."""
    characters, offsets = [], []
    for word in re.finditer(r"\S+", text):
        if characters:
            characters.append(" ")
            offsets.append(word.start() - 1)
        characters.append(word.group())
        offsets.extend(range(word.start(), word.end()))
    return "".join(characters), offsets


def _strip_heading(quote: str, evidence: dict, candidate: dict, record: dict) -> tuple[str, bool]:
    if "\n" not in quote or evidence.get("start") != 0:
        return quote, False
    prefix, body = quote.split("\n", 1)
    headings = set()
    for block in record["blocks"]:
        path = block.get("heading_path", [])
        for start in range(len(path)):
            headings.add(_heading_key(" > ".join(path[start:])))
        if block.get("kind") == "heading":
            headings.add(_heading_key(block["text"]))
    path = candidate.get("heading_path", [])
    if path:
        headings.add(_heading_key(path if isinstance(path, str) else " > ".join(path)))
    prefix_key = _heading_key(prefix)
    # One parser may put the first content word inside the heading. Verify that
    # exact heading tail is still at the start of the body before discarding it.
    continued_heading = any(heading.startswith(prefix_key + " ")
                            and _normalize(body).startswith(heading[len(prefix_key):].lstrip()) for heading in headings)
    if prefix_key in headings or continued_heading:
        return body, True
    return quote, False


def _current(entry: dict) -> bool:
    return bool(entry.get("eligible_for_processing") and entry.get("preferred_representation")
                and not entry.get("superseded"))


def _find_record(report: dict, dataset: TextDataset) -> tuple[dict | None, str | None]:
    provenance = report.get("provenance", {})
    url = provenance.get("source_url") or provenance.get("final_url")
    entries = [entry for entry in dataset.index.values() if entry.get("source_url") == url and _current(entry)]
    matches = [entry for entry in entries if entry.get("raw_sha256") == provenance.get("sha256")]
    selected = max(matches or entries, key=lambda entry: (entry.get("retrieved_at") or "", entry["document_id"]),
                   default=None)
    if selected is None:
        return None, None
    return dataset.record(selected["document_id"]), "hash" if matches else "url"


def anchor_candidate(candidate: dict, record: dict) -> tuple[dict | None, dict]:
    """Every quote must match exactly once; one failed quote drops the candidate."""
    content, offsets = _normalized_coordinates(record["content_text"])
    spans, prefixes = {}, 0
    evidence_items = candidate.get("evidence")
    if not isinstance(evidence_items, list) or not evidence_items:
        return None, dict(reason="missing_evidence")
    for number, evidence in enumerate(evidence_items, 1):
        raw_quote = evidence.get("quote")
        if not isinstance(raw_quote, str):
            return None, dict(reason="missing_quote", evidence_number=number)
        quote, stripped = _strip_heading(raw_quote, evidence, candidate, record)
        prefixes += stripped
        quote = _normalize(quote)
        positions = [match.start() for match in re.finditer(f"(?={re.escape(quote)})", content)] if quote else []
        if len(positions) != 1:
            return None, dict(reason="ambiguous_quote" if positions else "quote_not_found",
                              evidence_number=number, quote=quote, occurrences=len(positions))
        start = offsets[positions[0]]
        end = offsets[positions[0] + len(quote) - 1] + 1
        covering = [(index, block) for index, block in enumerate(record["blocks"], 1)
                    if block["end"] > start and block["start"] < end]
        if not covering:
            return None, dict(reason="quote_outside_blocks", evidence_number=number, quote=quote)
        for index, block in covering:
            first, last = max(start, block["start"]), min(end, block["end"])
            evidence_id = f"b{index:05d}:{first - block['start']}:{last - block['start']}"
            spans[evidence_id] = dict(evidence_id=evidence_id, block_id=block["block_id"], block_number=index,
                                      start=first, end=last, quote=record["content_text"][first:last],
                                      text_sha256=block["text_sha256"])
    converted = copy.deepcopy(candidate)
    converted["evidence"] = sorted(spans.values(), key=lambda span: (span["start"], span["end"]))
    first_block = converted["evidence"][0]["block_number"]
    converted["heading_path"] = list(record["blocks"][first_block - 1].get("heading_path", []))
    return converted, dict(heading_prefixes_stripped=prefixes)


def load_legacy_batch(path: Path, text_dir: Path | TextDataset) -> tuple[list[dict], dict]:
    """Return native reports and an auditable anchored/dropped/ambiguous list."""
    path = Path(path)
    batch = json.loads(path.read_text(encoding="utf-8-sig"))
    if batch.get("schema_version") != LEGACY_SCHEMA:
        raise ValueError(f"Not a {LEGACY_SCHEMA} batch: {path}")
    if not isinstance(batch.get("reports"), list):
        raise ValueError(f"Batch has no reports list: {path}")
    dataset = text_dir if isinstance(text_dir, TextDataset) else TextDataset(text_dir)
    reports, anchored, dropped = [], [], []
    counts = Counter()
    for report_number, original in enumerate(batch["reports"], 1):
        record, matched_by = _find_record(original, dataset)
        retained = []
        report_id = original.get("document_id") or str(report_number)
        for candidate in original.get("candidates", []):
            counts["retained_input"] += 1
            item = dict(batch=str(path), report_id=report_id, report_number=report_number,
                        candidate_id=candidate.get("candidate_id"), preferred_label=candidate.get("preferred_label"))
            if record is None:
                dropped.append(dict(item, reason="record_not_found"))
                continue
            converted, result = anchor_candidate(candidate, record)
            item["document_id"] = record["document_id"]
            if converted is None:
                dropped.append(dict(item, **result))
                continue
            reviews = [review for review in original.get("semantic_reviews", [])
                       if review.get("preferred_label") == candidate.get("preferred_label")
                       and review.get("primary_section_id") == candidate.get("primary_section_id")
                       and review.get("decision") == "supported" and review.get("issue") == "none"]
            if not reviews:
                dropped.append(dict(item, reason="missing_supported_review"))
                continue
            converted["review"] = dict(decision="supported", issue="none",
                                        reason="; ".join(dict.fromkeys(review["reason"] for review in reviews)))
            retained.append(converted)
            anchored.append(dict(item, matched_by=matched_by, **result))
            counts["heading_prefixes_stripped"] += result["heading_prefixes_stripped"]
        if record is not None:
            reports.append(dict(
                schema_version="swisstip.concept-candidates/v1", document_id=record["document_id"],
                source_url=record["source_url"], document_url=record["document_url"], title=record.get("title"),
                language=original.get("language") or record.get("language_declared") or record.get("language_hint"),
                content_sha256=record["content_sha256"], raw_sha256=record["acquisition"]["raw_sha256"],
                extractor_version=record["extractor_version"], job_id=f"legacy-{path.parent.name}",
                profile=original.get("active_profile") or batch.get("active_profile"),
                provider=original.get("provider") or batch.get("provider"),
                model=original.get("model") or batch.get("model"),
                model_identities=original.get("model_identities", []),
                prompts=original.get("effective_prompts", {}), generated_at=original.get("generated_at"),
                candidates=retained, legacy_batch=str(path), legacy_report_id=report_id))
    summary = dict(schema_version="swisstip.legacy-concepts-migration/v1", batch=str(path),
                   reports=len(batch["reports"]), retained_input=counts["retained_input"], anchored=len(anchored),
                   dropped=len(dropped), ambiguous=sum(item["reason"] == "ambiguous_quote" for item in dropped),
                   heading_prefixes_stripped=counts["heading_prefixes_stripped"],
                   drop_reasons=dict(Counter(item["reason"] for item in dropped)),
                   matches=dict(Counter(item["matched_by"] for item in anchored)),
                   anchored_candidates=anchored, dropped_candidates=dropped)
    return reports, summary
