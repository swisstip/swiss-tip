import unittest

from swisstip.extraction.html_blocks import decode, extract_html


class HtmlBlockTests(unittest.TestCase):
    def test_headings_lists_footnotes_and_unicode_keep_source_context(self):
        raw = """<html lang="de"><head><title>Bewilligung</title></head><body><main><h1>Aufenthalt</h1>
        <article id="art_1"><h2>Art. 1</h2><p>Die Bewilligung <b>ist erforderlich.</b><sup>1</sup></p>
        <ol start="3"><li><p>Pass mitbringen.</p><ul><li>Original vorlegen.</li></ul></li></ol>
        <div class="footnotes"><p id="fn-1">1 Geändert 2026.</p></div></article></main></body></html>""".encode()
        result = extract_html(raw, "https://example.gov/permit")
        blocks = result["blocks"]
        paragraph = next(b for b in blocks if b["text"].startswith("Die Bewilligung"))
        self.assertEqual(paragraph["text"], "Die Bewilligung ist erforderlich.1")
        self.assertEqual(paragraph["heading_path"], ["Aufenthalt", "Art. 1"])
        self.assertEqual(paragraph["source_locator"]["article_id"], "art_1")
        self.assertTrue(paragraph["source_locator"]["in_main"])
        nested = next(b for b in blocks if b["text"] == "Original vorlegen.")
        self.assertEqual(len(nested["source_locator"]["list_context"]), 2)
        self.assertEqual(nested["source_locator"]["list_context"][0]["start"], "3")
        self.assertTrue(blocks[-1]["source_locator"]["is_footnote"])
        self.assertEqual(blocks[-1]["text"], "1 Geändert 2026.")
        self.assertEqual(result["language_declared"], "de")
        self.assertEqual(result["main_headings"], ["Aufenthalt"])
        self.assertTrue(result["text_integrity"]["sequence_preserved"])

    def test_tables_preserve_cells_and_spans_without_duplicate_blocks(self):
        result = extract_html(b"<table><caption>Fees</caption><tr><th rowspan=\"2\">Permit</th><td>10</td></tr>"
                              b"<tr><td><p>20</p><p>per year</p></td></tr></table>", "https://example.gov/")
        self.assertEqual(len(result["blocks"]), 1)
        table = result["blocks"][0]
        self.assertEqual(table["rows"][0][0]["rowspan"], "2")
        self.assertEqual(len(table["rows"]), 2)
        self.assertEqual(table["caption"], "Fees")
        self.assertEqual(table["rows"][1][0]["text"], "20 per year")

    def test_client_side_table_rows_are_read_from_data_entities(self):
        entities = ('{"emptyColumns":[],"data":[{"telefonnummer":"<a href=\\"tel:144\\">144</a>","telefonnummer-sort":"144",'
                    '"bezeichnung":"Sanit\\u00e4tsnotruf"},{"telefonnummer":"117","bezeichnung":"Polizei"}]}')
        page = ("<html><body><main><h2>Notfall Nummern</h2><table data-entities='" + entities.replace("'", "&#39;") + "'>"
                "<thead><tr><th data-data=\"bezeichnung\">Bezeichnung</th><th data-data=\"telefonnummer\">Telefonnummer</th>"
                "</tr></thead><tbody></tbody></table></main></body></html>")
        result = extract_html(page.encode("utf-8"), "https://example.gov/")
        table = next(b for b in result["blocks"] if b["kind"] == "table")
        self.assertEqual(table["text"], "Bezeichnung\tTelefonnummer\nSanitätsnotruf\t144\nPolizei\t117")
        self.assertEqual([c["text"] for c in table["rows"][1]], ["Sanitätsnotruf", "144"])
        self.assertEqual(table["rows"][1][0]["source"], "data-entities")
        self.assertEqual(table["inline_data"]["rows"], 2)
        self.assertTrue(result["text_integrity"]["sequence_preserved"])
        self.assertNotIn("html_text_sequence_differs_review_required", result["warnings"])

    def test_city_of_zurich_contact_card_is_read_from_its_attributes(self):
        page = ("<html><body><main><h1>Zuzug</h1><h2>Dokumente</h2><p>Pass</p>"
                "<stzh-contact main-heading=\"Kontakt\" main-heading-level=\"2\" heading=\"Personenmeldeamt Zürich Süd\" "
                "street='[&#34;Stadthausquai 17&#34;]' street-info='[&#34;Stadthaus&#34;]' postal-code=\"8001\" "
                "location=\"Zürich\" numbers='[{&#34;type&#34;:&#34;TEL&#34;,&#34;label&#34;:&#34;Telefon&#34;,"
                "&#34;number&#34;:&#34;+41 44 412 15 15&#34;}]' websites=\"[]\"><stzh-accordion>"
                "<stzh-accordion-item heading=\"Karte\"><stzh-datalist><stzh-datalist-item "
                "value=\"Fahrplan nach Stadthausquai 17\" href=\"https://www.zvv.ch/fahrplan\"></stzh-datalist-item>"
                "</stzh-datalist></stzh-accordion-item><stzh-accordion-item heading=\"Öffnungszeiten\"><stzh-richtext>"
                "<h4>Mo–Fr</h4>08.00–16.30 Uhr</stzh-richtext></stzh-accordion-item></stzh-accordion>"
                "</stzh-contact></main></body></html>")
        result = extract_html(page.encode("utf-8"), "https://www.stadt-zuerich.ch/zuzug.html")
        texts = [(b["kind"], b["text"]) for b in result["blocks"]]
        self.assertEqual(texts[3:], [
            ("heading", "Kontakt"), ("heading", "Personenmeldeamt Zürich Süd"),
            ("address", "Stadthaus\nStadthausquai 17\n8001 Zürich\nTelefon +41 44 412 15 15"),
            ("heading", "Karte"), ("list_item", "Fahrplan nach Stadthausquai 17"),
            ("heading", "Öffnungszeiten"), ("heading", "Mo–Fr"), ("text", "08.00–16.30 Uhr")])
        by_text = {b["text"]: b for b in result["blocks"]}
        self.assertEqual(by_text["08.00–16.30 Uhr"]["heading_path"],
                         ["Zuzug", "Kontakt", "Personenmeldeamt Zürich Süd", "Mo–Fr"])
        self.assertEqual(by_text["Fahrplan nach Stadthausquai 17"]["links"][0]["href"], "https://www.zvv.ch/fahrplan")
        self.assertEqual(by_text["Karte"]["level"], 4)
        self.assertTrue(result["text_integrity"]["sequence_preserved"])

    def test_city_of_zurich_datatable_is_read_from_its_attributes(self):
        page = ("<main><h1>Schulferien</h1><stzh-datatable hide-column-headings "
                "columns='[{&#34;key&#34;:&#34;a&#34;,&#34;text&#34;:&#34;Schulbeginn&#34;},"
                "{&#34;key&#34;:&#34;b&#34;,&#34;text&#34;:&#34;17.8.2026&#34;}]' "
                "rows='[[{&#34;value&#34;:&#34;&lt;strong&gt;Herbstferien&lt;/strong&gt;&#34;},"
                "{&#34;value&#34;:&#34;5.10.2026 bis 16.10.2026&#34;}]]'>"
                "<stzh-heading level=\"2\" slot=\"heading\">Schuljahr 2026/27</stzh-heading></stzh-datatable></main>")
        result = extract_html(page.encode("utf-8"), "https://www.stadt-zuerich.ch/")
        blocks = [(b["kind"], b["text"]) for b in result["blocks"]]
        self.assertEqual(blocks, [("heading", "Schulferien"), ("heading", "Schuljahr 2026/27"),
                                  ("table", "Schulbeginn\t17.8.2026\nHerbstferien\t5.10.2026 bis 16.10.2026")])
        table = result["blocks"][-1]
        self.assertEqual(table["heading_path"], ["Schulferien", "Schuljahr 2026/27"])
        self.assertTrue(table["rows"][0][0]["header"])
        self.assertEqual(table["inline_data"]["rows"], 2)
        self.assertTrue(result["text_integrity"]["sequence_preserved"])

    def test_components_outside_a_contact_card_add_no_text(self):
        page = (b"<main><stzh-accordion-item heading=\"FAQ\"><p>Answer</p></stzh-accordion-item>"
                b"<stzh-datalist-item value=\"Link\"></stzh-datalist-item></main>")
        result = extract_html(page, "https://www.stadt-zuerich.ch/")
        self.assertEqual([b["text"] for b in result["blocks"]], ["Answer"])

    def test_data_entities_without_column_keys_are_ignored(self):
        page = (b"<table data-entities='{\"data\":[{\"a\":\"x\"}]}'><thead><tr><th>A</th></tr></thead>"
                b"<tbody><tr><td>1</td></tr></tbody></table>")
        table = extract_html(page, "https://example.gov/")["blocks"][0]
        self.assertEqual(table["text"], "A\n1")
        self.assertNotIn("inline_data", table)

    def test_furniture_is_labelled_not_removed(self):
        raw = (b"<html><body><header id=\"header\"><h1>Navigation</h1><div class=\"mod-breadcrumb\"><a href=\"/\">Home</a></div></header>"
               b"<nav><a href=\"/help\">Help</a></nav><div class=\"cookie-banner\"><p>We use cookies.</p></div>"
               b"<main><h1>Apply</h1><p>Apply now.</p><ul class=\"mdl-related-content\"><li><a href=\"/a\">Related A</a></li>"
               b"<li>Bring your <a href=\"/b\">passport</a></li></ul><div hidden><p>Alternate text.</p></div></main>"
               b"<footer>Contact us.</footer><script>doNotInclude()</script></body></html>")
        result = extract_html(raw, "https://example.gov/law")
        by_text = {b["text"]: b for b in result["blocks"]}
        self.assertEqual(by_text["Navigation"]["source_locator"]["furniture"], ["banner"])
        self.assertEqual(by_text["Home"]["source_locator"]["furniture"], ["banner", "breadcrumb"])
        self.assertEqual(by_text["Help"]["source_locator"]["region"], "nav")
        self.assertEqual(by_text["Help"]["source_locator"]["furniture"], ["navigation"])
        self.assertEqual(by_text["Help"]["links"][0]["resolved_url"], "https://example.gov/help")
        self.assertEqual(by_text["We use cookies."]["source_locator"]["furniture"], ["cookie-notice"])
        self.assertEqual(by_text["Related A"]["source_locator"]["furniture"], ["link-only", "related-links"])
        self.assertEqual(by_text["Bring your passport"]["source_locator"]["furniture"], ["related-links"])
        self.assertTrue(by_text["Alternate text."]["source_locator"]["explicit_hidden"])
        self.assertEqual(by_text["Contact us."]["source_locator"]["furniture"], ["footer"])
        self.assertEqual(result["main_headings"], ["Apply"])
        self.assertNotIn("doNotInclude", " ".join(result["blocks"] and [b["text"] for b in result["blocks"]]))
        self.assertEqual(result["ignored_nontext_elements"], {"script": 1})

    def test_main_headings_fall_back_when_the_page_has_no_main_element(self):
        result = extract_html(b"<html><body><header><h1>Site</h1></header><div><h1>Topic</h1><p>x</p></div>"
                              b"<footer><h1>Footer</h1></footer></body></html>", "https://example.gov/")
        self.assertEqual(result["main_headings"], ["Topic"])
        result = extract_html(b"<html><body><header><h1>Only</h1></header></body></html>", "https://example.gov/")
        self.assertEqual(result["main_headings"], ["Only"])

    def test_maintenance_error_and_shell_pages_are_classified(self):
        self.assertEqual(extract_html("<html><title>Wartungsarbeiten</title><body>Änderung bientôt.</body></html>".encode(),
                                      "https://example.gov/")["page_kind"], "maintenance_page")
        self.assertEqual(extract_html(b"<html><body><nav>DE FR</nav><main><h1>Error Page (404)</h1></main></body></html>",
                                      "https://example.gov/residence")["page_kind"], "error_page")
        raw = b"<html><script>window.CONTENT_ID=\"3454\"; window.IS_FRONTEND=true;</script><body>Navigation Login</body></html>"
        self.assertEqual(extract_html(raw, "https://sh.ch/CMS/page")["page_kind"], "application_shell")
        self.assertEqual(extract_html(b"<html><head><title>Fedlex</title></head><body><app-root></app-root></body></html>",
                                      "https://fedlex.admin.ch/eli/cc/2007/758/de")["page_kind"], "application_shell")

    def test_decoding_prefers_valid_utf8_then_http_charset_then_meta(self):
        utf8 = "<html><body>Zürich</body></html>".encode("utf-8")
        self.assertEqual(decode(utf8, "text/html; charset=iso-8859-1")[1], "utf-8")
        latin = "<html><body>Zürich</body></html>".encode("latin-1")
        text, encoding, declared, warnings = decode(latin, "text/html; charset=iso-8859-1")
        self.assertIn("Zürich", text)
        self.assertEqual(encoding, "iso8859-1")
        self.assertEqual(declared["http"], "iso-8859-1")
        self.assertEqual(warnings, [])
        meta = "<html><head><meta charset=\"windows-1252\"></head><body>Zürich</body></html>".encode("cp1252")
        self.assertEqual(decode(meta, "text/html")[1], "cp1252")
        text, encoding, _, warnings = decode(b"<html><body>Z\xfcrich</body></html>", None)
        self.assertEqual((encoding, warnings), ("windows-1252", ["character_encoding_fallback"]))
        self.assertIn("Zürich", text)
        self.assertEqual(decode(b"\xef\xbb\xbf<html>x</html>", None)[1], "utf-8-sig")

    def test_base_href_xml_declaration_and_empty_documents(self):
        raw = b"<?xml version=\"1.0\" encoding=\"utf-8\"?><html><head><base href=\"https://example.gov/base/\"></head>" \
              b"<body><p><a href=\"page.html\">Link</a></p></body></html>"
        result = extract_html(raw, "https://example.gov/")
        self.assertEqual(result["blocks"][0]["links"][0]["resolved_url"], "https://example.gov/base/page.html")
        empty = extract_html(b"   ", "https://example.gov/")
        self.assertEqual((empty["blocks"], empty["page_kind"]), ([], "ordinary_page"))

    def test_text_integrity_flags_dropped_text(self):
        result = extract_html(b"<html><body><p>Kept</p></body></html>", "https://example.gov/")
        self.assertNotIn("html_text_sequence_differs_review_required", result["warnings"])
        self.assertEqual(result["text_integrity"]["source_sequence_sha256"], result["text_integrity"]["extracted_sequence_sha256"])


if __name__ == "__main__":
    unittest.main()
