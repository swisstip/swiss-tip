from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from swisstip.ingestion.catalog import (dump_source_catalog, load_source_catalog,  # noqa: E402
                                        save_source_catalog, select_sources)


# A small catalogue in the shape of a pack's sources.json. The committed catalogues of the packs are checked in
# scripts/test/packs, which the package build does not run.
CATALOGUE = Path(__file__).parent / "fixtures" / "sources.json"


class SourceCatalogTests(unittest.TestCase):
    def test_the_fixture_catalogue_validates_offline(self) -> None:
        data = load_source_catalog(CATALOGUE)
        self.assertEqual([entry["definition"]["source_id"] for entry in data["sources"]],
                         ["ch-bag-health-insurance", "ch-bazg-moving", "ch-bsv-ahv", "ch-sem-residence-de", "zh-overview"])
        self.assertEqual({entry["definition"]["jurisdiction"] for entry in data["sources"]}, {"CH", "CH-ZH"})

    def test_selection_by_set_or_ids_keeps_catalogue_order(self) -> None:
        data = load_source_catalog(CATALOGUE)
        self.assertEqual(len(select_sources(data)), 5)
        self.assertEqual([e["definition"]["source_id"] for e in select_sources(data, scan_set="smoke")],
                         ["ch-sem-residence-de", "zh-overview"])
        chosen = select_sources(data, source_ids=["zh-overview", "ch-sem-residence-de"])
        self.assertEqual([e["definition"]["source_id"] for e in chosen], ["ch-sem-residence-de", "zh-overview"])
        with self.assertRaisesRegex(ValueError, "Unknown scan set"):
            select_sources(data, scan_set="nope")
        with self.assertRaisesRegex(ValueError, "Unknown source IDs"):
            select_sources(data, source_ids=["nope"])
        with self.assertRaisesRegex(ValueError, "not both"):
            select_sources(data, scan_set="smoke", source_ids=["zh-overview"])

    def test_malformed_catalogues_are_rejected(self) -> None:
        original = json.loads(CATALOGUE.read_text(encoding="utf-8"))
        cases = {
            "Unsupported source catalog schema": lambda d: d.update(schema_version="other/v1"),
            "Source jurisdiction outside catalog scope": lambda d: d["sources"][-1]["definition"].update(jurisdiction="CH-BE"),
            "Duplicate seed URL": lambda d: d["sources"][1]["definition"].update(start_url=d["sources"][0]["definition"]["start_url"]),
            "Seed host must be explicitly allowlisted": lambda d: d["sources"][0]["definition"].update(allowed_hosts=["other.example"]),
            "Seed path must be inside its allowlist": lambda d: d["sources"][0]["definition"].update(allowed_path_prefixes=["/elsewhere/"]),
            "Scan sets require unique, known source IDs": lambda d: d["scan_sets"].update(broken=["missing-id"]),
            "Invalid scan status": lambda d: d["sources"][0].update(scan_status="done"),
            "user_agent must be absent or 'browser'": lambda d: d["sources"][0].update(user_agent="Googlebot"),
            "Unknown planning topic": lambda d: d["sources"][0].update(topic_hints=["nope"]),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            for message, mutate in cases.items():
                data = deepcopy(original)
                mutate(data)
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message, msg=message):
                    load_source_catalog(path)


class CatalogueWriterTests(unittest.TestCase):
    """The writer the admin console edits a catalogue through; see docs/architecture/admin-console.md."""

    def test_a_saved_catalogue_round_trips_and_stays_loadable(self) -> None:
        original = load_source_catalog(CATALOGUE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            save_source_catalog(path, original)
            self.assertEqual(load_source_catalog(path), original)
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

    def test_a_catalogue_the_planner_would_refuse_is_never_written(self) -> None:
        data = load_source_catalog(CATALOGUE)
        data["sources"][0]["definition"]["language"] = "zz"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            with self.assertRaisesRegex(ValueError, "Invalid seed language hint"):
                save_source_catalog(path, data)
            self.assertFalse(path.exists())

    def test_an_empty_source_list_may_be_saved_but_not_planned(self) -> None:
        data = load_source_catalog(CATALOGUE)
        data["sources"] = []
        data["scan_sets"] = {}
        data["parallel_page_groups"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            save_source_catalog(path, data)  # a new pack starts with an empty catalogue
            self.assertIn('"sources": []', dump_source_catalog(data))
            with self.assertRaisesRegex(ValueError, "Source list cannot be empty"):
                load_source_catalog(path)


if __name__ == "__main__":
    unittest.main()
