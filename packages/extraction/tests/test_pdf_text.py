import unittest

from support import make_pdf
from swisstip.extraction.pdf_text import extract_pdf, paragraphs, repeated_lines


class PdfTextTests(unittest.TestCase):
    def test_native_text_and_blank_pages_are_distinguished(self):
        value = extract_pdf(make_pdf(["Permit application", None]))
        self.assertEqual(value["pdf_page_count"], 2)
        self.assertEqual(value["blocks"][0]["text"], "Permit application")
        self.assertEqual(value["blocks"][0]["kind"], "pdf_page")
        self.assertEqual(value["blocks"][0]["source_locator"], dict(page=1, paragraph=1, furniture=[]))
        self.assertEqual(value["pages_without_text"], [2])
        self.assertIn("some_pdf_pages_have_no_text_no_ocr_performed", value["warnings"])
        self.assertEqual(value["form_fields"], [])
        self.assertEqual(value["page_kind"], "pdf_document")

    def test_blank_pdf_reports_missing_text_without_inventing_ocr(self):
        value = extract_pdf(make_pdf([None]))
        self.assertEqual((value["blocks"], value["pages_without_text"]), ([], [1]))

    def test_paragraphs_split_at_blank_lines_only(self):
        self.assertEqual(paragraphs("One line\nsame paragraph\n\nSecond\n \n\nThird"), ["One line\nsame paragraph", "Second", "Third"])
        self.assertEqual(paragraphs("No blank lines\nat all"), ["No blank lines\nat all"])

    def test_repeated_first_and_last_lines_are_page_furniture(self):
        pages = [f"Kanton Bern Migrationsdienst\nInhalt {n}\n\nMehr Text\nSeite {n} von 4" for n in range(1, 5)]
        headers, footers = repeated_lines(pages)
        self.assertEqual(headers, {"kanton bern migrationsdienst"})
        self.assertEqual(footers, {"seite # von #"})
        self.assertEqual(repeated_lines(pages[:2]), (set(), set()))


if __name__ == "__main__":
    unittest.main()
