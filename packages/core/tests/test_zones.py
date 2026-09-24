"""Datasets keyed by collection zone instead of postal code (docs/architecture/dataset-connectors.md, section 7)."""

import hashlib
import unittest
from datetime import date, datetime

from pydantic import ValidationError

from swisstip.core.connector import ConnectorLookupRequest, WrongKey, calendar_lookup, summary_of
from swisstip.core.contracts import LookupOffer, LookupRequest, Period as OfferPeriod
from swisstip.core.datasets import (Dataset, DatasetManifest, DatasetRow, DatasetSource, Period, content_hash,
                                    validate_dataset, zone_key)
from test_datasets import sample_dataset

CSV = b"zone;termin\nA;2026-09-22\n"
URL = "https://data.example.ch/api/explore/v2.1/catalog/datasets/100096/exports/csv"
FINDER = "https://www.example.ch/apps/zonensuche"


def zone_dataset(rows: list[DatasetRow] | None = None, **changes) -> Dataset:
    rows = rows if rows is not None else [
        DatasetRow(zone="A", date=date(2026, 9, 22)), DatasetRow(zone="A", date=date(2026, 9, 25)),
        DatasetRow(zone="L West", date=date(2026, 9, 24))]
    fields = dict(
        dataset_id="basel-waste-kehricht", dataset_version="2026-09-24-v1", type="calendar",
        title="Household waste collection days, City of Basel", label="Household waste (Kehricht)",
        pack="mvp-zurich", concept_id="city-basel-waste-collection", jurisdiction="CH-BS-2701",
        publisher="Tiefbauamt Basel-Stadt", publisher_url="https://data.example.ch/explore/dataset/100096/",
        licence="CC-BY-4.0", sources=[DatasetSource(url=URL, sha256=hashlib.sha256(CSV).hexdigest(), bytes=len(CSV),
                                                    downloaded_on=date(2026, 9, 24))],
        period=Period(start=date(2026, 1, 1), end=date(2026, 12, 31)), postal_codes=[], key="zone",
        zones=sorted({row.zone for row in rows if row.zone is not None}), zone_lookup_url=FINDER, row_count=len(rows),
        created_at=datetime(2026, 9, 24, 10, 0), limitations=["Rows are not reviewed."], content_sha256="0" * 64)
    fields.update(changes)
    dataset = Dataset(manifest=DatasetManifest(**fields), rows=rows)
    dataset.manifest.content_sha256 = content_hash(dataset)
    return dataset


def request(zone: str | None = "A", **changes) -> ConnectorLookupRequest:
    return ConnectorLookupRequest(**{**dict(dataset_id="basel-waste-kehricht", zone=zone, limit=3), **changes})


