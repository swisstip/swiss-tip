"""Governed workflow control room: verified state, proposals and human decisions."""

from collections import Counter
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status as http_status
from fastapi.responses import RedirectResponse, Response

from swisstip.builder.autopilot.models import (Actor, ActorKind, AuthenticationKind,
                                               Decision, GateStatus, Proposal, ReviewMode,
                                               WorkflowState)
from swisstip.builder.autopilot.service import AutopilotService
from swisstip.builder.autopilot.store import WorkflowConflict, WorkflowNotFound
from swisstip.build.acceptance import load_acceptance
from swisstip.core.release import load_release

from ..app import actor_of, get_pack, render, require_editor
from ..data import PackData, sha256_bytes
from ..writes import WriteRefused, append_audit, pack_file_lock

read_router = APIRouter()
write_router = APIRouter()


def service_for(pack: PackData) -> AutopilotService:
    return AutopilotService(pack.root, pack.pack)


def human_actor(request: Request) -> Actor:
    hosted = bool(request.app.state.users)
    return Actor(kind=ActorKind.HUMAN, actor_id=actor_of(request),
                 authentication=(AuthenticationKind.HOSTED if hosted
                                 else AuthenticationKind.LOCAL_ASSERTED))


def require_workflow_editor(request: Request, actor: str = Depends(require_editor)) -> str:
    return actor


def context_of(pack: PackData) -> dict:
    service = service_for(pack)
    try:
        workflow = service.status()
        events = service.store.events()
    except WorkflowNotFound:
        return dict(workflow=None, events=[], proposal=None, proposal_artifacts=[], progress=None,
                    attestation_packet=None, network_packet=None, integrity_error=None,
                    gate_counts={}, actor_counts={}, decision_counts={})
    except (OSError, ValueError, WorkflowConflict) as exc:
        return dict(workflow=None, events=[], proposal=None, proposal_artifacts=[], progress=None,
                    attestation_packet=None, network_packet=None,
                    integrity_error=f"{type(exc).__name__}: {exc}", gate_counts={},
                    actor_counts={}, decision_counts={})
    pending = [(gate, progress) for gate, progress in workflow.gates.items()
               if progress.status == GateStatus.PENDING]
    proposal = None
    proposal_artifacts = []
    if len(pending) == 1 and pending[0][1].proposal is not None:
        proposal = Proposal.model_validate_json(service.store.read_artifact(pending[0][1].proposal))
        destinations = {item.artifact: item for item in proposal.promotions}
        for name, reference in proposal.artifacts.items():
            data = service.store.read_artifact(reference)
            try:
                preview = data.decode("utf-8")[:8000]
            except UnicodeDecodeError:
                preview = "(binary artifact; preview unavailable)"
            proposal_artifacts.append(dict(name=name, reference=reference,
                                           promotion=destinations.get(name), preview=preview))
    progress = next((event for event in reversed(events) if event.kind == "progress.reported"), None)
    packet = attestation_packet(pack) if workflow.state == WorkflowState.AWAITING_ATTESTATION else None
    network = network_packet(pack, service) if workflow.state == WorkflowState.AWAITING_DOWNLOAD_CONFIRMATION else None
    return dict(
        workflow=workflow, events=events, proposal=proposal, proposal_artifacts=proposal_artifacts,
        progress=progress, attestation_packet=packet, network_packet=network, integrity_error=None,
        gate_counts=dict(Counter(progress.status.value for progress in workflow.gates.values())),
        actor_counts=dict(Counter(event.actor.kind.value for event in events)),
        decision_counts=dict(Counter(event.kind for event in events if event.kind.startswith("proposal."))),
    )


def network_packet(pack: PackData, service: AutopilotService) -> dict:
    run = service.store.directory.parent
    policy = service.store.load_policy()
    plan_path, plugin_path = run / "plan.json", run / "plugin-plan.json"
    if not plan_path.is_file() or not plugin_path.is_file():
        return dict(ready=False, message="The coordinator has not prepared the acquisition plan yet.",
                    target_count=0, targets=[], request_ceiling=0,
                    max_hosts=policy.network_budget.max_hosts,
                    max_requests=policy.network_budget.max_requests,
                    max_bytes=policy.network_budget.max_bytes, catalogue_sha256=None, plugins={})
    plan = json_file(plan_path)
    targets = plan.get("targets", [])
    return dict(
        ready=True,
        catalogue_sha256=plan.get("catalogue_sha256"),
        targets=[dict(url=item.get("url"), hosts=item.get("allowed_redirect_hosts", []),
                      paths=item.get("allowed_path_prefixes", [])) for item in targets],
        target_count=len(targets), request_ceiling=len(targets) * 16,
        max_hosts=policy.network_budget.max_hosts,
        max_requests=policy.network_budget.max_requests,
        max_bytes=policy.network_budget.max_bytes,
        plugins=json_file(plugin_path),
    )


