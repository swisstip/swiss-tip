import json
import unittest
from datetime import date, datetime, timedelta

from swisstip.core.contracts import GetKnowledgeGraphRequest, KnowledgeGraphResult, ToolError
from swisstip.core.graph import graph_hash, review_counts
from swisstip.core.places import PlaceIndex
from swisstip.core.release import (EvidenceRecord, Freshness, GraphEdge, GraphNode, KnowledgeGraph, Provenance, Release,
                                   SourceDocument, content_hash, sha256_text)
from swisstip.core.validation import assert_valid
from swisstip.runtime.graph import BYTE_BUDGET, GraphCase, GraphChecks, GraphIndex, check_graph
from swisstip.runtime.service import ReleaseService

from test_service import PLACES, release_with_places

TEXT = ("Die kantonalen Migrationsämter erteilen die Aufenthaltsbewilligungen. Die Anmeldung erfolgt bei der "
        "Einwohnerkontrolle der Wohngemeinde. In der Stadt Zürich beim Personenmeldeamt. Waren werden beim Zoll angemeldet.")
UNREVIEWED = Provenance(kind="curated-statement", review_status="assistant-authored-unreviewed", author="agent")


def node(node_id: str, kind: str, label: str, summary: str, **extra) -> GraphNode:
    evidence_ids = [] if kind == "place" else ["graph-e1"]
    return GraphNode(node_id=node_id, kind=kind, label=label, summary=summary, evidence_ids=evidence_ids,
                     provenance=UNREVIEWED, **extra)


def edge(from_id: str, relation: str, to_id: str, statement: str, place: str | None = None) -> GraphEdge:
    return GraphEdge(edge_id=f"{from_id}.{relation}.{to_id}", from_id=from_id, relation=relation, to_id=to_id,
                     statement=statement, place=place, evidence_ids=[] if relation == "part_of" else ["graph-e1"],
                     provenance=UNREVIEWED)


