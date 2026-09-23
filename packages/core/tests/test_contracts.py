import unittest
from datetime import date
from typing import get_args

from pydantic import ValidationError

from swisstip.core import connector, contracts, datasets
from swisstip.core.contracts import (CONNECTOR_TOOL_CONTRACTS, TOOL_CONTRACTS, TOOL_DESCRIPTIONS, ConceptResolution,
                                     LookupOffer, LookupRequest, Period, Status, schema_bundle)


class LookupContractTests(unittest.TestCase):
    def test_lookup_is_in_the_bundle_but_not_in_the_served_tool_table(self):
        self.assertEqual(list(TOOL_CONTRACTS), ["get_coverage", "search", "resolve", "get_evidence"])
        self.assertEqual(list(CONNECTOR_TOOL_CONTRACTS), ["lookup"])
        bundle = schema_bundle()
        self.assertEqual(list(bundle["tools"]), ["get_coverage", "search", "resolve", "get_evidence", "lookup"])
        self.assertEqual(bundle["tools"]["lookup"]["description"], TOOL_DESCRIPTIONS["lookup"])
        self.assertEqual(bundle["tools"]["lookup"]["input"]["properties"]["limit"]["default"], 3)

    def test_the_request_defaults_and_bounds(self):
        request = LookupRequest(dataset_id="zurich-waste-bioabfall", postal_code="8001")
        self.assertEqual((request.as_of, request.start, request.end, request.limit), (None, None, None, 3))
        for bad in (dict(postal_code="801"), dict(postal_code="8001", limit=0), dict(postal_code="8001", limit=61),
                    dict(postal_code="8001", street="x")):
            with self.assertRaises(ValidationError):
                LookupRequest(dataset_id="d", **bad)

    def test_the_offer_is_omitted_from_a_resolution_without_one(self):
        without = ConceptResolution(concept_id="c", status=Status.SUPPORTED)
        self.assertNotIn("lookups", without.model_dump(mode="json", exclude_none=True))
        offer = LookupOffer(dataset_id="zurich-waste-bioabfall", type="calendar", title="Organic waste collection days, City of Zurich",
                            label="Organic waste (Bioabfall)", jurisdiction="CH-ZH-261", requires=["postal_code"],
                            accepts=["start", "end", "limit"], period=Period(start=date(2026, 1, 1), end=date(2026, 12, 31)),
                            publisher="Entsorgung + Recycling Zürich")
        with_offer = ConceptResolution(concept_id="c", status=Status.SUPPORTED, lookups=[offer])
        self.assertEqual(with_offer.model_dump(mode="json")["lookups"][0]["period"], {"start": "2026-01-01", "end": "2026-12-31"})

    def test_the_wire_repeats_the_connector_and_dataset_shapes(self):
        # The wire contract repeats the storage models instead of importing them (as it does for ReviewStatus); these
        # keep the two from drifting apart.
        self.assertEqual(set(contracts.LookupSource.model_fields), set(datasets.DatasetSource.model_fields))
        self.assertEqual(set(contracts.Period.model_fields), set(datasets.Period.model_fields))
        self.assertEqual(set(contracts.LookupEvent.model_fields), set(connector.ConnectorEvent.model_fields))
        self.assertEqual(set(contracts.LookupProvenance.model_fields), set(connector.ConnectorProvenance.model_fields))
        self.assertTrue(set(get_args(connector.ConnectorGapDimension)) < set(get_args(contracts.GapDimension)))
        self.assertIn("connector_unavailable", get_args(contracts.GapDimension))


if __name__ == "__main__":
    unittest.main()
