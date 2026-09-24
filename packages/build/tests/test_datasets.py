import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from swisstip.build import dataset_cli
from swisstip.build.datasets import (BuildError, Columns, build_dataset, import_csv_columns, load_dataset_curation,
                                     save_dataset_curation)
from swisstip.core.datasets import load_dataset, validate_dataset

FIXTURES = Path(__file__).parent / "fixtures" / "datasets"
CSV = (FIXTURES / "entsorgungskalender_test_2026.csv").read_bytes()
CREATED = datetime(2026, 9, 23, 10, 0)


class ImporterTests(unittest.TestCase):
    def test_rows_are_sorted_and_repeats_dropped(self):
        rows, repeated = import_csv_columns(CSV, Columns(postal_code="PLZ", date="Abholdatum"), "test.csv")
        self.assertEqual([(row.postal_code, row.date.isoformat()) for row in rows[:3]],
                         [("8001", "2026-01-05"), ("8001", "2026-01-12"), ("8001", "2026-09-28")])
        self.assertEqual(len(rows), 6)
        self.assertEqual(repeated, 1)
        self.assertIsNone(rows[0].location)

    def test_other_dialects_dates_and_a_location_column(self):
        data = "PLZ;Station;Datum\n8002;Tessinerplatz;08.09.2026\n8001; Neumarkt ;23/03/2026\n".encode("utf-8")
        rows, _ = import_csv_columns(data, Columns(postal_code="PLZ", date="Datum", location="Station"), "test.csv")
        self.assertEqual([(row.postal_code, row.date.isoformat(), row.location) for row in rows],
                         [("8001", "2026-03-23", "Neumarkt"), ("8002", "2026-09-08", "Tessinerplatz")])

    def test_bad_input_names_the_line(self):
        with self.assertRaises(BuildError) as missing:
            import_csv_columns(CSV, Columns(postal_code="Postleitzahl", date="Abholdatum"), "test.csv")
        self.assertIn("['Postleitzahl'] not found", str(missing.exception))
        with self.assertRaises(BuildError) as bad_code:
            import_csv_columns(b"PLZ,Abholdatum\n80011,2026-01-05\n", Columns(postal_code="PLZ", date="Abholdatum"), "test.csv")
        self.assertIn("line 2", str(bad_code.exception))
        with self.assertRaises(BuildError) as bad_date:
            import_csv_columns(b"PLZ,Abholdatum\n8001,Montag\n", Columns(postal_code="PLZ", date="Abholdatum"), "test.csv")
        self.assertIn("'Montag'", str(bad_date.exception))


class DatasetBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sources = self.root / "sources"
        self.curation = load_dataset_curation(FIXTURES / "dataset.yaml")
        self.fetched: list[str] = []

    def tearDown(self):
        self.temporary.cleanup()

    def fetch(self, data: bytes = CSV):
        def fetch(url: str) -> bytes:
            self.fetched.append(url)
            return data
        return fetch

    def test_the_first_build_downloads_pins_and_validates(self):
        dataset, report = build_dataset(self.curation, self.sources, fetch=self.fetch(), today=date(2026, 9, 23), created_at=CREATED)
        self.assertEqual(self.fetched, [self.curation.sources[0].url])
        self.assertEqual((dataset.manifest.dataset_version, dataset.manifest.row_count, dataset.manifest.postal_codes),
                         ("2026-09-23-v1", 6, ["8001", "8002"]))
        self.assertEqual(dataset.manifest.sources[0].sha256, hashlib.sha256(CSV).hexdigest())
        self.assertEqual(dataset.manifest.sources[0].downloaded_on, date(2026, 9, 23))
        self.assertEqual(validate_dataset(dataset, self.sources), [])
        self.assertTrue((self.sources / "entsorgungskalender_test_2026.csv").is_file())
        self.assertEqual((report["rows"], report["repeated_rows_dropped"], report["sources"][0]["fetched"]), (6, 1, True))
        self.assertEqual(len(dataset.manifest.limitations), 3)
        self.assertIn("CC0-1.0", dataset.manifest.limitations[2])
        # The curation now carries the pins, and a dump keeps them.
        self.assertEqual(self.curation.sources[0].sha256, hashlib.sha256(CSV).hexdigest())
        save_dataset_curation(self.root / "dataset.yaml", self.curation)
        self.assertEqual(load_dataset_curation(self.root / "dataset.yaml"), self.curation)

    def test_a_pinned_file_at_hand_builds_without_the_network(self):
        build_dataset(self.curation, self.sources, fetch=self.fetch(), today=date(2026, 9, 23), created_at=CREATED)
        dataset, report = build_dataset(self.curation, self.sources, fetch=None, today=date(2026, 9, 30), created_at=CREATED, version=2)
        self.assertEqual(dataset.manifest.dataset_version, "2026-09-23-v2")
        self.assertFalse(report["sources"][0]["fetched"])
        shutil.rmtree(self.sources)
        with self.assertRaises(BuildError) as offline:
            build_dataset(self.curation, self.sources, fetch=None, today=date(2026, 9, 30))
        self.assertIn("no network fetch", str(offline.exception))

    def test_a_changed_upstream_file_stops_the_build_unless_refreshed(self):
        build_dataset(self.curation, self.sources, fetch=self.fetch(), today=date(2026, 9, 23), created_at=CREATED)
        changed = CSV + b'8003,"2026-11-02"\n'
        shutil.rmtree(self.sources)
        with self.assertRaises(BuildError) as stopped:
            build_dataset(self.curation, self.sources, fetch=self.fetch(changed), today=date(2026, 12, 20))
        self.assertIn("--refresh", str(stopped.exception))
        self.assertFalse((self.sources / "entsorgungskalender_test_2026.csv").exists())
        dataset, _ = build_dataset(self.curation, self.sources, fetch=self.fetch(changed), refresh=True, today=date(2026, 12, 20),
                                   created_at=CREATED)
        self.assertEqual(dataset.manifest.dataset_version, "2026-12-20-v1")
        self.assertEqual(dataset.manifest.postal_codes, ["8001", "8002", "8003"])
        self.assertEqual(self.curation.sources[0].sha256, hashlib.sha256(changed).hexdigest())

    def test_rows_outside_the_period_fail_the_build(self):
        outside = CSV + b'8001,"2027-01-04"\n'
        with self.assertRaises(BuildError) as failed:
            build_dataset(self.curation, self.sources, fetch=self.fetch(outside), today=date(2026, 9, 23))
        self.assertIn("outside the published period", str(failed.exception))

    def test_the_command_writes_the_bundle_the_report_and_the_pins(self):
        packs = self.root / "packs"
        folder = packs / "datasets" / "test-pack" / "test-waste-bioabfall"
        folder.mkdir(parents=True)
        shutil.copy(FIXTURES / "dataset.yaml", folder / "dataset.yaml")
        with mock.patch.object(dataset_cli, "fetch_url", self.fetch()):
            code = dataset_cli.main(["--packs-dir", str(packs), "--pack", "test-pack", "--dataset", "test-waste-bioabfall"])
        self.assertEqual(code, 0)
        dataset = load_dataset(folder / "dataset.json")
        self.assertEqual(validate_dataset(dataset, packs / ".local" / "test-pack" / "datasets" / "test-waste-bioabfall"), [])
        report = json.loads((folder / "build-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["rows"], 6)
        pinned = load_dataset_curation(folder / "dataset.yaml")
        self.assertEqual(pinned.sources[0].sha256, hashlib.sha256(CSV).hexdigest())
        self.assertEqual(pinned.notes, self.curation.notes)
        # Offline, the second build uses the file at hand; a wrong pack name is refused.
        with mock.patch.object(dataset_cli, "fetch_url", self.fetch()):
            self.assertEqual(dataset_cli.main(["--packs-dir", str(packs), "--pack", "test-pack", "--dataset", "test-waste-bioabfall", "--offline"]), 0)
            self.assertEqual(dataset_cli.main(["--packs-dir", str(packs), "--pack", "other", "--dataset", "test-waste-bioabfall"]), 2)
        self.assertEqual(len(self.fetched), 1)


class ZoneBuildTests(unittest.TestCase):
    """A publisher that keys its dates by collection zone (Basel, St. Gallen) instead of postal code."""

    ZONE_CSV = "zone;termin\nL  West;2026-09-24\nA;2026-09-25\nA;2026-09-22\nA;2026-09-22\n".encode("utf-8-sig")
    FINDER = "https://www.example.ch/apps/zonensuche"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.sources = Path(self.temporary.name) / "sources"
        base = load_dataset_curation(FIXTURES / "dataset.yaml").model_dump(mode="json", exclude_none=True)
        base["importer"] = dict(name="csv-columns", columns=dict(zone="zone", date="termin"))
        base["zone_lookup_url"] = self.FINDER
        self.data = base

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, **changes):
        curation = type(load_dataset_curation(FIXTURES / "dataset.yaml")).model_validate({**self.data, **changes})
        return build_dataset(curation, self.sources, fetch=lambda url: self.ZONE_CSV, today=date(2026, 9, 24), created_at=CREATED)

    def test_rows_are_keyed_by_zone_with_spacing_normalised(self):
        rows, repeated = import_csv_columns(self.ZONE_CSV, Columns(zone="zone", date="termin"), "zones.csv")
        self.assertEqual([(row.zone, row.date.isoformat()) for row in rows],
                         [("A", "2026-09-22"), ("A", "2026-09-25"), ("L West", "2026-09-24")])
        self.assertEqual(repeated, 1)
        self.assertTrue(all(row.postal_code is None for row in rows))

    def test_a_zone_bundle_carries_its_zones_and_finder(self):
        dataset, report = self.build()
        manifest = dataset.manifest
        self.assertEqual((manifest.key, manifest.zones, manifest.postal_codes, manifest.zone_lookup_url),
                         ("zone", ["A", "L West"], [], self.FINDER))
        self.assertEqual(report["zones"], 2)
        self.assertIn(self.FINDER, manifest.limitations[1])
        self.assertEqual(validate_dataset(dataset, self.sources), [])

    def test_a_zone_column_needs_a_finder_and_a_postal_code_column_refuses_one(self):
        with self.assertRaises(BuildError):
            self.build(zone_lookup_url=None)
        with self.assertRaises(BuildError):
            self.build(importer=dict(name="csv-columns", columns=dict(postal_code="zone", date="termin")))
        with self.assertRaises(ValueError):
            Columns(zone="zone", postal_code="PLZ", date="termin")


if __name__ == "__main__":
    unittest.main()