def sample_graph() -> KnowledgeGraph:
    document = SourceDocument(document_id="graph-doc-1", source_url="https://www.ch.ch/federalism", document_url="https://www.ch.ch/federalism",
                              title="Federalism", publisher="ch.ch", language="de", accessed_on=date(2026, 9, 20),
                              raw_sha256="r" * 64, content_sha256=sha256_text(TEXT))
    evidence = EvidenceRecord(evidence_id="graph-e1", document_id="graph-doc-1", source_title="Federalism", publisher="ch.ch",
                              url="https://www.ch.ch/federalism", language="de", accessed_on=date(2026, 9, 20), start_offset=0,
                              end_offset=len(TEXT), original_excerpt=TEXT, excerpt_sha256=sha256_text(TEXT),
                              block_ids=["graph-doc-1:b00001"], content_sha256=sha256_text(TEXT), raw_sha256="r" * 64)
    nodes = [
        node("level.federal", "level", "Confederation", "Sets the rules on residence.", level="federal"),
        node("level.cantonal", "level", "Cantons", "Carry out federal law.", level="cantonal"),
        node("principle.executive-federalism", "principle", "Executive federalism", "The cantons implement federal law (BV Art. 46)."),
        node("domain.registration", "domain", "Registration on arrival", "Reporting arrival to the commune of residence.",
             names={"de": "Anmeldung"}, keywords=["register", "registration", "anmelden", "arrival"]),
        node("domain.residence", "domain", "Residence permits", "Permits to live in Switzerland.",
             names={"de": "Aufenthaltsbewilligungen"}, keywords=["permit", "residence permit", "Bewilligung"]),
        node("domain.customs", "domain", "Customs", "Bringing goods into Switzerland.", keywords=["customs", "Zoll", "import"]),
        node("role.residents-office", "role", "Residents' office", "The commune's office for arrivals.",
             names={"de": "Einwohnerkontrolle"}, level="municipal"),
        node("role.migration-office", "role", "Cantonal migration office", "Issues residence permits.",
             names={"de": "Migrationsämter"}, level="cantonal"),
        node("institution.zh-261-personenmeldeamt", "institution", "Personenmeldeamt Zürich",
             "The City of Zurich's population office.", level="municipal", place="CH-ZH-261"),
        node("institution.zh-migrationsamt", "institution", "Migrationsamt ZH", "The Canton of Zurich's migration office.",
             level="cantonal", place="CH-ZH"),
        node("institution.bazg", "institution", "Federal Office for Customs", "Customs.", level="federal", place="CH"),
        node("law.aig", "law", "AIG (SR 142.20)", "Federal act on foreign nationals.", level="federal", sr_number="SR 142.20"),
        node("pitfall.workplace-vs-residence", "pitfall", "Workplace is not residence",
             "Registration follows the commune of residence, not the workplace."),
        node("place.ch", "place", "Switzerland", "The Confederation.", place="CH", level="federal"),
        node("place.ch-zh", "place", "Canton of Zürich (CH-ZH)", "A canton.", place="CH-ZH", level="cantonal"),
        node("place.ch-zh-261", "place", "Zürich (CH-ZH-261)", "A municipality.", place="CH-ZH-261", level="municipal"),
    ]
    edges = [
        edge("level.cantonal", "governed_by", "principle.executive-federalism", "Cantons carry out federal law."),
        edge("domain.registration", "first_contact", "role.residents-office", "You register with the residents' office of your commune."),
        edge("domain.registration", "pitfall", "pitfall.workplace-vs-residence", "Register where you live, not where you work."),
        edge("domain.residence", "rules_set_by", "level.federal", "The Confederation sets the rules."),
        edge("domain.residence", "executed_by", "role.migration-office", "The cantonal migration offices issue permits."),
        edge("domain.residence", "legal_basis", "law.aig", "Residence rests on the AIG."),
        edge("domain.customs", "executed_by", "institution.bazg", "The federal customs office handles imports.", place="CH"),
        edge("role.residents-office", "instance", "institution.zh-261-personenmeldeamt", "In Zurich: the Personenmeldeamt.",
             place="CH-ZH-261"),
        edge("role.migration-office", "instance", "institution.zh-migrationsamt", "In the Canton of Zurich: the Migrationsamt.",
             place="CH-ZH"),
        edge("place.ch-zh", "part_of", "place.ch", "Zurich is a canton."),
        edge("place.ch-zh-261", "part_of", "place.ch-zh", "The city lies in the canton."),
    ]
    graph = KnowledgeGraph(graph_id="ch", title="Switzerland", created_at=datetime(2026, 9, 24, 12, 0),
                           freshness=Freshness(snapshot_date=date(2026, 9, 20), max_age_days=90,
                                               stale_from=date(2026, 9, 20) + timedelta(days=90)),
                           review_statuses={}, nodes=nodes, edges=edges, documents=[document], evidence=[evidence],
                           content_sha256="0" * 64)
    graph.review_statuses = review_counts(graph)
    graph.content_sha256 = graph_hash(graph)
    return graph


def graph_release() -> Release:
    release = release_with_places()
    release.knowledge_graph = sample_graph()
    release.topics[0].graph_nodes = ["domain.registration", "domain.residence"]
    release.manifest.content_sha256 = content_hash(release)
    assert_valid(release)
    return release


