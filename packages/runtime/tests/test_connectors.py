import unittest
from datetime import date, datetime

from swisstip.core.connector import ConnectorLookupRequest, calendar_lookup, summary_of
from swisstip.core.contracts import ToolError
from swisstip.core.datasets import Dataset, DatasetManifest, DatasetRow, DatasetSource, Period, content_hash
from swisstip.runtime.connectors import ConnectorRegistry, ConnectorUnavailable
from swisstip.runtime.service import ReleaseService
from test_service import sample_release

URL = "http://127.0.0.1:8100"


def dataset(dataset_id: str = "test-waste", concept_id: str = "city-arrival", jurisdiction: str = "CH-ZH-261",
            pack: str = "test", rows: list[DatasetRow] | None = None) -> Dataset:
    rows = rows if rows is not None else [DatasetRow(postal_code="8001", date=date(2026, 9, 28)),
                                          DatasetRow(postal_code="8001", date=date(2026, 10, 5)),
                                          DatasetRow(postal_code="8002", date=date(2026, 9, 29))]
    manifest = DatasetManifest(
        dataset_id=dataset_id, dataset_version="2026-09-23-v1", type="calendar", title="Organic waste collection days",
        label="Organic waste (Bioabfall)", pack=pack, concept_id=concept_id, jurisdiction=jurisdiction,
        publisher="ERZ", publisher_url="https://data.example.ch/dataset/bio", licence="CC0-1.0",
        sources=[DatasetSource(url="https://data.example.ch/bio_2026.csv", sha256="a" * 64, bytes=10, downloaded_on=date(2026, 9, 23))],
        period=Period(start=date(2026, 1, 1), end=date(2026, 12, 31)), postal_codes=sorted({r.postal_code for r in rows}),
        row_count=len(rows), created_at=datetime(2026, 9, 23, 10, 0), limitations=["Rows are not reviewed."], content_sha256="0" * 64)
    item = Dataset(manifest=manifest, rows=rows)
    item.manifest.content_sha256 = content_hash(item)
    return item


class FakeConnector:
    """A connector as the registry sees it: a callable answering /manifest and /lookup from bundles in memory."""

    def __init__(self, datasets: list[Dataset], invalid: dict[str, str] | None = None):
        self.datasets = {d.manifest.dataset_id: d for d in datasets}
        self.invalid = invalid or {}
        self.down = False
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, url: str, path: str, body: dict | None) -> tuple[int, dict]:
        self.calls.append((path, body))
        if self.down:
            raise ConnectorUnavailable(f"{url}: connection refused")
        if path == "/manifest":
            summaries = [summary_of(d, issue=self.invalid.get(d.manifest.dataset_id)) for d in self.datasets.values()]
            return 200, dict(schema_version="swiss-tip-connector/v1", connector_id="fake", types=["calendar"],
                             datasets=[s.model_dump(mode="json") for s in summaries])
        request = ConnectorLookupRequest.model_validate(body)
        if request.dataset_id not in self.datasets:
            return 400, dict(code="INVALID_ARGUMENT", message="unknown dataset", path="dataset_id")
        return 200, calendar_lookup(self.datasets[request.dataset_id], request).model_dump(mode="json")


