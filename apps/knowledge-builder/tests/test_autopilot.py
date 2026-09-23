import json
import io
import hashlib
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from swisstip.builder.autopilot.models import (Actor, ActorKind, ApprovalGate, AuthenticationKind,
                                               Decision, DecisionAuthority, DecisionClass,
                                               DelegationHandling, DelegationVerdict, Proposal, PromotionReceipt,
                                               ReviewMode, PromotionSpec, WorkflowState)
from swisstip.builder.autopilot.cli import main as cli_main
from swisstip.builder.autopilot.service import AutopilotService
from swisstip.builder.autopilot.store import WorkflowConflict, canonical_json
from swisstip.build.curation import load_curation, save_curation
from swisstip.ingestion import gap_report
from swisstip.ingestion.review_decisions import REVIEW_SCHEMA, observation_fingerprint
from swisstip.ingestion.download_cli import build_plan


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.coordinator = Actor(kind=ActorKind.COORDINATOR, actor_id="claude-code",
                                 authentication=AuthenticationKind.LOCAL_ASSERTED)
        self.human = Actor(kind=ActorKind.HUMAN, actor_id="Anna Meier",
                           authentication=AuthenticationKind.LOCAL_ASSERTED)
        self.service = AutopilotService(self.root, "self-employed-zurich")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def scope_details(questions=6):
        return {"scope_statement": "Self-employment in Zurich",
                "out_of_scope": ["Individual legal advice"],
                "questions": [f"Question {number}" for number in range(1, questions + 1)]}

    @staticmethod
    def catalogue_data(source_id="official-topic", title="Official topic",
                       notes="Official scope source"):
        source = {
            "definition": {"source_id": source_id, "start_url": "https://official.example/topic",
                           "allowed_hosts": ["official.example"], "allowed_path_prefixes": ["/topic"],
                           "canonical_authority": "Official Authority", "jurisdiction": "CH", "language": "de"},
            "title": title, "authority_level": "federal", "source_kind": "official_guidance",
            "priority": "P0", "topic_hints": ["self-employment"],
            "discovery": {"method": "official_page_link", "reference_url": "https://official.example/topic",
                          "located_on": "2026-09-06"},
            "scan_status": "ready", "notes": notes,
        }
        return {
            "schema_version": "source-catalog/v1", "artifact_id": "self-employed-zurich-sources",
            "version": "draft-1", "knowledge_space_id": "self-employed-zurich", "title": "Sources",
            "status": "SOURCES_ONLY",
            "scope": {"country_code": "CH", "canton_codes": ["CH-ZH"], "description": "", "exclusions": []},
            "planning_topics": [{"topic_id": "self-employment", "label": "Self-employment"}],
            "language_discovery": {"preferred_seed_language": "de"}, "crawl_profiles": {},
            "scan_sets": {"all": [source_id]}, "sources": [source],
        }

    def approved_scope(self, initialized, questions=6):
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        scope_document = drafts / "scope.md"
        scope_document.write_text("# Self-employment in Zurich\n", encoding="utf-8")
        pending = self.service.submit(
            ApprovalGate.SCOPE, "Approved scope", self.scope_details(questions), self.coordinator,
            initialized.revision, artifacts={"scope-document": scope_document}, promotions=[
                PromotionSpec(artifact="scope-document",
                              destination="docs/self-employed-zurich-acceptance-questions.md")])
        approved = self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE,
                                       pending.gates[ApprovalGate.SCOPE].proposal.sha256, "",
                                       self.human, pending.revision)
        return self.service.promote(ApprovalGate.SCOPE, self.coordinator, approved.revision)

    def approved_catalogue(self, scope):
        run = self.root / ".local" / "self-employed-zurich"
        drafts = run / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        catalogue = drafts / "sources.json"
        catalogue.write_text(json.dumps(self.catalogue_data(), indent=2) + "\n", encoding="utf-8")
        inventory = drafts / "sources.md"
        inventory.write_text("# Sources\n", encoding="utf-8")
        pending = self.service.submit(
            ApprovalGate.CATALOGUE, "One approved source", {"sources": 1}, self.coordinator,
            scope.revision, artifacts={"catalogue": catalogue, "inventory": inventory}, promotions=[
                PromotionSpec(artifact="catalogue", destination="releases/self-employed-zurich/sources.json"),
                PromotionSpec(artifact="inventory", destination="releases/self-employed-zurich/sources.md")])
        approved = self.service.decide(ApprovalGate.CATALOGUE, Decision.APPROVE,
                                       pending.gates[ApprovalGate.CATALOGUE].proposal.sha256, "",
                                       self.human, pending.revision)
        return self.service.promote(ApprovalGate.CATALOGUE, self.coordinator, approved.revision)

    def write_plan(self, *, workers=4):
        run = self.root / ".local" / "self-employed-zurich"
        pack = self.root / "releases" / "self-employed-zurich"
        plan = build_plan(pack / "sources.json", pack / "sources.md", scan_set=None,
                          source_ids=None, workers=workers)
        (run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (run / "plugin-plan.json").write_text(json.dumps({"enabled": [], "sources": []}), encoding="utf-8")
        return plan

    def awaiting_fact_review(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        drafting = scope.model_copy(deep=True)
        drafting.state = WorkflowState.GENERATING_KNOWLEDGE
        drafting.revision += 1
        drafting.event_sequence += 1
        event = self.service._event(scope, drafting, "test.phase-entered", self.coordinator,
                                    "Entered A4 drafting for review authority test")
        drafting.last_event_sha256 = event.sha256
        drafting = self.service.store.commit(scope.revision, drafting, event)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        curation = drafts / "curation.yaml"
        curation.write_text("""schema_version: swiss-tip-curation/v1
pack: self-employed-zurich
title: Self-employment
scope_statement: Self-employment in Zurich
out_of_scope: [Individual legal advice]
out_of_scope_response: Say that this is outside the pack.
coverage_policy: enforce
topics:
  - {topic_id: start, title: Start, description: Starting a business.}
concepts:
  - concept_id: registration
    topic_id: start
    label: Registration
    description: Registration requirements.
    questions: [Question 1, Question 2, Question 3, Question 4, Question 5, Question 6]
    facts:
      - fact_id: registration-1
        statement: Register with the authority.
        jurisdiction: CH-ZH
        provenance: {kind: curated-statement, review_status: human-reviewed, author: assistant, reviewed_by: Anna Meier}
        evidence:
          - {document_id: doc-one, first_block: 1, last_block: 1}
""", encoding="utf-8")
        dispositions = drafts / "curation-coverage.yaml"
        dispositions.write_text(
            "schema_version: swiss-tip-curation-coverage/v1\n"
            "pack: self-employed-zurich\n"
            "dispositions: []\n", encoding="utf-8")
        checkpoint = SimpleNamespace(details={"dataset_sha256": "dataset"})
        with patch.object(self.service, "_latest_checkpoint", return_value=checkpoint), \
                patch.object(self.service, "_dataset_sha256", return_value="dataset"):
            pending = self.service.submit(
                ApprovalGate.KNOWLEDGE_DESIGN, "Knowledge design", {}, self.coordinator,
                drafting.revision, artifacts={"curation": curation, "dispositions": dispositions}, promotions=[
                    PromotionSpec(artifact="curation", destination="releases/self-employed-zurich/curation.yaml"),
                    PromotionSpec(artifact="dispositions",
                                  destination="releases/self-employed-zurich/curation-coverage.yaml")])
        approved = self.service.decide(
            ApprovalGate.KNOWLEDGE_DESIGN, Decision.APPROVE,
            pending.gates[ApprovalGate.KNOWLEDGE_DESIGN].proposal.sha256, "", self.human,
            pending.revision)
        return self.service.promote(ApprovalGate.KNOWLEDGE_DESIGN, self.coordinator, approved.revision)

    def test_scope_proposal_is_hash_bound_and_advances_once(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        self.assertEqual(initialized.state, WorkflowState.DRAFTING_SCOPE)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True)
        scope_document = drafts / "scope.md"
        scope_document.write_text("# Scope\n", encoding="utf-8")
        pending = self.service.submit(ApprovalGate.SCOPE, "Three topics and eight questions",
                                      self.scope_details(8), self.coordinator,
                                      expected_revision=initialized.revision,
                                      artifacts={"scope-document": scope_document}, promotions=[
                                          PromotionSpec(artifact="scope-document",
                                                        destination="docs/self-employed-zurich-acceptance-questions.md")])
        self.assertEqual(pending.state, WorkflowState.AWAITING_SCOPE_APPROVAL)
        proposal = pending.gates[ApprovalGate.SCOPE].proposal
        self.assertIsNotNone(proposal)
        proposal_record = Proposal.model_validate_json(self.service.store.read_artifact(proposal))
        self.assertEqual(proposal_record.input_sha256s["workflow-policy"], initialized.policy.sha256)
        self.assertEqual(proposal_record.details["workflow_policy"]["network_budget"]["max_requests"], 100)
        with self.assertRaises(WorkflowConflict):
            self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE, "f" * 64, "", self.human,
                                expected_revision=pending.revision)
        approved = self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE, proposal.sha256, "",
                                       self.human, expected_revision=pending.revision)
        self.assertEqual(approved.state, WorkflowState.DISCOVERING_SOURCES)
        self.assertEqual(approved.revision, 3)
        self.assertEqual(len(self.service.store.events()), 3)
        with self.assertRaises(WorkflowConflict):
            self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE, proposal.sha256, "", self.human,
                                expected_revision=pending.revision)

    def test_fast_track_cannot_weaken_human_only_decisions(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FAST_TRACK,
                                           self.coordinator, delegation_profile="frontier-review",
                                           delegation_model="claude-frontier",
                                           delegation_prompt_sha256="a" * 64,
                                           delegation_response_schema_sha256="b" * 64)
        policy = self.service.store.load_policy()
        self.assertEqual(workflow.review_mode, ReviewMode.FAST_TRACK)
        self.assertEqual(policy.routing[DecisionClass.SCOPE], DecisionAuthority.HUMAN)
        self.assertEqual(policy.routing[DecisionClass.SOURCE_METADATA], DecisionAuthority.FRONTIER_MODEL)

    def test_duplicate_initialization_preserves_policy(self):
        first = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                        self.coordinator)
        before = self.service.store.policy_path.read_bytes()
        with self.assertRaises(WorkflowConflict):
            self.service.initialize("A different topic", ReviewMode.FAST_TRACK, self.coordinator,
                                    delegation_profile="frontier-review", delegation_model="claude-frontier",
                                    delegation_prompt_sha256="a" * 64,
                                    delegation_response_schema_sha256="b" * 64)
        self.assertEqual(self.service.store.policy_path.read_bytes(), before)
        self.assertEqual(self.service.status(), first)

    def test_tampered_proposal_cannot_be_approved(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True)
        scope_document = drafts / "scope.md"
        scope_document.write_text("# Scope\n", encoding="utf-8")
        pending = self.service.submit(ApprovalGate.SCOPE, "One topic", self.scope_details(1), self.coordinator,
                                      expected_revision=initialized.revision,
                                      artifacts={"scope-document": scope_document}, promotions=[
                                          PromotionSpec(artifact="scope-document",
                                                        destination="docs/self-employed-zurich-acceptance-questions.md")])
        proposal = pending.gates[ApprovalGate.SCOPE].proposal
        path = self.service.store.directory / proposal.path
        path.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(WorkflowConflict):
            self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE, proposal.sha256, "", self.human,
                                expected_revision=pending.revision)

    def test_stale_submission_leaves_no_artifact(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        with self.assertRaises(WorkflowConflict):
            self.service.submit(ApprovalGate.SCOPE, "One topic", self.scope_details(1), self.coordinator,
                                expected_revision=initialized.revision + 1)
        proposals = self.service.store.directory / "proposals"
        self.assertFalse(proposals.exists())

    def test_tampered_workflow_snapshot_fails_verification(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                           self.coordinator)
        data = workflow.model_dump(mode="json")
        data["topic"] = "A changed topic"
        self.service.store.workflow_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(WorkflowConflict):
            self.service.status()

    def test_only_an_approved_snapshotted_artifact_is_promoted(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        scope = drafts / "scope.md"
        scope.write_text("# Scope\n", encoding="utf-8")
        pending = self.service.submit(
            ApprovalGate.SCOPE, "Scope proposal", self.scope_details(), self.coordinator,
            initialized.revision, artifacts={"scope-document": scope},
            promotions=[PromotionSpec(artifact="scope-document",
                                      destination="docs/self-employed-zurich-acceptance-questions.md")])
        with self.assertRaises(ValueError):
            self.service.promote(ApprovalGate.SCOPE, self.coordinator, pending.revision)
        approved = self.service.decide(ApprovalGate.SCOPE, Decision.APPROVE,
                                       pending.gates[ApprovalGate.SCOPE].proposal.sha256, "",
                                       self.human, pending.revision)
        promoted = self.service.promote(ApprovalGate.SCOPE, self.coordinator, approved.revision)
        self.assertEqual((self.root / "docs" / "self-employed-zurich-acceptance-questions.md").read_text(),
                         "# Scope\n")
        self.assertEqual(promoted.state, WorkflowState.DISCOVERING_SOURCES)
        with self.assertRaises(ValueError):
            self.service.promote(ApprovalGate.SCOPE, self.coordinator, promoted.revision)

    def test_a4_allows_reviewed_curation_changes_but_freezes_coverage_dispositions(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        drafting = scope.model_copy(deep=True)
        drafting.state = WorkflowState.GENERATING_KNOWLEDGE
        drafting.revision += 1
        drafting.event_sequence += 1
        event = self.service._event(scope, drafting, "test.phase-entered", self.coordinator,
                                    "Entered A4 drafting for promotion integrity test")
        drafting.last_event_sha256 = event.sha256
        drafting = self.service.store.commit(scope.revision, drafting, event)

        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        curation = drafts / "curation.yaml"
        curation.write_text("approved curation\n", encoding="utf-8")
        dispositions = drafts / "curation-coverage.yaml"
        dispositions.write_text(
            "schema_version: swiss-tip-curation-coverage/v1\n"
            "pack: self-employed-zurich\n"
            "dispositions: []\n", encoding="utf-8")
        promotions = [
            PromotionSpec(artifact="curation", destination="releases/self-employed-zurich/curation.yaml"),
            PromotionSpec(artifact="dispositions",
                          destination="releases/self-employed-zurich/curation-coverage.yaml"),
        ]
        approved_scope = SimpleNamespace(
            pack="self-employed-zurich", scope_statement="Self-employment in Zurich",
            out_of_scope=["Individual legal advice"],
            concepts=[SimpleNamespace(questions=self.scope_details()["questions"])])
        checkpoint = SimpleNamespace(details={"dataset_sha256": "dataset"})
        with patch.object(self.service, "_latest_checkpoint", return_value=checkpoint), \
                patch.object(self.service, "_dataset_sha256", return_value="dataset"), \
                patch("swisstip.builder.autopilot.service.load_curation", return_value=approved_scope):
            pending = self.service.submit(
                ApprovalGate.KNOWLEDGE_DESIGN, "Knowledge design", {}, self.coordinator,
                drafting.revision, artifacts={"curation": curation, "dispositions": dispositions},
                promotions=promotions)
        approved = self.service.decide(
            ApprovalGate.KNOWLEDGE_DESIGN, Decision.APPROVE,
            pending.gates[ApprovalGate.KNOWLEDGE_DESIGN].proposal.sha256, "", self.human,
            pending.revision)
        promoted = self.service.promote(ApprovalGate.KNOWLEDGE_DESIGN, self.coordinator,
                                        approved.revision)
        pack = self.root / "releases" / "self-employed-zurich"
        (pack / "curation.yaml").write_text("human-reviewed curation\n", encoding="utf-8")
        self.service._require_promotion(
            promoted, ApprovalGate.KNOWLEDGE_DESIGN,
            mutable_destinations={"releases/self-employed-zurich/curation.yaml"})

        (pack / "curation-coverage.yaml").write_text("changed dispositions\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowConflict, "curation-coverage.yaml"):
            self.service._require_promotion(
                promoted, ApprovalGate.KNOWLEDGE_DESIGN,
                mutable_destinations={"releases/self-employed-zurich/curation.yaml"})

    def test_fact_review_requires_an_exact_human_receipt_and_approved_structure(self):
        awaiting = self.awaiting_fact_review()
        with self.assertRaisesRegex(ValueError, "no current human-review receipt"):
            self.service.complete_fact_review(self.human, awaiting.revision)

        reviewed = self.service.record_fact_review(["registration-1"], self.human, awaiting.revision)
        self.assertEqual(reviewed.state, WorkflowState.AWAITING_FACT_REVIEW)
        self.assertEqual(self.service.store.events()[-1].kind, "fact-review.recorded")
        curation_path = self.root / "releases/self-employed-zurich/curation.yaml"
        curation = load_curation(curation_path)
        curation.concepts[0].facts[0].statement = "Register with a different authority."
        save_curation(curation_path, curation)
        with self.assertRaisesRegex(ValueError, "no current human-review receipt"):
            self.service.complete_fact_review(self.human, reviewed.revision)

    def test_fact_review_refuses_scope_or_concept_drift_after_a4(self):
        awaiting = self.awaiting_fact_review()
        reviewed = self.service.record_fact_review(["registration-1"], self.human, awaiting.revision)
        curation_path = self.root / "releases/self-employed-zurich/curation.yaml"
        curation = load_curation(curation_path)
        curation.scope_statement = "All business law in Switzerland"
        save_curation(curation_path, curation)
        with self.assertRaisesRegex(WorkflowConflict, "structure outside facts"):
            self.service.complete_fact_review(self.human, reviewed.revision)

    def test_a4_refuses_another_pack_or_missing_a1_questions(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized, questions=1)
        drafting = scope.model_copy(deep=True)
        drafting.state = WorkflowState.GENERATING_KNOWLEDGE
        drafting.revision += 1
        drafting.event_sequence += 1
        event = self.service._event(scope, drafting, "test.phase-entered", self.coordinator,
                                    "Entered A4 drafting for continuity test")
        drafting.last_event_sha256 = event.sha256
        drafting = self.service.store.commit(scope.revision, drafting, event)
        drafts = self.root / ".local/self-employed-zurich/drafts"
        dispositions = drafts / "curation-coverage.yaml"
        dispositions.write_text(
            "schema_version: swiss-tip-curation-coverage/v1\npack: self-employed-zurich\ndispositions: []\n",
            encoding="utf-8")
        curation = drafts / "curation.yaml"
        curation.write_text("""schema_version: swiss-tip-curation/v1
pack: other-pack
title: Test
scope_statement: Self-employment in Zurich
out_of_scope: [Individual legal advice]
out_of_scope_response: Say so.
topics: [{topic_id: start, title: Start, description: Start.}]
concepts:
  - concept_id: registration
    topic_id: start
    label: Registration
    description: Registration.
    facts:
      - fact_id: registration-1
        statement: Register.
        provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: assistant}
        evidence: [{document_id: doc-one, first_block: 1}]
""", encoding="utf-8")
        checkpoint = SimpleNamespace(details={"dataset_sha256": "dataset"})
        promotions = [
            PromotionSpec(artifact="curation", destination="releases/self-employed-zurich/curation.yaml"),
            PromotionSpec(artifact="dispositions",
                          destination="releases/self-employed-zurich/curation-coverage.yaml")]
        with patch.object(self.service, "_latest_checkpoint", return_value=checkpoint), \
                patch.object(self.service, "_dataset_sha256", return_value="dataset"):
            with self.assertRaisesRegex(ValueError, "other-pack"):
                self.service.submit(ApprovalGate.KNOWLEDGE_DESIGN, "Wrong pack", {}, self.coordinator,
                                    drafting.revision,
                                    artifacts={"curation": curation, "dispositions": dispositions},
                                    promotions=promotions)
            curation.write_text(curation.read_text(encoding="utf-8").replace("pack: other-pack",
                                                                             "pack: self-employed-zurich"),
                                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "omits approved A1 questions"):
                self.service.submit(ApprovalGate.KNOWLEDGE_DESIGN, "Missing question", {}, self.coordinator,
                                    drafting.revision,
                                    artifacts={"curation": curation, "dispositions": dispositions},
                                    promotions=promotions)

    def test_proposal_artifacts_outside_drafts_are_refused(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        outside = self.root / "outside.md"
        outside.write_text("not a draft", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.submit(ApprovalGate.SCOPE, "Scope", self.scope_details(), self.coordinator,
                                initialized.revision, artifacts={"scope-document": outside})

    def test_oversized_proposal_artifact_is_refused_before_reading(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True)
        scope = drafts / "scope.md"
        with scope.open("wb") as handle:
            handle.seek(32 * 1024 * 1024)
            handle.write(b"x")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.service.submit(
                ApprovalGate.SCOPE, "Oversized scope", self.scope_details(), self.coordinator,
                initialized.revision, artifacts={"scope-document": scope}, promotions=[
                    PromotionSpec(artifact="scope-document",
                                  destination="docs/self-employed-zurich-acceptance-questions.md")])

    def interrupted_scope_promotion(self, *, append_event=False, destination_name=None):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        scope = drafts / "scope.md"
        scope.write_bytes(b"new bytes")
        pending = self.service.submit(
            ApprovalGate.SCOPE, "Scope proposal", self.scope_details(), self.coordinator,
            initialized.revision, artifacts={"scope-document": scope}, promotions=[
                PromotionSpec(artifact="scope-document",
                              destination="docs/self-employed-zurich-acceptance-questions.md")])
        current = self.service.decide(
            ApprovalGate.SCOPE, Decision.APPROVE,
            pending.gates[ApprovalGate.SCOPE].proposal.sha256, "", self.human, pending.revision)
        proposal_ref = current.gates[ApprovalGate.SCOPE].proposal
        proposal = Proposal.model_validate_json(self.service.store.read_artifact(proposal_ref))
        destination_relative = destination_name or "docs/self-employed-zurich-acceptance-questions.md"
        source = proposal.artifacts["scope-document"]
        receipt = PromotionReceipt(
            receipt_id="interrupted", workflow_id=current.workflow_id, gate=ApprovalGate.SCOPE,
            proposal_sha256=proposal_ref.sha256, outputs={destination_relative: source.sha256},
            promoted_at=current.updated_at, promoted_by=self.coordinator)
        receipt_relative = Path("promotions/scope-interrupted.json")
        receipt_ref, receipt_data = self.service.store.reference(
            receipt_relative, receipt, schema_version=receipt.schema_version)
        receipt_path = self.service.store.directory / receipt_relative
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(receipt_data)
        updated = current.model_copy(deep=True)
        updated.revision += 1
        updated.event_sequence += 1
        event = self.service._event(
            current, updated, "proposal.promoted", self.coordinator, "Interrupted promotion",
            gate=ApprovalGate.SCOPE, artifacts=[receipt_ref],
            metrics={"proposal_sha256": proposal_ref.sha256})
        updated.last_event_sha256 = event.sha256
        event_bytes_before = self.service.store.events_path.stat().st_size
        if append_event:
            with self.service.store.events_path.open("ab") as stream:
                stream.write(canonical_json(event))

        destination = self.root / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"new bytes")
        transaction = self.service.store.directory / "transactions" / "interrupted"
        transaction.mkdir(parents=True)
        backup = transaction / "backup-001.bin"
        backup.write_bytes(b"old bytes")
        marker = self.service.store.directory.parent / ".pack-transaction.json"
        marker.write_text(json.dumps({
            "schema_version": "swisstip.pack-transaction/v2",
            "transaction_dir": transaction.relative_to(self.service.store.directory.parent).as_posix(),
            "committed_event_sha256": event.sha256,
            "previous_event_sha256": current.last_event_sha256,
            "event_bytes_before": event_bytes_before,
            "event": event.model_dump(mode="json", exclude_none=True),
            "proposal": proposal_ref.model_dump(mode="json", exclude_none=True),
            "prepared_artifacts": [receipt_relative.as_posix()],
            "writes": [{
                "destination": destination_relative,
                "backup": backup.relative_to(self.service.store.directory.parent).as_posix(),
                "backup_sha256": hashlib.sha256(b"old bytes").hexdigest(),
            }],
        }), encoding="utf-8")
        return current, destination, marker, receipt_path, event_bytes_before

    def test_pack_lock_recovers_an_interrupted_uncommitted_promotion(self):
        workflow, destination, marker, receipt, _ = self.interrupted_scope_promotion()
        with self.service.store.pack_locked():
            pass
        self.assertEqual(destination.read_bytes(), b"old bytes")
        self.assertFalse(receipt.exists())
        self.assertFalse(marker.exists())
        self.assertEqual(self.service.status(), workflow)

    def test_pack_lock_removes_event_tail_and_receipt_if_workflow_commit_was_interrupted(self):
        workflow, destination, marker, receipt, event_bytes_before = self.interrupted_scope_promotion(
            append_event=True)

        with self.service.store.pack_locked():
            pass

        self.assertEqual(self.service.store.events_path.stat().st_size, event_bytes_before)
        self.assertFalse(receipt.exists())
        self.assertFalse(marker.exists())
        self.assertEqual(self.service.status(), workflow)

    def test_pack_recovery_refuses_a_destination_not_approved_by_the_proposal(self):
        workflow, destination, marker, receipt, _ = self.interrupted_scope_promotion(
            destination_name=".git/config")
        workflow_bytes = self.service.store.workflow_path.read_bytes()
        with self.assertRaisesRegex(WorkflowConflict, "unauthorized destination"):
            with self.service.store.pack_locked():
                pass
        self.assertEqual(destination.read_bytes(), b"new bytes")
        self.assertTrue(marker.exists())
        self.assertTrue(receipt.exists())
        self.assertEqual(self.service.store.workflow_path.read_bytes(), workflow_bytes)

    def test_pack_recovery_refuses_a_tampered_backup(self):
        _, destination, marker, receipt, _ = self.interrupted_scope_promotion()
        transaction = self.service.store.directory / "transactions/interrupted/backup-001.bin"
        transaction.write_bytes(b"tampered backup")
        with self.assertRaisesRegex(WorkflowConflict, "backup hash differs"):
            with self.service.store.pack_locked():
                pass
        self.assertEqual(destination.read_bytes(), b"new bytes")
        self.assertTrue(marker.exists())
        self.assertTrue(receipt.exists())

    def workflow_transaction_fixture(self, *, append_event=False, commit_workflow=False):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                           self.coordinator)
        updated = workflow.model_copy(deep=True)
        updated.revision += 1
        updated.event_sequence += 1
        relative = Path("progress/interrupted.json")
        reference, data = self.service.store.reference(relative, {"interrupted": True})
        event = self.service._event(workflow, updated, "progress.reported", self.coordinator,
                                    "Interrupted generic commit", artifacts=[reference])
        updated.last_event_sha256 = event.sha256
        artifact = self.service.store.directory / relative
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(data)
        event_bytes_before = self.service.store.events_path.stat().st_size
        if append_event or commit_workflow:
            with self.service.store.events_path.open("ab") as stream:
                stream.write(canonical_json(event))
        if commit_workflow:
            self.service.store.workflow_path.write_bytes(canonical_json(updated))
        marker = self.service.store.transaction_path
        marker.write_bytes(canonical_json({
            "schema_version": "swisstip.workflow-transaction/v1",
            "workflow_id": workflow.workflow_id,
            "previous_event_sha256": workflow.last_event_sha256,
            "event_bytes_before": event_bytes_before,
            "event": event.model_dump(mode="json", exclude_none=True),
            "prepared_artifacts": [relative.as_posix()],
        }))
        return workflow, updated, artifact, marker, event_bytes_before

    def test_generic_workflow_recovery_removes_orphan_artifact_and_event_tail(self):
        for append_event in (False, True):
            with self.subTest(append_event=append_event):
                if append_event:
                    self.temporary.cleanup()
                    self.setUp()
                workflow, _, artifact, marker, event_bytes_before = self.workflow_transaction_fixture(
                    append_event=append_event)
                self.assertEqual(self.service.status(), workflow)
                self.assertFalse(artifact.exists())
                self.assertFalse(marker.exists())
                self.assertEqual(self.service.store.events_path.stat().st_size, event_bytes_before)

    def test_generic_workflow_recovery_keeps_a_committed_transition(self):
        _, updated, artifact, marker, _ = self.workflow_transaction_fixture(commit_workflow=True)
        self.assertEqual(self.service.status(), updated)
        self.assertTrue(artifact.exists())
        self.assertFalse(marker.exists())

    def test_workflow_marker_cleanup_failure_cannot_roll_back_a_committed_event(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                           self.coordinator)
        original_unlink = Path.unlink
        failed = False

        def unlink(path, *args, **kwargs):
            nonlocal failed
            if path == self.service.store.transaction_path and not failed:
                failed = True
                raise PermissionError("simulated marker contention")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            updated = self.service.report_progress(self.coordinator, "Committed progress", {}, workflow.revision)

        self.assertEqual(updated.revision, workflow.revision + 1)
        self.assertEqual(self.service.store.events()[-1].kind, "progress.reported")
        self.assertFalse(self.service.store.transaction_path.exists())

    def test_promotion_marker_cleanup_failure_keeps_promoted_bytes_and_receipt(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        drafts = self.root / ".local/self-employed-zurich/drafts"
        drafts.mkdir(parents=True)
        scope = drafts / "scope.md"
        scope.write_text("# Scope\n", encoding="utf-8")
        proposed = self.service.submit(
            ApprovalGate.SCOPE, "Scope", self.scope_details(), self.coordinator, initialized.revision,
            artifacts={"scope-document": scope}, promotions=[
                PromotionSpec(artifact="scope-document",
                              destination="docs/self-employed-zurich-acceptance-questions.md")])
        approved = self.service.decide(
            ApprovalGate.SCOPE, Decision.APPROVE, proposed.gates[ApprovalGate.SCOPE].proposal.sha256,
            "", self.human, proposed.revision)
        marker = self.service.store.directory.parent / ".pack-transaction.json"
        original_unlink = Path.unlink
        failed = False

        def unlink(path, *args, **kwargs):
            nonlocal failed
            if path == marker and not failed:
                failed = True
                raise PermissionError("simulated marker contention")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            promoted = self.service.promote(ApprovalGate.SCOPE, self.coordinator, approved.revision)

        self.assertEqual(promoted.state, WorkflowState.DISCOVERING_SOURCES)
        self.assertEqual((self.root / "docs/self-employed-zurich-acceptance-questions.md").read_text(),
                         "# Scope\n")
        self.assertEqual(self.service.store.events()[-1].kind, "proposal.promoted")
        self.assertFalse(marker.exists())

    def test_offline_workflow_reaches_exception_gate_through_bound_checkpoints(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        catalogue_gate = self.approved_catalogue(scope)

        run = self.root / ".local" / "self-employed-zurich"
        plan = self.write_plan()
        acquiring = self.service.confirm_download(self.human, catalogue_gate.revision)
        self.assertEqual(acquiring.state, WorkflowState.ACQUIRING)

        target = plan["targets"][0]
        response = run / "pages" / target["url_id"] / "attempt-001" / "response.html"
        response.parent.mkdir(parents=True)
        response.write_bytes(b"<html><main>Official topic</main></html>")
        snapshot = {"relative_path": response.relative_to(run).as_posix(),
                "requested_url": target["url"], "final_url": target["url"],
                "content_type": "text/html", "sha256": hashlib.sha256(response.read_bytes()).hexdigest(),
                "bytes_downloaded": len(response.read_bytes()),
                "retrieved_at": "2026-09-23T00:00:00+00:00", "review_flags": []}
        manifest = {**target, "status": "saved", "snapshots": [snapshot],
                "report": {"requests_sent": 2, "bytes_downloaded": len(response.read_bytes()),
                       "pages": [], "skipped": [], "stop_reason": "frontier-exhausted"}}
        (response.parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (response.parent.parent / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")

        (run / "summary.json").write_text(json.dumps({
            "target_count": 1, "counts": {"saved": 1}, "saved_bytes": 100,
            "results": [{"report": {"requests_sent": 2}}]}), encoding="utf-8")
        (run / "gap-report.json").write_text(json.dumps({"counts": {"gap": {}}}), encoding="utf-8")
        extracting = self.service.complete_acquisition(self.coordinator, acquiring.revision)
        text = run / "text"
        documents = text / "documents"
        documents.mkdir(parents=True)
        (text / "index.json").write_text("[]", encoding="utf-8")
        (text / "summary.json").write_text("{}", encoding="utf-8")
        (documents / "doc-one.json").write_text("{}", encoding="utf-8")

        def validate(text_dir, run_dir, check_raw=True):
            result = {"passed": True, "records": 1, "blocks": 4}
            (text_dir / "validation.json").write_text(json.dumps(result), encoding="utf-8")
            return result

        with patch("swisstip.extraction.validate.validate_dataset", side_effect=validate):
            extracted = self.service.complete_extraction(self.coordinator, extracting.revision)
        pending = self.service.submit(ApprovalGate.EXCEPTIONS, "No material exceptions",
                                      {"outstanding": 0}, self.coordinator, extracted.revision)
        self.assertEqual(pending.state, WorkflowState.AWAITING_EXCEPTION_APPROVAL)
        proposal = Proposal.model_validate_json(
            self.service.store.read_artifact(pending.gates[ApprovalGate.EXCEPTIONS].proposal))
        self.assertIn("gap-report", proposal.artifacts)
        self.assertEqual(proposal.details["outstanding"], 0)
        self.assertEqual(proposal.details["gap_report_sha256"],
                         hashlib.sha256((run / "gap-report.json").read_bytes()).hexdigest())
        self.assertTrue(any(event.kind == "checkpoint.download-confirmation"
                            for event in self.service.store.events()))
        self.assertTrue(any(event.kind == "checkpoint.extraction"
                            for event in self.service.store.events()))

    def test_a3_accepts_an_exact_human_disposition_of_an_open_acquisition_exception(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        catalogue_gate = self.approved_catalogue(scope)
        run = self.root / ".local" / "self-employed-zurich"
        plan = self.write_plan()
        acquiring = self.service.confirm_download(self.human, catalogue_gate.revision)
        target = plan["targets"][0]
        page = run / "pages" / target["url_id"]
        page.mkdir(parents=True)
        manifest = {**target, "status": "not_saved", "snapshots": [],
                    "error": "HTTP Error 403: Forbidden",
                    "report": {"stop_reason": "frontier-exhausted", "skipped": [], "pages": []}}
        (page / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (run / "summary.json").write_text(json.dumps({
            "target_count": 1, "counts": {"not_saved": 1}, "saved_bytes": 0,
            "results": [manifest], "supplements": [],
        }), encoding="utf-8")
        initial_gaps = gap_report.build_report(run)
        (run / "gap-report.json").write_text(json.dumps(initial_gaps), encoding="utf-8")
        extracting = self.service.complete_acquisition(self.coordinator, acquiring.revision)
        text = run / "text"
        (text / "documents").mkdir(parents=True)
        (text / "index.json").write_text("[]", encoding="utf-8")
        (text / "summary.json").write_text("{}", encoding="utf-8")

        def validate(text_dir, run_dir, check_raw=True):
            result = {"passed": True, "records": 0, "blocks": 0}
            (text_dir / "validation.json").write_text(json.dumps(result), encoding="utf-8")
            return result

        with patch("swisstip.extraction.validate.validate_dataset", side_effect=validate):
            extracted = self.service.complete_extraction(self.coordinator, extracting.revision)
        with self.assertRaisesRegex(ValueError, "without a typed human disposition"):
            self.service.submit(ApprovalGate.EXCEPTIONS, "Review exceptions", {}, self.coordinator,
                                extracted.revision)

        current_report = json.loads((run / "gap-report.json").read_text(encoding="utf-8"))
        row = current_report["targets"][0]
        review = {
            "review_status": "human-reviewed", "decision": "approved",
            "reviewed_by": "Anna Meier", "reviewed_on": "2026-09-23",
            "reason": "Accepted as an unavailable source for this bounded demonstration.",
        }
        (run / "review-decisions.json").write_text(json.dumps({
            "schema_version": REVIEW_SCHEMA, "catalogue_sha256": plan["catalogue_sha256"],
            "acquisition": [{"url": row["url"],
                             "observation_sha256": observation_fingerprint(row), **review}],
            "text_documents": [], "scope": review,
        }), encoding="utf-8")
        pending = self.service.submit(ApprovalGate.EXCEPTIONS, "Reviewed acquisition exception", {},
                                      self.coordinator, extracted.revision)
        proposal = Proposal.model_validate_json(
            self.service.store.read_artifact(pending.gates[ApprovalGate.EXCEPTIONS].proposal))
        self.assertEqual(proposal.details["outstanding"], 0)
        self.assertEqual(proposal.artifacts["gap-report"].sha256,
                         hashlib.sha256((run / "gap-report.json").read_bytes()).hexdigest())

    def test_catalogue_promotion_requires_both_catalogue_files(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        catalogue = drafts / "sources.json"
        catalogue.write_text(json.dumps(self.catalogue_data()), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must promote sources.json and sources.md"):
            self.service.submit(
                ApprovalGate.CATALOGUE, "Incomplete catalogue", {}, self.coordinator, scope.revision,
                artifacts={"catalogue": catalogue}, promotions=[
                    PromotionSpec(artifact="catalogue", destination="releases/self-employed-zurich/sources.json")])

    def test_a2_refuses_a_malformed_catalogue_before_human_approval(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        drafts = self.root / ".local/self-employed-zurich/drafts"
        catalogue = drafts / "sources.json"
        catalogue.write_text("{}\n", encoding="utf-8")
        inventory = drafts / "sources.md"
        inventory.write_text("# Sources\n", encoding="utf-8")
        with self.assertRaises((KeyError, ValueError)):
            self.service.submit(
                ApprovalGate.CATALOGUE, "Malformed", {}, self.coordinator, scope.revision,
                artifacts={"catalogue": catalogue, "inventory": inventory}, promotions=[
                    PromotionSpec(artifact="catalogue", destination="releases/self-employed-zurich/sources.json"),
                    PromotionSpec(artifact="inventory", destination="releases/self-employed-zurich/sources.md")])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_promotion_refuses_a_symlink_destination(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        drafts = self.root / ".local/self-employed-zurich/drafts"
        drafts.mkdir(parents=True)
        scope = drafts / "scope.md"
        scope.write_text("# Scope\n", encoding="utf-8")
        pending = self.service.submit(
            ApprovalGate.SCOPE, "Scope", self.scope_details(), self.coordinator, initialized.revision,
            artifacts={"scope-document": scope}, promotions=[
                PromotionSpec(artifact="scope-document",
                              destination="docs/self-employed-zurich-acceptance-questions.md")])
        approved = self.service.decide(
            ApprovalGate.SCOPE, Decision.APPROVE, pending.gates[ApprovalGate.SCOPE].proposal.sha256,
            "", self.human, pending.revision)
        target = self.root / "unrelated.md"
        target.write_text("keep", encoding="utf-8")
        docs = self.root / "docs"
        docs.mkdir()
        link = docs / "self-employed-zurich-acceptance-questions.md"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation is not permitted")
        with self.assertRaisesRegex(ValueError, "uses a symlink"):
            self.service.promote(ApprovalGate.SCOPE, self.coordinator, approved.revision)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_changed_plan_after_human_confirmation_blocks_acquisition_completion(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        run = self.root / ".local" / "self-employed-zurich"
        promoted = self.approved_catalogue(scope)
        plan = self.write_plan()
        acquiring = self.service.confirm_download(self.human, promoted.revision)
        plan["targets"].append({"allowed_redirect_hosts": ["other.example"]})
        (run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (run / "summary.json").write_text(json.dumps({"target_count": 2, "counts": {"saved": 2},
                                                       "saved_bytes": 20, "results": []}), encoding="utf-8")
        (run / "gap-report.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(WorkflowConflict):
            self.service.complete_acquisition(self.coordinator, acquiring.revision)

    def test_tampered_plan_is_refused_before_download_confirmation(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        promoted = self.approved_catalogue(scope)
        plan = self.write_plan()
        plan["targets"][0]["allowed_redirect_hosts"].append("attacker.example")
        run = self.root / ".local" / "self-employed-zurich"
        (run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

        with self.assertRaisesRegex(WorkflowConflict, "does not match the promoted catalogue"):
            self.service.confirm_download(self.human, promoted.revision)

    def test_source_plugin_plan_is_refused_before_download_confirmation(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        scope = self.approved_scope(initialized)
        run = self.root / ".local" / "self-employed-zurich"
        drafts = run / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        catalogue = drafts / "sources.json"
        catalogue.write_text(json.dumps(self.catalogue_data()), encoding="utf-8")
        inventory = drafts / "sources.md"
        inventory.write_text("# Sources\n", encoding="utf-8")
        pending = self.service.submit(
            ApprovalGate.CATALOGUE, "Catalogue", {}, self.coordinator, scope.revision,
            artifacts={"catalogue": catalogue, "inventory": inventory}, promotions=[
                PromotionSpec(artifact="catalogue", destination="releases/self-employed-zurich/sources.json"),
                PromotionSpec(artifact="inventory", destination="releases/self-employed-zurich/sources.md")])
        approved = self.service.decide(ApprovalGate.CATALOGUE, Decision.APPROVE,
                                       pending.gates[ApprovalGate.CATALOGUE].proposal.sha256, "",
                                       self.human, pending.revision)
        promoted = self.service.promote(ApprovalGate.CATALOGUE, self.coordinator, approved.revision)
        digest = hashlib.sha256((self.root / "releases" / "self-employed-zurich" / "sources.json").read_bytes()).hexdigest()
        (run / "plan.json").write_text(json.dumps({"catalogue_sha256": digest, "targets": []}), encoding="utf-8")
        (run / "plugin-plan.json").write_text(json.dumps({
            "sources": [{"url": "https://fedlex.data.admin.ch/eli/cc/27/317_321_377/en"}]}),
            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.confirm_download(self.human, promoted.revision)

    def test_cli_initializes_submits_and_verifies_without_an_approval_command(self):
        details = self.root / "scope.json"
        details.write_text(json.dumps(self.scope_details()), encoding="utf-8")
        drafts = self.root / ".local" / "cli-pack" / "drafts"
        drafts.mkdir(parents=True)
        scope_document = drafts / "scope.md"
        scope_document.write_text("# Scope\n", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(["init", "--packs-dir", str(self.root), "--pack", "cli-pack",
                             "--topic", "A topic", "--json"])
        self.assertEqual(code, 0)
        initialized = json.loads(output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(["submit", "--packs-dir", str(self.root), "--pack", "cli-pack",
                             "--gate", "scope", "--summary", "Two topics and six questions",
                             "--details", str(details), "--expected-revision", str(initialized["revision"]),
                             "--artifact", f"scope-document={scope_document}",
                             "--promote", "scope-document=docs/cli-pack-acceptance-questions.md",
                             "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["state"], "awaiting_scope_approval")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli_main(["verify", "--packs-dir", str(self.root), "--pack", "cli-pack"]), 0)
        self.assertNotIn("decide", cli_parser_commands())

    def test_cli_refuses_fast_track_without_a_delegation_profile(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = cli_main(["init", "--packs-dir", str(self.root), "--pack", "fast-pack",
                             "--topic", "A topic", "--review-mode", "fast-track"])
        self.assertEqual(code, 1)
        self.assertIn("delegation profile, model, prompt hash and schema hash", error.getvalue())

    def test_progress_is_durable_but_does_not_change_state(self):
        initialized = self.service.initialize("Self-employment in Zurich", ReviewMode.FULL_REVIEW,
                                              self.coordinator)
        reported = self.service.report_progress(
            self.coordinator, "Scouting official sources",
            {"scouts_completed": 2, "scouts_total": 3, "current_branch": "cantonal"},
            initialized.revision)
        self.assertEqual(reported.state, initialized.state)
        self.assertEqual(reported.revision, initialized.revision + 1)
        event = self.service.store.events()[-1]
        self.assertEqual(event.kind, "progress.reported")
        self.assertEqual(event.metrics["scouts_completed"], 2)

    def test_fast_track_delegates_only_support_work_to_a_distinct_pinned_model(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FAST_TRACK,
                                           self.coordinator, delegation_profile="frontier-review",
                                           delegation_model="claude-review",
                                           delegation_prompt_sha256="a" * 64,
                                           delegation_response_schema_sha256="b" * 64)
        scope = self.approved_scope(workflow)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        subject = drafts / "source-metadata.json"
        subject.write_text(json.dumps({"source_id": "source-1", "title": "Official source",
                                       "notes": "Official scope source"}), encoding="utf-8")
        prepared = self.service.prepare_delegation(
            DecisionClass.SOURCE_METADATA, "source-1", subject, [], "Check source metadata",
            "claude-proposer", "request-proposer-1", self.coordinator, scope.revision)
        item_ref = next(reference for reference in self.service.store.events()[-1].artifacts
                        if reference.schema_version == "swisstip.autopilot-delegation-item/v2")
        reviewer = Actor(kind=ActorKind.FRONTIER_MODEL, actor_id="claude-review",
                         authentication=AuthenticationKind.MODEL_RESPONSE)
        decided = self.service.record_delegated_decision(
            item_ref.sha256, DelegationVerdict.APPROVE, "Metadata matches the source packet.",
            "anthropic-assistant", "claude-review", "claude-review", "a" * 64, "b" * 64,
            reviewer, prepared.revision)
        self.assertEqual(decided.state, WorkflowState.DISCOVERING_SOURCES)
        self.assertEqual(self.service.store.events()[-1].kind, "delegation.approve")
        catalogue = drafts / "sources.json"
        catalogue.write_text(json.dumps(self.catalogue_data(
            source_id="source-1", title="Official source", notes="Official scope source"), indent=2) + "\n",
            encoding="utf-8")
        inventory = drafts / "sources.md"
        inventory.write_text("# Sources\n", encoding="utf-8")
        pending = self.service.submit(
            ApprovalGate.CATALOGUE, "Delegated metadata plus human source boundaries", {"sources": 1},
            self.coordinator, decided.revision,
            artifacts={"catalogue": catalogue, "inventory": inventory}, promotions=[
                PromotionSpec(artifact="catalogue", destination="releases/self-employed-zurich/sources.json"),
                PromotionSpec(artifact="inventory", destination="releases/self-employed-zurich/sources.md")])
        proposal = Proposal.model_validate_json(
            self.service.store.read_artifact(pending.gates[ApprovalGate.CATALOGUE].proposal))
        self.assertEqual(len(proposal.delegation_uses), 1)
        self.assertEqual(proposal.delegation_uses[0].handling, DelegationHandling.APPLIED)
        approved = self.service.decide(ApprovalGate.CATALOGUE, Decision.APPROVE,
                                       pending.gates[ApprovalGate.CATALOGUE].proposal.sha256, "",
                                       self.human, pending.revision)
        promoted = self.service.promote(ApprovalGate.CATALOGUE, self.coordinator, approved.revision)
        self.assertTrue((self.root / "releases/self-employed-zurich/sources.json").is_file())
        with self.assertRaises(ValueError):
            self.service.prepare_delegation(
                DecisionClass.HIGH_IMPACT_FACT, "fact-1", subject, [], "Not delegable",
                "claude-proposer", "request-proposer-2", self.coordinator, promoted.revision)

    def test_approved_non_blocking_variant_is_bound_into_the_human_a5_proposal(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FAST_TRACK,
                                           self.coordinator, delegation_profile="frontier-review",
                                           delegation_model="claude-review",
                                           delegation_prompt_sha256="a" * 64,
                                           delegation_response_schema_sha256="b" * 64)
        scope = self.approved_scope(workflow, questions=1)
        drafting = scope.model_copy(deep=True)
        drafting.state = WorkflowState.GENERATING_TESTS
        drafting.revision += 1
        drafting.event_sequence += 1
        event = self.service._event(scope, drafting, "test.phase-entered", self.coordinator,
                                    "Entered A5 drafting for isolated delegation contract test")
        drafting.last_event_sha256 = event.sha256
        drafting = self.service.store.commit(scope.revision, drafting, event)

        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        case = {
            "case_id": "REG-1", "label": "Retrieval wording variant",
            "question": "Where do I register?", "expected_answer": "Use the competent authority.",
            "blocking": False, "quarantine_reason": "Exploratory retrieval wording",
            "steps": [{"search": {"query": "register competent authority",
                                    "expect_concept": "registration"}}],
        }
        subject = drafts / "regression-variant.json"
        subject.write_text(json.dumps(case), encoding="utf-8")
        prepared = self.service.prepare_delegation(
            DecisionClass.NON_BLOCKING_VARIANT, "REG-1", subject, [], "Review retrieval-only variant",
            "claude-proposer", "request-proposer-1", self.coordinator, drafting.revision)
        item_ref = next(reference for reference in self.service.store.events()[-1].artifacts
                        if reference.schema_version == "swisstip.autopilot-delegation-item/v2")
        reviewer = Actor(kind=ActorKind.FRONTIER_MODEL, actor_id="claude-review",
                         authentication=AuthenticationKind.MODEL_RESPONSE)
        decided = self.service.record_delegated_decision(
            item_ref.sha256, DelegationVerdict.APPROVE, "Variant is retrieval-only and non-blocking.",
            "anthropic-assistant", "claude-review", "claude-review", "a" * 64, "b" * 64,
            reviewer, prepared.revision)

        acceptance = drafts / "acceptance.yaml"
        acceptance.write_text(json.dumps({
            "schema_version": "swiss-tip-acceptance/v1", "pack": "self-employed-zurich",
            "cases": [{"case_id": "UAT-1", "label": "Registration", "question": "Question 1",
                       "expected_answer": "Use the competent authority.",
                       "steps": [{"search": {"query": "register", "expect_concept": "registration"}}]}],
        }), encoding="utf-8")
        regression = drafts / "regression.yaml"
        regression.write_text(json.dumps({
            "schema_version": "swiss-tip-acceptance/v1", "pack": "self-employed-zurich", "cases": [case],
        }), encoding="utf-8")
        pending = self.service.submit(
            ApprovalGate.ACCEPTANCE, "Acceptance and regression suites", {"cases": 2}, self.coordinator,
            decided.revision, artifacts={"acceptance": acceptance, "regression": regression}, promotions=[
                PromotionSpec(artifact="acceptance", destination="releases/self-employed-zurich/acceptance.yaml"),
                PromotionSpec(artifact="regression", destination="releases/self-employed-zurich/regression.yaml")])

        proposal = Proposal.model_validate_json(
            self.service.store.read_artifact(pending.gates[ApprovalGate.ACCEPTANCE].proposal))
        self.assertEqual(proposal.delegation_uses[0].decision_class, DecisionClass.NON_BLOCKING_VARIANT)
        self.assertEqual(proposal.delegation_uses[0].handling, DelegationHandling.APPLIED)
        self.assertEqual(pending.state, WorkflowState.AWAITING_ACCEPTANCE_APPROVAL)

    def test_delegated_source_metadata_rejection_and_revision_are_enforced_at_a2(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FAST_TRACK,
                                           self.coordinator, delegation_profile="frontier-review",
                                           delegation_model="claude-review",
                                           delegation_prompt_sha256="a" * 64,
                                           delegation_response_schema_sha256="b" * 64)
        scope = self.approved_scope(workflow)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        subject = drafts / "source-metadata.json"
        subject.write_text(json.dumps({"source_id": "source-1", "title": "Official source",
                                       "notes": "Unsupported description"}), encoding="utf-8")
        prepared = self.service.prepare_delegation(
            DecisionClass.SOURCE_METADATA, "source-1", subject, [], "Check source metadata",
            "claude-proposer", "request-proposer-1", self.coordinator, scope.revision)
        item_ref = next(reference for reference in self.service.store.events()[-1].artifacts
                        if reference.schema_version == "swisstip.autopilot-delegation-item/v2")
        reviewer = Actor(kind=ActorKind.FRONTIER_MODEL, actor_id="claude-review",
                         authentication=AuthenticationKind.MODEL_RESPONSE)
        decided = self.service.record_delegated_decision(
            item_ref.sha256, DelegationVerdict.REJECT, "The description is unsupported.",
            "anthropic-assistant", "claude-review", "claude-review", "a" * 64, "b" * 64,
            reviewer, prepared.revision)
        with self.assertRaisesRegex(ValueError, "already has a final verdict"):
            self.service.record_delegated_decision(
                item_ref.sha256, DelegationVerdict.APPROVE, "Changed verdict.",
                "anthropic-assistant", "claude-review", "claude-review", "a" * 64, "b" * 64,
                reviewer, decided.revision)

        inventory = drafts / "sources.md"
        inventory.write_text("# Sources\n", encoding="utf-8")
        catalogue = drafts / "sources.json"
        catalogue.write_text(json.dumps(self.catalogue_data(
            source_id="source-1", title="Official source", notes="Unsupported description"), indent=2),
            encoding="utf-8")
        promotions = [
            PromotionSpec(artifact="catalogue", destination="releases/self-employed-zurich/sources.json"),
            PromotionSpec(artifact="inventory", destination="releases/self-employed-zurich/sources.md")]
        with self.assertRaisesRegex(ValueError, "reviewer rejected source-1"):
            self.service.submit(ApprovalGate.CATALOGUE, "Rejected metadata", {}, self.coordinator,
                                decided.revision, artifacts={"catalogue": catalogue, "inventory": inventory},
                                promotions=promotions)

        catalogue.write_text(json.dumps(self.catalogue_data(
            source_id="source-1", title="Official source", notes="Human-revised description"), indent=2),
            encoding="utf-8")
        pending = self.service.submit(ApprovalGate.CATALOGUE, "Human revised metadata", {}, self.coordinator,
                                      decided.revision,
                                      artifacts={"catalogue": catalogue, "inventory": inventory},
                                      promotions=promotions)
        proposal = Proposal.model_validate_json(
            self.service.store.read_artifact(pending.gates[ApprovalGate.CATALOGUE].proposal))
        self.assertEqual(proposal.delegation_uses[0].handling, DelegationHandling.HUMAN_REVIEW)
        self.assertEqual(proposal.delegation_uses[0].verdict, DelegationVerdict.REJECT)

    def test_delegated_subject_schemas_cannot_smuggle_boundaries_or_fact_checks(self):
        workflow = self.service.initialize("Self-employment in Zurich", ReviewMode.FAST_TRACK,
                                           self.coordinator, delegation_profile="frontier-review",
                                           delegation_model="claude-review",
                                           delegation_prompt_sha256="a" * 64,
                                           delegation_response_schema_sha256="b" * 64)
        scope = self.approved_scope(workflow)
        drafts = self.root / ".local" / "self-employed-zurich" / "drafts"
        metadata = drafts / "source-metadata.json"
        metadata.write_text(json.dumps({"source_id": "source-1", "title": "Official",
                                        "notes": "Note", "allowed_hosts": ["attacker.example"]}),
                            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "permits only"):
            self.service.prepare_delegation(
                DecisionClass.SOURCE_METADATA, "source-1", metadata, [], "Unsafe metadata",
                "claude-proposer", "request-1", self.coordinator, scope.revision)

        case = {
            "case_id": "REG-1", "label": "Variant", "question": "Where do I register?",
            "expected_answer": "At the authority.", "blocking": False,
            "quarantine_reason": "Exploratory wording", "steps": [
                {"search": {"query": "register", "expect_concept": "registration"}},
                {"resolve": {"concept_ids": ["registration"]}}],
            "claims": [{"claim": "A fact", "concept": "registration", "statement_contains": ["authority"]}],
        }
        variant = drafts / "variant.json"
        variant.write_text(json.dumps(case), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "prepared only while drafting acceptance"):
            self.service.prepare_delegation(
                DecisionClass.NON_BLOCKING_VARIANT, "REG-1", variant, [], "Unsafe variant",
                "claude-proposer", "request-2", self.coordinator, scope.revision)
        with self.assertRaisesRegex(ValueError, "retrieval-only"):
            self.service._delegated_subject(DecisionClass.NON_BLOCKING_VARIANT, "REG-1",
                                            variant.read_bytes())


def cli_parser_commands():
    from swisstip.builder.autopilot.cli import parser
    action = next(action for action in parser()._actions if action.dest == "command")
    return set(action.choices)


if __name__ == "__main__":
    unittest.main()
