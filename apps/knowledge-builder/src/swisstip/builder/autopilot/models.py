"""Persisted contracts of the governed knowledge-base workflow."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from . import (DECISION_SCHEMA_VERSION, EVENT_SCHEMA_VERSION, POLICY_SCHEMA_VERSION,
               PROPOSAL_SCHEMA_VERSION, WORKFLOW_SCHEMA_VERSION)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReviewMode(StrEnum):
    FULL_REVIEW = "full-review"
    FAST_TRACK = "fast-track"


class WorkflowState(StrEnum):
    DRAFTING_SCOPE = "drafting_scope"
    AWAITING_SCOPE_APPROVAL = "awaiting_scope_approval"
    DISCOVERING_SOURCES = "discovering_sources"
    AWAITING_CATALOGUE_APPROVAL = "awaiting_catalogue_approval"
    AWAITING_DOWNLOAD_CONFIRMATION = "awaiting_download_confirmation"
    ACQUIRING = "acquiring"
    EXTRACTING = "extracting"
    AWAITING_EXCEPTION_APPROVAL = "awaiting_exception_approval"
    GENERATING_KNOWLEDGE = "generating_knowledge"
    AWAITING_KNOWLEDGE_DESIGN_APPROVAL = "awaiting_knowledge_design_approval"
    AWAITING_FACT_REVIEW = "awaiting_fact_review"
    GENERATING_TESTS = "generating_tests"
    AWAITING_ACCEPTANCE_APPROVAL = "awaiting_acceptance_approval"
    VALIDATING = "validating"
    AWAITING_ATTESTATION = "awaiting_attestation"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalGate(StrEnum):
    SCOPE = "scope"
    CATALOGUE = "catalogue"
    EXCEPTIONS = "exceptions"
    KNOWLEDGE_DESIGN = "knowledge-design"
    ACCEPTANCE = "acceptance"
    ATTESTATION = "attestation"


class GateStatus(StrEnum):
    UNOPENED = "unopened"
    DRAFTING = "drafting"
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes-requested"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"
    NOT_REQUIRED = "not-required"


class ActorKind(StrEnum):
    HUMAN = "human"
    COORDINATOR = "coordinator"
    FRONTIER_MODEL = "frontier-model"
    SYSTEM = "system"


class AuthenticationKind(StrEnum):
    HOSTED = "hosted-authenticated"
    LOCAL_ASSERTED = "local-asserted"
    MODEL_RESPONSE = "model-response"
    SYSTEM = "system"


class Decision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    REJECT = "reject"


class DelegationVerdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


class DelegationHandling(StrEnum):
    APPLIED = "applied"
    OMITTED = "omitted"
    HUMAN_REVIEW = "human-review"


class DecisionAuthority(StrEnum):
    DETERMINISTIC = "deterministic"
    FRONTIER_MODEL = "frontier-model"
    HUMAN = "human"


class DecisionClass(StrEnum):
    IDENTIFIER_NORMALIZATION = "identifier-normalization"
    EXACT_DUPLICATE = "exact-duplicate"
    SCOPE = "scope"
    SOURCE_BOUNDARY = "source-boundary"
    MATERIAL_EXCEPTION = "material-exception"
    HIGH_IMPACT_FACT = "high-impact-fact"
    NUMERIC_OR_DATED_FACT = "numeric-or-dated-fact"
    JURISDICTION_ROUTING = "jurisdiction-routing"
    CONFLICT = "conflict"
    BLOCKING_TEST_POLICY = "blocking-test-policy"
    ATTESTATION = "attestation"
    SOURCE_METADATA = "source-metadata"
    NAVIGATION_DISPOSITION = "navigation-disposition"
    NON_BLOCKING_VARIANT = "non-blocking-variant"


class CheckpointKind(StrEnum):
    DOWNLOAD_CONFIRMATION = "download-confirmation"
    ACQUISITION = "acquisition"
    EXTRACTION = "extraction"
    FACT_REVIEW = "fact-review"
    VALIDATION = "validation"
    READINESS = "readiness"


HUMAN_ONLY = frozenset({
    DecisionClass.SCOPE, DecisionClass.SOURCE_BOUNDARY, DecisionClass.MATERIAL_EXCEPTION,
    DecisionClass.HIGH_IMPACT_FACT, DecisionClass.NUMERIC_OR_DATED_FACT,
    DecisionClass.JURISDICTION_ROUTING, DecisionClass.CONFLICT,
    DecisionClass.BLOCKING_TEST_POLICY, DecisionClass.ATTESTATION,
})
DETERMINISTIC_ONLY = frozenset({DecisionClass.IDENTIFIER_NORMALIZATION, DecisionClass.EXACT_DUPLICATE})
FAST_TRACK_ELIGIBLE = frozenset({
    DecisionClass.SOURCE_METADATA, DecisionClass.NON_BLOCKING_VARIANT,
})
DELEGATION_TARGETS = {
    DecisionClass.SOURCE_METADATA: (ApprovalGate.CATALOGUE, "sources.json"),
    DecisionClass.NAVIGATION_DISPOSITION: (ApprovalGate.KNOWLEDGE_DESIGN, "curation-coverage.yaml"),
    DecisionClass.NON_BLOCKING_VARIANT: (ApprovalGate.ACCEPTANCE, "regression.yaml"),
}


class Actor(Strict):
    kind: ActorKind
    actor_id: str = Field(min_length=1, max_length=200)
    authentication: AuthenticationKind

    @model_validator(mode="after")
    def authentication_matches_kind(self) -> "Actor":
        allowed = {
            ActorKind.HUMAN: {AuthenticationKind.HOSTED, AuthenticationKind.LOCAL_ASSERTED},
            ActorKind.COORDINATOR: {AuthenticationKind.LOCAL_ASSERTED},
            ActorKind.FRONTIER_MODEL: {AuthenticationKind.MODEL_RESPONSE},
            ActorKind.SYSTEM: {AuthenticationKind.SYSTEM},
        }
        if self.authentication not in allowed[self.kind]:
            raise ValueError(f"{self.authentication} cannot authenticate {self.kind}")
        return self


class ArtifactRef(Strict):
    path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=0)
    media_type: str = "application/json"
    schema_version: str | None = None

    @field_validator("path")
    @classmethod
    def path_is_relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized.split("/", 1)[0] or ".." in normalized.split("/"):
            raise ValueError("artifact path must be relative and remain inside the workflow directory")
        return normalized


class GateProgress(Strict):
    status: GateStatus = GateStatus.UNOPENED
    proposal: ArtifactRef | None = None
    decision: ArtifactRef | None = None
    reason: str | None = None


class NetworkBudget(Strict):
    max_hosts: int = Field(default=20, ge=1, le=200)
    max_requests: int = Field(default=100, ge=1, le=5000)
    max_bytes: int = Field(default=100_000_000, ge=1)


class ModelBudget(Strict):
    max_requests: int = Field(default=400, ge=1, le=10_000)
    max_input_characters: int = Field(default=1_200_000, ge=1)


class WorkflowPolicy(Strict):
    schema_version: Literal["swisstip.autopilot-policy/v1"] = POLICY_SCHEMA_VERSION
    router_version: Literal["risk-router/v1"] = "risk-router/v1"
    review_mode: ReviewMode
    network_budget: NetworkBudget = Field(default_factory=NetworkBudget)
    model_budget: ModelBudget = Field(default_factory=ModelBudget)
    routing: dict[DecisionClass, DecisionAuthority]
    delegation_profile: str | None = None
    delegation_model: str | None = None
    delegation_prompt_sha256: Sha256 | None = None
    delegation_response_schema_sha256: Sha256 | None = None
    created_at: datetime

    @model_validator(mode="after")
    def routing_cannot_weaken_the_floor(self) -> "WorkflowPolicy":
        expected = set(DecisionClass)
        if set(self.routing) != expected:
            missing = sorted(item.value for item in expected - set(self.routing))
            extra = sorted(str(item) for item in set(self.routing) - expected)
            raise ValueError(f"routing must name every decision class; missing={missing}, extra={extra}")
        for item in HUMAN_ONLY:
            if self.routing[item] != DecisionAuthority.HUMAN:
                raise ValueError(f"{item} is always human")
        for item in DETERMINISTIC_ONLY:
            if self.routing[item] != DecisionAuthority.DETERMINISTIC:
                raise ValueError(f"{item} is always deterministic")
        if self.review_mode == ReviewMode.FULL_REVIEW:
            for item in FAST_TRACK_ELIGIBLE:
                if self.routing[item] != DecisionAuthority.HUMAN:
                    raise ValueError(f"{item} is human in full-review mode")
        elif not (self.delegation_profile and self.delegation_model
                  and self.delegation_prompt_sha256 and self.delegation_response_schema_sha256):
            raise ValueError("fast-track mode needs profile, model, prompt hash and response-schema hash")
        return self


class DelegationUse(Strict):
    item: ArtifactRef
    decision: ArtifactRef
    decision_class: DecisionClass
    target_gate: ApprovalGate
    target_artifact: str
    member_id: str = Field(min_length=1)
    member_sha256: Sha256
    verdict: DelegationVerdict
    handling: DelegationHandling
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def target_is_code_owned(self) -> "DelegationUse":
        if DELEGATION_TARGETS.get(self.decision_class) != (self.target_gate, self.target_artifact):
            raise ValueError("delegation use target differs from its decision class")
        return self


class Proposal(Strict):
    schema_version: Literal["swisstip.autopilot-proposal/v1", "swisstip.autopilot-proposal/v2"] = PROPOSAL_SCHEMA_VERSION
    proposal_id: str
    workflow_id: str
    gate: ApprovalGate
    revision: int = Field(ge=1)
    summary: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    input_sha256s: dict[str, Sha256] = Field(default_factory=dict)
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    promotions: list["PromotionSpec"] = Field(default_factory=list)
    delegation_uses: list[DelegationUse] = Field(default_factory=list)
    created_at: datetime
    created_by: Actor

    @model_validator(mode="after")
    def promotions_name_snapshotted_artifacts(self) -> "Proposal":
        missing = [item.artifact for item in self.promotions if item.artifact not in self.artifacts]
        if missing:
            raise ValueError(f"promotions name unknown artifacts: {missing}")
        destinations = [item.destination for item in self.promotions]
        if len(destinations) != len(set(destinations)):
            raise ValueError("proposal promotions have duplicate destinations")
        if any(item.target_gate != self.gate for item in self.delegation_uses):
            raise ValueError("proposal delegation uses must target this gate")
        decisions = [item.decision.sha256 for item in self.delegation_uses]
        if len(decisions) != len(set(decisions)):
            raise ValueError("proposal repeats a delegated decision")
        return self


class PromotionSpec(Strict):
    artifact: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    destination: str = Field(min_length=1)
    expected_sha256: Sha256 | None = None

    @field_validator("destination")
    @classmethod
    def destination_is_relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized.split("/", 1)[0] or ".." in normalized.split("/"):
            raise ValueError("promotion destination must be workspace-relative")
        return normalized


class HumanDecision(Strict):
    schema_version: Literal["swisstip.autopilot-decision/v1"] = DECISION_SCHEMA_VERSION
    decision_id: str
    workflow_id: str
    gate: ApprovalGate
    proposal_sha256: Sha256
    policy_sha256: Sha256
    workflow_revision: int = Field(ge=1)
    decision: Decision
    note: str = ""
    decided_at: datetime
    actor: Actor

    @model_validator(mode="after")
    def decision_is_human_and_explained(self) -> "HumanDecision":
        if self.actor.kind != ActorKind.HUMAN:
            raise ValueError("human approval requires a human actor")
        if self.decision != Decision.APPROVE and not self.note.strip():
            raise ValueError("requesting changes or rejecting a workflow needs a note")
        return self


class PhaseCheckpoint(Strict):
    schema_version: Literal["swisstip.autopilot-checkpoint/v1"] = "swisstip.autopilot-checkpoint/v1"
    checkpoint_id: str
    workflow_id: str
    kind: CheckpointKind
    inputs: dict[str, ArtifactRef]
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by: Actor


class FactReviewReceipt(Strict):
    schema_version: Literal["swisstip.autopilot-fact-review/v1"] = "swisstip.autopilot-fact-review/v1"
    receipt_id: str
    workflow_id: str
    knowledge_design_proposal_sha256: Sha256
    curation_sha256: Sha256
    fact_sha256s: dict[str, Sha256] = Field(min_length=1)
    reviewed_at: datetime
    reviewed_by: Actor

    @model_validator(mode="after")
    def reviewer_is_human(self) -> "FactReviewReceipt":
        if self.reviewed_by.kind != ActorKind.HUMAN:
            raise ValueError("fact review receipt requires a human actor")
        return self


class PromotionReceipt(Strict):
    schema_version: Literal["swisstip.autopilot-promotion/v1"] = "swisstip.autopilot-promotion/v1"
    receipt_id: str
    workflow_id: str
    gate: ApprovalGate
    proposal_sha256: Sha256
    outputs: dict[str, Sha256]
    promoted_at: datetime
    promoted_by: Actor

    @model_validator(mode="after")
    def coordinator_promotes(self) -> "PromotionReceipt":
        if self.promoted_by.kind not in {ActorKind.COORDINATOR, ActorKind.SYSTEM}:
            raise ValueError("approved artifacts are promoted by the coordinator or system")
        return self


class DelegationItem(Strict):
    schema_version: Literal["swisstip.autopilot-delegation-item/v1", "swisstip.autopilot-delegation-item/v2"] = \
        "swisstip.autopilot-delegation-item/v2"
    item_id: str
    workflow_id: str
    decision_class: DecisionClass
    subject_id: str
    subject_sha256: Sha256
    evidence_sha256s: list[Sha256]
    target_gate: ApprovalGate | None = None
    target_artifact: str | None = None
    member_id: str | None = None
    member_sha256: Sha256 | None = None
    subject: ArtifactRef | None = None
    evidence: list[ArtifactRef] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    policy_sha256: Sha256
    proposer_model: str = Field(min_length=1)
    proposer_request_id: str = Field(min_length=1)
    created_at: datetime
    created_by: Actor

    @model_validator(mode="after")
    def coordinator_prepares_item(self) -> "DelegationItem":
        if self.created_by.kind != ActorKind.COORDINATOR:
            raise ValueError("a coordinator prepares a delegation item")
        if self.schema_version.endswith("/v2"):
            expected = DELEGATION_TARGETS.get(self.decision_class)
            if expected != (self.target_gate, self.target_artifact) or not self.member_id or not self.member_sha256:
                raise ValueError("v2 delegation item needs its code-owned artifact member target")
        return self


class DelegatedDecision(Strict):
    schema_version: Literal["swisstip.autopilot-delegated-decision/v1", "swisstip.autopilot-delegated-decision/v2"] = \
        "swisstip.autopilot-delegated-decision/v2"
    decision_id: str
    workflow_id: str
    item_sha256: Sha256
    decision_class: DecisionClass
    subject_id: str
    subject_sha256: Sha256
    evidence_sha256s: list[Sha256]
    target_gate: ApprovalGate | None = None
    target_artifact: str | None = None
    member_id: str | None = None
    member_sha256: Sha256 | None = None
    policy_sha256: Sha256
    verdict: DelegationVerdict
    reason: str = Field(min_length=1, max_length=1000)
    provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    observed_model: str = Field(min_length=1)
    prompt_sha256: Sha256
    response_schema_sha256: Sha256
    decided_at: datetime
    actor: Actor

    @model_validator(mode="after")
    def frontier_identity_is_explicit(self) -> "DelegatedDecision":
        if self.actor.kind != ActorKind.FRONTIER_MODEL:
            raise ValueError("a delegated decision requires a frontier-model actor")
        if self.actor.actor_id != self.observed_model:
            raise ValueError("frontier-model actor must be the observed model identity")
        if self.requested_model != self.observed_model:
            raise ValueError("observed frontier model differs from the requested model")
        if self.schema_version.endswith("/v2"):
            expected = DELEGATION_TARGETS.get(self.decision_class)
            if expected != (self.target_gate, self.target_artifact) or not self.member_id or not self.member_sha256:
                raise ValueError("v2 delegated decision needs its code-owned artifact member target")
        return self


class Workflow(Strict):
    schema_version: Literal["swisstip.autopilot-workflow/v1"] = WORKFLOW_SCHEMA_VERSION
    workflow_id: str
    pack: str
    topic: str = Field(min_length=1)
    review_mode: ReviewMode
    state: WorkflowState
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    policy: ArtifactRef
    gates: dict[ApprovalGate, GateProgress]
    event_sequence: int = Field(ge=0)
    last_event_sha256: Sha256 | None = None
    failure: str | None = None

    @model_validator(mode="after")
    def every_gate_is_present(self) -> "Workflow":
        if set(self.gates) != set(ApprovalGate):
            raise ValueError("workflow must carry progress for every approval gate")
        return self


class Event(Strict):
    schema_version: Literal["swisstip.autopilot-event/v1"] = EVENT_SCHEMA_VERSION
    event_id: str
    workflow_id: str
    workflow_revision: int = Field(ge=1)
    sequence: int = Field(ge=1)
    at: datetime
    kind: str = Field(min_length=1)
    actor: Actor
    state_before: WorkflowState | None = None
    state_after: WorkflowState
    gate: ApprovalGate | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)
    previous_sha256: Sha256 | None = None
    workflow_sha256: Sha256
    sha256: Sha256


def default_policy(review_mode: ReviewMode, created_at: datetime,
                   delegation_profile: str | None = None,
                   delegation_model: str | None = None,
                   delegation_prompt_sha256: str | None = None,
                   delegation_response_schema_sha256: str | None = None) -> WorkflowPolicy:
    routing = {item: DecisionAuthority.HUMAN for item in DecisionClass}
    for item in DETERMINISTIC_ONLY:
        routing[item] = DecisionAuthority.DETERMINISTIC
    if review_mode == ReviewMode.FAST_TRACK:
        for item in FAST_TRACK_ELIGIBLE:
            routing[item] = DecisionAuthority.FRONTIER_MODEL
    return WorkflowPolicy(review_mode=review_mode, routing=routing, delegation_profile=delegation_profile,
                          delegation_model=delegation_model,
                          delegation_prompt_sha256=delegation_prompt_sha256,
                          delegation_response_schema_sha256=delegation_response_schema_sha256,
                          created_at=created_at)