def attestation_packet(pack: PackData) -> dict:
    pack_dir = pack.pack_dir
    required = ["release.json", "build-report.json", "curation-coverage.json",
                "acceptance.yaml", "acceptance-report.json", "regression.yaml",
                "regression-report.json", "semantic-index.json"]
    missing = [name for name in required if not (pack_dir / name).is_file()]
    if missing:
        return dict(ready=False, message="Final validation artifacts are still being prepared.", missing=missing)
    release = load_release(pack_dir / "release.json")
    suite = load_acceptance(pack_dir / "acceptance.yaml")
    build = json_file(pack_dir / "build-report.json")
    coverage = json_file(pack_dir / "curation-coverage.json")
    acceptance = json_file(pack_dir / "acceptance-report.json")
    regression = json_file(pack_dir / "regression-report.json")
    files = {}
    for name in required:
        path = pack_dir / name
        files[name] = sha256_bytes(path.read_bytes())
    return dict(
        ready=True,
        release_id=release.manifest.release_id,
        content_sha256=release.manifest.content_sha256,
        counts=dict(topics=len(release.topics), concepts=len(release.concepts),
                    facts=len(release.facts), evidence=len(release.evidence),
                    documents=len(release.documents)),
        review_statuses=release.manifest.review_statuses,
        dropped=len(build.get("dropped", [])),
        coverage=coverage.get("counts", {}),
        acceptance=dict(cases=acceptance.get("cases"), failed=acceptance.get("failed", []),
                        quarantined=acceptance.get("quarantined", []), suite_sha256=suite.digest()),
        regression=dict(passed=regression.get("passed"), runs=regression.get("runs", {})),
        limitations=release.manifest.limitations,
        files=files,
    )


def json_file(path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))


@read_router.get("/packs/{pack}/workflow")
def index(request: Request, pack: PackData = Depends(get_pack)):
    return render(request, "workflow/index.html", pack=pack, active="workflow", **context_of(pack))


@read_router.get("/packs/{pack}/workflow/status")
def status(request: Request, pack: PackData = Depends(get_pack)):
    return render(request, "workflow/status.html", pack=pack, active="workflow", **context_of(pack))


@read_router.get("/packs/{pack}/workflow/artifacts/{artifact_sha256}")
def proposal_artifact(artifact_sha256: str, download: bool = False,
                      pack: PackData = Depends(get_pack)):
    service = service_for(pack)
    try:
        workflow = service.status()
        pending = [progress for progress in workflow.gates.values()
                   if progress.status == GateStatus.PENDING and progress.proposal is not None]
        if len(pending) != 1:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "No pending proposal artifact")
        proposal = Proposal.model_validate_json(service.store.read_artifact(pending[0].proposal))
        matches = [(name, reference) for name, reference in proposal.artifacts.items()
                   if reference.sha256 == artifact_sha256]
        if len(matches) != 1:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Unknown pending proposal artifact")
        name, reference = matches[0]
        data = service.store.read_artifact(reference)
    except HTTPException:
        raise
    except (OSError, ValueError, WorkflowConflict) as exc:
        raise HTTPException(http_status.HTTP_409_CONFLICT, f"Workflow artifact verification failed: {exc}") from exc
    headers = {"X-Content-SHA256": reference.sha256, "X-Content-Length": str(reference.bytes),
               "Content-Disposition": ("attachment" if download else "inline") + f'; filename="{name}.bin"'}
    if download:
        return Response(content=data, media_type="application/octet-stream", headers=headers)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            "Binary artifacts are available only through exact-byte download") from exc
    headers["Content-Disposition"] = f'inline; filename="{name}.txt"'
    return Response(content=text, media_type="text/plain; charset=utf-8", headers=headers)


@write_router.post("/packs/{pack}/workflow")
def initialize(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_workflow_editor),
               topic: str = Form(...), review_mode: str = Form(ReviewMode.FULL_REVIEW.value),
               delegation_profile: str = Form(""), delegation_model: str = Form(""),
               delegation_prompt_sha256: str = Form(""),
               delegation_response_schema_sha256: str = Form("")):
    before = sha256_bytes(pack.root.joinpath(".local", pack.pack, "autopilot", "workflow.json").read_bytes()) \
        if pack.root.joinpath(".local", pack.pack, "autopilot", "workflow.json").is_file() else None
    try:
        mode = ReviewMode(review_mode)
        workflow = service_for(pack).initialize(topic, mode, human_actor(request),
                                                delegation_profile=delegation_profile.strip() or None,
                                                delegation_model=delegation_model.strip() or None,
                                                delegation_prompt_sha256=delegation_prompt_sha256.strip() or None,
                                                delegation_response_schema_sha256=delegation_response_schema_sha256.strip() or None)
    except (OSError, ValueError, WorkflowConflict) as exc:
        raise WriteRefused(f"workflow initialization refused: {exc}") from exc
    path = service_for(pack).store.workflow_path
    append_audit(pack, actor, path, before, sha256_bytes(path.read_bytes()),
                 f"initialize {mode.value} workflow", [workflow.workflow_id])
    return RedirectResponse(f"/packs/{pack.pack}/workflow?notice=workflow+initialized", status_code=303)


