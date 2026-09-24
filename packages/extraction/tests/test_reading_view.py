import unittest

from swisstip.extraction.html_blocks import extract_html
from swisstip.extraction.reading_view import render
from swisstip.extraction.records import attach_offsets


class ReadingViewTests(unittest.TestCase):
    def test_view_numbers_blocks_and_marks_furniture(self):
        raw = (b"<html><body><nav><a href=\"/\">Home</a></nav><main><h1>Permit</h1><p>Apply <b>now</b>.</p>"
               b"<table><tr><th>Fee</th><td>10</td></tr></table><div hidden><p>Hidden</p></div></main>"
               b"<footer>Contact</footer></body></html>")
        record = dict(document_id="doc-abc", source_url="https://example.gov/p", document_url="https://example.gov/p",
                      title="Permit", representation="html", status="extracted", attribution=dict(kind="catalogue", source_ids=["x"]),
                      language_hint="de", acquisition=dict(retrieved_at="2026-09-11", attempt="attempt-001", raw_sha256="r" * 64),
                      exclusion_reasons=[])
        record.update(extract_html(raw, "https://example.gov/p"))
        attach_offsets(record)
        view = render(record)
        self.assertIn("# Permit", view)
        self.assertIn("b00001 text [nav, navigation]: Home", view)
        self.assertIn("## b00002 Permit", view)
        self.assertIn("  b00003 paragraph: Apply now.", view)
        self.assertIn("| Fee | 10 |", view)
        self.assertIn("[hidden]: Hidden", view)
        self.assertIn("[footer, footer]: Contact", view)
        self.assertIn("`doc-abc`", view)


if __name__ == "__main__":
    unittest.main()
