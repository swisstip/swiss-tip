import hashlib
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.datasets import (Dataset, DatasetInvalid, DatasetManifest, DatasetRow, DatasetSource, Period,
                                    assert_valid_dataset, content_hash, dump_dataset, load_dataset, source_filename,
                                    validate_dataset)

CSV = b'\xef\xbb\xbf"PLZ","Abholdatum"\n8001,"2026-01-05"\n8001,"2026-01-12"\n8002,"2026-01-06"\n'
URL = "https://data.example.ch/dataset/entsorgungskalender_bioabfall/download/entsorgungskalender_bioabfall_2026.csv"


def sample_dataset(rows: list[DatasetRow] | None = None) -> Dataset:
    rows = rows if rows is not None else [
        DatasetRow(postal_code="8001", date=date(2026, 1, 5)), DatasetRow(postal_code="8001", date=date(2026, 1, 12)),
        DatasetRow(postal_code="8002", date=date(2026, 1, 6), location="Tessinerplatz")]
    manifest = DatasetManifest(
        dataset_id="zurich-waste-bioabfall", dataset_version="2026-09-23-v1", type="calendar",
        title="Organic waste collection days, City of Zurich", label="Organic waste (Bioabfall)",
        pack="mvp-zurich", concept_id="city-zurich-organic-paper-cardboard", jurisdiction="CH-ZH-261",
        publisher="Entsorgung + Recycling Zürich", publisher_url="https://data.example.ch/dataset/entsorgungskalender_bioabfall",
        licence="CC0-1.0", sources=[DatasetSource(url=URL, sha256=hashlib.sha256(CSV).hexdigest(), bytes=len(CSV),
                                                  downloaded_on=date(2026, 9, 23))],
        period=Period(start=date(2026, 1, 1), end=date(2026, 12, 31)),
        postal_codes=sorted({row.postal_code for row in rows}), row_count=len(rows),
        created_at=datetime(2026, 9, 23, 10, 0), limitations=["Rows are not reviewed."], content_sha256="0" * 64)
    dataset = Dataset(manifest=manifest, rows=rows)
    dataset.manifest.content_sha256 = content_hash(dataset)
    return dataset


class DatasetTests(unittest.TestCase):
    def test_a_valid_bundle_round_trips(self):
        dataset = sample_dataset()
        self.assertEqual(validate_dataset(dataset), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dataset.json"
            path.write_text(dump_dataset(dataset), encoding="utf-8")
            loaded = load_dataset(path)
        self.assertEqual(loaded, dataset)
        self.assertEqual(loaded.manifest.schema_version, "swiss-tip-dataset/v1")
        self.assertEqual(loaded.manifest.refresh, "bundled")
        # A row without a location is dumped without the key, so the hash does not depend on a null.
        self.assertNotIn("location", dataset.rows[0].model_dump(mode="json"))
        self.assertEqual(dataset.rows[2].model_dump(mode="json")["location"], "Tessinerplatz")

    def test_tampering_and_inconsistencies_are_reported(self):
        dataset = sample_dataset()
        dataset.rows[0].date = date(2026, 1, 6)
        self.assertIn("manifest content_sha256 does not match the rows", validate_dataset(dataset))

        unsorted = sample_dataset([DatasetRow(postal_code="8002", date=date(2026, 1, 6)), DatasetRow(postal_code="8001", date=date(2026, 1, 5))])
        self.assertIn("rows are not sorted by postal code and date", validate_dataset(unsorted))

        repeated = sample_dataset([DatasetRow(postal_code="8001", date=date(2026, 1, 5))] * 2)
        self.assertIn("rows are repeated", validate_dataset(repeated))

        outside = sample_dataset([DatasetRow(postal_code="8001", date=date(2027, 1, 4))])
        self.assertIn("1 date(s) lie outside the published period, first 2027-01-04", validate_dataset(outside))

        empty = sample_dataset([])
        self.assertIn("the dataset has no rows", validate_dataset(empty))

        dataset = sample_dataset()
        dataset.manifest.postal_codes = ["8001"]
        dataset.manifest.row_count = 2
        dataset.manifest.jurisdiction = "ZH-261"
        dataset.manifest.concept_id = "-bad"
        issues = validate_dataset(dataset)
        self.assertIn("manifest postal_codes differ from the rows", issues)
        self.assertIn("manifest row_count is 2, the bundle holds 3 rows", issues)
        self.assertIn("malformed jurisdiction: 'ZH-261'", issues)
        self.assertIn("malformed concept_id: '-bad'", issues)
        with self.assertRaises(DatasetInvalid) as raised:
            assert_valid_dataset(dataset)
        self.assertEqual(raised.exception.issues, issues)

    def test_the_downloaded_files_are_checked_against_the_manifest(self):
        dataset = sample_dataset()
        self.assertEqual(source_filename(URL), "entsorgungskalender_bioabfall_2026.csv")
        with self.assertRaises(ValueError):
            source_filename("https://data.example.ch/dataset/")
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary)
            self.assertEqual(validate_dataset(dataset, sources),
                             [f"downloaded file entsorgungskalender_bioabfall_2026.csv is missing from {sources}"])
            (sources / "entsorgungskalender_bioabfall_2026.csv").write_bytes(CSV)
            self.assertEqual(validate_dataset(dataset, sources), [])
            (sources / "entsorgungskalender_bioabfall_2026.csv").write_bytes(CSV + b'8003,"2026-01-07"\n')
            [issue] = validate_dataset(dataset, sources)
            self.assertTrue(issue.startswith("downloaded file entsorgungskalender_bioabfall_2026.csv differs"))

    def test_the_models_reject_what_the_validator_need_not_see(self):
        with self.assertRaises(ValidationError):
            Period(start=date(2026, 12, 31), end=date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            DatasetRow(postal_code="80011", date=date(2026, 1, 5))
        with self.assertRaises(ValidationError):
            DatasetSource(url=URL, sha256="abc", bytes=1, downloaded_on=date(2026, 9, 23))
        with self.assertRaises(ValidationError):
            DatasetRow(postal_code="8001", date=date(2026, 1, 5), street="Bahnhofstrasse")
        self.assertTrue(Period(start=date(2026, 1, 1), end=date(2026, 12, 31)).holds(date(2026, 12, 31)))
        self.assertFalse(Period(start=date(2026, 1, 1), end=date(2026, 12, 31)).holds(date(2027, 1, 1)))


if __name__ == "__main__":
    unittest.main()
