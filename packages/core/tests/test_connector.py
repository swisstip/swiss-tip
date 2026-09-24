import unittest
from datetime import date

from pydantic import ValidationError

from swisstip.core.connector import (ConnectorLookupRequest, ConnectorLookupResponse, ConnectorManifest,
                                     calendar_lookup, summary_of)
from swisstip.core.datasets import DatasetRow
from test_datasets import sample_dataset


def rows(*dates: date, postal_code: str = "8001") -> list[DatasetRow]:
    return [DatasetRow(postal_code=postal_code, date=day) for day in dates]


class ConnectorContractTests(unittest.TestCase):
    def test_the_manifest_lists_every_bundle_with_its_binding_and_state(self):
        dataset = sample_dataset()
        ok = summary_of(dataset)
        refused = summary_of(dataset, issue="rows are repeated")
        manifest = ConnectorManifest(connector_id="calendar-connector", types=["calendar"], datasets=[ok, refused])
        self.assertEqual(manifest.schema_version, "swiss-tip-connector/v1")
        self.assertEqual((ok.status, ok.pack, ok.concept_id, ok.jurisdiction), ("ok", "mvp-zurich", "city-zurich-organic-paper-cardboard", "CH-ZH-261"))
        self.assertEqual((ok.requires, ok.accepts), (["postal_code"], ["start", "end", "limit"]))
        self.assertEqual(ok.postal_codes, ["8001", "8002"])
        self.assertNotIn("issue", ok.model_dump(mode="json"))
        self.assertEqual((refused.status, refused.issue), ("invalid", "rows are repeated"))
        # The wire is strict in both directions.
        with self.assertRaises(ValidationError):
            ConnectorManifest.model_validate({**manifest.model_dump(mode="json"), "extra": 1})
        with self.assertRaises(ValidationError):
            ConnectorLookupRequest(dataset_id="d", postal_code="8001", limit=61)
        with self.assertRaises(ValidationError):
            ConnectorLookupRequest(dataset_id="d", postal_code="8001", limit=1, start=date(2026, 2, 1), end=date(2026, 1, 1))

    def test_the_next_dates_of_a_postal_code(self):
        dataset = sample_dataset(rows(date(2026, 9, 21), date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12))
                                 + rows(date(2026, 9, 23), postal_code="8002"))
        response = calendar_lookup(dataset, ConnectorLookupRequest(dataset_id="zurich-waste-bioabfall", postal_code="8001",
                                                                   start=date(2026, 9, 23), limit=2))
        self.assertEqual(response.status, "SUPPORTED")
        self.assertEqual([event.date for event in response.events], [date(2026, 9, 28), date(2026, 10, 5)])
        self.assertEqual(response.events[0].label, "Organic waste (Bioabfall)")
        self.assertTrue(response.truncated)
        self.assertEqual(response.gaps, [])
        self.assertEqual(response.provenance.period.end, date(2026, 12, 31))
        self.assertEqual(response.provenance.sources[0].downloaded_on, date(2026, 9, 23))
        self.assertEqual(ConnectorLookupResponse.model_validate(response.model_dump(mode="json")), response)

        whole_year = calendar_lookup(dataset, ConnectorLookupRequest(dataset_id="zurich-waste-bioabfall", postal_code="8001", limit=60))
        self.assertEqual(len(whole_year.events), 4)
        self.assertFalse(whole_year.truncated)

        october = calendar_lookup(dataset, ConnectorLookupRequest(dataset_id="zurich-waste-bioabfall", postal_code="8001",
                                                                  start=date(2026, 10, 1), end=date(2026, 10, 31), limit=60))
        self.assertEqual([event.date for event in october.events], [date(2026, 10, 5), date(2026, 10, 12)])

    def test_the_location_is_carried_through(self):
        response = calendar_lookup(sample_dataset(), ConnectorLookupRequest(dataset_id="zurich-waste-bioabfall", postal_code="8002", limit=1))
        self.assertEqual(response.events[0].location, "Tessinerplatz")
        self.assertNotIn("location", calendar_lookup(sample_dataset(), ConnectorLookupRequest(
            dataset_id="zurich-waste-bioabfall", postal_code="8001", limit=1)).events[0].model_dump(mode="json"))

    def test_every_gap_is_named(self):
        dataset = sample_dataset(rows(date(2026, 3, 23)))

        def gap(**arguments):
            response = calendar_lookup(dataset, ConnectorLookupRequest(dataset_id="zurich-waste-bioabfall", limit=3, **arguments))
            self.assertEqual((response.status, response.events), ("OUT_OF_COVERAGE", []))
            [only] = response.gaps
            return only

        outside = gap(postal_code="8304")
        self.assertEqual((outside.dimension, outside.published_values), ("postal_code_not_covered", ["8001"]))
        self.assertIn("8304", outside.message)

        next_year = gap(postal_code="8001", start=date(2027, 1, 4))
        self.assertEqual((next_year.dimension, next_year.published_values), ("period_not_published", ["2026-01-01", "2026-12-31"]))

        before = gap(postal_code="8001", end=date(2025, 12, 31))
        self.assertEqual(before.dimension, "period_not_published")

        passed = gap(postal_code="8001", start=date(2026, 9, 23))
        self.assertEqual((passed.dimension, passed.published_values), ("no_dates_in_range", ["2026-01-01", "2026-12-31"]))
        self.assertIn("2026-12-31", passed.message)


if __name__ == "__main__":
    unittest.main()