class RegistryTests(unittest.TestCase):
    def test_binding_rules_and_health(self):
        connector = FakeConnector([dataset(), dataset("other-pack", pack="other"), dataset("no-concept", concept_id="nowhere"),
                                   dataset("outside", concept_id="zh-registration", jurisdiction="CH-BE-351"),
                                   dataset("broken")], invalid={"broken": "rows are repeated"})
        registry = ConnectorRegistry([URL], sample_release(), client=connector)
        self.assertEqual(sorted(registry.datasets), ["test-waste"])
        [entry] = registry.health()
        self.assertEqual((entry["status"], entry["connector_id"], entry["registered"]), ("ok", "fake", ["test-waste"]))
        rejected = {item["dataset_id"]: item["reason"] for item in entry["rejected"]}
        self.assertIn("serves 'test'", rejected["other-pack"])
        self.assertIn("not published", rejected["no-concept"])
        self.assertIn("outside", rejected["outside"])
        self.assertIn("rows are repeated", rejected["broken"])
        # A canton-level dataset behind a cantonal concept is fine; the offer then reaches every municipality below.
        cantonal = ConnectorRegistry([URL], sample_release(), client=FakeConnector([dataset(concept_id="zh-registration", jurisdiction="CH-ZH")]))
        self.assertEqual([s.dataset_id for s in cantonal.offers("zh-registration", "CH-ZH-261")], ["test-waste"])
        self.assertEqual([s.dataset_id for s in cantonal.offers("zh-registration", "CH-ZH")], ["test-waste"])
        self.assertEqual(cantonal.offers("zh-registration", "CH-BE"), [])
        self.assertEqual(cantonal.offers("city-arrival", "CH-ZH-261"), [])

    def test_an_unreachable_connector_is_probed_again_on_health(self):
        connector = FakeConnector([dataset()])
        connector.down = True
        registry = ConnectorRegistry([URL], sample_release(), client=connector)
        self.assertEqual(registry.datasets, {})
        self.assertEqual(registry.health()[0]["status"], "unreachable")
        connector.down = False
        self.assertEqual(registry.health()[0]["status"], "ok")
        self.assertEqual(sorted(registry.datasets), ["test-waste"])
        # A registered connector is not re-read on every probe.
        manifests = [call for call in connector.calls if call[0] == "/manifest"]
        self.assertEqual(len(manifests), 3)
        registry.health()
        self.assertEqual(len([call for call in connector.calls if call[0] == "/manifest"]), 3)

    def test_a_manifest_that_is_not_the_contract(self):
        registry = ConnectorRegistry([URL], sample_release(), client=lambda url, path, body: (200, {"hello": "world"}))
        self.assertEqual(registry.health()[0]["status"], "manifest_invalid")


class LookupAcceptanceTests(unittest.TestCase):
    def suite(self, **lookup):
        from swisstip.core.acceptance import AcceptanceFile
        step = dict(dict(dataset_id="test-waste", postal_code="8001", as_of="2026-09-23", limit=1), **lookup)
        return AcceptanceFile(pack="test", cases=[dict(
            case_id="L-1", label="Next organic waste date", question="When is the next organic waste collection in 8001?",
            expected_answer="2026-09-28.", steps=[dict(lookup=step)])])

    def test_a_lookup_step_is_judged_only_with_a_connector(self):
        from swisstip.runtime.acceptance import check_acceptance
        without = check_acceptance(ReleaseService(sample_release()), self.suite(expect_first_date="2026-09-28"))
        [step] = without["results"][0]["steps"]
        self.assertEqual((without["passed"], step["judged"], step["passed"]), (True, False, True))
        service = ReleaseService(sample_release(), connectors=ConnectorRegistry([URL], sample_release(), client=FakeConnector([dataset()])))
        passing = check_acceptance(service, self.suite(expect_status="SUPPORTED", expect_first_date="2026-09-28"))
        [step] = passing["results"][0]["steps"]
        self.assertEqual((passing["passed"], step["status"], step["dates"]), (True, "SUPPORTED", ["2026-09-28"]))
        failing = check_acceptance(service, self.suite(expect_status="OUT_OF_COVERAGE", expect_gap="postal_code_not_covered",
                                                        expect_first_date="2026-10-05"))
        self.assertFalse(failing["passed"])
        self.assertEqual(len(failing["results"][0]["issues"]), 3)
        gap = check_acceptance(service, self.suite(postal_code="8304", expect_status="OUT_OF_COVERAGE", expect_gap="postal_code_not_covered"))
        self.assertTrue(gap["passed"])


