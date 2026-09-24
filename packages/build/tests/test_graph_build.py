import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from swisstip.build.curation import Curation, save_curation
from swisstip.build.graph_build import build_graph, graph_places
from swisstip.build.graph_cli import main as graph_cli
from swisstip.build.graph_curation import GraphCuration, load_graph_curation, save_graph_curation
from swisstip.build.graph_derive import apply_derivation, derive, law_identity
from swisstip.build.graph_embed import graph_for
from swisstip.build.graph_merge import merge_proposals
from swisstip.build.places import load_place_register
from swisstip.build.release_build import BuildError, build_release
from swisstip.core.graph import dump_graph
from swisstip.core.release import dump_release, load_release
from swisstip.core.validation import validate_release
from swisstip.extraction.extract_cli import run_extraction

from test_release_build import CURATION, PAGE, make_run

REGISTER = dict(schema_version="swiss-tip-places/v1", country="CH", title="Register", publisher="BFS",
                url="https://bfs.example/register", accessed_on="2026-09-18", raw_sha256="p" * 64,
                places=[dict(code="CH-ZH", name="Zürich"), dict(code="CH-ZH-261", name="Zürich")])

GRAPH = """
schema_version: swiss-tip-graph-curation/v1
graph: ch
title: Switzerland
place_register: ../../config/places/register.json
institutions:
  - {institution_id: ch-sem, name: SEM, level: federal, body: administration, jurisdiction: CH, urls: [www.sem.example]}
packs: [test]
role_rules:
  role.residents-office: ["ch-*"]
nodes:
  - node_id: domain.residence
    kind: domain
    label: Residence and registration
    summary: Registering on arrival and the residence permit.
    keywords: [register, arrival]
    provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: agent}
    evidence:
      - text: {document_id: DOC, first_block: 3, last_block: 3}
  - node_id: role.residents-office
    kind: role
    label: Residents' office
    names: {en: arrival}
    summary: Where a person registers on arrival.
    provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: agent}
    evidence:
      - text: {document_id: DOC, first_block: 3, last_block: 3}
edges:
  - edge_id: residence.first_contact.residents-office
    from_id: domain.residence
    relation: first_contact
    to_id: role.residents-office
    statement: A person registers with the residents' office within 14 days of arrival.
    provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: agent}
    evidence:
      - text: {document_id: DOC, first_block: 3, last_block: 4}
"""


class GraphBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        run = make_run(self.root, PAGE)
        run_extraction(run, log=lambda *a, **k: None)
        self.text = run / "text"
        self.document_id = json.loads((self.text / "index.json").read_text(encoding="utf-8"))[0]["document_id"]
        (self.root / "config" / "places").mkdir(parents=True)
        (self.root / "config" / "places" / "register.json").write_text(json.dumps(REGISTER), encoding="utf-8")
        # The pack: its curation bridges the residence topic to the graph's domain, its release is built.
        pack_data = yaml.safe_load(CURATION.replace("DOC", self.document_id))
        pack_data["institutions"] = [dict(institution_id="ch-sem", name="SEM", level="federal", body="administration",
                                          jurisdiction="CH", urls=["www.sem.example"])]
        pack_data["topics"][0]["graph_nodes"] = ["domain.residence"]
        self.pack_dir = self.root / "releases" / "test"
        self.pack_dir.mkdir(parents=True)
        self.pack_curation = Curation.model_validate(pack_data)
        save_curation(self.pack_dir / "curation.yaml", self.pack_curation)
        release, _ = build_release(self.pack_curation, self.text, "test-v1")
        (self.pack_dir / "release.json").write_text(dump_release(release), encoding="utf-8")
        self.graph_dir = self.root / "graphs" / "ch"
        self.graph_dir.mkdir(parents=True)
        self.graph_path = self.graph_dir / "graph.yaml"
        self.graph_path.write_text(GRAPH.replace("DOC", self.document_id), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def curation(self) -> GraphCuration:
        return load_graph_curation(self.graph_path)

    def derived(self) -> GraphCuration:
        curation = self.curation()
        register = load_place_register(self.root / "config" / "places" / "register.json")
        apply_derivation(curation, derive(curation, self.root, register))
        return curation

    def compile(self, curation: GraphCuration, **options):
        return build_graph(curation, self.text, self.root, places=graph_places(curation, self.graph_path), **options)

    def test_compile_resolves_text_citations_and_validates(self):
        graph, report = self.compile(self.curation())
        self.assertEqual(report["citation_outcomes"], {"same-snapshot": 3})
        self.assertEqual(report["dropped"], [])
        edge = graph.edges[0]
        excerpt = next(e for e in graph.evidence if e.evidence_id == edge.evidence_ids[0]).original_excerpt
        self.assertEqual(excerpt, "Register within 14 days of arrival.\n\nRegister before starting work.")
        self.assertEqual(graph.review_statuses, {"assistant-authored-unreviewed": 3})
        self.assertEqual(graph.institutions[0].institution_id, "ch-sem")

    def test_derive_adds_places_institutions_bridges_and_instances(self):
        curation = self.derived()
        node_ids = {n.node_id for n in curation.nodes}
        self.assertTrue({"place.ch", "place.ch-zh", "institution.ch-sem"} <= node_ids)
        edge_ids = {e.edge_id for e in curation.edges}
        self.assertIn("domain.residence.published_by.institution.ch-sem", edge_ids)
        self.assertIn("role.residents-office.instance.institution.ch-sem", edge_ids)
        self.assertIn("place.ch-zh.part_of.place.ch", edge_ids)
        graph, report = self.compile(curation)
        self.assertEqual(report["dropped"], [])
        self.assertIn("pack", report["citation_outcomes"])
        self.assertTrue(any(e.evidence_id.startswith("graph-test-") for e in graph.evidence))

    def test_derive_is_idempotent_and_never_changes_what_a_person_touched(self):
        curation = self.derived()
        register = load_place_register(self.root / "config" / "places" / "register.json")
        again = apply_derivation(curation, derive(curation, self.root, register))
        self.assertEqual((again.added, again.updated), ([], []))
        node = next(n for n in curation.nodes if n.node_id == "institution.ch-sem")
        node.summary = "Edited by a curator."
        node.provenance.author = "curator"
        third = apply_derivation(curation, derive(curation, self.root, register))
        self.assertIn("institution.ch-sem", third.kept)
        self.assertEqual(next(n for n in curation.nodes if n.node_id == "institution.ch-sem").summary, "Edited by a curator.")

    def test_names_must_occur_in_the_excerpts(self):
        curation = self.curation()
        curation.nodes[1].names = {"de": "Einwohnerkontrolle"}
        with self.assertRaisesRegex(BuildError, "Einwohnerkontrolle"):
            self.compile(curation)

    def test_reviewed_serving_withholds_unreviewed_items_and_their_edges(self):
        curation = self.curation()
        curation.serve = "reviewed"
        curation.nodes[0].provenance.review_status = "human-reviewed"
        curation.nodes[0].provenance.reviewed_by = "Reviewer"
        graph, report = self.compile(curation)
        self.assertEqual([n.node_id for n in graph.nodes], ["domain.residence"])
        self.assertIn("role.residents-office", report["withheld"])
        self.assertEqual(graph.edges, [])

    def test_a_changed_pack_excerpt_drops_the_derived_claim(self):
        curation = self.derived()
        edge = next(e for e in curation.edges if e.relation == "published_by")
        edge.evidence[0].pack.excerpt_sha256 = "0" * 64
        graph, report = self.compile(curation)
        self.assertNotIn(edge.edge_id, {e.edge_id for e in graph.edges})
        self.assertTrue(any(d.get("edge_id") == edge.edge_id for d in report["dropped"]))

    def test_the_pack_build_embeds_the_compiled_graph(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(graph_cli(["derive", str(self.graph_path), "--apply"]), 0)
            self.assertEqual(graph_cli(["compile", str(self.graph_path), "--text", str(self.text)]), 0)
        pack = copy.deepcopy(self.pack_curation)
        pack.knowledge_graph = "../../graphs/ch/graph.json"
        release, report = build_release(pack, self.text, "test-v2",
                                        knowledge_graph=graph_for(pack, self.pack_dir / "curation.yaml"))
        self.assertEqual(validate_release(release, self.text), [])
        self.assertEqual(release.topics[0].graph_nodes, ["domain.residence"])
        self.assertEqual(report["knowledge_graph"]["graph_id"], "ch")
        self.assertEqual(json.loads((self.graph_dir / "graph.json").read_text(encoding="utf-8"))["graph_id"], "ch")

    def test_merge_turns_evidence_strings_into_citations_and_reports_what_it_refuses(self):
        curation = self.curation()
        curation.nodes.append(curation.nodes[1].model_copy(update=dict(
            node_id="role.migration-office", label="Migration office", summary="(to be written)", names={}, evidence=[],
            provenance=curation.nodes[1].provenance.model_copy(update=dict(author="skeleton")))))
        curation.nodes.append(curation.nodes[1].model_copy(update=dict(
            node_id="pitfall.unsupported", kind="pitfall", label="Unsupported", summary="(to be written)", names={},
            evidence=[], provenance=curation.nodes[1].provenance.model_copy(update=dict(author="skeleton")))))
        pack_evidence = load_release(self.pack_dir / "release.json").evidence[0].evidence_id
        proposal = dict(
            nodes=[dict(node_id="role.migration-office", kind="role", label="Migration office", level="cantonal",
                        summary="Issues permits.", names={"en": "arrival"}, evidence=[f"test:{pack_evidence}"]),
                   dict(node_id="pitfall.ghost", kind="pitfall", label="Ghost", summary="x", evidence=["test:e-nowhere"])],
            edges=[dict(from_id="domain.residence", relation="executed_by", to_id="role.migration-office",
                        statement="Permits are issued by the migration office.", evidence=[f"text:{self.document_id}:3-4"]),
                   dict(from_id="role.migration-office", relation="executed_by", to_id="domain.residence",
                        statement="Backwards.", evidence=[f"test:{pack_evidence}"])],
            gaps=["Nothing on approvals."])
        report = merge_proposals(curation, [("graph-reader agent (test)", proposal)], self.root, {self.document_id})
        self.assertEqual(report.nodes_filled, ["role.migration-office"])
        self.assertEqual(report.edges_added, ["domain.residence.executed_by.role.migration-office"])
        self.assertTrue(any("e-nowhere" in reason for reason in report.rejected))
        self.assertTrue(any("cannot link a role to a domain" in reason for reason in report.rejected))
        self.assertEqual(report.unsupported_skeleton, ["pitfall.unsupported"])
        self.assertIn("graph-reader agent (test): Nothing on approvals.", report.gaps)
        filled = next(n for n in curation.nodes if n.node_id == "role.migration-office")
        self.assertEqual(filled.provenance.author, "graph-reader agent (test)")
        self.assertEqual(filled.evidence[0].pack.excerpt_sha256, load_release(self.pack_dir / "release.json").evidence[0].excerpt_sha256)
        graph, compiled = self.compile(curation)
        self.assertIn("domain.residence.executed_by.role.migration-office", {e.edge_id for e in graph.edges})

    def test_merge_folds_a_proposed_institution_onto_the_one_derive_made(self):
        curation = self.derived()
        pack_evidence = load_release(self.pack_dir / "release.json").evidence[0].evidence_id
        proposal = dict(nodes=[dict(node_id="institution.sem-by-reader", kind="institution", label="SEM", level="federal",
                                    place="CH", summary="The migration secretariat.", evidence=[f"test:{pack_evidence}"])],
                        edges=[dict(from_id="domain.residence", relation="approved_by", to_id="institution.sem-by-reader",
                                    statement="The SEM approves.", evidence=[f"test:{pack_evidence}"])])
        report = merge_proposals(curation, [("graph-reader agent (test)", proposal)], self.root, {self.document_id})
        self.assertEqual(report.nodes_folded, ["institution.sem-by-reader -> institution.ch-sem"])
        self.assertEqual(report.edges_added, ["domain.residence.approved_by.institution.ch-sem"])
        self.assertNotIn("institution.sem-by-reader", {n.node_id for n in curation.nodes})

    def test_merge_recognises_a_derived_edge_under_another_identifier(self):
        curation = self.derived()
        derived = next(e for e in curation.edges if e.relation == "instance")
        pack_evidence = load_release(self.pack_dir / "release.json").evidence[0].evidence_id
        proposal = dict(edges=[dict(from_id=derived.from_id, relation="instance", to_id=derived.to_id, place=derived.place,
                                    statement="Restated by a reader.", evidence=[f"test:{pack_evidence}"])])
        report = merge_proposals(curation, [("graph-reader agent (test)", proposal)], self.root, {self.document_id})
        self.assertEqual(report.edges_added, [])
        self.assertEqual(report.kept, [f"{derived.from_id}.instance.{derived.to_id}.{derived.place.lower()}"])
        self.assertEqual(sum(1 for e in curation.edges if e.relation == "instance"), 1)

    def test_embed_puts_the_graph_into_the_current_release(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(graph_cli(["derive", str(self.graph_path), "--apply"]), 0)
            self.assertEqual(graph_cli(["compile", str(self.graph_path), "--text", str(self.text)]), 0)
        text = (self.pack_dir / "curation.yaml").read_text(encoding="utf-8")
        (self.pack_dir / "curation.yaml").write_text(text.replace("title: Test pack", "title: Test pack\nknowledge_graph: ../../graphs/ch/graph.json"),
                                                     encoding="utf-8")
        before = load_release(self.pack_dir / "release.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(graph_cli(["embed", str(self.pack_dir)]), 0)
        after = load_release(self.pack_dir / "release.json")
        self.assertNotEqual(after.manifest.release_id, before.manifest.release_id)
        self.assertEqual(after.facts, before.facts)
        self.assertEqual(after.knowledge_graph.graph_id, "ch")
        self.assertEqual(after.topics[0].graph_nodes, ["domain.residence"])
        self.assertEqual(validate_release(after, self.text), [])

    def test_save_round_trips(self):
        curation = self.derived()
        save_graph_curation(self.graph_path, curation)
        self.assertEqual(load_graph_curation(self.graph_path), curation)

    def test_law_identity_strips_articles_and_keeps_places_of_cantonal_laws(self):
        self.assertEqual(law_identity("AIG, SR 142.20, Art. 12", "federal", "CH").node_id, "law.aig")
        self.assertEqual(law_identity("AIG, SR 142.20, Art. 12", "federal", "CH").number, "SR 142.20")
        self.assertEqual(law_identity("FZA, SR 0.142.112.681, Annex I, Art. 6", "federal", "CH").label,
                         "FZA (SR 0.142.112.681)")
        cantonal = law_identity("Zürcher Steuerbuch 87.3, Rz 24", "cantonal", "CH-ZH")
        self.assertEqual(cantonal.node_id, "law.zuercher-steuerbuch-87-3-ch-zh")
        self.assertEqual(law_identity("RL 144.110", "cantonal", "CH-BE").number, "RL 144.110")


if __name__ == "__main__":
    unittest.main()
