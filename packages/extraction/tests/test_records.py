import unittest

from support import make_pdf
from swisstip.extraction.records import (assemble, attach_offsets, extract_bytes, language_hint, title_for,
                                         url_language)
from swisstip.extraction.run_reader import document_id


def item(**overrides) -> dict:
    base = dict(pointer="pages/x/latest.json", plugin_id=None, url="https://www.zh.example/de/permit.html",
                source_url="https://www.zh.example/de/permit.html", document_url="https://www.zh.example/de/permit.html",
                requested_url="https://www.zh.example/de/permit.html", version_uri=None, language=None,
                registry_entries=[], references=[dict(label="Permit", catalogue_line=1)], discoveries=[], imported_from=None,
                status="saved", http_status=200, relative_path="pages/x/attempt-001/response.html", attempt="attempt-001",
                attribution=dict(kind="catalogue", source_ids=["zh-permit"]))
    base.update(overrides)
    return base


def with_snapshot(entry: dict, raw: bytes, content_type: str = "text/html", flags=()) -> dict:
    import hashlib
    digest = hashlib.sha256(raw).hexdigest()
    entry["snapshot"] = dict(relative_path=entry["relative_path"].split("/", 1)[1] if entry["relative_path"].startswith("pages/") else entry["relative_path"],
                             requested_url=entry["requested_url"], final_url=entry["document_url"], content_type=content_type,
                             sha256=digest, bytes_downloaded=len(raw), retrieved_at="2026-09-11T06:00:00+00:00",
                             review_flags=list(flags))
    entry["raw_sha256"] = digest
    entry["document_id"] = document_id(entry["source_url"], digest)
    return entry


class RecordTests(unittest.TestCase):
    def test_document_id_depends_on_source_and_bytes_not_on_the_attempt(self):
        self.assertEqual(document_id("https://a", "ff"), document_id("https://a", "ff"))
        self.assertNotEqual(document_id("https://a", "ff"), document_id("https://a", "fe"))
        self.assertNotEqual(document_id("https://a", "ff"), document_id("https://b", "ff"))
        self.assertTrue(document_id("https://a", "ff").startswith("doc-"))

    def test_url_language_uses_whole_segments_and_file_suffixes(self):
        self.assertEqual(url_language("https://www.sem.admin.ch/sem/fr/home/themen.html"), "fr")
        self.assertEqual(url_language("https://www.fedlex.admin.ch/eli/cc/2007/758/de"), "de")
        self.assertEqual(url_language("https://www.zh.example/it-services/page.html"), None)
        self.assertEqual(url_language("https://www.zh.example/docs/merkblatt_fr.pdf"), "fr")
        self.assertEqual(url_language("https://www.zh.example/docs/merkblatt-en.pdf"), "en")
        self.assertEqual(url_language("https://www.zh.example/docs/leaflet.pdf"), None)

    def test_language_hint_prefers_manifest_then_registry_then_url(self):
        self.assertEqual(language_hint(item(language="fr")), "fr")
        self.assertEqual(language_hint(item(registry_entries=[dict(definition=dict(language="it"))])), "it")
        self.assertEqual(language_hint(item()), "de")
        self.assertIsNone(language_hint(item(source_url="https://x/y", document_url="https://x/y")))

    def test_title_rule(self):
        self.assertEqual(title_for("Page title", [dict(label="Ref")], ["H1"], "https://u"), "Page title")
        self.assertEqual(title_for("input-de", [dict(label="Ref")], ["H1"], "https://u"), "Ref")
        self.assertEqual(title_for("", [], ["H1"], "https://u"), "H1")
        self.assertEqual(title_for(None, [], [], "https://u"), "https://u")

    def test_offsets_round_trip_and_stable_block_ids(self):
        document = {"document_id": "doc-one", "blocks": [dict(text="Permit"), dict(text="Apply now.")]}
        attach_offsets(document)
        self.assertEqual(document["blocks"][1]["block_id"], "doc-one:b00002")
        self.assertEqual(document["content_text"], "Permit\n\nApply now.")
        for block in document["blocks"]:
            self.assertEqual(document["content_text"][block["start"]:block["end"]], block["text"])

    def test_dispatch_text_image_and_unknown(self):
        value, representation = extract_bytes(b"a,b\n1,2\n", "https://x/data.csv", "text/csv", "pages/x/response.bin")
        self.assertEqual((representation, value["blocks"][0]["kind"]), ("text", "plain_text"))
        value, representation = extract_bytes(b"\x89PNG\r\n\x1a\n....", "https://x/i.png", "image/png", "pages/x/response.bin")
        self.assertEqual((representation, value["warnings"]), ("image", ["image_requires_ocr"]))
        with self.assertRaises(ValueError):
            extract_bytes(b"\x00\x01\x02binary", "https://x/b", "application/octet-stream", "pages/x/response.bin")
        with self.assertRaises(ValueError):  # the same on every libxml2 version
            extract_bytes(b"\x00\x01\x02\x03", "https://x/b", "application/octet-stream", "pages/x/response.html")
        page = "<p>Anmeldung</p>".encode("utf-16")  # a BOM, then NUL bytes, and no markup signature in bytes
        value, representation = extract_bytes(page, "https://x/p.html", "text/html", "pages/x/response.html")
        self.assertEqual(representation, "html")
        self.assertEqual(value["blocks"][0]["text"], "Anmeldung")
        value, representation = extract_bytes(make_pdf(["Hello"]), "https://x/a.pdf", "application/pdf", "pages/x/response.pdf")
        self.assertEqual(representation, "pdf")

    def test_assemble_records_exclusion_by_review_flag_and_failures(self):
        raw = b"<html><head><title>T</title></head><body><main><p>Text</p></main></body></html>"
        entry = with_snapshot(item(), raw, flags=["javascript_application_shell"])
        record = assemble(entry, raw, "m" * 64, "run", "c" * 64)
        self.assertEqual(record["status"], "excluded_source_response")
        self.assertEqual(record["exclusion_reasons"], ["javascript_application_shell"])
        self.assertFalse(record["eligible_for_processing"])
        self.assertEqual(record["title"], "T")
        self.assertEqual(record["acquisition"]["attempt"], "attempt-001")
        self.assertEqual(record["catalogue_sha256"], "c" * 64)
        entry = with_snapshot(item(), raw, flags=["browser-rendered-dom-not-raw-http-response"])
        record = assemble(entry, raw, "m" * 64, "run", None)
        self.assertEqual(record["status"], "extracted")
        self.assertIn("browser-rendered-dom-not-raw-http-response", record["warnings"])
        broken = b"\x00\x01\x02"
        entry = with_snapshot(item(relative_path="pages/x/attempt-001/response.bin"), broken, "application/octet-stream")
        record = assemble(entry, broken, "m" * 64, "run", None)
        self.assertEqual((record["status"], record["representation"], record["blocks"]), ("extraction_failed", "unknown", []))
        self.assertTrue(record["warnings"][0].startswith("ValueError"))


if __name__ == "__main__":
    unittest.main()
