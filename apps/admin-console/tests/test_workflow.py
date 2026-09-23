import json
import unittest

from fastapi.testclient import TestClient

from swisstip.admin_console.app import create_app
from swisstip.admin_console.writes import read_audit
from swisstip.build.curation import load_curation, save_curation
from swisstip.builder.autopilot.models import (Actor, ActorKind, ApprovalGate,
                                               AuthenticationKind, Decision, ReviewMode,
                                               WorkflowState)
from swisstip.builder.autopilot.service import AutopilotService

from support import PackFixture


class WorkflowScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = PackFixture()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.fixture.restore()
        self.app = create_app(self.fixture.root, actor="Anna Meier")
        self.client = TestClient(self.app)
        self.pack = self.app.state.console.pack("test-pack")
        self.service = AutopilotService(self.fixture.root, "test-pack")
        self.coordinator = Actor(kind=ActorKind.COORDINATOR, actor_id="claude-code",
                                 authentication=AuthenticationKind.LOCAL_ASSERTED)

    def pending_scope(self, scope_statement="Self-employment in Zurich",
                      out_of_scope=None):
        out_of_scope = out_of_scope or ["Individual legal advice"]
        workflow = self.service.initialize(scope_statement, ReviewMode.FULL_REVIEW,
                                           self.coordinator)
        drafts = self.fixture.root / ".local" / "test-pack" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        scope = drafts / "scope.md"
        scope.write_text(f"# {scope_statement}\n", encoding="utf-8")
        from swisstip.builder.autopilot.models import PromotionSpec
        return self.service.submit(ApprovalGate.SCOPE, "Two topics and six questions",
                                   {"scope_statement": scope_statement,
                                    "out_of_scope": out_of_scope,
                                    "questions": [f"Question {number}" for number in range(1, 7)]},
                                   self.coordinator,
                                   expected_revision=workflow.revision,
                                   artifacts={"scope-document": scope}, promotions=[
                                       PromotionSpec(artifact="scope-document",
                                                     destination="docs/test-pack-acceptance-questions.md")])

    def test_verified_proposal_is_rendered_and_human_approval_advances(self):
        pending = self.pending_scope()
        page = self.client.get("/packs/test-pack/workflow")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Two topics and six questions", page.text)
        proposal = pending.gates[ApprovalGate.SCOPE].proposal
        response = self.client.post("/packs/test-pack/workflow/decision", data={
            "action": "approve", "expected_revision": pending.revision,
            "proposal_sha256": proposal.sha256, "note": "Scope accepted."},
            follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[:500])
        self.assertEqual(self.service.status().state, WorkflowState.DISCOVERING_SOURCES)
        decision_event = self.service.store.events()[-1]
        self.assertEqual(decision_event.actor.actor_id, "Anna Meier")
        self.assertEqual(decision_event.actor.kind, ActorKind.HUMAN)
        self.assertTrue(any("approve workflow gate scope" in entry["reason"] for entry in read_audit(self.pack)))

    def test_pending_proposal_shows_exact_artifact_hash_preview_and_destination(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                           self.coordinator)
        drafts = self.fixture.root / ".local" / "test-pack" / "drafts"
        drafts.mkdir(parents=True)
        scope = drafts / "scope.md"
        scope.write_text("# Approved scope bytes\n", encoding="utf-8")
        from swisstip.builder.autopilot.models import PromotionSpec
        pending = self.service.submit(
            ApprovalGate.SCOPE, "Scope proposal",
            {"scope_statement": "Self-employment in Zurich",
             "out_of_scope": ["Individual legal advice"],
             "questions": [f"Question {number}" for number in range(1, 7)]}, self.coordinator,
            workflow.revision, artifacts={"scope-document": scope}, promotions=[
                PromotionSpec(artifact="scope-document",
                              destination="docs/test-pack-acceptance-questions.md")])
        page = self.client.get("/packs/test-pack/workflow")
        reference = pending.gates[ApprovalGate.SCOPE].proposal
        self.assertIn("Approved scope bytes", page.text)
        self.assertIn("docs/test-pack-acceptance-questions.md", page.text)
        proposal = self.service.store.read_artifact(reference)
        self.assertTrue(proposal)
        import hashlib
        digest = hashlib.sha256(scope.read_bytes()).hexdigest()
        self.assertIn(digest, page.text)
        self.assertIn(f"/packs/test-pack/workflow/artifacts/{digest}", page.text)

        full = self.client.get(f"/packs/test-pack/workflow/artifacts/{digest}")
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.content, scope.read_bytes())
        self.assertEqual(full.headers["x-content-sha256"], digest)
        self.assertEqual(full.headers["content-type"], "text/plain; charset=utf-8")

        downloaded = self.client.get(f"/packs/test-pack/workflow/artifacts/{digest}?download=true")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, scope.read_bytes())
        self.assertIn("attachment", downloaded.headers["content-disposition"])

    def test_workflow_artifact_route_exposes_only_the_pending_proposal(self):
        pending = self.pending_scope()
        proposal_digest = pending.gates[ApprovalGate.SCOPE].proposal.sha256
        response = self.client.get(f"/packs/test-pack/workflow/artifacts/{proposal_digest}")
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f"/packs/test-pack/workflow/artifacts/{'0' * 64}")
        self.assertEqual(response.status_code, 404)

    def test_pending_proposal_renders_delegated_support_handling(self):
        pending = self.pending_scope()
        proposal_ref = pending.gates[ApprovalGate.SCOPE].proposal
        proposal_path = self.service.store.directory / proposal_ref.path
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        proposal["delegation_uses"] = [{
            "item": {"path": "items/item.json", "sha256": "a" * 64, "bytes": 1},
            "decision": {"path": "decisions/decision.json", "sha256": "b" * 64, "bytes": 1},
            "decision_class": "source-metadata", "target_gate": "catalogue",
            "target_artifact": "sources.json", "member_id": "official-source",
            "member_sha256": "c" * 64, "verdict": "escalate", "handling": "human-review",
            "reason": "Authority wording needs a person.",
        }]
        proposal["gate"] = "catalogue"
        # This test exercises only the template fragment; context construction verifies real artifacts elsewhere.
        from swisstip.builder.autopilot.models import Proposal
        from swisstip.admin_console.app import templates
        rendered = templates.get_template("workflow/status_body.html").render(
            workflow=pending, integrity_error=None, progress=None, gate_counts={}, actor_counts={},
            proposal=Proposal.model_validate(proposal), proposal_artifacts=[], read_only=False,
            pack=self.pack, network_packet=None, attestation_packet=None, events=[])
        self.assertIn("Fast-track support reviews", rendered)
        self.assertIn("official-source", rendered)
        self.assertIn("human-review", rendered)
        self.assertIn("Authority wording needs a person.", rendered)

    def test_stale_proposal_hash_is_refused_without_an_audit_entry(self):
        pending = self.pending_scope()
        before = len(read_audit(self.pack))
        response = self.client.post("/packs/test-pack/workflow/decision", data={
            "action": "approve", "expected_revision": pending.revision,
            "proposal_sha256": "f" * 64, "note": ""})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(read_audit(self.pack)), before)
        self.assertEqual(self.service.status().state, WorkflowState.AWAITING_SCOPE_APPROVAL)

    def test_read_only_mode_registers_no_workflow_write_routes(self):
        app = create_app(self.fixture.root, actor="Anna Meier", read_only=True)
        registered = {(getattr(route, "path", ""), method) for route in app.routes
                      for method in getattr(route, "methods", ())}
        self.assertNotIn(("/packs/{pack}/workflow", "POST"), registered)
        self.assertNotIn(("/packs/{pack}/workflow/decision", "POST"), registered)

    def test_workflow_only_pack_is_discovered(self):
        service = AutopilotService(self.fixture.root, "workflow-only")
        service.initialize("A new topic", ReviewMode.FULL_REVIEW, self.coordinator)
        app = create_app(self.fixture.root, actor="Anna Meier")
        self.assertIn("workflow-only", app.state.console.pack_names())
        page = TestClient(app).get("/packs/workflow-only/workflow")
        self.assertEqual(page.status_code, 200)
        self.assertIn("A new topic", page.text)

    def test_post_a2_page_waits_for_the_plan_without_failing(self):
        scope = self.pending_scope()
        scope = self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE,
                                    scope.gates[ApprovalGate.SCOPE].proposal.sha256, "",
                                    Actor(kind=ActorKind.HUMAN, actor_id="Anna Meier",
                                          authentication=AuthenticationKind.LOCAL_ASSERTED), scope.revision)
        scope = self.service.promote(ApprovalGate.SCOPE, self.coordinator, scope.revision)
        drafts = self.fixture.root / ".local" / "test-pack" / "drafts"
        catalogue = drafts / "sources.json"
        catalogue.write_bytes(self.pack.catalogue_path.read_bytes())
        inventory = drafts / "sources.md"
        inventory.write_text("# Sources\n", encoding="utf-8")
        from swisstip.builder.autopilot.models import PromotionSpec
        pending = self.service.submit(
            ApprovalGate.CATALOGUE, "Catalogue", {}, self.coordinator, scope.revision,
            artifacts={"catalogue": catalogue, "inventory": inventory}, promotions=[
                PromotionSpec(artifact="catalogue", destination="releases/test-pack/sources.json",
                              expected_sha256=self.pack.catalogue.sha256),
                PromotionSpec(artifact="inventory", destination="releases/test-pack/sources.md")])
        approved = self.service.decide(
            ApprovalGate.CATALOGUE, Decision.APPROVE,
            pending.gates[ApprovalGate.CATALOGUE].proposal.sha256, "",
            Actor(kind=ActorKind.HUMAN, actor_id="Anna Meier",
                  authentication=AuthenticationKind.LOCAL_ASSERTED), pending.revision)

        page = self.client.get("/packs/test-pack/workflow")

        self.assertEqual(approved.state, WorkflowState.AWAITING_DOWNLOAD_CONFIRMATION)
        self.assertEqual(page.status_code, 200)
        self.assertIn("has not prepared the acquisition plan yet", page.text)

    def test_governed_fact_confirmation_records_a_workflow_receipt(self):
        current_curation = self.pack.curation.current()
        scope = self.pending_scope(current_curation.scope_statement, current_curation.out_of_scope)
        human = Actor(kind=ActorKind.HUMAN, actor_id="Anna Meier",
                      authentication=AuthenticationKind.LOCAL_ASSERTED)
        scope = self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE,
                                    scope.gates[ApprovalGate.SCOPE].proposal.sha256, "",
                                    human, scope.revision)
        scope = self.service.promote(ApprovalGate.SCOPE, self.coordinator, scope.revision)
        drafting = scope.model_copy(deep=True)
        drafting.state = WorkflowState.GENERATING_KNOWLEDGE
        drafting.revision += 1
        drafting.event_sequence += 1
        event = self.service._event(scope, drafting, "test.phase-entered", self.coordinator,
                                    "Entered A4 drafting for console integration test")
        drafting.last_event_sha256 = event.sha256
        drafting = self.service.store.commit(scope.revision, drafting, event)
        drafts = self.fixture.root / ".local" / "test-pack" / "drafts"
        curation = drafts / "curation.yaml"
        proposed_curation = load_curation(self.pack.curation_path)
        proposed_curation.concepts[0].questions = [f"Question {number}" for number in range(1, 7)]
        save_curation(curation, proposed_curation)
        dispositions = drafts / "curation-coverage.yaml"
        dispositions.write_text(
            "schema_version: swiss-tip-curation-coverage/v1\npack: test-pack\ndispositions: []\n",
            encoding="utf-8")
        from types import SimpleNamespace
        from unittest.mock import patch
        checkpoint = SimpleNamespace(details={"dataset_sha256": "dataset"})
        from swisstip.builder.autopilot.models import PromotionSpec
        with patch.object(self.service, "_latest_checkpoint", return_value=checkpoint), \
                patch.object(self.service, "_dataset_sha256", return_value="dataset"):
            pending = self.service.submit(
                ApprovalGate.KNOWLEDGE_DESIGN, "Knowledge design", {}, self.coordinator,
                drafting.revision, artifacts={"curation": curation, "dispositions": dispositions}, promotions=[
                    PromotionSpec(artifact="curation", destination="releases/test-pack/curation.yaml",
                                  expected_sha256=self.pack.curation.sha256),
                    PromotionSpec(artifact="dispositions",
                                  destination="releases/test-pack/curation-coverage.yaml")])
        approved = self.service.decide(
            ApprovalGate.KNOWLEDGE_DESIGN, Decision.APPROVE,
            pending.gates[ApprovalGate.KNOWLEDGE_DESIGN].proposal.sha256, "", human, pending.revision)
        awaiting = self.service.promote(ApprovalGate.KNOWLEDGE_DESIGN, self.coordinator,
                                        approved.revision)

        response = self.client.post(
            "/packs/test-pack/review/registration-deadline-1/confirm",
            data={"sha256": self.pack.curation.sha256}, follow_redirects=False)

        self.assertEqual(response.status_code, 303, response.text[:500])
        self.assertEqual(self.service.store.events()[-1].kind, "fact-review.recorded")


if __name__ == "__main__":
    unittest.main()