class LookupToolTests(unittest.TestCase):
    def setUp(self):
        self.connector = FakeConnector([dataset()])
        self.service = ReleaseService(sample_release(), connectors=ConnectorRegistry([URL], sample_release(), client=self.connector))

    def test_without_connectors_nothing_changes(self):
        service = ReleaseService(sample_release())
        self.assertEqual(service.tools(), ["get_coverage", "search", "resolve", "get_evidence"])
        result = service.dispatch("resolve", {"concept_ids": ["city-arrival"], "jurisdiction": {"municipality_id": "CH-ZH-261"}})
        self.assertNotIn("lookups", result.results[0].model_dump(mode="json", exclude_none=True))
        self.assertNotIn("lookup", result.guidance_for_caller)
        error = service.dispatch("lookup", {"dataset_id": "test-waste", "postal_code": "8001"})
        self.assertIsInstance(error, ToolError)

    def test_resolve_offers_the_dataset_for_the_place_it_covers(self):
        self.assertEqual(self.service.tools()[-1], "lookup")
        result = self.service.dispatch("resolve", {"concept_ids": ["city-arrival"], "jurisdiction": {"municipality_id": "CH-ZH-261"},
                                                   "as_of": "2026-09-23"})
        [offer] = result.results[0].lookups
        self.assertEqual((offer.dataset_id, offer.requires, offer.accepts), ("test-waste", ["postal_code"], ["start", "end", "limit"]))
        self.assertEqual(offer.period.end, date(2026, 12, 31))
        self.assertIn("call lookup", result.guidance_for_caller)
        # The offer stands behind the concept for its place only: a cantonal request reaches no city dataset,
        # and a concept without a dataset carries no offer.
        cantonal = self.service.dispatch("resolve", {"concept_ids": ["city-arrival", "zh-registration"],
                                                     "jurisdiction": {"canton_code": "CH-ZH"}, "as_of": "2026-09-23"})
        self.assertEqual([item.lookups for item in cantonal.results], [[], []])

    def test_lookup_forwards_and_wraps_the_answer(self):
        result = self.service.dispatch("lookup", {"dataset_id": "test-waste", "postal_code": "8001", "as_of": "2026-09-23", "limit": 1})
        self.assertEqual((result.status.value, result.release_id, result.truncated), ("SUPPORTED", "test-v1", True))
        self.assertEqual([e.date.isoformat() for e in result.events], ["2026-09-28"])
        self.assertEqual(result.provenance.publisher_url, "https://data.example.ch/dataset/bio")
        self.assertEqual(result.provenance.sources[0].sha256, "a" * 64)
        self.assertIn("ERZ", result.guidance_for_caller)
        self.assertEqual(result.limitations[-1], "Rows are not reviewed.")
        # The request the connector saw: start defaulted to as_of, end left open.
        self.assertEqual(self.connector.calls[-1], ("/lookup", dict(dataset_id="test-waste", postal_code="8001", start="2026-09-23",
                                                                    end=None, limit=1)))
        gap = self.service.dispatch("lookup", {"dataset_id": "test-waste", "postal_code": "8304", "as_of": "2026-09-23"})
        self.assertEqual((gap.status.value, gap.gaps[0].dimension, gap.gaps[0].published_values), ("OUT_OF_COVERAGE", "postal_code_not_covered", ["8001", "8002"]))
        self.assertIn("gaps", gap.guidance_for_caller)
        later = self.service.dispatch("lookup", {"dataset_id": "test-waste", "postal_code": "8001", "start": "2027-01-04"})
        self.assertEqual(later.gaps[0].dimension, "period_not_published")

    def test_typed_errors_and_the_unavailable_gap(self):
        unknown = self.service.dispatch("lookup", {"dataset_id": "nowhere", "postal_code": "8001"})
        self.assertIsInstance(unknown, ToolError)
        self.assertIn("test-waste", unknown.error.issues[0].message)
        malformed = self.service.dispatch("lookup", {"dataset_id": "test-waste", "postal_code": "80"})
        self.assertIsInstance(malformed, ToolError)
        self.connector.down = True
        result = self.service.dispatch("lookup", {"dataset_id": "test-waste", "postal_code": "8001", "as_of": "2026-09-23"})
        self.assertEqual((result.status.value, result.gaps[0].dimension, result.events), ("OUT_OF_COVERAGE", "connector_unavailable", []))
        self.assertIn("unaffected", result.gaps[0].message)
        self.assertEqual(result.provenance.dataset_version, "2026-09-23-v1")


if __name__ == "__main__":
    unittest.main()
