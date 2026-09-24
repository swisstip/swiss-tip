import unittest

from swisstip.extraction.anchors import make_anchor, relocate
from swisstip.extraction.html_blocks import extract_html
from swisstip.extraction.records import attach_offsets

URL = "https://www.sem.example/faq.html"


def record(html: bytes, document_id: str) -> dict:
    value = dict(document_id=document_id, source_url=URL, acquisition=dict(raw_sha256=document_id * 4),
                 **extract_html(html, URL))
    attach_offsets(value)
    return value


ORIGINAL = record(b"<html><body><main><h1>FAQ</h1><h2>Registration</h2><p>Register within 14 days of arrival.</p>"
                  b"<p>Register before starting work.</p><h2>Permits</h2><p>Permit B is issued for five years.</p></main></body></html>",
                  "doc-old")


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self.anchor = make_anchor(ORIGINAL, 3, 4)

    def test_anchor_captures_hashes_offsets_and_context(self):
        self.assertEqual(self.anchor["block_ids"], ["doc-old:b00003", "doc-old:b00004"])
        self.assertEqual(self.anchor["heading_path"], ["FAQ", "Registration"])
        self.assertEqual(self.anchor["excerpt"], "Register within 14 days of arrival.\n\nRegister before starting work.")
        with self.assertRaises(ValueError):
            make_anchor(ORIGINAL, 5, 9)

    def test_same_text_when_only_bytes_changed(self):
        twin = record(b"<html><!-- build --><body><main><h1>FAQ</h1><h2>Registration</h2><p>Register within 14 days of arrival.</p>"
                      b"<p>Register before starting work.</p><h2>Permits</h2><p>Permit B is issued for five years.</p></main></body></html>",
                      "doc-twin")
        result = relocate(self.anchor, twin)
        self.assertEqual((result["outcome"], result["block_ids"]), ("same-text", ["doc-twin:b00003", "doc-twin:b00004"]))
        self.assertEqual(result["warnings"], [])

    def test_moved_when_a_question_is_inserted_above(self):
        moved = record(b"<html><body><main><h1>FAQ</h1><h2>New question</h2><p>New answer.</p><h2>Registration</h2>"
                       b"<p>Register within 14 days of arrival.</p><p>Register before starting work.</p>"
                       b"<h2>Permits</h2><p>Permit B is issued for five years.</p></main></body></html>", "doc-new")
        result = relocate(self.anchor, moved, ORIGINAL)
        self.assertEqual(result["outcome"], "moved")
        self.assertEqual(result["block_ids"], ["doc-new:b00005", "doc-new:b00006"])
        self.assertEqual(result["excerpt"], self.anchor["excerpt"])
        self.assertEqual(result["warnings"], [])

    def test_context_and_neighbourhood_changes_are_warned(self):
        changed = record(b"<html><body><main><h1>FAQ</h1><h2>Arrival</h2><p>Register within 14 days of arrival.</p>"
                         b"<p>Register before starting work.</p><p>Exception: cross-border commuters.</p>"
                         b"<h2>Permits</h2><p>Permit B is issued for five years.</p></main></body></html>", "doc-ctx")
        result = relocate(self.anchor, changed, ORIGINAL)
        self.assertEqual(result["outcome"], "moved")
        self.assertEqual(result["warnings"], ["context-changed", "neighbourhood-changed"])

    def test_ambiguous_resolved_by_heading_path_or_reported(self):
        anchor = make_anchor(ORIGINAL, 4)
        doubled = record(b"<html><body><main><h1>FAQ</h1><h2>Intro</h2><p>Register before starting work.</p>"
                         b"<h2>Registration</h2><p>Register before starting work.</p></main></body></html>", "doc-two")
        result = relocate(anchor, doubled)
        self.assertEqual((result["outcome"], result["block_ids"]), ("moved", ["doc-two:b00005"]))
        tripled = record(b"<html><body><main><h1>FAQ</h1><h2>Intro</h2><p>Register before starting work.</p>"
                         b"<p>Register before starting work.</p></main></body></html>", "doc-three")
        result = relocate(anchor, tripled)
        self.assertEqual(result["outcome"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)

    def test_restructured_blocks_are_found_by_text(self):
        merged = record(b"<html><body><main><h1>FAQ</h1><h2>Registration</h2><div>Register within 14 days of arrival.<br><br>"
                        b"Register before starting work.</div></main></body></html>", "doc-merged")
        anchor = make_anchor(ORIGINAL, 3)
        result = relocate(anchor, merged)
        self.assertEqual(result["outcome"], "moved")
        self.assertIn("blocks-restructured", result["warnings"])
        self.assertEqual(result["excerpt"], "Register within 14 days of arrival.")

    def test_changed_text_lists_closest_blocks(self):
        reworded = record(b"<html><body><main><h1>FAQ</h1><h2>Registration</h2><p>Register within 14 days after you arrive.</p>"
                          b"<p>Register before starting work.</p></main></body></html>", "doc-changed")
        result = relocate(self.anchor, reworded, ORIGINAL)
        self.assertEqual(result["outcome"], "changed")
        first = result["candidates"][0]
        self.assertEqual(first["old_block_id"], "doc-old:b00003")
        self.assertEqual(first["matches"][0]["text"], "Register within 14 days after you arrive.")
        with self.assertRaises(ValueError):
            relocate(self.anchor, dict(reworded, source_url="https://other"))


if __name__ == "__main__":
    unittest.main()
