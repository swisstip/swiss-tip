"""Commands over the workflow contracts; generators remain external clients."""

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, date, datetime
from functools import wraps
from pathlib import Path

from swisstip.build.acceptance import load_acceptance, load_regression, parse_acceptance
from swisstip.build.coverage import load_dispositions
from swisstip.build.curation import load_curation, parse_curation
from swisstip.core.acceptance import Case
from swisstip.core.readiness import load_readiness, readiness_status
from swisstip.core.release import load_release
from swisstip.ingestion.catalog import validate_source_catalog
from swisstip.ingestion.download_cli import build_plan
from swisstip.runtime.semantic import load_index, semantic_index_binding

from .models import (Actor, ActorKind, ApprovalGate, CheckpointKind, Decision,
                     DecisionAuthority, DecisionClass, DELEGATION_TARGETS, DelegatedDecision, DelegationHandling,
                     DelegationItem, DelegationUse, DelegationVerdict, Event, FactReviewReceipt,
                     GateProgress, GateStatus, HumanDecision, PhaseCheckpoint, PromotionReceipt,
                     PromotionSpec, Proposal, ReviewMode, Workflow, WorkflowState,
                     default_policy)
from .policy import can_delegate
from .store import (WorkflowConflict, WorkflowStore, atomic_write, canonical_json,
                    event_hash, sha256_bytes, workflow_hash)

PACK = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_WORKFLOW_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_WORKFLOW_PACKET_BYTES = 64 * 1024 * 1024

SUBMIT_STATES = {
    ApprovalGate.SCOPE: WorkflowState.DRAFTING_SCOPE,
    ApprovalGate.CATALOGUE: WorkflowState.DISCOVERING_SOURCES,
    ApprovalGate.EXCEPTIONS: WorkflowState.EXTRACTING,
    ApprovalGate.KNOWLEDGE_DESIGN: WorkflowState.GENERATING_KNOWLEDGE,
    ApprovalGate.ACCEPTANCE: WorkflowState.GENERATING_TESTS,
}
PENDING_STATES = {
    ApprovalGate.SCOPE: WorkflowState.AWAITING_SCOPE_APPROVAL,
    ApprovalGate.CATALOGUE: WorkflowState.AWAITING_CATALOGUE_APPROVAL,
    ApprovalGate.EXCEPTIONS: WorkflowState.AWAITING_EXCEPTION_APPROVAL,
    ApprovalGate.KNOWLEDGE_DESIGN: WorkflowState.AWAITING_KNOWLEDGE_DESIGN_APPROVAL,
    ApprovalGate.ACCEPTANCE: WorkflowState.AWAITING_ACCEPTANCE_APPROVAL,
}
APPROVED_STATES = {
    ApprovalGate.SCOPE: WorkflowState.DISCOVERING_SOURCES,
    ApprovalGate.CATALOGUE: WorkflowState.AWAITING_DOWNLOAD_CONFIRMATION,
    ApprovalGate.EXCEPTIONS: WorkflowState.GENERATING_KNOWLEDGE,
    ApprovalGate.KNOWLEDGE_DESIGN: WorkflowState.AWAITING_FACT_REVIEW,
    ApprovalGate.ACCEPTANCE: WorkflowState.VALIDATING,
}
REVISION_STATES = {
    ApprovalGate.SCOPE: WorkflowState.DRAFTING_SCOPE,
    ApprovalGate.CATALOGUE: WorkflowState.DISCOVERING_SOURCES,
    ApprovalGate.EXCEPTIONS: WorkflowState.EXTRACTING,
    ApprovalGate.KNOWLEDGE_DESIGN: WorkflowState.GENERATING_KNOWLEDGE,
    ApprovalGate.ACCEPTANCE: WorkflowState.GENERATING_TESTS,
}
PROMOTION_DESTINATIONS = {
    ApprovalGate.SCOPE: (re.compile(r"docs/[a-z0-9-]+-acceptance-questions\.md"),),
    ApprovalGate.CATALOGUE: (re.compile(r"releases/[a-z0-9-]+/sources\.(?:json|md)"),),
    ApprovalGate.EXCEPTIONS: (),
    ApprovalGate.KNOWLEDGE_DESIGN: (
        re.compile(r"releases/[a-z0-9-]+/curation\.yaml"),
        re.compile(r"releases/[a-z0-9-]+/curation-coverage\.yaml"),
        re.compile(r"releases/[a-z0-9-]+/basis-review\.md"),
    ),
    ApprovalGate.ACCEPTANCE: (
        re.compile(r"releases/[a-z0-9-]+/acceptance\.yaml"),
        re.compile(r"releases/[a-z0-9-]+/regression\.yaml"),
    ),
}
REQUIRED_PROMOTIONS = {
    ApprovalGate.SCOPE: frozenset(),
    ApprovalGate.CATALOGUE: frozenset({"sources.json", "sources.md"}),
    ApprovalGate.EXCEPTIONS: frozenset(),
    ApprovalGate.KNOWLEDGE_DESIGN: frozenset({"curation.yaml", "curation-coverage.yaml"}),
    ApprovalGate.ACCEPTANCE: frozenset({"acceptance.yaml", "regression.yaml"}),
}


def now() -> datetime:
    return datetime.now(UTC)


