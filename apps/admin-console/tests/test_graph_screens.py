"""The knowledge graph screens: overview, explorer data, edit, review, refusal, Derive preview and compile."""

import time
import unittest

import yaml
from fastapi.testclient import TestClient

from swisstip.admin_console.app import create_app
from swisstip.admin_console.graph_data import GraphData, write_graph
from swisstip.admin_console.writes import WriteRefused, read_audit
from swisstip.build.graph_curation import load_graph_curation
from swisstip.core.release import load_release

from support import PackFixture


def graph_yaml(pack: str, evidence_id: str, excerpt_sha256: str) -> str:
    citation = [dict(pack=dict(pack=pack, evidence_id=evidence_id, excerpt_sha256=excerpt_sha256))]
    unreviewed = dict(kind="curated-statement", review_status="assistant-authored-unreviewed", author="graph-reader agent (test)")
    return yaml.safe_dump(dict(
        schema_version="swiss-tip-graph-curation/v1", graph="ch", title="Switzerland", packs=[pack],
        nodes=[dict(node_id="domain.registration", kind="domain", label="Registration", summary="Registering on arrival.",
                    keywords=["register", "arrival"], provenance=unreviewed, evidence=citation),
               dict(node_id="role.residents-office", kind="role", label="Residents' office", level="municipal",
                    summary="Where a person registers.", provenance=unreviewed, evidence=citation)],
        edges=[dict(edge_id="domain.registration.first_contact.role.residents-office", from_id="domain.registration",
                    relation="first_contact", to_id="role.residents-office", statement="Register with the residents' office.",
                    provenance=unreviewed, evidence=citation)]), sort_keys=False)


class GraphScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = PackFixture()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.fixture.restore()
        root = self.fixture.root
        evidence = load_release(root / "releases" / "test-pack" / "release.json").evidence[0]
        folder = root / "graphs" / "ch"
        folder.mkdir(parents=True)
        (folder / "graph.yaml").write_text(graph_yaml("test-pack", evidence.evidence_id, evidence.excerpt_sha256),
                                           encoding="utf-8", newline="\n")
        self.app = create_app(root, actor="tester")
        self.client = TestClient(self.app)
        self.graph: GraphData = self.app.state.graphs.graph("ch")

    def curation(self):
        return load_graph_curation(self.graph.curation_path)

    def test_the_home_page_and_the_graph_screens_render(self):
        self.assertIn("Knowledge graphs", self.client.get("/").text)
        for path in ("/graphs/ch", "/graphs/ch/explore", "/graphs/ch/items", "/graphs/ch/review",
                     "/graphs/ch/items/role.residents-office", "/graphs/ch/items/domain.registration.first_contact.role.residents-office"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        data = self.client.get("/graphs/ch/explore.json").json()
        self.assertEqual({n["id"] for n in data["nodes"]}, {"domain.registration", "role.residents-office"})
        self.assertEqual(data["edges"][0]["relation"], "first_contact")
        self.assertEqual(self.client.get("/graphs/nowhere").status_code, 404)

    def test_an_edit_takes_the_item_over_and_is_audited(self):
        response = self.client.post("/graphs/ch/items/role.residents-office/save",
                                    data=dict(sha256=self.graph.curation.sha256, label="Residents' office",
                                              summary="The commune's registration office.", names="", keywords="Einwohnerkontrolle",
                                              level="municipal", place=""), follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        node = next(n for n in self.curation().nodes if n.node_id == "role.residents-office")
        self.assertEqual((node.summary, node.provenance.author), ("The commune's registration office.", "tester"))
        self.assertEqual(read_audit(self.graph)[0]["reason"], "edit role.residents-office")

    def test_a_write_that_would_not_compile_is_refused(self):
        before = self.graph.curation_path.read_bytes()

        def invent_a_name(curation):
            curation.nodes[1].names = {"de": "Einwohnerkontrolle"}
        with self.assertRaises(WriteRefused):
            write_graph(self.graph, invent_a_name, "tester", "invent a name")
        self.assertEqual(self.graph.curation_path.read_bytes(), before)

    def test_review_confirms_with_the_reviewer_and_rejects_with_a_note(self):
        self.client.post("/graphs/ch/items/domain.registration/review",
                         data=dict(sha256=self.graph.curation.sha256, action="confirm"), follow_redirects=False)
        node = next(n for n in self.curation().nodes if n.node_id == "domain.registration")
        self.assertEqual((node.provenance.review_status, node.provenance.reviewed_by), ("human-reviewed", "tester"))
        refused = self.client.post("/graphs/ch/items/role.residents-office/review",
                                   data=dict(sha256=self.graph.curation.sha256, action="reject"))
        self.assertEqual(refused.status_code, 422)
        self.client.post("/graphs/ch/items/role.residents-office/review",
                         data=dict(sha256=self.graph.curation.sha256, action="reject", note="not what the page says"),
                         follow_redirects=False)
        curation = self.curation()
        self.assertEqual([n.node_id for n in curation.nodes], ["domain.registration"])
        self.assertEqual(curation.edges, [])
        self.assertTrue((self.graph.console_dir / "rejected.jsonl").is_file())

    def test_bulk_review_can_cover_every_item_matching_the_filter(self):
        page = self.client.get("/graphs/ch/review").text
        self.assertIn("Select all shown", page)
        self.client.post("/graphs/ch/review/bulk", data=dict(sha256=self.graph.curation.sha256, action="confirm",
                                                              all_matching="1", filter_kind="edge"), follow_redirects=False)
        curation = self.curation()
        self.assertEqual(curation.edges[0].provenance.review_status, "human-reviewed")
        self.assertTrue(all(n.provenance.review_status != "human-reviewed" for n in curation.nodes))
        self.assertIn("bulk review of 1 items", curation.edges[0].provenance.notes[-1])

    def test_derive_is_previewed_and_compile_runs_as_a_job(self):
        preview = self.client.post("/graphs/ch/derive", data={})
        self.assertIn("Derive preview", preview.text)
        response = self.client.post("/graphs/ch/compile", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        job = next(iter(self.app.state.jobs.jobs.values()))
        self.app.state.jobs.wait(job, timeout=60)
        for _ in range(50):
            if job.status != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job.status, "finished", job.error)
        self.assertTrue(self.graph.compiled_path.is_file())
        self.assertIn("compiled", self.client.get("/graphs/ch").text)


if __name__ == "__main__":
    unittest.main()
