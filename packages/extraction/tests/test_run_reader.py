import tempfile
import unittest
from pathlib import Path

from support import CATALOGUE_URL, FAILED_URL, FEDLEX_HTML_URL, FEDLEX_URL, IN_SCOPE_URL, OUT_OF_SCOPE_URL, make_run
from swisstip.extraction.run_reader import RunError, load_plan, read_verified, select_items, snapshot_items


class RunReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.temporary.name))
        self.items, self.unavailable = snapshot_items(self.run)
        self.by_url = {item["url"]: item for item in self.items}

    def tearDown(self):
        self.temporary.cleanup()

    def test_every_intact_snapshot_becomes_an_item_with_its_attribution(self):
        self.assertEqual(len(self.items), 6)
        catalogue = self.by_url[CATALOGUE_URL]
        self.assertEqual(catalogue["attribution"], dict(kind="catalogue", source_ids=["zh-permit"]))
        self.assertEqual(catalogue["attempt"], "attempt-002")
        self.assertTrue(catalogue["relative_path"].startswith("pages/"))
        self.assertEqual(self.by_url[IN_SCOPE_URL]["attribution"]["kind"], "in-scope")
        self.assertEqual(self.by_url[IN_SCOPE_URL]["language"], "de")
        self.assertEqual(self.by_url[OUT_OF_SCOPE_URL]["attribution"]["kind"], "out-of-scope")
        act = self.by_url[FEDLEX_HTML_URL]
        self.assertEqual(act["attribution"], dict(kind="plugin-document", plugin_id="fedlex", source_ids=["ch-fedlex-aig"],
                                                  resolved_from=FEDLEX_URL))
        self.assertEqual(act["source_url"], FEDLEX_URL)
        self.assertTrue(act["relative_path"].startswith("fedlex-documents/pages/"))
        self.assertEqual(len({item["document_id"] for item in self.items}), 6)

    def test_unavailable_targets_are_listed_separately(self):
        self.assertEqual([entry["url"] for entry in self.unavailable], [FAILED_URL])
        self.assertEqual(self.unavailable[0]["attribution"]["source_ids"], ["zh-missing"])
        self.assertEqual(self.unavailable[0]["error"], "HTTP Error 404: Not Found")

    def test_selection_by_scope_kind_source_and_id(self):
        attributed = select_items(self.items)
        self.assertNotIn(OUT_OF_SCOPE_URL, {i["url"] for i in attributed})
        self.assertEqual(len(attributed), 5)
        self.assertEqual(len(select_items(self.items, scope="all")), 6)
        self.assertEqual([i["url"] for i in select_items(self.items, kinds=["in-scope"])], [IN_SCOPE_URL])
        fedlex = {i["url"] for i in select_items(self.items, sources=["ch-fedlex-aig"])}
        self.assertEqual(len(fedlex), 3)
        self.assertIn(FEDLEX_URL, fedlex)
        self.assertIn(FEDLEX_HTML_URL, fedlex)
        wanted = self.by_url[IN_SCOPE_URL]["document_id"]
        self.assertEqual([i["url"] for i in select_items(self.items, document_ids=[wanted])], [IN_SCOPE_URL])
        with self.assertRaises(RunError):
            select_items(self.items, scope="everything")

    def test_read_verified_stops_on_changed_bytes_and_unsafe_paths(self):
        item = self.by_url[IN_SCOPE_URL]
        self.assertTrue(read_verified(self.run, item).startswith(b"<html>"))
        (self.run / item["relative_path"]).write_bytes(b"<html>tampered</html>")
        with self.assertRaises(RunError):
            read_verified(self.run, item)
        item["relative_path"] = "../outside.html"
        with self.assertRaises(RunError):
            read_verified(self.run, item)

    def test_plan_is_required(self):
        with self.assertRaises(RunError):
            load_plan(Path(self.temporary.name))


if __name__ == "__main__":
    unittest.main()
