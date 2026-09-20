"""Markdown reading view of a text record, for the knowledge expert.

One line per block with its block number, indented by heading depth. Page
furniture and hidden text are marked so the eye can skip them; nothing is
left out. The JSON record stays the source of truth.
"""

SKIP_REGIONS = {"nav", "header", "footer"}


def block_marks(block: dict) -> str:
    locator = block.get("source_locator", {})
    marks = []
    if locator.get("region") in SKIP_REGIONS:
        marks.append(locator["region"])
    marks.extend(locator.get("furniture", []))
    if locator.get("explicit_hidden"):
        marks.append("hidden")
    if locator.get("is_footnote"):
        marks.append("footnote")
    if locator.get("page") is not None:
        marks.append(f"page {locator['page']}")
    return f" [{', '.join(marks)}]" if marks else ""


def render_table(block: dict) -> list[str]:
    rows = block.get("rows") or []
    lines = []
    if block.get("caption"):
        lines.append(f"*{block['caption']}*")
    for index, row in enumerate(rows):
        lines.append("| " + " | ".join(cell["text"].replace("|", "/") for cell in row) + " |")
        if index == 0:
            lines.append("|" + " --- |" * len(row))
    return lines or [block["text"].replace("\n", " / ")]


def render(record: dict) -> str:
    acquisition = record.get("acquisition", {})
    attribution = record.get("attribution", {})
    lines = [f"# {record.get('title') or record['source_url']}", "",
             f"- Document: `{record['document_id']}` ({record.get('representation')}, {record.get('status')})",
             f"- Source URL: <{record['source_url']}>"]
    if record.get("document_url") and record["document_url"] != record["source_url"]:
        lines.append(f"- Document URL: <{record['document_url']}>")
    if record.get("version_uri"):
        lines.append(f"- Version: <{record['version_uri']}>")
    lines.extend([
        f"- Attribution: {attribution.get('kind')} {', '.join(attribution.get('source_ids', []))}".rstrip(),
        f"- Language: declared `{record.get('language_declared')}`, hint `{record.get('language_hint')}`",
        f"- Retrieved: {acquisition.get('retrieved_at')} ({acquisition.get('attempt')})",
        f"- Raw sha256: `{acquisition.get('raw_sha256')}`",
        f"- Text sha256: `{record.get('content_sha256')}`",
    ])
    if record.get("warnings"):
        lines.append(f"- Warnings: {', '.join(record['warnings'])}")
    if record.get("exclusion_reasons"):
        lines.append(f"- Excluded: {', '.join(record['exclusion_reasons'])}")
    lines.extend(["", "Block numbers are the `bNNNNN` suffix of the block IDs; cite a range by its first and last number.", ""])
    for block in record.get("blocks", []):
        number = block["block_id"].rsplit(":b", 1)[1]
        depth = len(block.get("heading_path", []))
        indent = "  " * min(depth, 6)
        marks = block_marks(block)
        if block["kind"] == "heading":
            level = min(block.get("level", 2) + 1, 6)
            lines.append(f"{'#' * level} b{number} {block['text']}{marks}")
        elif block["kind"] == "table":
            lines.append(f"{indent}b{number} table{marks}")
            lines.extend(f"{indent}{line}" for line in render_table(block))
        else:
            text = block["text"].replace("\n", f"\n{indent}      ")
            lines.append(f"{indent}b{number} {block['kind']}{marks}: {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