class GraphToolTests(unittest.TestCase):
    def setUp(self):
        self.service = ReleaseService(graph_release())

    def call(self, **arguments) -> KnowledgeGraphResult:
        result = self.service.dispatch("get_knowledge_graph", arguments)
        self.assertIsInstance(result, KnowledgeGraphResult)
        return result

    def test_the_tool_is_offered_first_only_with_a_graph(self):
        self.assertEqual(self.service.tools(), ["get_knowledge_graph", "get_coverage", "search", "resolve", "get_evidence"])
        plain = ReleaseService(release_with_places())
        self.assertEqual(plain.tools(), ["get_coverage", "search", "resolve", "get_evidence"])
        self.assertIsInstance(plain.dispatch("get_knowledge_graph", {}), ToolError)

    def test_instructions_and_descriptions_put_the_graph_first(self):
        self.assertIn("Start every new subject with get_knowledge_graph", self.service.instructions)
        for name in ("search", "resolve", "get_coverage", "get_evidence"):
            self.assertTrue(self.service.tool_description(name).startswith("For a new subject call get_knowledge_graph first"))
        self.assertIn("three calls", self.service.tool_description("get_coverage"))
        self.assertIn("Call this FIRST", self.service.tool_description("get_knowledge_graph"))
        schema = self.service.tool_input_schema("get_knowledge_graph")
        self.assertIn("what to ask", schema["properties"]["jurisdiction"]["description"])
        plain = ReleaseService(release_with_places())
        self.assertNotIn("get_knowledge_graph", plain.instructions)
        self.assertFalse(plain.tool_description("search").startswith("For a new subject"))

    def test_question_and_city_reach_the_office_at_the_users_place(self):
        result = self.call(question="Czech citizen starting work, by when must I register my arrival?",
                           jurisdiction={"city": "Zurich"})
        served = {n.node_id for n in result.nodes}
        self.assertEqual(result.match_strength, "strong")
        self.assertIn("domain.registration", served)
        self.assertIn("institution.zh-261-personenmeldeamt", served)
        self.assertEqual(result.executed_scope.municipality_id, "CH-ZH-261")
        self.assertTrue(result.place_dependence.known)
        self.assertEqual(result.place_dependence.depends_on, "municipality")
        self.assertEqual(result.covered_topics, ["residence"])
        self.assertEqual(result.next_search.jurisdiction.city, "Zurich")
        self.assertIn("Einwohnerkontrolle", result.next_search.terms)
        self.assertEqual(result.review_status, "assistant-authored-unreviewed")
        self.assertTrue(all(n.review_status is None for n in result.nodes))
        self.assertEqual(result.edges[0].source_url, "https://www.ch.ch/federalism")

    def test_no_place_returns_generic_roles_and_asks_for_the_municipality(self):
        result = self.call(question="Czech citizen starting work in Zurich next week, when do I register?")
        served = {n.node_id for n in result.nodes}
        self.assertIn("role.residents-office", served)
        self.assertNotIn("institution.zh-261-personenmeldeamt", served)
        self.assertIn("pitfall.workplace-vs-residence", served)
        self.assertEqual(result.executed_scope.country_code, "CH")
        self.assertEqual(result.place_dependence.depends_on, "municipality")
        self.assertFalse(result.place_dependence.known)
        self.assertIn("municipality", result.place_dependence.ask)
        self.assertIsNone(result.next_search.jurisdiction)
        self.assertIn("never assume one", result.guidance_for_caller)

    def test_a_canton_reaches_the_cantonal_office_but_not_a_municipal_one(self):
        result = self.call(question="residence permit", jurisdiction={"canton": "ZH"})
        served = {n.node_id for n in result.nodes}
        self.assertIn("institution.zh-migrationsamt", served)
        self.assertEqual(result.place_dependence.depends_on, "canton")
        self.assertTrue(result.place_dependence.known)
        other = self.call(question="residence permit", jurisdiction={"canton": "Bern"})
        self.assertNotIn("institution.zh-migrationsamt", {n.node_id for n in other.nodes})

    def test_a_federal_domain_needs_no_place_and_an_uncovered_domain_is_named(self):
        result = self.call(question="customs on imported goods")
        self.assertEqual(result.place_dependence.depends_on, "none")
        self.assertTrue(result.place_dependence.known)
        self.assertEqual(result.covered_topics, [])
        self.assertIn("publishes no facts on customs", result.guidance_for_caller)

    def test_unmatched_questions_and_the_root_page(self):
        self.assertEqual(self.call(question="quantum chromodynamics").match_strength, "none")
        root = self.call()
        self.assertEqual({d.node_id for d in root.domains}, {"domain.registration", "domain.residence", "domain.customs"})
        self.assertTrue(next(d for d in root.domains if d.node_id == "domain.residence").covered)
        self.assertEqual({n.kind for n in root.nodes}, {"level", "principle"})
        self.assertIsNone(root.next_search)

    def test_node_ids_expand_and_reviewed_only_withholds(self):
        result = self.call(node_ids=["role.migration-office"], jurisdiction={"canton": "ZH"})
        self.assertIn("institution.zh-migrationsamt", {n.node_id for n in result.nodes})
        self.assertEqual(self.call(question="residence permit", reviewed_only=True).nodes, [])

    def test_graph_evidence_is_readable_and_the_result_fits_the_budget(self):
        evidence = self.service.dispatch("get_evidence", {"evidence_ids": ["graph-e1"]})
        self.assertEqual(evidence.evidence[0].url, "https://www.ch.ch/federalism")
        result = self.call(question="register arrival residence permit customs", jurisdiction={"city": "Zurich"})
        size = len(json.dumps(result.model_dump(mode="json", exclude_none=True), ensure_ascii=False, separators=(",", ":")))
        self.assertLessEqual(size, BYTE_BUDGET)

    def test_a_place_the_register_does_not_hold_is_a_typed_error_or_a_note(self):
        self.assertIsInstance(self.service.dispatch("get_knowledge_graph", {"question": "register", "jurisdiction": {"country": "France"}}),
                              KnowledgeGraphResult)
        self.assertIsInstance(self.service.dispatch("get_knowledge_graph", {"question": "x" * 600}), ToolError)

    def test_checks_replay_with_the_server_code(self):
        index = GraphIndex(sample_graph())
        checks = GraphChecks(graph="ch", cases=[
            GraphCase(case_id="zurich", question="register arrival", jurisdiction={"city": "Zurich"},
                      expect_nodes=["institution.zh-261-personenmeldeamt"], expect_place_dependence="municipality"),
            GraphCase(case_id="no-place", question="register arrival", expect_absent=["institution.zh-261-personenmeldeamt"],
                      expect_place_dependence="municipality"),
            GraphCase(case_id="wrong", question="register arrival", expect_nodes=["law.aig"], blocking=False)])
        report = check_graph(index, PlaceIndex(PLACES, ["CH"]), checks)
        self.assertEqual(report["failed_blocking"], [])
        self.assertEqual(report["passed"], 2)

    def test_a_place_name_is_not_a_subject_word(self):
        # "Zürich" is in the Personenmeldeamt's label, linked to registration through its role; it must not match.
        self.assertEqual(self.service.graph_index.rank("opening hours of the Zurich zoo in Switzerland"), ([], "none"))
        self.assertEqual(self.call(question="register in Zurich").match_strength, "strong")

    def test_an_answer_is_shortened_before_links_are_cut(self):
        index = self.service.graph_index
        long = "A long sentence about the office that a caller does not need to orient itself. " * 12
        index.nodes["role.residents-office"] = index.nodes["role.residents-office"].model_copy(update=dict(summary=long))
        index.nodes["institution.zh-261-personenmeldeamt"] = index.nodes["institution.zh-261-personenmeldeamt"].model_copy(
            update=dict(summary=long * 8))
        result = self.call(question="register arrival", jurisdiction={"city": "Zurich"})
        served = {n.node_id: n for n in result.nodes}
        self.assertIn("institution.zh-261-personenmeldeamt", served)
        self.assertIsNone(served["institution.zh-261-personenmeldeamt"].summary)
        self.assertEqual(served["domain.registration"].summary, "Reporting arrival to the commune of residence.")


