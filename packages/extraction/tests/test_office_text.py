import io
import unittest
import zipfile

from swisstip.extraction.office_text import extract_openxml, extract_rtf
from swisstip.extraction.records import extract_bytes

WORD_XML = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Permit </w:t></w:r><w:r><w:t>documents</w:t></w:r></w:p><w:p><w:r><w:t>  </w:t></w:r></w:p>
<w:p><w:r><w:t>Required: passport.</w:t></w:r></w:p></w:body></w:document>"""
SHEET_XML = b"""<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>120</v></c></row>
<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><f>B1*2</f><v>240</v></c></row></sheetData></worksheet>"""
SHARED_XML = b"""<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Fee</t></si><si><t>Double</t></si></sst>"""


def package(parts: dict[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return raw.getvalue()


class OfficeTextTests(unittest.TestCase):
    def test_docx_paragraphs_keep_order_and_skip_blank_runs(self):
        value = extract_openxml(package({"word/document.xml": WORD_XML}))
        self.assertEqual(value["representation"], "docx")
        self.assertEqual([b["text"] for b in value["blocks"]], ["Permit documents", "Required: passport."])
        self.assertEqual(value["blocks"][1]["source_locator"], dict(part="word/document.xml", paragraph=3))
        self.assertIn("macros_not_executed", value["warnings"])

    def test_xlsx_rows_render_shared_strings_and_keep_formulas(self):
        value = extract_openxml(package({"xl/workbook.xml": b"<workbook/>", "xl/sharedStrings.xml": SHARED_XML,
                                         "xl/worksheets/sheet1.xml": SHEET_XML}))
        self.assertEqual(value["representation"], "xlsx")
        self.assertEqual([b["text"] for b in value["blocks"]], ["Fee | 120", "Double | 240 [formula: B1*2]"])
        self.assertEqual(value["blocks"][1]["cells"][1]["formula"], "B1*2")

    def test_rtf_formatting_and_embedded_picture_are_not_source_text(self):
        raw = br"{\rtf1\ansi Permit \b documents\b0\par {\pict\pngblip 89504e47}Required: passport.}"
        value = extract_rtf(raw)
        self.assertEqual([b["text"] for b in value["blocks"]], ["Permit documents", "Required: passport."])

    def test_dispatch_recognises_office_signatures(self):
        value, representation = extract_bytes(package({"word/document.xml": WORD_XML}), "https://x", "application/octet-stream", "a.bin")
        self.assertEqual(representation, "docx")
        with self.assertRaises(ValueError):
            extract_bytes(package({"other.xml": b"<x/>"}), "https://x", "application/zip", "a.bin")


if __name__ == "__main__":
    unittest.main()
