import hashlib
import tempfile
import unittest
from pathlib import Path

from support import CATALOGUE_URL, NEW_PAGE, make_run
from swisstip.extraction import EXTRACTOR_VERSION
from swisstip.extraction.dataset import reusable
from swisstip.extraction.html_blocks import extract_html
from swisstip.extraction.legacy import adopt
from swisstip.extraction.records import attach_offsets
from swisstip.extraction.run_reader import document_id, snapshot_items


def old_style_record(raw: bytes, url: str) -> dict:
    """A record the predecessor's extractor would have written for these bytes."""
    old_id = "doc-" + hashlib.sha256(b"pages/abc/attempt-001/response.html").hexdigest()[:20]
    value = dict(schema_version="swisstip.source-intermediate/v1", document_id=old_id, corpus_id="hackathon-residence-2026-09-11",
                 source_url=url, document_url=url, version_uri=None, source_registry=[], catalogue_references=[],
                 discovery_provenance=[dict(reason="existing-catalogue-seed")], extractor_version="expanded-1.2.0",
                 acquisition=dict(path="pages/abc/attempt-001/response.html", manifest_path="pages/abc/latest.json",
                                  manifest_sha256="0" * 64, raw_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                                  retrieved_at="2026-09-11T06:27:08+00:00", requested_url=url,
                                  declared_content_type="text/html;charset=utf-8", review_flags=[]),
                 representation_group="source-old", representation="html", semantic_status="pending-semantic-review",
                 eligible_for_processing=True, status="extracted", exclusion_reasons=[], language_hint=None)
    extracted = extract_html(raw, url)
    for block in extracted["blocks"]:
        block["source_locator"].pop("furniture")
    value.update(extracted, title=extracted["html_title"])
    attach_offsets(value)
    value.update(identical_raw_snapshots=[], html_counterparts=[], preferred_representation=True)
    return value


class LegacyAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.temporary.name))
        items, _ = snapshot_items(self.run)
        self.item = next(item for item in items if item["url"] == CATALOGUE_URL)
        self.old = old_style_record(NEW_PAGE, CATALOGUE_URL)

    def tearDown(self):
        self.temporary.cleanup()

    def test_adoption_rewrites_identity_and_keeps_text_and_hashes(self):
        record = adopt(self.old, self.item, run_name="run", catalogue_sha256="c" * 64, manifest_digest="m" * 64,
                       dataset_name="hackathon-residence-all-languages-2026-09-11", record_file="documents/x.json")
        new_id = document_id(CATALOGUE_URL, self.old["acquisition"]["raw_sha256"])
        self.assertEqual(record["document_id"], new_id)
        self.assertEqual(record["blocks"][0]["block_id"], f"{new_id}:b00001")
        self.assertEqual(record["content_sha256"], self.old["content_sha256"])
        self.assertEqual([b["text_sha256"] for b in record["blocks"]], [b["text_sha256"] for b in self.old["blocks"]])
        self.assertEqual(record["acquisition"]["attempt"], "attempt-002")
        self.assertEqual(record["acquisition"]["manifest_sha256"], "m" * 64)
        self.assertEqual(record["attribution"], dict(kind="catalogue", source_ids=["zh-permit"]))
        self.assertEqual(record["catalogue_references"][0]["label"], "Permit overview")
        self.assertEqual(record["catalogue_sha256"], "c" * 64)
        self.assertEqual(record["main_headings"], ["Anmeldung"])
        self.assertEqual(record["language_hint"], "de")
        self.assertEqual(record["extractor_version"], "expanded-1.2.0")
        self.assertEqual(record["imported_text"]["document_id"], self.old["document_id"])
        self.assertNotIn("semantic_status", record)
        self.assertTrue(reusable(record, self.item["raw_sha256"], EXTRACTOR_VERSION))
        self.assertFalse(reusable(dict(record, imported_text=None), self.item["raw_sha256"], EXTRACTOR_VERSION))

    def test_adoption_refuses_other_bytes_and_broken_records(self):
        with self.assertRaises(ValueError):
            adopt(self.old, dict(self.item, raw_sha256="f" * 64), run_name="run", catalogue_sha256=None, manifest_digest="m",
                  dataset_name="d", record_file="f")
        broken = dict(self.old, content_sha256="0" * 64)
        with self.assertRaises(ValueError):
            adopt(broken, self.item, run_name="run", catalogue_sha256=None, manifest_digest="m", dataset_name="d", record_file="f")

    def test_review_flags_of_the_new_run_decide_exclusion(self):
        item = dict(self.item, snapshot=dict(self.item["snapshot"], review_flags=["possible_access_challenge"]))
        record = adopt(self.old, item, run_name="run", catalogue_sha256=None, manifest_digest="m", dataset_name="d", record_file="f")
        self.assertEqual(record["status"], "excluded_source_response")
        self.assertEqual(record["exclusion_reasons"], ["possible_access_challenge"])
        self.assertFalse(record["preferred_representation"])


if __name__ == "__main__":
    unittest.main()