class TopicEmbedder:
    """A fake local embedder: a text's vector says which domain it is about, by the first cue it contains."""

    model = "qwen3-embedding:0.6b"
    CUES = [("Registration", "moved into"), ("Residence",), ("Customs", "parcel"), ("zoo",)]

    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, 0

    def model_digest(self):
        return "d" * 64

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise OSError("connection refused")
        vectors = []
        for text in texts:
            text = text.split("Query: ")[-1] if text.startswith("Instruct:") else text.split("\n")[0]
            hit = next((i for i, cues in enumerate(self.CUES) if any(cue in text for cue in cues)), 3)
            vectors.append([1.0 if i == hit else 0.0 for i in range(4)])
        return vectors


class GraphSemanticTests(unittest.TestCase):
    def service(self, embedder) -> ReleaseService:
        service = ReleaseService(graph_release())
        service.graph_embedder = embedder
        return service

    def test_embeddings_find_a_domain_the_words_miss(self):
        service = self.service(TopicEmbedder())
        self.assertEqual(service.graph_index.rank("I moved into a flat last week"), ([], "none"))
        result = service.dispatch("get_knowledge_graph", {"question": "I moved into a flat last week"})
        self.assertEqual((result.match_strength, result.covered_topics), ("strong", ["residence"]))
        self.assertIn("domain.registration", {n.node_id for n in result.nodes})

    def test_a_lexical_match_far_from_every_domain_is_at_most_weak(self):
        embedder = TopicEmbedder()
        service = self.service(embedder)
        self.assertEqual(service.graph_index.lexical_rank("customs at the zoo")[1], "strong")
        ranking = (service.dispatch("get_knowledge_graph", {"question": "customs at the zoo"}), service.graph_index.ranking("customs at the zoo"))
        self.assertEqual((ranking[0].match_strength, ranking[1].mode, ranking[1].best_cosine), ("weak", "hybrid", 0.0))
        calls = embedder.calls
        service.dispatch("get_knowledge_graph", {"question": "a parcel from Germany"})
        self.assertEqual(embedder.calls, calls + 1)  # the domain vectors are made once

    def test_a_failing_embedder_falls_back_to_words_and_says_so(self):
        result = self.service(TopicEmbedder(fail=True)).dispatch("get_knowledge_graph", {"question": "register my arrival"})
        self.assertEqual(result.match_strength, "strong")
        self.assertIn("matched by words only", result.limitations[-1])
        plain = ReleaseService(graph_release()).dispatch("get_knowledge_graph", {"question": "register my arrival"})
        self.assertNotIn("matched by words only", " ".join(plain.limitations))