class ZoneKeyTests(unittest.TestCase):
    def test_zone_key_ignores_case_spacing_and_a_leading_word(self):
        for spelling in ("L West", "l west", "LWest", "l-west", "Zone L West", "Abfuhrgebiet L-West"):
            self.assertEqual(zone_key(spelling), "lwest", spelling)
        self.assertEqual(zone_key("Kreis 1"), "1")
        self.assertEqual(zone_key("Zone"), "zone", "a zone named only 'Zone' keeps its name")

    def test_a_zone_bundle_validates_and_a_postal_code_bundle_keeps_its_shape(self):
        self.assertEqual(validate_dataset(zone_dataset()), [])
        postal = sample_dataset().model_dump(mode="json")
        self.assertNotIn("key", postal["manifest"])
        self.assertNotIn("zones", postal["manifest"])
        self.assertNotIn("zone", postal["rows"][0])

    def test_a_row_carries_exactly_one_key(self):
        with self.assertRaises(ValidationError):
            DatasetRow(date=date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            DatasetRow(postal_code="8001", zone="A", date=date(2026, 1, 1))

    def test_inconsistent_zone_bundles_are_reported(self):
        self.assertIn("manifest zones differ from the rows", validate_dataset(zone_dataset(zones=["A"])))
        self.assertIn("a dataset keyed by zone needs an https zone_lookup_url",
                      validate_dataset(zone_dataset(zone_lookup_url=None)))
        self.assertIn("a dataset keyed by zone lists postal codes", validate_dataset(zone_dataset(postal_codes=["4001"])))
        mixed = [DatasetRow(zone="A", date=date(2026, 9, 22)), DatasetRow(postal_code="4001", date=date(2026, 9, 23))]
        self.assertIn("a row lacks the dataset's key zone", validate_dataset(zone_dataset(mixed, zones=["A"])))
        twins = [DatasetRow(zone="L West", date=date(2026, 9, 22)), DatasetRow(zone="L-West", date=date(2026, 9, 23))]
        self.assertIn("two zones differ only in case, spaces or hyphens", validate_dataset(zone_dataset(twins)))
        backwards = list(reversed(zone_dataset().rows))
        self.assertIn("rows are not sorted by zone and date", validate_dataset(zone_dataset(backwards, zones=["A", "L West"])))

    def test_the_summary_requires_the_zone_and_names_the_finder(self):
        summary = summary_of(zone_dataset())
        self.assertEqual((summary.requires, summary.key, summary.zones, summary.zone_lookup_url),
                         (["zone"], "zone", ["A", "L West"], FINDER))
        self.assertNotIn("key", summary_of(sample_dataset()).model_dump(mode="json"))

    def test_the_next_dates_of_a_zone_however_the_user_spells_it(self):
        dataset = zone_dataset()
        answer = calendar_lookup(dataset, request("zone l-west", start=date(2026, 9, 23)))
        self.assertEqual((answer.status, [event.date for event in answer.events]), ("SUPPORTED", [date(2026, 9, 24)]))
        answer = calendar_lookup(dataset, request("a", start=date(2026, 9, 23)))
        self.assertEqual([event.date for event in answer.events], [date(2026, 9, 25)])

    def test_an_unknown_zone_lists_the_zones_and_the_finder(self):
        answer = calendar_lookup(zone_dataset(), request("Q"))
        self.assertEqual(answer.status, "OUT_OF_COVERAGE")
        self.assertEqual([gap.dimension for gap in answer.gaps], ["zone_not_covered"])
        self.assertEqual(answer.gaps[0].published_values, ["A", "L West"])
        self.assertIn(FINDER, answer.gaps[0].message)

    def test_a_zone_past_its_last_date_and_a_range_outside_the_period(self):
        dataset = zone_dataset()
        self.assertEqual([g.dimension for g in calendar_lookup(dataset, request("A", start=date(2026, 9, 26))).gaps],
                         ["no_dates_in_range"])
        self.assertEqual([g.dimension for g in calendar_lookup(dataset, request("A", start=date(2027, 1, 4))).gaps],
                         ["period_not_published"])

    def test_the_other_key_is_a_malformed_request(self):
        with self.assertRaises(WrongKey):
            calendar_lookup(zone_dataset(), request(None, postal_code="4001"))
        with self.assertRaises(WrongKey):
            calendar_lookup(sample_dataset(), ConnectorLookupRequest(dataset_id="zurich-waste-bioabfall", zone="A", limit=1))
        with self.assertRaises(ValidationError):
            request(None)
        with self.assertRaises(ValidationError):
            request("A", postal_code="4001")

    def test_the_caller_contract_takes_a_zone(self):
        self.assertEqual(LookupRequest(dataset_id="d", zone="L West").zone, "L West")
        with self.assertRaises(ValidationError):
            LookupRequest(dataset_id="d")
        with self.assertRaises(ValidationError):
            LookupRequest(dataset_id="d", zone="A", postal_code="4001")
        offer = LookupOffer(dataset_id="d", type="calendar", title="t", label="l", jurisdiction="CH-BS-2701",
                            requires=["postal_code"], accepts=[], publisher="p",
                            period=OfferPeriod(start=date(2026, 1, 1), end=date(2026, 12, 31)))
        self.assertNotIn("zones", offer.model_dump(mode="json"))
        self.assertNotIn("zone_lookup_url", offer.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