@write_router.post("/packs/{pack}/workflow/decision")
def decide(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_workflow_editor),
           action: str = Form(...), expected_revision: int = Form(...),
           proposal_sha256: str = Form(...), note: str = Form("")):
    service = service_for(pack)
    try:
        current = service.status()
        pending = [gate for gate, progress in current.gates.items() if progress.status == GateStatus.PENDING]
        if len(pending) != 1:
            raise ValueError(f"expected one pending gate, found {len(pending)}")
        decision = Decision(action)
        before = sha256_bytes(service.store.workflow_path.read_bytes())
        updated = service.decide(pending[0], decision, proposal_sha256, note,
                                 human_actor(request), expected_revision)
    except (OSError, ValueError, WorkflowConflict) as exc:
        raise WriteRefused(f"workflow decision refused: {exc}") from exc
    append_audit(pack, actor, service.store.workflow_path, before,
                 sha256_bytes(service.store.workflow_path.read_bytes()),
                 f"{decision.value} workflow gate {pending[0].value}",
                 [updated.workflow_id, pending[0].value])
    notice = quote_plus(f"{decision.value} {pending[0].value}")
    return RedirectResponse(f"/packs/{pack.pack}/workflow?notice={notice}", status_code=303)


@write_router.post("/packs/{pack}/workflow/confirm-download")
def confirm_download(request: Request, pack: PackData = Depends(get_pack),
                     actor: str = Depends(require_workflow_editor),
                     expected_revision: int = Form(...), confirm_pack: str = Form(...)):
    if confirm_pack.strip() != pack.pack:
        raise WriteRefused("type the pack name to confirm the approved network plan")
    service = service_for(pack)
    before = sha256_bytes(service.store.workflow_path.read_bytes())
    try:
        updated = service.confirm_download(human_actor(request), expected_revision)
    except (OSError, ValueError, WorkflowConflict) as exc:
        raise WriteRefused(f"download confirmation refused: {exc}") from exc
    append_audit(pack, actor, service.store.workflow_path, before,
                 sha256_bytes(service.store.workflow_path.read_bytes()),
                 "confirm exact acquisition plan and network budget", [updated.workflow_id])
    return RedirectResponse(f"/packs/{pack.pack}/workflow?notice=download+confirmed", status_code=303)


@write_router.post("/packs/{pack}/workflow/complete-fact-review")
def complete_fact_review(request: Request, pack: PackData = Depends(get_pack),
                         actor: str = Depends(require_workflow_editor),
                         expected_revision: int = Form(...)):
    service = service_for(pack)
    before = sha256_bytes(service.store.workflow_path.read_bytes())
    try:
        with pack_file_lock(pack):
            updated = service.complete_fact_review(human_actor(request), expected_revision)
    except (OSError, ValueError, WorkflowConflict) as exc:
        raise WriteRefused(f"fact-review checkpoint refused: {exc}") from exc
    append_audit(pack, actor, service.store.workflow_path, before,
                 sha256_bytes(service.store.workflow_path.read_bytes()),
                 "complete human fact-review checkpoint", [updated.workflow_id])
    return RedirectResponse(f"/packs/{pack.pack}/workflow?notice=fact+review+complete", status_code=303)


@write_router.post("/packs/{pack}/workflow/attest")
def attest(request: Request, pack: PackData = Depends(get_pack),
           actor: str = Depends(require_workflow_editor), expected_revision: int = Form(...)):
    service = service_for(pack)
    before = sha256_bytes(service.store.workflow_path.read_bytes())
    try:
        with pack_file_lock(pack):
            updated = service.record_attestation(human_actor(request), expected_revision)
    except (OSError, ValueError, WorkflowConflict) as exc:
        raise WriteRefused(f"attestation refused: {exc}") from exc
    append_audit(pack, actor, service.store.workflow_path, before,
                 sha256_bytes(service.store.workflow_path.read_bytes()),
                 "attest ready release", [updated.workflow_id])
    return RedirectResponse(f"/packs/{pack.pack}/workflow?notice=release+attested", status_code=303)
