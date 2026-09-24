import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from swisstip.core.graph import (GraphInvalid, assert_valid_graph, check_embedded_graph, dump_graph, graph_hash,
                                 load_graph, review_counts, validate_graph)
from swisstip.core.release import (EvidenceRecord, Freshness, GraphEdge, GraphNode, Institution, KnowledgeGraph, Place,
                                   PlaceRegister, Provenance, SourceDocument, content_hash, sha256_text)

from test_release import sample_release

TEXT = "Residence permits\n\nThe cantonal migration offices issue the residence permits.\n\nArt. 12 AIG"


def unreviewed() -> Provenance:
    return Provenance(kind="curated-statement", review_status="assistant-authored-unreviewed", author="test")


def sample_graph(**changes) -> KnowledgeGraph:
    excerpt = "The cantonal migration offices issue the residence permits."
    start = TEXT.index(excerpt)
    document = SourceDocument(document_id="graph-doc-sem", source_url="https://www.sem.admin.ch/residence",
                              document_url="https://www.sem.admin.ch/residence", title="Residence", publisher="SEM",
                              institution_id="ch-sem", language="en", accessed_on=date(2026, 9, 20), raw_sha256="r" * 64,
                              content_sha256=sha256_text(TEXT))
    evidence = EvidenceRecord(evidence_id="graph-e1", document_id="graph-doc-sem", source_title="Residence", publisher="SEM",
                              institution_id="ch-sem", url="https://www.sem.admin.ch/residence", language="en",
                              accessed_on=date(2026, 9, 20), start_offset=start, end_offset=start + len(excerpt),
                              original_excerpt=excerpt, excerpt_sha256=sha256_text(excerpt), block_ids=["graph-doc-sem:b00002"],
                              content_sha256=sha256_text(TEXT), raw_sha256="r" * 64)
    nodes = [
        GraphNode(node_id="domain.residence", kind="domain", label="Residence permits", summary="Permits to live in Switzerland.",
                  keywords=["permit", "Aufenthaltsbewilligung"], evidence_ids=["graph-e1"], provenance=unreviewed()),
        GraphNode(node_id="role.cantonal-migration-office", kind="role", label="Cantonal migration office",
                  names={"de": "Migrationsamt"}, summary="Issues residence permits.", evidence_ids=["graph-e1"],
                  provenance=unreviewed()),
        GraphNode(node_id="institution.zh-migrationsamt", kind="institution", label="Migrationsamt ZH", level="cantonal",
                  place="CH-ZH", summary="The migration office of the Canton of Zurich.", evidence_ids=["graph-e1"],
                  provenance=unreviewed()),
        GraphNode(node_id="place.ch", kind="place", label="Switzerland", place="CH", level="federal",
                  summary="The Confederation.", provenance=unreviewed()),
        GraphNode(node_id="place.ch-zh", kind="place", label="Canton of Zurich", place="CH-ZH", level="cantonal",
                  summary="A canton.", provenance=unreviewed()),
    ]
    edges = [
        GraphEdge(edge_id="residence-executed-by-migration-office", from_id="domain.residence", relation="executed_by",
                  to_id="role.cantonal-migration-office", statement="The cantonal migration offices issue residence permits.",
                  evidence_ids=["graph-e1"], provenance=unreviewed()),
        GraphEdge(edge_id="migration-office-zh", from_id="role.cantonal-migration-office", relation="instance",
                  to_id="institution.zh-migrationsamt", place="CH-ZH",
                  statement="In the Canton of Zurich this is the Migrationsamt.", evidence_ids=["graph-e1"],
                  provenance=unreviewed()),
        GraphEdge(edge_id="zh-part-of-ch", from_id="place.ch-zh", relation="part_of", to_id="place.ch",
                  statement="The Canton of Zurich is a canton of Switzerland.", provenance=unreviewed()),
    ]
    graph = KnowledgeGraph(graph_id="ch", title="Switzerland", created_at=datetime(2026, 9, 24, 12, 0),
                           freshness=Freshness(snapshot_date=date(2026, 9, 20), max_age_days=90,
                                               stale_from=date(2026, 9, 20) + timedelta(days=90)),
                           review_statuses={}, nodes=nodes, edges=edges,
                           institutions=[Institution(institution_id="ch-sem", name="SEM", level="federal",
                                                     body="administration", jurisdiction="CH")],
                           documents=[document], evidence=[evidence], content_sha256="0" * 64)
    for key, value in changes.items():
        setattr(graph, key, value)
    return seal(graph)


