import json
import tempfile
import unittest
from pathlib import Path

from swisstip.builder.cli import main as cli
from swisstip.builder.graph_pipeline import GraphPipeline
from swisstip.builder.pipeline import Pipeline
from swisstip.core.release import load_release

from test_pipeline import CURATION, make_root

GRAPH = """
schema_version: swiss-tip-graph-curation/v1
graph: ch
title: Switzerland
packs: [test]
role_rules:
  role.residents-office: ["catalogue-*"]
nodes:
  - node_id: domain.residence
    kind: domain
    label: Residence and registration
    summary: Registering on arrival.
    keywords: [register, arrival]
    provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: agent}
    evidence:
      - pack: {pack: test, evidence_id: EVIDENCE, excerpt_sha256: HASH}
  - node_id: role.residents-office
    kind: role
    label: Residents' office
    level: municipal
    summary: Where a person registers on arrival.
    provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: agent}
    evidence:
      - pack: {pack: test, evidence_id: EVIDENCE, excerpt_sha256: HASH}
edges:
  - edge_id: residence.first_contact.residents-office
    from_id: domain.residence
    relation: first_contact
    to_id: role.residents-office
    statement: A person registers with the residents' office within 14 days of arrival.
    provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: agent}
    evidence:
      - pack: {pack: test, evidence_id: EVIDENCE, excerpt_sha256: HASH}
"""
CHECKS = """
schema_version: swiss-tip-graph-checks/v1
graph: ch
cases:
  - case_id: registration-without-place
    question: When do I register my arrival?
    expect_nodes: [domain.residence, role.residents-office]
    expect_place_dependence: municipality
"""


class GraphPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self.temporary.name))
        Pipeline(self.root, "test", log=lambda *a: None).run_stages(until="validate-text")
        index = json.loads((self.root / ".local/test/text/index.json").read_text(encoding="utf-8"))
        curation = CURATION.replace("DOC", index[0]["document_id"]).replace(
            "description: Permits and registration.}", "description: Permits and registration., graph_nodes: [domain.residence]}")
        self.pack_curation = self.root / "releases/test/curation.yaml"
        self.pack_curation.write_text(curation, encoding="utf-8")
        Pipeline(self.root, "test", log=lambda *a: None).run_stages(start="build", until="validate-release")
        evidence = load_release(self.root / "releases/test/release.json").evidence[0]
        self.graph_dir = self.root / "graphs" / "ch"
        self.graph_dir.mkdir(parents=True)
        (self.graph_dir / "graph.yaml").write_text(
            GRAPH.replace("EVIDENCE", evidence.evidence_id).replace("HASH", evidence.excerpt_sha256), encoding="utf-8")
        (self.graph_dir / "checks.yaml").write_text(CHECKS, encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def run_graph(self, **options) -> dict:
        report = GraphPipeline(self.root, "ch", log=lambda *a: None, **options).run_stages("derive", "check")
        return {s["stage"]: s for s in report["stages"]}

    def test_derive_compile_and_check_then_skip_when_unchanged(self):
        first = self.run_graph(apply_derive=True)
        self.assertEqual({name: first[name]["status"] for name in ("derive", "compile", "check")},
                         {"derive": "ran", "compile": "ran", "check": "ran"})
        self.assertTrue(first["derive"]["applied"])
        self.assertGreater(first["derive"]["added"], 0)
        self.assertTrue((self.graph_dir / "graph.json").is_file())
        self.assertEqual(first["check"]["passed"], 1)
        second = self.run_graph()
        self.assertEqual(second["compile"]["status"], "skipped")
        self.assertEqual(second["derive"]["added"], 0)
        self.assertTrue((self.graph_dir / "pipeline-report.json").is_file())

    def test_a_failing_check_fails_the_pipeline(self):
        (self.graph_dir / "checks.yaml").write_text(CHECKS.replace("expect_place_dependence: municipality",
                                                                   "expect_place_dependence: canton"), encoding="utf-8")
        stages = self.run_graph()
        self.assertEqual(stages["check"]["status"], "failed")

    def test_the_pack_build_embeds_the_graph_and_rebuilds_when_it_changes(self):
        self.run_graph(apply_derive=True)
        text = self.pack_curation.read_text(encoding="utf-8")
        self.pack_curation.write_text(text.replace("title: Test pack", "title: Test pack\nknowledge_graph: ../../graphs/ch/graph.json"),
                                      encoding="utf-8")
        report = Pipeline(self.root, "test", log=lambda *a: None).run_stages(start="build", until="validate-release")
        self.assertEqual({s["stage"]: s["status"] for s in report["stages"]}["build"], "ran")
        release = load_release(self.root / "releases/test/release.json")
        self.assertEqual(release.knowledge_graph.graph_id, "ch")
        self.assertEqual(release.topics[0].graph_nodes, ["domain.residence"])
        again = Pipeline(self.root, "test", log=lambda *a: None).run_stages(start="build", until="build")
        self.assertEqual({s["stage"]: s["status"] for s in again["stages"]}["build"], "skipped")

    def test_the_cli_builds_a_graph(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            code = cli(["ch", "--graph", "--packs-dir", str(self.root), "--from", "derive", "--apply-derive"])
        self.assertEqual(code, 0)
        self.assertTrue((self.graph_dir / "checks-report.json").is_file())


if __name__ == "__main__":
    unittest.main()