class GraphRegressionTests(unittest.TestCase):
    def test_the_suites_questions_are_judged_by_the_topics_bridges(self):
        from swisstip.core.acceptance import AcceptanceFile
        from swisstip.runtime.graph_regression import graph_regression
        suite = AcceptanceFile(pack="test", cases=[
            dict(case_id="hit", label="hit", question="When must I register my arrival?", expected_answer="x",
                 steps=[dict(search=dict(query="When must I register my arrival?", expect_concept="deadline"))]),
            dict(case_id="miss", label="miss", question="What does customs charge?", expected_answer="x",
                 steps=[dict(search=dict(query="What does customs charge?", expect_concept="deadline"))]),
            dict(case_id="unbridged", label="unbridged", question="Whom do I contact?", expected_answer="x",
                 steps=[dict(search=dict(query="Whom do I contact?", expect_concept="contact"))]),
            dict(case_id="decline", label="decline", question="Opening hours of the Zurich zoo", expected_answer="x",
                 steps=[dict(search=dict(query="Opening hours of the Zurich zoo", expect_strength="none"))]),
            dict(case_id="misled", label="misled", question="register a permit", expected_answer="x", blocking=False,
                 quarantine_reason="test", steps=[dict(search=dict(query="register a permit", expect_strength="weak"))])])
        report = graph_regression(ReleaseService(graph_release()), suite)
        outcome = {r["case_id"]: r["passed"] for r in report["results"]}
        self.assertEqual(outcome, {"hit": True, "miss": False, "unbridged": None, "decline": True, "misled": False})
        self.assertEqual((report["subject"], report["decline"]), (dict(cases=2, passed=1, first=1), dict(cases=2, passed=1)))
        self.assertEqual((report["unbridged"], report["failed_blocking"]), (["contacts"], ["miss"]))
        with self.assertRaises(ValueError):
            graph_regression(ReleaseService(graph_release(), knowledge_graph=False), suite)


if __name__ == "__main__":
    unittest.main()
