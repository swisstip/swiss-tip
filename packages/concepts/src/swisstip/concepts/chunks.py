"""Pack whole blocks and enumerate exact source spans for model selection."""

import hashlib

from .sections import build_sections


def evidence_spans(block: dict, section_id: str, start: int = 0, end: int | None = None) -> list[dict]:
    text = block["text"]
    end = len(text) if end is None else end
    spans = []
    while start < end:
        stop = min(start + 500, end)
        if stop < end:
            boundary = text.rfind(" ", start + 250, stop)
            if boundary > start:
                stop = boundary
        piece = text[start:stop]
        left = start + len(piece) - len(piece.lstrip())
        right = start + len(piece.rstrip())
        if left < right:
            absolute = block["start"] + left
            quote = text[left:right]
            spans.append(dict(evidence_id=f"b{block['block_number']:05d}:{left}:{right}",
                              section_id=section_id, block_id=block.get("block_id"),
                              block_number=block["block_number"], start=absolute,
                              end=absolute + len(quote), quote=quote, text=quote,
                              text_sha256=block.get("text_sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest()))
        start = stop
    return spans


def _cost(spans: list[dict]) -> int:
    return sum(len(span["text"]) + 32 for span in spans)


def _fragments(block: dict, section_id: str, maximum: int, overlap: int) -> list[tuple[int, int, list[dict]]]:
    text, start, result = block["text"], 0, []
    while start < len(text):
        low, high = start + 1, min(len(text), start + maximum)
        stop = low
        while low <= high:
            middle = (low + high) // 2
            if _cost(evidence_spans(block, section_id, start, middle)) <= maximum:
                stop, low = middle, middle + 1
            else:
                high = middle - 1
        if stop < len(text):
            boundary = text.rfind(" ", start + 1, stop + 1)
            if boundary > start:
                stop = boundary
        spans = evidence_spans(block, section_id, start, stop)
        if spans:
            result.append((start, stop, spans))
        if stop >= len(text):
            break
        next_start = max(start + 1, stop - overlap)
        # Move to a word boundary, ensuring even an unbroken word makes progress.
        if next_start > 0 and not text[next_start - 1].isspace():
            boundary = text.find(" ", next_start, stop)
            next_start = boundary + 1 if boundary >= 0 else stop
        while next_start < len(text) and text[next_start].isspace():
            next_start += 1
        start = next_start
    return result


def pack_chunks(record: dict, sections: list[dict] | None = None, *,
                chunk_content_characters: int = 6400, chunk_overlap_characters: int = 400) -> list[dict]:
    if chunk_content_characters < 500 or not 0 <= chunk_overlap_characters <= chunk_content_characters // 2:
        raise ValueError("chunk size must be at least 500 and overlap at most half the size")
    sections = build_sections(record) if sections is None else sections
    chunks, current = [], None

    def flush():
        nonlocal current
        if current is not None:
            current["chunk_index"] = len(chunks) + 1
            current["section_ids"] = [part["section_id"] for part in current["sections"]]
            chunks.append(current)
            current = None

    for section in sections:
        if not section["kept"]:
            continue
        skipped = section["link_only"]
        if skipped:
            flush()
        section_start = section["span_blocks"][0]["start"]
        for block in section["span_blocks"]:
            whole = evidence_spans(block, section["section_id"])
            fragments = [(0, len(block["text"]), whole)] if _cost(whole) <= chunk_content_characters else _fragments(
                block, section["section_id"], chunk_content_characters, chunk_overlap_characters)
            for start, end, spans in fragments:
                cost = _cost(spans)
                if current and current["characters"] + cost > chunk_content_characters:
                    flush()
                if current is None:
                    current = dict(sections=[], evidence_spans=[], characters=0,
                                   status="skipped_link_only" if skipped else "ready")
                if not current["sections"] or current["sections"][-1]["section_id"] != section["section_id"]:
                    current["sections"].append(dict(section_id=section["section_id"], heading_path=section["heading_path"],
                                                    fragment_start=block["start"] + start - section_start))
                current["evidence_spans"].extend(spans)
                current["characters"] += cost
                if len(fragments) > 1:
                    flush()
        if skipped:
            flush()
    flush()
    for chunk in chunks:
        for span in chunk["evidence_spans"]:
            if record["content_text"][span["start"]:span["end"]] != span["quote"]:
                raise ValueError(f"record offsets do not round-trip for {span['evidence_id']}")
    return chunks
