"""Text of public Office documents without launching Office or executing macros.

DOCX and XLSX are read from their OpenXML parts with lxml. Legacy Word files
follow the MS-DOC piece-table text algorithm
(https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/01d5d8c4-cf9c-4ef9-80fd-439e763cfe01)
and need `olefile`; RTF needs `striprtf`. Both are in the optional `office`
dependency group; without them the record is `extraction_failed` with a
reason, never silently empty.
"""

import io
import re
import struct
import zipfile

from lxml import etree


class OfficeDependencyMissing(RuntimeError):
    pass


def block(kind: str, text: str, location: dict, **extra) -> dict:
    return dict(kind=kind, text=text, heading_path=[], source_locator=location, links=[], **extra)


def result(blocks: list[dict], kind: str, **extra) -> dict:
    return dict(blocks=blocks, representation=kind, html_title=None, main_headings=[], language_declared=None,
                page_kind="office_document", decoding=kind,
                warnings=["office_layout_not_rendered", "macros_not_executed"], **extra)


def extract_openxml(raw: bytes) -> dict:
    blocks = []
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError("Office archive expands beyond 100 MiB")
        names = archive.namelist()
        if "word/document.xml" in names:
            for name in sorted(names, key=lambda n: (n != "word/document.xml", n)):
                if not re.fullmatch(r"word/(?:document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml", name):
                    continue
                root = etree.fromstring(archive.read(name), parser)
                for number, node in enumerate(root.xpath('//*[local-name()="p"]'), 1):
                    text = "".join(node.xpath('.//*[local-name()="t" or local-name()="instrText"]/text()'))
                    if text.strip():
                        blocks.append(block("office_paragraph", text, dict(part=name, paragraph=number)))
            return result(blocks, "docx")
        if "xl/workbook.xml" in names:
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = etree.fromstring(archive.read("xl/sharedStrings.xml"), parser)
                shared = ["".join(n.xpath('.//*[local-name()="t"]/text()')) for n in root]
            for name in sorted(names):
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                    root = etree.fromstring(archive.read(name), parser)
                    for row in root.xpath('//*[local-name()="row"]'):
                        cells = []
                        for cell in row:
                            values = cell.xpath('./*[local-name()="v"]/text()')
                            text = values[0] if values else "".join(cell.xpath('.//*[local-name()="t"]/text()'))
                            if cell.get("t") == "s" and text:
                                text = shared[int(text)]
                            formula = "".join(cell.xpath('./*[local-name()="f"]/text()'))
                            cells.append(dict(address=cell.get("r"), value=text, formula=formula or None,
                                              cell_type=cell.get("t"), style_id=cell.get("s")))
                        rendered = " | ".join(c["value"] if not c["formula"] else c["value"] + " [formula: " + c["formula"] + "]"
                                              for c in cells)
                        if rendered.strip(" |"):
                            blocks.append(block("spreadsheet_row", rendered, dict(part=name, row=row.get("r")), cells=cells))
                elif re.fullmatch(r"xl/comments\d+\.xml", name):
                    root = etree.fromstring(archive.read(name), parser)
                    for node in root.xpath('//*[local-name()="comment"]'):
                        text = "".join(node.xpath('.//*[local-name()="t"]/text()'))
                        if text:
                            blocks.append(block("spreadsheet_comment", text, dict(part=name, cell=node.get("ref"))))
            return result(blocks, "xlsx", spreadsheet_note="Cached values and formulas retained; formulas are not recalculated.")
        raise ValueError("Unsupported OpenXML package")


def legacy_word_pieces(word: bytes, table: bytes) -> str:
    # The FIB's variable-length arrays locate FibRgFcLcb97 instead of assuming
    # one absolute offset for every Word revision.
    u16 = lambda buf, pos: struct.unpack_from("<H", buf, pos)[0]  # noqa: E731
    u32 = lambda buf, pos: struct.unpack_from("<I", buf, pos)[0]  # noqa: E731
    position = 32
    position += 2 + 2 * u16(word, position)
    position += 2 + 4 * u16(word, position)
    pair_count = u16(word, position)
    position += 2
    if pair_count <= 33:
        raise ValueError("Legacy Word format has no supported CLX descriptor")
    offset, size = struct.unpack_from("<II", word, position + 33 * 8)
    clx = table[offset:offset + size]
    position = 0
    while position < len(clx) and clx[position] == 1:
        position += 3 + u16(clx, position + 1)
    if position >= len(clx) or clx[position] != 2:
        raise ValueError("Word piece table not found")
    length = u32(clx, position + 1)
    pieces = clx[position + 5:position + 5 + length]
    if len(pieces) != length or (length - 4) % 12:
        raise ValueError("Malformed Word piece table")
    count = (length - 4) // 12
    positions = struct.unpack_from("<" + "I" * (count + 1), pieces)
    if list(positions) != sorted(positions):
        raise ValueError("Unordered Word character positions")
    text = []
    for index in range(count):
        fc = u32(pieces, 4 * (count + 1) + 8 * index + 2)
        compressed = bool(fc & 0x40000000)
        offset = fc & 0x3FFFFFFF
        if compressed:
            offset //= 2
        size = (positions[index + 1] - positions[index]) * (1 if compressed else 2)
        data = word[offset:offset + size]
        if len(data) != size:
            raise ValueError("Word text span lies outside stream")
        text.append(data.decode("cp1252" if compressed else "utf-16-le"))
    return "".join(text)


def extract_ole_word(raw: bytes) -> dict:
    try:
        import olefile
    except ImportError as exc:
        raise OfficeDependencyMissing("olefile is not installed; install swisstip-extraction[office]") from exc
    with olefile.OleFileIO(io.BytesIO(raw)) as ole:
        if not ole.exists("WordDocument"):
            raise ValueError("OLE container is not a supported Word document")
        word = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", word, 10)[0]
        if flags & 0x0100:
            raise ValueError("Encrypted Word document")
        table = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
        text = legacy_word_pieces(word, table)
    blocks = []
    for number, paragraph in enumerate(re.split(r"[\r\n\x07\x0b\x0c]+", text), 1):
        if paragraph.strip():
            blocks.append(block("legacy_word_paragraph", paragraph, dict(stream="WordDocument", paragraph=number)))
    return result(blocks, "doc", legacy_word_note="Text stories and field codes retained; layout and revision visibility are unverified.")


def extract_rtf(raw: bytes) -> dict:
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:
        raise OfficeDependencyMissing("striprtf is not installed; install swisstip-extraction[office]") from exc
    text = rtf_to_text(raw.decode("latin-1"))
    blocks = [block("paragraph", line, dict(paragraph=number))
              for number, line in enumerate(text.splitlines(), 1) if line.strip()]
    return dict(blocks=blocks, representation="rtf", decoding="striprtf", html_title=None, main_headings=[],
                language_declared=None, page_kind="rtf_document", warnings=["rtf_layout_and_embedded_objects_unverified"])
