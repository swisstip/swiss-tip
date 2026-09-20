import json
import tempfile
import unittest
from pathlib import Path

import contextlib
import io

from support import (ACT_PAGE, CATALOGUE_URL, FEDLEX_HTML_URL, FEDLEX_PDF_URL, FEDLEX_URL, IN_SCOPE_URL, NEW_PAGE, OLD_PAGE,
                     make_run, save_page, sha256)
from swisstip.extraction.dataset import check_output_location, read_json
from swisstip.extraction.extract_cli import main, run_extraction
from swisstip.extraction.run_reader import document_id


class ExtractCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = make_run(self.root)
        self.text = self.run / "text"

    def tearDown(self):
        self.temporary.cleanup()

    def index(self) -> dict:
        return {entry["document_url"] + "|" + entry["representation"]: entry for entry in read_json(self.text / "index.json")}

    def test_first_run_writes_records_views_index_and_summary(self):
        code, summary = run_extraction(self.run, log=lambda *a, **k: None)
        self.assertEqual(code, 0)
        self.assertEqual(summary["records"], 5)
        self.assertEqual(summary["extracted_now"], 5)
        self.assertEqual(summary["attribution_kinds"], {"catalogue": 2, "in-scope": 1, "plugin-document": 2})
        index = self.index()
        catalogue = index[CATALOGUE_URL + "|html"]
        self.assertEqual(catalogue["document_id"], document_id(CATALOGUE_URL, sha256(NEW_PAGE)))
        self.assertEqual(catalogue["attempt"], "attempt-002")
        self.assertEqual(catalogue["title"], "Aufenthalt")
        self.assertTrue((self.text / catalogue["reading_file"]).exists())
        record = read_json(self.text / catalogue["file"])
        self.assertIn("Neu: Online-Schalter.", record["content_text"])
        self.assertEqual(record["blocks"][0]["source_locator"]["furniture"], ["banner", "navigation"])
        shell = index[FEDLEX_URL + "|html"]
        self.assertEqual(shell["status"], "excluded_source_response")
        self.assertIsNone(shell["reading_file"])
        act_pdf = index[FEDLEX_PDF_URL + "|pdf"]
        act_html = [e["document_id"] for e in read_json(self.text / "index.json")
                    if e["source_url"] == FEDLEX_URL and e["representation"] == "html" and e["eligible_for_processing"]]
        self.assertEqual(len(act_html), 1)
        self.assertEqual(act_pdf["html_counterparts"], act_html)
        self.assertFalse(act_pdf["preferred_representation"])
        self.assertEqual(read_json(self.text / "unavailable.json")[0]["status"], "failed")
        self.assertEqual(read_json(self.text / "summary.json")["catalogue_sha256"], "c" * 64)
        self.assertTrue((self.text / "index.md").read_text(encoding="utf-8").startswith("# Text dataset: run"))

    def test_second_run_reuses_and_force_re_extracts(self):
        run_extraction(self.run, log=lambda *a, **k: None)
        first = read_json(self.text / "index.json")
        code, summary = run_extraction(self.run, log=lambda *a, **k: None)
        self.assertEqual((summary["reused"], summary["extracted_now"]), (5, 0))
        self.assertEqual(read_json(self.text / "index.json"), first)
        code, summary = run_extraction(self.run, force=True, log=lambda *a, **k: None)
        self.assertEqual((summary["reused"], summary["extracted_now"]), (0, 5))

    def test_scope_all_adds_out_of_scope_pages_and_keeps_earlier_records(self):
        run_extraction(self.run, kinds=["in-scope"], log=lambda *a, **k: None)
        self.assertEqual(len(read_json(self.text / "index.json")), 1)
        code, summary = run_extraction(self.run, scope="all", log=lambda *a, **k: None)
        self.assertEqual(summary["records"], 6)
        self.assertEqual(summary["reused"], 1)
        pdf = self.index()["https://www.other.example/leaflet.pdf|pdf"]
        self.assertEqual((pdf["attribution_kind"], pdf["pdf_pages_without_text"]), ("out-of-scope", [2]))

    def test_superseded_attempt_is_marked_and_pruned(self):
        old_run = make_run(self.root / "old", new_attempt=False)
        run_extraction(old_run, output=self.text, log=lambda *a, **k: None)
        old_id = document_id(CATALOGUE_URL, sha256(OLD_PAGE))
        self.assertTrue((self.text / "documents" / f"{old_id}.json").exists())
        code, summary = run_extraction(self.run, output=self.text, log=lambda *a, **k: None)
        entries = {e["document_id"]: e for e in read_json(self.text / "index.json")}
        self.assertTrue(entries[old_id]["superseded"])
        self.assertEqual(summary["superseded"], 1)
        code, summary = run_extraction(self.run, output=self.text, prune_superseded=True, log=lambda *a, **k: None)
        self.assertEqual(summary["pruned"], 1)
        self.assertFalse((self.text / "documents" / f"{old_id}.json").exists())
        self.assertNotIn(old_id, {e["document_id"] for e in read_json(self.text / "index.json")})

    def test_identical_text_records_are_linked_across_urls(self):
        print_url = IN_SCOPE_URL + "?print=1"
        plan = read_json(self.run / "plan.json")
        plan["targets"].append(dict(url=print_url, url_id=sha256(print_url.encode()), references=[], registry_entries=[],
                                    attribution=dict(kind="in-scope", source_ids=["zh-permit"], advertised_languages=[], reasons=[])))
        (self.run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        body = (self.run / "pages" / sha256(IN_SCOPE_URL.encode()) / "attempt-001" / "response.html").read_bytes()
        twin = body.replace(b"<html>", b"<html><!-- print view -->")
        save_page(self.run / "pages", print_url, [(twin, "html", "text/html", [])])
        run_extraction(self.run, log=lambda *a, **k: None)
        entries = {e["document_id"]: e for e in read_json(self.text / "index.json")}
        twin_id = document_id(print_url, sha256(twin))
        original_id = document_id(IN_SCOPE_URL, sha256(body))
        self.assertEqual(entries[twin_id]["identical_text_records"], [original_id])
        self.assertEqual(entries[original_id]["identical_text_records"], [twin_id])
        self.assertEqual(entries[twin_id]["identical_raw_snapshots"], [])

    def test_plugin_attribution_wins_over_a_page_twin_with_the_same_bytes(self):
        plan = read_json(self.run / "plan.json")
        plan["targets"].append(dict(url=FEDLEX_HTML_URL, url_id=sha256(FEDLEX_HTML_URL.encode()), references=[], registry_entries=[],
                                    attribution=dict(kind="out-of-scope", source_ids=[], advertised_languages=[], reasons=[])))
        (self.run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        save_page(self.run / "pages", FEDLEX_HTML_URL, [(ACT_PAGE, "html", "text/html", [])], source_page_url=FEDLEX_URL)
        code, summary = run_extraction(self.run, scope="all", log=lambda *a, **k: None)
        self.assertEqual(summary["selection"]["duplicates_skipped"], 1)
        act = self.index()[FEDLEX_HTML_URL + "|html"]
        self.assertEqual((act["attribution_kind"], act["source_ids"], act["plugin_id"]), ("plugin-document", ["ch-fedlex-aig"], "fedlex"))
        self.assertTrue(read_json(self.text / act["file"])["acquisition"]["path"].startswith("fedlex-documents/"))

    def test_output_location_guards(self):
        with self.assertRaises(ValueError):
            check_output_location(self.run, self.run / "pages" / "text")
        with self.assertRaises(ValueError):
            check_output_location(self.run, self.run / "fedlex-documents" / "text")
        with self.assertRaises(ValueError):
            check_output_location(self.run, self.run)
        check_output_location(self.run, self.run / "text")
        check_output_location(self.run, self.root / "elsewhere")

    def test_command_line_entry_point_and_workers(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(["--run", str(self.run), "--workers", "2", "--no-reading-view"])
            self.assertEqual(code, 0)
            self.assertFalse((self.text / "reading").exists())
            self.assertEqual(len(read_json(self.text / "index.json")), 5)
            self.assertEqual(main(["--run", str(self.root / "nowhere")]), 2)

    def test_extraction_failure_sets_exit_code_and_errors_file(self):
        target = self.run / "pages" / sha256(IN_SCOPE_URL.encode())
        manifest = read_json(target / "latest.json")
        broken = b"\x00\x01\x02\x03"
        (target / "attempt-001" / "response.html").write_bytes(broken)
        manifest["snapshots"][0].update(sha256=sha256(broken), bytes_downloaded=len(broken), content_type="application/octet-stream")
        (target / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        code, summary = run_extraction(self.run, log=lambda *a, **k: None)
        self.assertEqual(code, 1)
        self.assertEqual(summary["failed_in_selection"], 1)
        self.assertEqual(read_json(self.text / "errors.json")[0]["source_url"], IN_SCOPE_URL)


if __name__ == "__main__":
    unittest.main()