def seal(graph: KnowledgeGraph) -> KnowledgeGraph:
    graph.review_statuses = review_counts(graph)
    graph.content_sha256 = graph_hash(graph)
    return graph


class GraphTests(unittest.TestCase):
    def test_valid_graph_round_trips(self):
        graph = sample_graph()
        self.assertEqual(validate_graph(graph, {"CH", "CH-ZH"}), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "graph.json"
            path.write_text(dump_graph(graph), encoding="utf-8")
            self.assertEqual(load_graph(path), graph)

    def test_relation_endpoints_are_enforced(self):
        graph = sample_graph()
        graph.edges[0].to_id = "domain.residence"
        graph.edges[0].from_id = "role.cantonal-migration-office"
        issues = validate_graph(seal(graph))
        self.assertIn("edge residence-executed-by-migration-office: executed_by cannot start at a role", issues)
        self.assertIn("edge residence-executed-by-migration-office: executed_by cannot point to a domain", issues)

    def test_dangling_edges_and_unknown_evidence_are_reported(self):
        graph = sample_graph()
        graph.edges[0].to_id = "role.nowhere"
        graph.nodes[0].evidence_ids = ["graph-missing"]
        issues = validate_graph(seal(graph))
        self.assertIn("edge residence-executed-by-migration-office points to unknown node role.nowhere", issues)
        self.assertIn("node domain.residence references unknown evidence graph-missing", issues)

    def test_places_must_be_in_the_register_and_instances_need_their_place(self):
        graph = sample_graph()
        self.assertIn("node institution.zh-migrationsamt names place CH-ZH, which the place register does not list",
                      validate_graph(graph, {"CH"}))
        graph.edges[1].place = "CH-BE"
        self.assertIn("edge migration-office-zh holds for CH-BE but its institution speaks for CH-ZH", validate_graph(seal(graph)))
        graph.edges[1].place = None
        self.assertIn("edge migration-office-zh is an instance without a place", validate_graph(seal(graph)))

    def test_claims_need_evidence_but_place_containment_does_not(self):
        graph = sample_graph()
        self.assertFalse([issue for issue in validate_graph(graph) if "zh-part-of-ch" in issue])
        graph.edges[0].evidence_ids = []
        self.assertIn("edge residence-executed-by-migration-office cites no evidence", validate_graph(seal(graph)))

    def test_node_names_levels_and_hash(self):
        graph = sample_graph()
        graph.nodes[2].level = "federal"
        graph.nodes[0].node_id = "role.residence"
        issues = validate_graph(graph)
        self.assertIn("content_sha256 does not match the graph body", issues)
        self.assertIn("node institution.zh-migrationsamt is federal but its place CH-ZH is cantonal", issues)
        self.assertIn("node role.residence is not named `domain.<slug>`", issues)

    def test_human_review_needs_a_reviewer_and_reviewed_serving_withholds_the_rest(self):
        with self.assertRaises(ValueError):
            Provenance(kind="curated-statement", review_status="human-reviewed", author="test")
        graph = sample_graph(serve="reviewed")
        self.assertTrue(any("served unreviewed" in issue for issue in validate_graph(graph)))
        with self.assertRaises(GraphInvalid):
            assert_valid_graph(graph)

    def test_a_release_embeds_its_graph_and_bridges_topics_to_domains(self):
        release = sample_release()
        release.place_register = PlaceRegister(title="Register", publisher="BFS", url="https://bfs.example",
                                               accessed_on=date(2026, 9, 18), raw_sha256="p" * 64,
                                               places=[Place(code="CH", name="Schweiz"), Place(code="CH-ZH", name="Zürich")])
        release.knowledge_graph = sample_graph()
        release.topics[0].graph_nodes = ["domain.residence"]
        self.assertEqual(check_embedded_graph(release), [])
        before = content_hash(release)
        release.knowledge_graph.nodes[0].summary = "Changed."
        self.assertNotEqual(content_hash(release), before)
        release.knowledge_graph = seal(release.knowledge_graph)
        release.topics[0].graph_nodes = ["role.cantonal-migration-office"]
        self.assertEqual(check_embedded_graph(release),
                         ["topic t1 names role.cantonal-migration-office, which is not a domain of the graph"])
        release.knowledge_graph = None
        self.assertEqual(check_embedded_graph(release), ["topic t1 names graph nodes but the release carries no graph"])


if __name__ == "__main__":
    unittest.main()
