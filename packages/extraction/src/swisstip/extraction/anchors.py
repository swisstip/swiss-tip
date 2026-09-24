"""Re-anchor a curated block range after its page was downloaded again.

An anchor remembers what a curated excerpt looked like: block hashes, heading
path, offsets, text. `relocate` finds the same excerpt in a newer record of
the same source and says how confident that is. It relocates text; it does
not judge whether the rule still holds, which is why neighbourhood changes
are reported as warnings for the expert.
"""

import difflib
from collections import Counter

OUTCOMES = ("same-text", "moved", "ambiguous", "changed")


def block_range(record: dict, first_block: int, last_block: int | None = None) -> list[dict]:
    last_block = last_block or first_block
    if first_block < 1 or last_block < first_block or last_block > len(record["blocks"]):
        raise ValueError(f"Block range {first_block}-{last_block} outside {record['document_id']}")
    return record["blocks"][first_block - 1:last_block]


def make_anchor(record: dict, first_block: int, last_block: int | None = None) -> dict:
    blocks = block_range(record, first_block, last_block)
    start, end = blocks[0]["start"], blocks[-1]["end"]
    return dict(document_id=record["document_id"], source_url=record["source_url"],
                content_sha256=record["content_sha256"], raw_sha256=record["acquisition"]["raw_sha256"],
                block_ids=[b["block_id"] for b in blocks], block_hashes=[b["text_sha256"] for b in blocks],
                heading_path=list(blocks[0].get("heading_path", [])), start=start, end=end,
                excerpt=record["content_text"][start:end])


def hash_sequence_positions(record: dict, hashes: list[str]) -> list[int]:
    """Zero-based block indexes where the hash sequence starts."""
    values = [b["text_sha256"] for b in record["blocks"]]
    width = len(hashes)
    return [index for index in range(len(values) - width + 1) if values[index:index + width] == hashes]


def text_positions(record: dict, excerpt: str) -> list[int]:
    positions, start = [], 0
    content = record["content_text"]
    while excerpt:
        found = content.find(excerpt, start)
        if found < 0:
            break
        positions.append(found)
        start = found + 1
    return positions


def blocks_covering(record: dict, start: int, end: int) -> list[dict]:
    return [b for b in record["blocks"] if b["end"] > start and b["start"] < end]


def neighbourhood(record: dict, heading_path: list[str], exclude: set[str]) -> Counter:
    return Counter(b["text_sha256"] for b in record["blocks"]
                   if list(b.get("heading_path", [])) == list(heading_path) and b["text_sha256"] not in exclude)


def closest_blocks(anchor: dict, record: dict, old_record: dict | None, limit: int = 3) -> list[dict]:
    """For a changed excerpt, the new blocks that read most like the old ones."""
    old_texts = {}
    if old_record:
        by_id = {b["block_id"]: b["text"] for b in old_record["blocks"]}
        old_texts = {block_id: by_id.get(block_id, "") for block_id in anchor["block_ids"]}
    else:
        old_texts = {anchor["block_ids"][0]: anchor["excerpt"]}
    candidates = []
    for block_id, old_text in old_texts.items():
        scored = sorted(((difflib.SequenceMatcher(None, old_text, b["text"]).ratio(), b) for b in record["blocks"]),
                        key=lambda pair: pair[0], reverse=True)[:limit]
        candidates.append(dict(old_block_id=block_id, old_text=old_text,
                               matches=[dict(block_id=b["block_id"], ratio=round(ratio, 3), text=b["text"]) for ratio, b in scored]))
    return candidates


def resolved(anchor: dict, record: dict, blocks: list[dict], outcome: str, old_record: dict | None) -> dict:
    start, end = blocks[0]["start"], blocks[-1]["end"]
    excerpt = record["content_text"][start:end]
    warnings = []
    if list(blocks[0].get("heading_path", [])) != list(anchor["heading_path"]):
        warnings.append("context-changed")
    if old_record is not None:
        exclude = set(anchor["block_hashes"])
        if neighbourhood(old_record, anchor["heading_path"], exclude) != neighbourhood(record, blocks[0].get("heading_path", []), exclude):
            warnings.append("neighbourhood-changed")
    if excerpt != anchor["excerpt"]:
        warnings.append("excerpt-text-differs")
    return dict(outcome=outcome, document_id=record["document_id"], block_ids=[b["block_id"] for b in blocks],
                start=start, end=end, excerpt=excerpt, heading_path=list(blocks[0].get("heading_path", [])),
                content_sha256=record["content_sha256"], raw_sha256=record["acquisition"]["raw_sha256"],
                warnings=warnings, candidates=[])


def relocate(anchor: dict, record: dict, old_record: dict | None = None) -> dict:
    """Find the anchored excerpt in `record`, a newer text record of the same source."""
    if record.get("source_url") != anchor["source_url"]:
        raise ValueError("relocate expects a record of the same source URL")
    if record["content_sha256"] == anchor["content_sha256"]:
        blocks = blocks_covering(record, anchor["start"], anchor["end"])
        return resolved(anchor, record, blocks, "same-text", None)
    positions = hash_sequence_positions(record, anchor["block_hashes"])
    width = len(anchor["block_hashes"])
    if len(positions) > 1:
        same_context = [p for p in positions if list(record["blocks"][p].get("heading_path", [])) == list(anchor["heading_path"])]
        if len(same_context) == 1:
            positions = same_context
    if len(positions) == 1:
        return resolved(anchor, record, record["blocks"][positions[0]:positions[0] + width], "moved", old_record)
    if len(positions) > 1:
        return dict(outcome="ambiguous", document_id=record["document_id"], block_ids=[], start=None, end=None,
                    excerpt=None, heading_path=None, content_sha256=record["content_sha256"],
                    raw_sha256=record["acquisition"]["raw_sha256"], warnings=[],
                    candidates=[dict(block_ids=[b["block_id"] for b in record["blocks"][p:p + width]],
                                     heading_path=record["blocks"][p].get("heading_path", [])) for p in positions])
    found = text_positions(record, anchor["excerpt"])
    if len(found) == 1:
        start = found[0]
        blocks = blocks_covering(record, start, start + len(anchor["excerpt"]))
        result = resolved(anchor, record, blocks, "moved", old_record)
        result.update(start=start, end=start + len(anchor["excerpt"]), excerpt=anchor["excerpt"])
        result["warnings"] = [w for w in result["warnings"] if w != "excerpt-text-differs"] + ["blocks-restructured"]
        return result
    if len(found) > 1:
        return dict(outcome="ambiguous", document_id=record["document_id"], block_ids=[], start=None, end=None,
                    excerpt=None, heading_path=None, content_sha256=record["content_sha256"],
                    raw_sha256=record["acquisition"]["raw_sha256"], warnings=["matched-by-text"],
                    candidates=[dict(start=p, end=p + len(anchor["excerpt"]),
                                     block_ids=[b["block_id"] for b in blocks_covering(record, p, p + len(anchor["excerpt"]))])
                                for p in found])
    return dict(outcome="changed", document_id=record["document_id"], block_ids=[], start=None, end=None, excerpt=None,
                heading_path=None, content_sha256=record["content_sha256"], raw_sha256=record["acquisition"]["raw_sha256"],
                warnings=[], candidates=closest_blocks(anchor, record, old_record))