def pack_transaction(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.store.pack_locked():
            return method(self, *args, **kwargs)
    return wrapped


class AutopilotService:
    def __init__(self, root: Path, pack: str):
        if not PACK.fullmatch(pack):
            raise ValueError("pack must be a lowercase kebab-case identifier")
        self.root, self.pack = Path(root).resolve(), pack
        self.store = WorkflowStore(self.root, pack)

    def initialize(self, topic: str, review_mode: ReviewMode, actor: Actor,
                   delegation_profile: str | None = None,
                   delegation_model: str | None = None,
                   delegation_prompt_sha256: str | None = None,
                   delegation_response_schema_sha256: str | None = None) -> Workflow:
        if actor.kind not in {ActorKind.COORDINATOR, ActorKind.HUMAN}:
            raise ValueError("a coordinator or person initializes a workflow")
        timestamp, workflow_id = now(), str(uuid.uuid4())
        policy = default_policy(review_mode, timestamp, delegation_profile, delegation_model,
                    delegation_prompt_sha256, delegation_response_schema_sha256)
        policy_ref, policy_data = self.store.reference(Path("policy.json"), policy,
                                                       schema_version=policy.schema_version)
        gates = {gate: GateProgress(status=GateStatus.DRAFTING if gate == ApprovalGate.SCOPE
                                    else GateStatus.UNOPENED) for gate in ApprovalGate}
        workflow = Workflow(workflow_id=workflow_id, pack=self.pack, topic=topic.strip(), review_mode=review_mode,
                            state=WorkflowState.DRAFTING_SCOPE, revision=1, created_at=timestamp,
                            updated_at=timestamp, policy=policy_ref, gates=gates,
                            event_sequence=1, last_event_sha256="0" * 64)
        event = Event(event_id=str(uuid.uuid4()), workflow_id=workflow_id, workflow_revision=1, sequence=1,
                      at=timestamp, kind="workflow.initialized", actor=actor,
                      state_after=WorkflowState.DRAFTING_SCOPE,
                      summary=f"Initialized {review_mode.value} workflow for {self.pack}",
                      workflow_sha256=workflow_hash(workflow), sha256="0" * 64)
        event.sha256 = event_hash(event)
        workflow.last_event_sha256 = event.sha256
        brief = {"topic": topic.strip(), "pack": self.pack, "review_mode": review_mode.value,
                 "delegation_profile": delegation_profile, "delegation_model": delegation_model,
                 "delegation_prompt_sha256": delegation_prompt_sha256,
                 "delegation_response_schema_sha256": delegation_response_schema_sha256}
        return self.store.initialize(workflow, policy, event, brief, policy_data)

    def status(self) -> Workflow:
        return self.store.load()

    def cancel(self, actor: Actor, note: str, expected_revision: int) -> Workflow:
        current = self.store.load()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if current.state in {WorkflowState.READY, WorkflowState.FAILED, WorkflowState.CANCELLED}:
            raise ValueError(f"cannot cancel workflow in terminal state {current.state.value}")
        if actor.kind not in {ActorKind.COORDINATOR, ActorKind.HUMAN}:
            raise ValueError("a coordinator or person cancels a workflow")
        if not note.strip():
            raise ValueError("cancelling a workflow needs a note")
        updated = current.model_copy(deep=True)
        updated.state = WorkflowState.CANCELLED
        updated.updated_at = now()
        updated.revision += 1
        updated.event_sequence += 1
        event = self._event(current, updated, "workflow.cancelled", actor, note.strip())
        updated.last_event_sha256 = event.sha256
        return self.store.commit(expected_revision, updated, event)

    def report_progress(self, actor: Actor, summary: str, metrics: dict,
                        expected_revision: int) -> Workflow:
        current = self.store.load()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if current.state in {WorkflowState.READY, WorkflowState.FAILED, WorkflowState.CANCELLED}:
            raise ValueError(f"cannot report progress in terminal state {current.state.value}")
        if actor.kind not in {ActorKind.COORDINATOR, ActorKind.SYSTEM}:
            raise ValueError("progress is reported by the coordinator or system")
        if not summary.strip():
            raise ValueError("progress needs a concise summary")
        if any(not isinstance(value, (int, float, str, bool, type(None))) for value in metrics.values()):
            raise ValueError("progress metrics must be scalar JSON values")
        return self._event_transition(current, actor, "progress.reported", summary.strip(), [], {},
                                      metrics=metrics)

    @pack_transaction
    def confirm_download(self, actor: Actor, expected_revision: int) -> Workflow:
        current = self._current(expected_revision, WorkflowState.AWAITING_DOWNLOAD_CONFIRMATION)
        if actor.kind != ActorKind.HUMAN:
            raise ValueError("download confirmation requires a person")
        self._require_promotion(current, ApprovalGate.CATALOGUE)
        plan_path = self.store.directory.parent / "plan.json"
        plugin_plan_path = self.store.directory.parent / "plugin-plan.json"
        pack_dir = self.root / "releases" / self.pack
        catalogue_path, inventory_path = pack_dir / "sources.json", pack_dir / "sources.md"
        plan = self._json(plan_path)
        plugin_plan = self._json(plugin_plan_path)
        if plugin_plan.get("sources"):
            raise ValueError("governed acquisition refuses source plugins until their hosts and budgets are explicit")
        policy = self.store.load_policy()
        if plan.get("catalogue_sha256") != self._sha256_path(catalogue_path):
            raise WorkflowConflict("the acquisition plan is for another catalogue")
        workers = plan.get("workers")
        if type(workers) is not int or workers not in range(1, 5):
            raise ValueError("acquisition plan workers must be an integer from 1 through 4")
        expected_plan = build_plan(catalogue_path, inventory_path, scan_set=None, source_ids=None,
                                   workers=workers)
        comparable_plan = dict(plan)
        comparable_plan.pop("created_at", None)
        comparable_plan.pop("network_budget", None)
        expected_plan.pop("created_at", None)
        if canonical_json(comparable_plan) != canonical_json(expected_plan):
            raise WorkflowConflict("the acquisition plan does not match the promoted catalogue")
        hosts = {host for target in plan.get("targets", []) for host in target.get("allowed_redirect_hosts", [])}
        targets = len(plan.get("targets", []))
        planned_request_ceiling = targets * 16
        if len(hosts) > policy.network_budget.max_hosts:
            raise ValueError(f"plan has {len(hosts)} hosts, above the approved {policy.network_budget.max_hosts}")
        if planned_request_ceiling > policy.network_budget.max_requests:
            raise ValueError(f"plan may make {planned_request_ceiling} requests, above the approved "
                             f"{policy.network_budget.max_requests}")
        approved_budget = {"max_requests": policy.network_budget.max_requests,
                           "max_bytes": policy.network_budget.max_bytes}
        if plan.get("network_budget") not in (None, approved_budget):
            raise WorkflowConflict("the acquisition plan names another network budget")
        if plan.get("network_budget") is None:
            plan["network_budget"] = approved_budget
            atomic_write(plan_path, json.dumps(plan, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
        return self._checkpoint_transition(current, CheckpointKind.DOWNLOAD_CONFIRMATION, actor,
                                           WorkflowState.ACQUIRING,
                                           {"plan": plan_path, "catalogue": catalogue_path,
                                            "inventory": inventory_path,
                                            "plugin-plan": plugin_plan_path},
                                           {"targets": targets, "hosts": len(hosts),
                                            "request_ceiling": planned_request_ceiling,
                                            "max_bytes": policy.network_budget.max_bytes},
                                           "Human confirmed the exact acquisition plan and budget")

    @pack_transaction
    def complete_acquisition(self, actor: Actor, expected_revision: int) -> Workflow:
        current = self._current(expected_revision, WorkflowState.ACQUIRING)
        run = self.store.directory.parent
        self._require_checkpoint_current(CheckpointKind.DOWNLOAD_CONFIRMATION, {
            "plan": run / "plan.json",
            "catalogue": self.root / "releases" / self.pack / "sources.json",
            "inventory": self.root / "releases" / self.pack / "sources.md",
            "plugin-plan": run / "plugin-plan.json",
        })
        summary_path, gaps_path = run / "summary.json", run / "gap-report.json"
        summary = self._json(summary_path)
        if summary.get("supplements"):
            raise ValueError("governed acquisition refuses unbudgeted plugin supplements")
        if summary.get("target_count", 0) < 1 or summary.get("counts", {}).get("pending", 0):
            raise ValueError("acquisition still has pending targets")
        policy = self.store.load_policy()
        usage = summary.get("network_usage") or {}
        actual_bytes = usage.get("charged_bytes")
        if actual_bytes is None:
            actual_bytes = sum((item.get("report") or {}).get("bytes_downloaded", 0)
                               for item in summary.get("results", []))
        if actual_bytes > policy.network_budget.max_bytes:
            raise ValueError("downloaded bytes exceed the approved network budget")
        requests = usage.get("charged_requests")
        if requests is None:
            requests = sum((item.get("report") or {}).get("requests_sent", 0)
                           for item in summary.get("results", []))
        if requests > policy.network_budget.max_requests:
            raise ValueError("requests sent exceed the approved network budget")
        return self._checkpoint_transition(current, CheckpointKind.ACQUISITION, actor,
                                           WorkflowState.EXTRACTING,
                                           {"summary": summary_path, "gaps": gaps_path},
                                           {"targets": summary["target_count"], "requests": requests,
                                            "downloaded_bytes": actual_bytes,
                                            "saved_bytes": summary.get("saved_bytes", 0)},
                                           "Acquisition completed and its reports were bound")

    @pack_transaction
    def complete_extraction(self, actor: Actor, expected_revision: int) -> Workflow:
        current = self._current(expected_revision, WorkflowState.EXTRACTING)
        run = self.store.directory.parent
        self._require_checkpoint_current(CheckpointKind.DOWNLOAD_CONFIRMATION, {
            "plan": run / "plan.json",
            "catalogue": self.root / "releases" / self.pack / "sources.json",
            "inventory": self.root / "releases" / self.pack / "sources.md",
            "plugin-plan": run / "plugin-plan.json",
        })
        self._require_checkpoint_current(CheckpointKind.ACQUISITION, {
            "summary": run / "summary.json",
        })
        self._refresh_gap_report(run)
        text = run / "text"
        from swisstip.extraction.validate import validate_dataset
        validation = validate_dataset(text, run, check_raw=True)
        validation_path = text / "validation.json"
        if validation.get("passed") is not True:
            raise ValueError("text validation did not pass")
        if self._has_checkpoint(CheckpointKind.EXTRACTION):
            raise ValueError("extraction checkpoint already recorded")
        dataset_sha256 = self._dataset_sha256(text)
        return self._checkpoint_transition(current, CheckpointKind.EXTRACTION, actor,
                                           WorkflowState.EXTRACTING, {
                                               "validation": validation_path,
                                               "text-index": text / "index.json",
                                               "text-summary": text / "summary.json",
                                               "plan": run / "plan.json",
                                               "acquisition-summary": run / "summary.json",
                                               "gap-report": run / "gap-report.json",
                                           },
                                           {"records": validation.get("records", 0),
                                            "blocks": validation.get("blocks", 0),
                                            "dataset_sha256": dataset_sha256},
                                           "Extraction passed deterministic validation")

    @pack_transaction
    def complete_fact_review(self, actor: Actor, expected_revision: int) -> Workflow:
        current = self._current(expected_revision, WorkflowState.AWAITING_FACT_REVIEW)
        if actor.kind != ActorKind.HUMAN:
            raise ValueError("fact-review completion requires a person")
        # Review intentionally changes curation after A4; every other approved design artifact stays immutable.
        self._require_promotion(current, ApprovalGate.KNOWLEDGE_DESIGN,
                    mutable_destinations={f"releases/{self.pack}/curation.yaml"})
        pack_dir = self.root / "releases" / self.pack
        curation_path, release_path = pack_dir / "curation.yaml", pack_dir / "release.json"
        curation = load_curation(curation_path)
        approved_curation = self._approved_curation()
        self._require_approved_curation_structure(approved_curation, curation)
        receipts = self._current_fact_review_receipts(current)
        curation_statuses = {}
        for concept in curation.concepts:
            for fact in concept.facts:
                status = fact.provenance.review_status
                curation_statuses[status] = curation_statuses.get(status, 0) + 1
                receipt = receipts.get(fact.fact_id)
                if receipt is None or receipt.fact_sha256s.get(fact.fact_id) != sha256_bytes(canonical_json(fact)):
                    raise ValueError(f"fact {fact.fact_id} has no current human-review receipt")
                if receipt.reviewed_by.actor_id != fact.provenance.reviewed_by:
                    raise ValueError(f"fact {fact.fact_id} review receipt names another reviewer")
        if current.review_mode == ReviewMode.FULL_REVIEW and set(curation_statuses) != {"human-reviewed"}:
            raise ValueError(f"full-review curation still has non-human facts: {curation_statuses}")

        from ..pipeline import Pipeline, next_release_id
        existing_id = load_release(release_path).manifest.release_id if release_path.is_file() else None
        release_id = next_release_id(existing_id, self.pack, date.today())
        report = Pipeline(self.root, self.pack, run_dir=self.store.directory.parent,
                  release_id=release_id, require_same_snapshot=True, lock_pack=False,
                          log=lambda message: None).run_stages("build", "coverage")
        if report["exit_code"]:
            failure = next((stage.get("error") for stage in report["stages"]
                            if stage.get("status") == "failed"), "post-review build failed")
            raise ValueError(failure)
        release = load_release(release_path)
        statuses = {}
        for fact in release.facts:
            statuses[fact.provenance.review_status] = statuses.get(fact.provenance.review_status, 0) + 1
        if current.review_mode == ReviewMode.FULL_REVIEW and set(statuses) != {"human-reviewed"}:
            raise ValueError(f"full-review mode still has non-human facts: {statuses}")
        if current.review_mode == ReviewMode.FAST_TRACK:
            if set(statuses) != {"human-reviewed"}:
                raise ValueError("served fact delegation is disabled; fast-track releases still require human-reviewed facts")
        curation = load_curation(curation_path)
        curated = sum(len(concept.facts) for concept in curation.concepts)
        if curated != len(release.facts):
            raise ValueError("curation and release fact counts differ; rebuild after review")
        return self._checkpoint_transition(current, CheckpointKind.FACT_REVIEW, actor,
                                           WorkflowState.GENERATING_TESTS,
                                           {"curation": curation_path, "release": release_path,
                                            "dispositions": pack_dir / "curation-coverage.yaml",
                                            "build-report": pack_dir / "build-report.json",
                                            "coverage": pack_dir / "curation-coverage.json"},
                                           {"facts": len(release.facts), "review_statuses": statuses},
                                           "Human verified the fact-review checkpoint")

    @pack_transaction
    def record_fact_review(self, fact_ids: list[str], actor: Actor,
                           expected_revision: int) -> Workflow:
        current = self._current(expected_revision, WorkflowState.AWAITING_FACT_REVIEW)
        if actor.kind != ActorKind.HUMAN:
            raise ValueError("fact review requires a person")
        ids = list(dict.fromkeys(fact_id.strip() for fact_id in fact_ids if fact_id.strip()))
        if not ids:
            raise ValueError("fact review needs at least one fact ID")
        curation_path = self.root / "releases" / self.pack / "curation.yaml"
        curation = load_curation(curation_path)
        facts = {fact.fact_id: fact for concept in curation.concepts for fact in concept.facts}
        missing = sorted(set(ids) - facts.keys())
        if missing:
            raise ValueError(f"cannot review unknown facts: {missing}")
        for fact_id in ids:
            fact = facts[fact_id]
            if (fact.provenance.review_status != "human-reviewed"
                    or fact.provenance.reviewed_by != actor.actor_id):
                raise ValueError(f"fact {fact_id} is not marked reviewed by {actor.actor_id}")
        proposal = current.gates[ApprovalGate.KNOWLEDGE_DESIGN].proposal
        if proposal is None:
            raise ValueError("knowledge design has no approved proposal")
        receipt = FactReviewReceipt(
            receipt_id=str(uuid.uuid4()), workflow_id=current.workflow_id,
            knowledge_design_proposal_sha256=proposal.sha256,
            curation_sha256=self._sha256_path(curation_path),
            fact_sha256s={fact_id: sha256_bytes(canonical_json(facts[fact_id])) for fact_id in ids},
            reviewed_at=now(), reviewed_by=actor)
        relative = Path("fact-reviews") / f"{current.revision + 1:04d}-{receipt.receipt_id}.json"
        reference, data = self.store.reference(relative, receipt, schema_version=receipt.schema_version)
        return self._event_transition(
            current, actor, "fact-review.recorded", f"Recorded human review of {len(ids)} fact(s)",
            [reference], {relative: (reference, data)},
            metrics={"facts": len(ids)})

    def prepare_delegation(self, decision_class: DecisionClass, subject_id: str,
                           subject_path: Path, evidence_paths: list[Path], summary: str,
                           proposer_model: str, proposer_request_id: str, actor: Actor,
                           expected_revision: int) -> Workflow:
        current = self.store.load()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if actor.kind != ActorKind.COORDINATOR:
            raise ValueError("a coordinator prepares a delegated review")
        if current.review_mode != ReviewMode.FAST_TRACK:
            raise ValueError("delegation is available only in fast-track mode")
        policy = self.store.load_policy()
        if not can_delegate(policy, decision_class):
            raise ValueError(f"{decision_class.value} remains human-routed")
        target_gate, target_artifact = DELEGATION_TARGETS[decision_class]
        if current.state != SUBMIT_STATES[target_gate]:
            raise ValueError(f"{decision_class.value} delegation is prepared only while drafting {target_gate.value}")
        drafts = (self.store.directory.parent / "drafts").resolve()
        subject_path = Path(subject_path).resolve()
        evidence_paths = [Path(path).resolve() for path in evidence_paths]
        paths = [subject_path, *evidence_paths]
        if any(not path.is_relative_to(drafts) or not path.is_file() or path.is_symlink() for path in paths):
            raise ValueError(f"delegation inputs must be regular files under {drafts}")
        sizes = [path.stat().st_size for path in paths]
        if any(size > MAX_WORKFLOW_ARTIFACT_BYTES for size in sizes) or sum(sizes) > MAX_WORKFLOW_PACKET_BYTES:
            raise ValueError("delegation packet exceeds the workflow artifact size limit")
        member_id, member_sha256 = self._delegated_subject(decision_class, subject_id,
                                                           subject_path.read_bytes())
        item_id = str(uuid.uuid4())
        prepared = {}
        references = []
        for number, path in enumerate(paths):
            relative = Path("delegations") / "items" / item_id / f"{number:03d}-{path.name}"
            reference, data = self.store.reference(relative, path.read_bytes(), media_type="application/octet-stream")
            references.append(reference)
            prepared[relative] = (reference, data)
        item = DelegationItem(item_id=str(uuid.uuid4()), workflow_id=current.workflow_id,
                              decision_class=decision_class, subject_id=subject_id,
                              subject_sha256=references[0].sha256,
                              evidence_sha256s=[item.sha256 for item in references[1:]],
                              target_gate=target_gate, target_artifact=target_artifact,
                              member_id=member_id, member_sha256=member_sha256,
                              subject=references[0], evidence=references[1:], summary=summary,
                              policy_sha256=current.policy.sha256, proposer_model=proposer_model,
                              proposer_request_id=proposer_request_id,
                              created_at=now(), created_by=actor)
        relative = Path("delegations") / "items" / f"{item.item_id}.json"
        reference, data = self.store.reference(relative, item, schema_version=item.schema_version)
        prepared[relative] = (reference, data)
        return self._event_transition(current, actor, "delegation.prepared",
                                      f"Prepared frontier review of {subject_id}",
                                      [reference, *references], prepared)

    def record_delegated_decision(self, item_sha256: str, verdict: DelegationVerdict,
                                  reason: str, provider: str, requested_model: str,
                                  observed_model: str, prompt_sha256: str,
                                  response_schema_sha256: str, actor: Actor,
                                  expected_revision: int) -> Workflow:
        current = self.store.load()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if current.state in {WorkflowState.READY, WorkflowState.FAILED, WorkflowState.CANCELLED}:
            raise ValueError(f"cannot record delegation in terminal state {current.state.value}")
        if actor.kind != ActorKind.FRONTIER_MODEL:
            raise ValueError("a frontier model records a delegated decision")
        item_ref, item = self._delegation_item(item_sha256)
        if item.schema_version != "swisstip.autopilot-delegation-item/v2":
            raise ValueError("legacy delegation items are audit-only and cannot receive an operational verdict")
        if current.state != SUBMIT_STATES[item.target_gate]:
            raise ValueError(f"delegated review must finish while drafting {item.target_gate.value}")
        if any(decision.item_sha256 == item_sha256 for _, decision, _, _ in self._delegated_decisions()):
            raise ValueError("a delegation item already has a final verdict")
        if item.proposer_model == actor.actor_id:
            raise ValueError("the proposing and reviewing model calls must use distinct identities")
        policy = self.store.load_policy()
        if item.policy_sha256 != current.policy.sha256 or not can_delegate(policy, item.decision_class):
            raise WorkflowConflict("delegation item is stale or no longer model-routed")
        if requested_model != policy.delegation_model or observed_model != policy.delegation_model:
            raise ValueError("frontier reviewer identity differs from the model pinned at A1")
        if (prompt_sha256 != policy.delegation_prompt_sha256
                or response_schema_sha256 != policy.delegation_response_schema_sha256):
            raise ValueError("frontier-review prompt or response schema differs from A1 policy")
        decision = DelegatedDecision(
            decision_id=str(uuid.uuid4()), workflow_id=current.workflow_id,
            item_sha256=item_sha256, decision_class=item.decision_class,
            subject_id=item.subject_id, subject_sha256=item.subject_sha256,
            evidence_sha256s=item.evidence_sha256s, policy_sha256=current.policy.sha256,
            target_gate=item.target_gate, target_artifact=item.target_artifact,
            member_id=item.member_id, member_sha256=item.member_sha256,
            verdict=verdict, reason=reason, provider=provider, requested_model=requested_model,
            observed_model=observed_model, prompt_sha256=prompt_sha256,
            response_schema_sha256=response_schema_sha256, decided_at=now(), actor=actor)
        relative = Path("delegations") / "decisions" / f"{decision.decision_id}.json"
        reference, data = self.store.reference(relative, decision, schema_version=decision.schema_version)
        return self._event_transition(current, actor, f"delegation.{verdict.value}",
                                      f"{verdict.value} delegated review of {item.subject_id}",
                                      [item_ref, reference], {relative: (reference, data)})


    @pack_transaction
    def complete_validation(self, actor: Actor, expected_revision: int) -> Workflow:
        current = self._current(expected_revision, WorkflowState.VALIDATING)
        self._require_promotion(current, ApprovalGate.ACCEPTANCE)
        pack_dir = self.root / "releases" / self.pack
        self._require_checkpoint_current(CheckpointKind.FACT_REVIEW, {
            "curation": pack_dir / "curation.yaml",
            "dispositions": pack_dir / "curation-coverage.yaml",
        })
        paths = {name: pack_dir / name for name in (
            "release.json", "build-report.json", "curation-coverage.json",
            "acceptance.yaml", "acceptance-report.json", "regression.yaml",
            "regression-report.json", "semantic-index.json")}
        release = load_release(paths["release.json"])
        build, coverage, acceptance = (self._json(paths[name]) for name in (
            "build-report.json", "curation-coverage.json", "acceptance-report.json"))
        suite = load_acceptance(paths["acceptance.yaml"])
        regression_acceptance, regression_suite, _ = load_regression(pack_dir)
        regression = self._json(paths["regression-report.json"])
        semantic_index = load_index(paths["semantic-index.json"], release)
        expected_semantic_binding = semantic_index_binding(paths["semantic-index.json"], semantic_index)
        digest = release.manifest.content_sha256
        if (build.get("release_id") != release.manifest.release_id or build.get("dropped")
            or build.get("content_sha256") != digest
            or build.get("release_sha256") != self._sha256_path(paths["release.json"])
            or build.get("curation_sha256") != self._sha256_path(pack_dir / "curation.yaml")
            or build.get("text_index_sha256") != self._sha256_path(self.store.directory.parent / "text" / "index.json")
            or build.get("acceptance_suite_sha256") != suite.digest()
            or release.manifest.acceptance_suite_sha256 != suite.digest()):
            raise ValueError("build report is stale or contains dropped facts")
        if (coverage.get("content_sha256") != digest or coverage.get("policy") != "enforce"
            or coverage.get("dispositions_sha256") != self._sha256_path(pack_dir / "curation-coverage.yaml")
                or coverage.get("clean") is not True or coverage.get("counts", {}).get("unclassified_sections") != 0):
            raise ValueError("enforced curation coverage is not clean for this release")
        if (acceptance.get("content_sha256") != digest or acceptance.get("suite_sha256") != suite.digest()
                or acceptance.get("passed") is not True):
            raise ValueError("acceptance report is stale or has blocking failures")
        hybrid = regression.get("runs", {}).get("hybrid", {})
        if (regression.get("content_sha256") != digest
                or regression.get("acceptance_sha256") != regression_acceptance.digest()
                or regression.get("regression_sha256") != regression_suite.digest()
                or regression.get("semantic_index") != expected_semantic_binding
                or regression.get("passed") is not True
                or hybrid.get("retrieval_mode") != "hybrid" or hybrid.get("failed")):
            raise ValueError("regression report is stale, incomplete or has blocking failures")
        return self._checkpoint_transition(current, CheckpointKind.VALIDATION, actor,
                                           WorkflowState.AWAITING_ATTESTATION, paths,
                                           {"release_id": release.manifest.release_id,
                                            "content_sha256": digest,
                                            "cases": acceptance.get("cases", 0)},
                                           "Build, coverage and acceptance checks passed")

    @pack_transaction
    def record_attestation(self, actor: Actor, expected_revision: int) -> Workflow:
        current = self._current(expected_revision, WorkflowState.AWAITING_ATTESTATION)
        if actor.kind != ActorKind.HUMAN:
            raise ValueError("attestation requires a person")
        pack_dir = self.root / "releases" / self.pack
        release_path, readiness_path = pack_dir / "release.json", pack_dir / "readiness.json"
        self._require_checkpoint_current(CheckpointKind.VALIDATION, {
            "release.json": release_path,
            "build-report.json": pack_dir / "build-report.json",
            "curation-coverage.json": pack_dir / "curation-coverage.json",
            "acceptance.yaml": pack_dir / "acceptance.yaml",
            "acceptance-report.json": pack_dir / "acceptance-report.json",
            "regression.yaml": pack_dir / "regression.yaml",
            "regression-report.json": pack_dir / "regression-report.json",
            "semantic-index.json": pack_dir / "semantic-index.json",
        })
        from ..pipeline import Pipeline
        report = Pipeline(self.root, self.pack, run_dir=self.store.directory.parent,
                          thorough=True, attested_by=actor.actor_id,
                          lock_pack=False, log=lambda message: None).run_stages("ready", "ready")
        if report["exit_code"]:
            failure = next((stage.get("error") for stage in report["stages"]
                            if stage.get("status") == "failed"), "readiness pipeline failed")
            raise ValueError(failure)
        self._require_checkpoint_current(CheckpointKind.VALIDATION, {
            "release.json": release_path,
            "build-report.json": pack_dir / "build-report.json",
            "curation-coverage.json": pack_dir / "curation-coverage.json",
            "acceptance.yaml": pack_dir / "acceptance.yaml",
            "acceptance-report.json": pack_dir / "acceptance-report.json",
            "regression.yaml": pack_dir / "regression.yaml",
            "regression-report.json": pack_dir / "regression-report.json",
            "semantic-index.json": pack_dir / "semantic-index.json",
        })
        status = readiness_status(release_path)
        if status.get("status") != "ready":
            raise ValueError(f"release is not ready: {status.get('reason')}")
        readiness = load_readiness(readiness_path)
        if readiness.attested_by != actor.actor_id:
            raise ValueError("readiness record was attested by another actor")
        release = load_release(release_path)
        suite = load_acceptance(pack_dir / "acceptance.yaml")
        if readiness.content_sha256 != release.manifest.content_sha256 or readiness.suite_sha256 != suite.digest():
            raise ValueError("readiness record is for another release content or acceptance suite")
        return self._checkpoint_transition(current, CheckpointKind.READINESS, actor,
                                           WorkflowState.READY,
                                           {"release": release_path, "readiness": readiness_path,
                                            "acceptance": pack_dir / "acceptance.yaml",
                                            "pipeline-report": pack_dir / "pipeline-report.json"},
                                           {"release_id": readiness.release_id,
                                            "gates": len(readiness.gates)},
                                           "Human attestation and readiness record verified",
                                           approved_gate=ApprovalGate.ATTESTATION)

    def submit(self, gate: ApprovalGate, summary: str, details: dict, actor: Actor,
               expected_revision: int, input_sha256s: dict[str, str] | None = None,
               artifacts: dict[str, Path] | None = None,
               promotions: list[PromotionSpec] | None = None) -> Workflow:
        current = self.store.load()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if actor.kind not in {ActorKind.COORDINATOR, ActorKind.SYSTEM}:
            raise ValueError("proposals are submitted by the coordinator or the system")
        if gate not in SUBMIT_STATES or current.state != SUBMIT_STATES[gate]:
            raise ValueError(f"cannot submit {gate.value} while workflow is {current.state.value}")
        if gate == ApprovalGate.SCOPE:
            if (not isinstance(details.get("scope_statement"), str)
                    or not details["scope_statement"].strip()
                    or not isinstance(details.get("out_of_scope"), list)
                    or not details["out_of_scope"]
                    or not isinstance(details.get("questions"), list)
                    or not details["questions"]):
                raise ValueError("A1 details need scope_statement, out_of_scope and questions")
            policy = self.store.load_policy()
            details = {**details, "workflow_policy": policy.model_dump(mode="json")}
        if gate == ApprovalGate.EXCEPTIONS and not self._has_checkpoint(CheckpointKind.EXTRACTION):
            raise ValueError("cannot submit acquisition exceptions before extraction validates")
        proposal_inputs = dict(input_sha256s or {})
        if gate == ApprovalGate.SCOPE:
            proposal_inputs["workflow-policy"] = current.policy.sha256
        if gate in {ApprovalGate.CATALOGUE, ApprovalGate.KNOWLEDGE_DESIGN, ApprovalGate.ACCEPTANCE}:
            self._require_promotion(current, ApprovalGate.SCOPE)
            scope_progress = current.gates[ApprovalGate.SCOPE]
            proposal_inputs["scope-proposal"] = scope_progress.proposal.sha256
        if gate == ApprovalGate.CATALOGUE:
            if artifacts is None or promotions is None:
                raise ValueError("A2 needs promoted catalogue artifacts")
            catalogue_spec = next((item for item in promotions
                                   if Path(item.destination).name == "sources.json"), None)
            inventory_spec = next((item for item in promotions
                                   if Path(item.destination).name == "sources.md"), None)
            if (catalogue_spec is None or catalogue_spec.artifact not in artifacts
                    or inventory_spec is None or inventory_spec.artifact not in artifacts):
                raise ValueError("A2 must promote sources.json and sources.md")
            validate_source_catalog(json.loads(Path(artifacts[catalogue_spec.artifact]).read_text(encoding="utf-8")))
            Path(artifacts[inventory_spec.artifact]).read_text(encoding="utf-8")
            build_plan(Path(artifacts[catalogue_spec.artifact]), Path(artifacts[inventory_spec.artifact]),
                       scan_set=None, source_ids=None, workers=1)
        if gate == ApprovalGate.EXCEPTIONS:
            run = self.store.directory.parent
            self._require_checkpoint_current(CheckpointKind.ACQUISITION, {
                "summary": run / "summary.json",
            })
            self._refresh_gap_report(run)
            checkpoint_ref = self._latest_checkpoint_ref(CheckpointKind.EXTRACTION)
            checkpoint = self._latest_checkpoint(CheckpointKind.EXTRACTION)
            current_dataset = self._dataset_sha256(self.store.directory.parent / "text")
            if checkpoint.details.get("dataset_sha256") != current_dataset:
                raise WorkflowConflict("text dataset changed after extraction validation")
            supplied = proposal_inputs.get("extraction-checkpoint")
            if supplied is not None and supplied != checkpoint_ref.sha256:
                raise WorkflowConflict("A3 proposal names another extraction checkpoint")
            proposal_inputs["extraction-checkpoint"] = checkpoint_ref.sha256
            gap_path = self.store.directory.parent / "gap-report.json"
            gap_report = self._json(gap_path)
            counts = gap_report.get("counts", {})
            outstanding = counts.get("outstanding")
            if type(outstanding) is not int:
                gap_counts = counts.get("gap", {})
                outstanding = sum(value for key, value in gap_counts.items()
                                  if key not in {"none", "navigation-page", "javascript-shell"}
                                  and type(value) is int)
            if outstanding:
                raise ValueError(f"A3 has {outstanding} acquisition exception(s) without a typed human disposition")
            gap_digest = self._sha256_path(gap_path)
            details = {**details, "outstanding": outstanding,
                       "gap_report_sha256": gap_digest}
            exception_report = gap_path.read_bytes()
        else:
            exception_report = None
        if gate == ApprovalGate.KNOWLEDGE_DESIGN:
            checkpoint = self._latest_checkpoint(CheckpointKind.EXTRACTION)
            if checkpoint.details.get("dataset_sha256") != self._dataset_sha256(self.store.directory.parent / "text"):
                raise WorkflowConflict("text dataset changed before knowledge-design submission")
            if artifacts is None or promotions is None:
                raise ValueError("A4 needs promoted curation artifacts")
            curation_spec = next((item for item in promotions if Path(item.destination).name == "curation.yaml"), None)
            if curation_spec is None or curation_spec.artifact not in artifacts:
                raise ValueError("A4 must promote curation.yaml")
            proposed_curation = load_curation(artifacts[curation_spec.artifact])
            if proposed_curation.pack != self.pack:
                raise ValueError(f"A4 curation is for pack {proposed_curation.pack!r}, not {self.pack!r}")
            scope = self._approved_proposal(ApprovalGate.SCOPE)
            if (proposed_curation.scope_statement != scope.details["scope_statement"]
                    or proposed_curation.out_of_scope != scope.details["out_of_scope"]):
                raise ValueError("A4 curation scope differs from the approved A1 proposal")
            curated_questions = {question for concept in proposed_curation.concepts for question in concept.questions}
            missing_questions = [question for question in scope.details["questions"]
                                 if question not in curated_questions]
            if missing_questions:
                raise ValueError(f"A4 curation omits approved A1 questions: {missing_questions}")
            dispositions_spec = next((item for item in promotions
                                      if Path(item.destination).name == "curation-coverage.yaml"), None)
            if dispositions_spec is None or dispositions_spec.artifact not in artifacts:
                raise ValueError("A4 must promote curation-coverage.yaml")
            dispositions = load_dispositions(artifacts[dispositions_spec.artifact])
            if dispositions.pack != self.pack:
                raise ValueError(f"A4 coverage dispositions are for pack {dispositions.pack!r}, not {self.pack!r}")
        if gate == ApprovalGate.ACCEPTANCE:
            if artifacts is None or promotions is None:
                raise ValueError("A5 needs promoted acceptance artifacts")
            acceptance_spec = next((item for item in promotions
                                    if Path(item.destination).name == "acceptance.yaml"), None)
            if acceptance_spec is None or acceptance_spec.artifact not in artifacts:
                raise ValueError("A5 must promote acceptance.yaml")
            suite = load_acceptance(artifacts[acceptance_spec.artifact])
            if suite.pack != self.pack:
                raise ValueError(f"A5 acceptance suite is for pack {suite.pack!r}, not {self.pack!r}")
            scope = self._approved_proposal(ApprovalGate.SCOPE)
            suite_questions = {case.question for case in suite.cases}
            missing_questions = [question for question in scope.details["questions"]
                                 if question not in suite_questions]
            if missing_questions:
                raise ValueError(f"A5 acceptance suite omits approved A1 questions: {missing_questions}")
            regression_spec = next((item for item in promotions
                                    if Path(item.destination).name == "regression.yaml"), None)
            if regression_spec is None or regression_spec.artifact not in artifacts:
                raise ValueError("A5 must promote regression.yaml")
            regression_suite = load_acceptance(artifacts[regression_spec.artifact])
            if regression_suite.pack != self.pack:
                raise ValueError(f"A5 regression suite is for pack {regression_suite.pack!r}, not {self.pack!r}")
            duplicate_cases = sorted({case.case_id for case in suite.cases}
                                     & {case.case_id for case in regression_suite.cases})
            if duplicate_cases:
                raise ValueError(f"A5 acceptance and regression suites duplicate case IDs: {duplicate_cases}")
        required = self._required_promotions(gate)
        if required:
            promoted_names = {Path(item.destination).name for item in (promotions or [])}
            missing = required - promoted_names
            if missing:
                raise ValueError(f"{gate.value} proposal omits required promoted artifacts: {sorted(missing)}")
            named_artifacts = set(artifacts or {})
            absent = sorted(item.artifact for item in (promotions or []) if item.artifact not in named_artifacts)
            if absent:
                raise ValueError(f"{gate.value} promotions name missing proposal artifacts: {absent}")
        proposal_revision = 1 + sum(1 for event in self.store.events(10_000)
                                    if event.kind == "proposal.submitted" and event.gate == gate)
        prepared = {}
        artifact_refs = {}
        artifact_bytes = {}
        artifact_directory = Path("proposals") / gate.value / f"{proposal_revision:04d}-artifacts"
        drafts = (self.store.directory.parent / "drafts").resolve()
        proposal_bytes = len(exception_report or b"")
        for name, source in (artifacts or {}).items():
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                raise ValueError(f"invalid proposal artifact name: {name}")
            source = Path(source).resolve()
            if not source.is_relative_to(drafts) or not source.is_file() or source.is_symlink():
                raise ValueError(f"proposal artifacts must be regular files under {drafts}: {source}")
            size = source.stat().st_size
            if size > MAX_WORKFLOW_ARTIFACT_BYTES:
                raise ValueError(f"proposal artifact exceeds {MAX_WORKFLOW_ARTIFACT_BYTES} bytes: {source}")
            proposal_bytes += size
            if proposal_bytes > MAX_WORKFLOW_PACKET_BYTES:
                raise ValueError("proposal artifacts exceed the aggregate workflow size limit")
            relative = artifact_directory / f"{name}-{source.name}"
            reference, data = self.store.reference(relative, source.read_bytes(),
                                                   media_type="application/octet-stream")
            artifact_refs[name] = reference
            artifact_bytes[name] = data
            prepared[relative] = (reference, data)
        if exception_report is not None:
            relative = artifact_directory / "gap-report.json"
            reference, data = self.store.reference(relative, exception_report,
                                                   media_type="application/json")
            artifact_refs["gap-report"] = reference
            artifact_bytes["gap-report"] = data
            prepared[relative] = (reference, data)
        delegation_uses = self._delegation_uses(gate, artifact_bytes, promotions or [])
        proposal = Proposal(proposal_id=str(uuid.uuid4()), workflow_id=current.workflow_id, gate=gate,
                            revision=proposal_revision,
                            summary=summary, details=details, input_sha256s=proposal_inputs,
                            artifacts=artifact_refs, promotions=promotions or [], delegation_uses=delegation_uses,
                            created_at=now(), created_by=actor)
        relative = Path("proposals") / gate.value / f"{proposal.revision:04d}.json"
        proposal_ref, proposal_data = self.store.reference(relative, proposal,
                                   schema_version=proposal.schema_version)
        prepared[relative] = (proposal_ref, proposal_data)
        updated = current.model_copy(deep=True)
        updated.state = PENDING_STATES[gate]
        updated.updated_at = now()
        updated.revision += 1
        updated.event_sequence += 1
        updated.gates[gate] = GateProgress(status=GateStatus.PENDING, proposal=proposal_ref)
        delegated_refs = [reference for use in delegation_uses for reference in (use.item, use.decision)]
        event = self._event(current, updated, "proposal.submitted", actor, proposal.summary, gate,
                    [proposal_ref, *artifact_refs.values(), *delegated_refs])
        updated.last_event_sha256 = event.sha256
        return self.store.commit(expected_revision, updated, event, prepared)

    def promote(self, gate: ApprovalGate, actor: Actor, expected_revision: int) -> Workflow:
        if actor.kind not in {ActorKind.COORDINATOR, ActorKind.SYSTEM}:
            raise ValueError("a coordinator or system promotes approved artifacts")
        with self.store.pack_locked(), self.store.workflow_locked_under_pack():
            current = self.store._load_verified_unlocked()
            if current.revision != expected_revision:
                raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
            progress = current.gates[gate]
            if progress.status != GateStatus.APPROVED or progress.proposal is None or progress.decision is None:
                raise ValueError(f"{gate.value} has no approved proposal to promote")
            proposal = Proposal.model_validate_json(self.store._read_artifact(progress.proposal))
            artifact_bytes = {name: self.store._read_artifact(reference)
                              for name, reference in proposal.artifacts.items()}
            events = self.store._events_unverified()
            expected_uses = self._delegation_uses(gate, artifact_bytes, proposal.promotions,
                                                  events=events, unlocked=True)
            if expected_uses != proposal.delegation_uses:
                raise WorkflowConflict("delegated support decisions changed after proposal submission")
            if not proposal.promotions:
                raise ValueError(f"{gate.value} proposal has no artifacts to promote")
            promoted_names = {Path(item.destination).name for item in proposal.promotions}
            required = self._required_promotions(gate)
            missing = required - promoted_names
            if missing:
                raise ValueError(f"{gate.value} proposal omits required promoted artifacts: {sorted(missing)}")
            if any(event.kind == "proposal.promoted" and event.gate == gate
                   and event.metrics.get("proposal_sha256") == progress.proposal.sha256 for event in events):
                raise ValueError(f"{gate.value} proposal is already promoted")
            allowed, outputs, writes = PROMOTION_DESTINATIONS.get(gate, ()), {}, []
            for item in proposal.promotions:
                if not any(pattern.fullmatch(item.destination) for pattern in allowed):
                    raise ValueError(f"{gate.value} cannot promote to {item.destination}")
                if (gate == ApprovalGate.SCOPE
                        and item.destination != f"docs/{self.pack}-acceptance-questions.md"):
                    raise ValueError("scope proposal can promote only this pack's acceptance questions")
                expected_prefix = f"releases/{self.pack}/" if item.destination.startswith("releases/") else None
                if expected_prefix and not item.destination.startswith(expected_prefix):
                    raise ValueError("proposal cannot promote into another pack")
                destination = (self.root / item.destination).resolve()
                if not destination.is_relative_to(self.root):
                    raise ValueError("promotion destination leaves the packs workspace")
                unresolved = self.root
                for part in Path(item.destination).parts:
                    unresolved /= part
                    if unresolved.is_symlink():
                        raise ValueError(f"promotion destination uses a symlink: {item.destination}")
                source = proposal.artifacts[item.artifact]
                data = self.store._read_artifact(source)
                before = self._sha256_path(destination) if destination.is_file() else None
                if before == source.sha256:
                    outputs[item.destination] = source.sha256
                    continue
                if destination.is_file() and item.expected_sha256 is None:
                    raise WorkflowConflict(f"{item.destination} exists but the proposal names no expected SHA-256")
                if item.expected_sha256 != before:
                    raise WorkflowConflict(f"{item.destination} changed since the proposal was prepared")
                writes.append((destination, data, destination.read_bytes() if destination.is_file() else None))
                outputs[item.destination] = source.sha256
            receipt = PromotionReceipt(receipt_id=str(uuid.uuid4()), workflow_id=current.workflow_id,
                                       gate=gate, proposal_sha256=progress.proposal.sha256,
                                       outputs=outputs, promoted_at=now(), promoted_by=actor)
            relative = Path("promotions") / f"{gate.value}-{receipt.receipt_id}.json"
            reference, receipt_data = self.store.reference(relative, receipt,
                                                           schema_version=receipt.schema_version)
            updated = current.model_copy(deep=True)
            updated.updated_at = now()
            updated.revision += 1
            updated.event_sequence += 1
            event = self._event(current, updated, "proposal.promoted", actor,
                                f"Promoted approved {gate.value} artifacts", gate=gate,
                                artifacts=[reference], metrics={"proposal_sha256": progress.proposal.sha256})
            updated.last_event_sha256 = event.sha256
            written = []
            readiness_path = self.root / "releases" / self.pack / "readiness.json"
            readiness_before = readiness_path.read_bytes() if readiness_path.is_file() else None
            transaction_dir = self.store.directory / "transactions" / receipt.receipt_id
            transaction_dir.mkdir(parents=True)
            transaction_writes = []
            for number, (destination, _, before_data) in enumerate(writes, 1):
                backup = None
                if before_data is not None:
                    backup_path = transaction_dir / f"backup-{number:03d}.bin"
                    atomic_write(backup_path, before_data)
                    backup = backup_path.relative_to(self.store.directory.parent).as_posix()
                transaction_writes.append({
                    "destination": destination.relative_to(self.root).as_posix(),
                    "backup": backup,
                    "backup_sha256": sha256_bytes(before_data) if before_data is not None else None,
                })
            if gate == ApprovalGate.ACCEPTANCE and readiness_before is not None:
                backup_path = transaction_dir / "readiness.json"
                atomic_write(backup_path, readiness_before)
                transaction_writes.append({
                    "destination": readiness_path.relative_to(self.root).as_posix(),
                    "backup": backup_path.relative_to(self.store.directory.parent).as_posix(),
                    "backup_sha256": sha256_bytes(readiness_before),
                })
            transaction_marker = self.store.directory.parent / ".pack-transaction.json"
            atomic_write(transaction_marker, canonical_json({
                "schema_version": "swisstip.pack-transaction/v2",
                "transaction_dir": transaction_dir.relative_to(self.store.directory.parent).as_posix(),
                "committed_event_sha256": event.sha256,
                "previous_event_sha256": current.last_event_sha256,
                "event_bytes_before": self.store.events_path.stat().st_size,
                "event": event.model_dump(mode="json", exclude_none=True),
                "proposal": progress.proposal.model_dump(mode="json", exclude_none=True),
                "prepared_artifacts": [relative.as_posix()],
                "writes": transaction_writes,
            }))
            committed = False
            try:
                for destination, data, before_data in writes:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, destination)
                    written.append((destination, before_data))
                if gate == ApprovalGate.ACCEPTANCE:
                    readiness_path.unlink(missing_ok=True)
                self.store.commit_unlocked(current.revision, updated, event,
                                           {relative: (reference, receipt_data)})
                committed = True
            except Exception:
                if not committed:
                    for destination, before_data in reversed(written):
                        if before_data is None:
                            destination.unlink(missing_ok=True)
                        else:
                            atomic_write(destination, before_data)
                    if gate == ApprovalGate.ACCEPTANCE and readiness_before is not None:
                        atomic_write(readiness_path, readiness_before)
                    transaction_marker.unlink(missing_ok=True)
                    shutil.rmtree(transaction_dir, ignore_errors=True)
                raise
            try:
                transaction_marker.unlink(missing_ok=True)
                shutil.rmtree(transaction_dir, ignore_errors=True)
            except OSError:
                # Pack files and workflow receipt committed together; recovery performs cleanup later.
                pass
        return self.store.load_verified()

    def decide(self, gate: ApprovalGate, decision: Decision, proposal_sha256: str, note: str,
               actor: Actor, expected_revision: int) -> Workflow:
        current = self.store.load()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if gate not in PENDING_STATES or current.state != PENDING_STATES[gate]:
            raise ValueError(f"{gate.value} is not pending while workflow is {current.state.value}")
        progress = current.gates[gate]
        if progress.proposal is None or progress.proposal.sha256 != proposal_sha256:
            raise WorkflowConflict("proposal changed; reload it before deciding")
        policy = self.store.load_policy()
        record = HumanDecision(decision_id=str(uuid.uuid4()), workflow_id=current.workflow_id, gate=gate,
                               proposal_sha256=proposal_sha256, policy_sha256=current.policy.sha256,
                               workflow_revision=current.revision, decision=decision, note=note,
                               decided_at=now(), actor=actor)
        relative = Path("approvals") / gate.value / f"{current.revision:04d}-{record.decision_id}.json"
        decision_ref, decision_data = self.store.reference(relative, record,
                                                           schema_version=record.schema_version)
        # Loading validates the immutable policy floor again immediately before transition.
        if policy.review_mode != current.review_mode:
            raise WorkflowConflict("workflow policy and review mode disagree")
        updated = current.model_copy(deep=True)
        updated.updated_at = now()
        updated.revision += 1
        updated.event_sequence += 1
        if decision == Decision.APPROVE:
            updated.state = APPROVED_STATES[gate]
            updated.gates[gate] = GateProgress(status=GateStatus.APPROVED, proposal=progress.proposal,
                                               decision=decision_ref, reason=note or None)
        elif decision == Decision.REQUEST_CHANGES:
            updated.state = REVISION_STATES[gate]
            updated.gates[gate] = GateProgress(status=GateStatus.CHANGES_REQUESTED,
                                               proposal=progress.proposal, decision=decision_ref, reason=note)
        else:
            updated.state = WorkflowState.CANCELLED
            updated.gates[gate] = GateProgress(status=GateStatus.REJECTED, proposal=progress.proposal,
                                               decision=decision_ref, reason=note)
        event = self._event(current, updated, f"proposal.{decision.value}", actor,
                            f"{decision.value} {gate.value}: {note}".rstrip(": "), gate, [decision_ref])
        updated.last_event_sha256 = event.sha256
        return self.store.commit(expected_revision, updated, event,
                     {relative: (decision_ref, decision_data)})

    def _event(self, current: Workflow, updated: Workflow, kind: str, actor: Actor, summary: str,
               gate: ApprovalGate | None = None, artifacts=None, metrics=None) -> Event:
        event = Event(event_id=str(uuid.uuid4()), workflow_id=current.workflow_id,
                      workflow_revision=updated.revision, sequence=updated.event_sequence, at=now(), kind=kind,
                      actor=actor, state_before=current.state, state_after=updated.state, gate=gate,
                      artifacts=artifacts or [], summary=summary, metrics=metrics or {},
                      previous_sha256=current.last_event_sha256,
                      workflow_sha256=workflow_hash(updated), sha256="0" * 64)
        event.sha256 = event_hash(event)
        return event

    def _current(self, expected_revision: int, state: WorkflowState) -> Workflow:
        current = self.store.load()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if current.state != state:
            raise ValueError(f"workflow is {current.state.value}, expected {state.value}")
        return current

    def _has_checkpoint(self, kind: CheckpointKind) -> bool:
        return any(event.kind == f"checkpoint.{kind.value}" for event in self.store.events(10_000))

    def _require_promotion(self, workflow: Workflow, gate: ApprovalGate,
                           mutable_destinations: set[str] | None = None) -> None:
        progress = workflow.gates[gate]
        if progress.proposal is None:
            raise ValueError(f"{gate.value} has no approved proposal")
        matching = [event for event in self.store.events(10_000)
                    if event.kind == "proposal.promoted" and event.gate == gate
                    and event.metrics.get("proposal_sha256") == progress.proposal.sha256]
        if not matching:
            raise ValueError(f"approved {gate.value} artifacts have not been promoted")
        receipt_ref = next((reference for reference in matching[-1].artifacts
                            if reference.schema_version == "swisstip.autopilot-promotion/v1"), None)
        if receipt_ref is None:
            raise WorkflowConflict(f"{gate.value} promotion event has no receipt")
        receipt = PromotionReceipt.model_validate_json(self.store.read_artifact(receipt_ref))
        missing = self._required_promotions(gate) - {Path(item).name for item in receipt.outputs}
        if missing:
            raise WorkflowConflict(f"{gate.value} promotion receipt omits required artifacts: {sorted(missing)}")
        mutable_destinations = mutable_destinations or set()
        for destination, digest in receipt.outputs.items():
            if destination in mutable_destinations:
                continue
            path = (self.root / destination).resolve()
            if not path.is_relative_to(self.root) or self._sha256_path(path) != digest:
                raise WorkflowConflict(f"promoted artifact changed after approval: {destination}")

    def _latest_checkpoint_ref(self, kind: CheckpointKind):
        for event in reversed(self.store.events(10_000)):
            if event.kind != f"checkpoint.{kind.value}":
                continue
            reference = next((item for item in event.artifacts
                              if item.schema_version == "swisstip.autopilot-checkpoint/v1"), None)
            if reference is None:
                raise WorkflowConflict(f"{kind.value} checkpoint event has no checkpoint record")
            return reference
        raise ValueError(f"no {kind.value} checkpoint has been recorded")

    def _approved_proposal(self, gate: ApprovalGate) -> Proposal:
        workflow = self.store.load()
        progress = workflow.gates[gate]
        if progress.status != GateStatus.APPROVED or progress.proposal is None:
            raise ValueError(f"{gate.value} has no approved proposal")
        return Proposal.model_validate_json(self.store.read_artifact(progress.proposal))

    def _approved_curation(self):
        proposal = self._approved_proposal(ApprovalGate.KNOWLEDGE_DESIGN)
        spec = next((item for item in proposal.promotions
                     if Path(item.destination).name == "curation.yaml"), None)
        if spec is None or spec.artifact not in proposal.artifacts:
            raise WorkflowConflict("approved knowledge design has no curation artifact")
        return parse_curation(self.store.read_artifact(proposal.artifacts[spec.artifact]).decode("utf-8"))

    @staticmethod
    def _require_approved_curation_structure(approved, current) -> None:
        if (approved.model_dump(mode="json", exclude={"concepts"})
                != current.model_dump(mode="json", exclude={"concepts"})):
            raise WorkflowConflict("curation structure outside facts changed after A4 approval")
        approved_concepts = {concept.concept_id: concept for concept in approved.concepts}
        current_ids = {concept.concept_id for concept in current.concepts}
        if not current_ids <= approved_concepts.keys():
            raise WorkflowConflict("curation added a concept after A4 approval")
        for concept in current.concepts:
            approved_concept = approved_concepts[concept.concept_id]
            if (concept.model_dump(mode="json", exclude={"facts"})
                    != approved_concept.model_dump(mode="json", exclude={"facts"})):
                raise WorkflowConflict(f"concept {concept.concept_id} changed after A4 approval")
            approved_facts = {fact.fact_id for fact in approved_concept.facts}
            current_facts = {fact.fact_id for fact in concept.facts}
            if not current_facts <= approved_facts:
                raise WorkflowConflict(f"concept {concept.concept_id} added facts after A4 approval")

    def _current_fact_review_receipts(self, workflow: Workflow) -> dict[str, FactReviewReceipt]:
        proposal = workflow.gates[ApprovalGate.KNOWLEDGE_DESIGN].proposal
        if proposal is None:
            raise ValueError("knowledge design has no approved proposal")
        receipts = {}
        for event in self.store.events(10_000):
            if event.kind != "fact-review.recorded":
                continue
            reference = next((item for item in event.artifacts
                              if item.schema_version == "swisstip.autopilot-fact-review/v1"), None)
            if reference is None:
                raise WorkflowConflict("fact-review event has no receipt")
            receipt = FactReviewReceipt.model_validate_json(self.store.read_artifact(reference))
            if (receipt.workflow_id != workflow.workflow_id
                    or receipt.knowledge_design_proposal_sha256 != proposal.sha256):
                continue
            for fact_id in receipt.fact_sha256s:
                receipts[fact_id] = receipt
        return receipts

    def _required_promotions(self, gate: ApprovalGate) -> frozenset[str]:
        if gate == ApprovalGate.SCOPE:
            return frozenset({f"{self.pack}-acceptance-questions.md"})
        return REQUIRED_PROMOTIONS.get(gate, frozenset())

    def _latest_checkpoint(self, kind: CheckpointKind) -> PhaseCheckpoint:
        reference = self._latest_checkpoint_ref(kind)
        return PhaseCheckpoint.model_validate_json(self.store.read_artifact(reference))

    def _require_checkpoint_current(self, kind: CheckpointKind,
                                    paths: dict[str, Path]) -> None:
        checkpoint = self._latest_checkpoint(kind)
        for name, path in paths.items():
            reference = checkpoint.inputs.get(name)
            if reference is None:
                raise WorkflowConflict(f"{kind.value} checkpoint omitted {name}")
            if self._sha256_path(path) != reference.sha256:
                raise WorkflowConflict(f"{name} changed after the {kind.value} checkpoint")

    def _checkpoint_transition(self, current: Workflow, kind: CheckpointKind, actor: Actor,
                               state: WorkflowState, paths: dict[str, Path], details: dict,
                               summary: str, approved_gate: ApprovalGate | None = None) -> Workflow:
        prepared = {}
        references = {}
        input_directory = Path("checkpoints") / f"{current.revision + 1:04d}-{kind.value}-inputs"
        for name, path in paths.items():
            path = Path(path)
            if not path.is_file():
                raise ValueError(f"required workflow input does not exist: {path}")
            relative = input_directory / f"{name}-{path.name}"
            reference, data = self.store.reference(relative, path.read_bytes(),
                                                   media_type="application/octet-stream")
            references[name] = reference
            prepared[relative] = (reference, data)
        checkpoint = PhaseCheckpoint(checkpoint_id=str(uuid.uuid4()), workflow_id=current.workflow_id,
                                     kind=kind, inputs=references, details=details, created_at=now(),
                                     created_by=actor)
        relative = Path("checkpoints") / f"{current.revision + 1:04d}-{kind.value}.json"
        reference, data = self.store.reference(relative, checkpoint, schema_version=checkpoint.schema_version)
        prepared[relative] = (reference, data)
        updated = current.model_copy(deep=True)
        updated.state = state
        updated.updated_at = now()
        updated.revision += 1
        updated.event_sequence += 1
        if approved_gate is not None:
            updated.gates[approved_gate] = GateProgress(status=GateStatus.APPROVED,
                                                        decision=reference, reason=summary)
        event = self._event(current, updated, f"checkpoint.{kind.value}", actor, summary,
                            gate=approved_gate, artifacts=[reference, *references.values()])
        updated.last_event_sha256 = event.sha256
        return self.store.commit(current.revision, updated, event, prepared)

    def _event_transition(self, current: Workflow, actor: Actor, kind: str, summary: str,
                          artifacts: list, prepared: dict, gate: ApprovalGate | None = None,
                          metrics: dict | None = None) -> Workflow:
        updated = current.model_copy(deep=True)
        updated.updated_at = now()
        updated.revision += 1
        updated.event_sequence += 1
        event = self._event(current, updated, kind, actor, summary, gate=gate,
                    artifacts=artifacts, metrics=metrics)
        updated.last_event_sha256 = event.sha256
        return self.store.commit(current.revision, updated, event, prepared)

    def _delegated_subject(self, decision_class: DecisionClass, subject_id: str,
                           data: bytes) -> tuple[str, str]:
        if decision_class == DecisionClass.SOURCE_METADATA:
            try:
                value = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("source-metadata delegation subject must be UTF-8 JSON") from exc
            if not isinstance(value, dict) or set(value) != {"source_id", "title", "notes"}:
                raise ValueError("source-metadata subject permits only source_id, title and notes")
            if value.get("source_id") != subject_id:
                raise ValueError("source-metadata subject_id must equal source_id")
            if any(not isinstance(value.get(name), str) or not value[name].strip()
                   for name in ("source_id", "title", "notes")):
                raise ValueError("source-metadata fields must be non-empty strings")
            canonical = {name: value[name] for name in ("source_id", "title", "notes")}
            return subject_id, sha256_bytes(canonical_json(canonical))
        if decision_class == DecisionClass.NON_BLOCKING_VARIANT:
            try:
                value = json.loads(data)
                case = Case.model_validate(value)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError("non-blocking-variant subject must be one valid case as UTF-8 JSON") from exc
            if case.case_id != subject_id:
                raise ValueError("non-blocking-variant subject_id must equal case_id")
            if (case.blocking or case.answer is not None or case.claims or case.not_served
                    or case.must_not_serve or not case.steps or any(step.search is None for step in case.steps)):
                raise ValueError("non-blocking-variant must be retrieval-only, non-blocking and have no answer or fact claims")
            return case.case_id, sha256_bytes(canonical_json(case))
        raise ValueError(f"{decision_class.value} has no deterministic delegated subject schema")

    def _artifact_members(self, decision_class: DecisionClass, data: bytes) -> dict[str, str]:
        if decision_class == DecisionClass.SOURCE_METADATA:
            try:
                catalogue = validate_source_catalog(json.loads(data))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError("delegated source metadata requires a valid sources.json artifact") from exc
            return {
                entry["definition"]["source_id"]: sha256_bytes(canonical_json({
                    "source_id": entry["definition"]["source_id"],
                    "title": entry["title"],
                    "notes": entry["notes"],
                }))
                for entry in catalogue["sources"]
            }
        if decision_class == DecisionClass.NON_BLOCKING_VARIANT:
            try:
                suite = parse_acceptance(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("delegated regression variants require a valid regression.yaml artifact") from exc
            if suite.pack != self.pack:
                raise ValueError(f"regression.yaml is for pack {suite.pack!r}, not {self.pack!r}")
            return {case.case_id: sha256_bytes(canonical_json(case)) for case in suite.cases}
        raise ValueError(f"{decision_class.value} has no deterministic artifact-member schema")

    def _delegation_uses(self, gate: ApprovalGate, artifact_bytes: dict[str, bytes],
                         promotions: list[PromotionSpec], *, events: list[Event] | None = None,
                         unlocked: bool = False) -> list[DelegationUse]:
        all_items = self._delegation_items(events=events, unlocked=unlocked)
        items = [(reference, item) for reference, item in all_items
                 if item.schema_version == "swisstip.autopilot-delegation-item/v2"
                 and item.target_gate == gate]
        decisions = self._delegated_decisions(events=events, unlocked=unlocked, items=all_items)
        decision_by_item = {decision.item_sha256: (decision_ref, decision)
                            for decision_ref, decision, _, _ in decisions}
        unfinished = [item.member_id for reference, item in items if reference.sha256 not in decision_by_item]
        if unfinished:
            raise ValueError(f"delegated review is unfinished for {sorted(unfinished)}")
        if not items:
            return []
        artifact_names = {Path(spec.destination).name: spec.artifact for spec in promotions}
        by_class: dict[DecisionClass, dict[str, str]] = {}
        for _, item in items:
            artifact_name = artifact_names.get(item.target_artifact)
            if artifact_name is None or artifact_name not in artifact_bytes:
                raise ValueError(f"{gate.value} proposal omits delegated target {item.target_artifact}")
            if item.decision_class not in by_class:
                by_class[item.decision_class] = self._artifact_members(
                    item.decision_class, artifact_bytes[artifact_name])
        seen = set()
        uses = []
        for item_ref, item in items:
            decision_ref, decision = decision_by_item[item_ref.sha256]
            identity = (item.decision_class, item.member_id, item.member_sha256)
            if identity in seen:
                raise WorkflowConflict(f"multiple delegated verdicts target the same member bytes: {item.member_id}")
            seen.add(identity)
            actual_sha256 = by_class[item.decision_class].get(item.member_id)
            exact = actual_sha256 == item.member_sha256
            if exact and decision.verdict == DelegationVerdict.REJECT:
                raise ValueError(f"delegated reviewer rejected {item.member_id}; omit or revise it before human review")
            if exact and decision.verdict == DelegationVerdict.APPROVE:
                handling = DelegationHandling.APPLIED
            elif actual_sha256 is None:
                handling = DelegationHandling.OMITTED
            else:
                handling = DelegationHandling.HUMAN_REVIEW
            uses.append(DelegationUse(
                item=item_ref, decision=decision_ref, decision_class=item.decision_class,
                target_gate=item.target_gate, target_artifact=item.target_artifact,
                member_id=item.member_id, member_sha256=item.member_sha256,
                verdict=decision.verdict, handling=handling, reason=decision.reason))
        return sorted(uses, key=lambda use: (use.decision_class.value, use.member_id, use.member_sha256))

    def _delegation_items(self, *, events: list[Event] | None = None,
                          unlocked: bool = False) -> list[tuple]:
        items = []
        events = events if events is not None else (
            self.store._events_unverified() if unlocked else self.store.events(10_000))
        read_artifact = self.store._read_artifact if unlocked else self.store.read_artifact
        for event in events:
            if event.kind != "delegation.prepared":
                continue
            for reference in event.artifacts:
                if reference.schema_version in {
                    "swisstip.autopilot-delegation-item/v1",
                    "swisstip.autopilot-delegation-item/v2",
                }:
                    item = DelegationItem.model_validate_json(read_artifact(reference))
                    if item.schema_version.endswith("/v2"):
                        if item.subject is None:
                            raise WorkflowConflict("typed delegation item has no subject artifact")
                        member_id, member_sha256 = self._delegated_subject(
                            item.decision_class, item.subject_id, read_artifact(item.subject))
                        if (member_id, member_sha256) != (item.member_id, item.member_sha256):
                            raise WorkflowConflict("delegation item target differs from its snapshotted subject")
                    items.append((reference, item))
        return items

    def _delegated_decisions(self, *, events: list[Event] | None = None, unlocked: bool = False,
                             items: list[tuple] | None = None) -> list[tuple]:
        events = events if events is not None else (
            self.store._events_unverified() if unlocked else self.store.events(10_000))
        read_artifact = self.store._read_artifact if unlocked else self.store.read_artifact
        items = {reference.sha256: (reference, item) for reference, item in (
            items if items is not None else self._delegation_items(events=events, unlocked=unlocked))}
        decisions = []
        for event in events:
            if not event.kind.startswith("delegation.") or event.kind == "delegation.prepared":
                continue
            for reference in event.artifacts:
                if reference.schema_version not in {
                    "swisstip.autopilot-delegated-decision/v1",
                    "swisstip.autopilot-delegated-decision/v2",
                }:
                    continue
                decision = DelegatedDecision.model_validate_json(read_artifact(reference))
                if decision.item_sha256 not in items:
                    raise WorkflowConflict("delegated decision names an unknown item")
                item_ref, item = items[decision.item_sha256]
                if decision.schema_version.endswith("/v2") and (
                        decision.decision_class != item.decision_class
                        or decision.subject_id != item.subject_id
                        or decision.subject_sha256 != item.subject_sha256
                        or decision.evidence_sha256s != item.evidence_sha256s
                        or decision.target_gate != item.target_gate
                        or decision.target_artifact != item.target_artifact
                        or decision.member_id != item.member_id
                        or decision.member_sha256 != item.member_sha256):
                    raise WorkflowConflict("delegated decision differs from its immutable item")
                decisions.append((reference, decision, item_ref, item))
        return decisions

    def _delegation_item(self, digest: str) -> tuple:
        for reference, item in self._delegation_items():
            if reference.sha256 == digest:
                return reference, item
        raise ValueError("no prepared delegation item has that SHA-256")

    @staticmethod
    def _sha256_path(path: Path) -> str:
        from .store import sha256_bytes
        if not Path(path).is_file():
            raise ValueError(f"required workflow input does not exist: {path}")
        return sha256_bytes(Path(path).read_bytes())

    @staticmethod
    def _json(path: Path) -> dict:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object at {path}")
        return value

    @staticmethod
    def _refresh_gap_report(run: Path) -> dict:
        from swisstip.ingestion import gap_report

        run = Path(run)
        report = gap_report.build_report(run)
        decisions = run / "review-decisions.json"
        report["review_decisions_sha256"] = sha256_bytes(decisions.read_bytes()) if decisions.is_file() else None
        atomic_write(run / "gap-report.json",
                     json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
        return report

    @staticmethod
    def _dataset_sha256(text: Path) -> str:
        from .store import sha256_bytes
        text = Path(text)
        paths = [text / "index.json", *sorted((text / "documents").glob("*.json"))]
        if not paths or any(not path.is_file() for path in paths):
            raise ValueError("text dataset index or documents are missing")
        digest_input = b"".join(path.relative_to(text).as_posix().encode("utf-8") + b"\0"
                                + sha256_bytes(path.read_bytes()).encode("ascii") + b"\n"
                                for path in paths)
        return sha256_bytes(digest_input)
