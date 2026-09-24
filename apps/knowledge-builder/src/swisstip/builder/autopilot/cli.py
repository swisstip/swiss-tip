"""Coordinate a governed knowledge-base workflow without crossing human approval gates.

The CLI is the machine-facing surface used by Claude Code. It can initialize,
inspect, verify, submit proposals and cancel a workflow. It deliberately has no
approval or attestation command; those actions belong to the admin console.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .models import (Actor, ActorKind, ApprovalGate, AuthenticationKind,
                     DecisionClass, DelegationVerdict, PromotionSpec, ReviewMode)
from .service import AutopilotService
from .store import WorkflowConflict, WorkflowNotFound


def coordinator(identity: str) -> Actor:
    return Actor(kind=ActorKind.COORDINATOR, actor_id=identity,
                 authentication=AuthenticationKind.LOCAL_ASSERTED)


def frontier_model(identity: str) -> Actor:
    return Actor(kind=ActorKind.FRONTIER_MODEL, actor_id=identity,
                 authentication=AuthenticationKind.MODEL_RESPONSE)


def print_result(value, as_json: bool) -> None:
    data = value.model_dump(mode="json", exclude_none=True)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"{data['pack']}: {data['state']} (revision {data['revision']})")
    print(f"topic: {data['topic']}")
    pending = [name for name, gate in data["gates"].items() if gate["status"] == "pending"]
    print("pending human gate: " + (", ".join(pending) if pending else "none"))


def load_object(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("proposal details must be a JSON object")
    return value


def inputs(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        name, separator, digest = value.partition("=")
        if not separator or not name or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("--input takes NAME=<lowercase SHA-256>")
        if name in result:
            raise ValueError(f"duplicate input name: {name}")
        result[name] = digest
    return result


def named_paths(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ValueError("--artifact takes NAME=PATH")
        if name in result:
            raise ValueError(f"duplicate artifact name: {name}")
        result[name] = Path(path)
    return result


def promotion(value: str) -> PromotionSpec:
    parts = value.split("=", 2)
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError("promotion is ARTIFACT=DESTINATION[=EXPECTED_SHA256]")
    return PromotionSpec(artifact=parts[0], destination=parts[1],
                         expected_sha256=parts[2] if len(parts) == 3 else None)


def metric(value: str) -> tuple[str, object]:
    name, separator, raw = value.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError("metric is NAME=JSON_VALUE")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    if not isinstance(parsed, (int, float, str, bool, type(None))):
        raise argparse.ArgumentTypeError("metric value must be a scalar")
    return name, parsed


def common(command: argparse.ArgumentParser) -> None:
    command.add_argument("--packs-dir", type=Path, required=True)
    command.add_argument("--pack", required=True)
    command.add_argument("--json", action="store_true", help="print the complete workflow as JSON")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="initialize a workflow")
    common(initialize)
    initialize.add_argument("--topic", required=True)
    initialize.add_argument("--review-mode", choices=[item.value for item in ReviewMode],
                            default=ReviewMode.FULL_REVIEW.value)
    initialize.add_argument("--delegation-profile")
    initialize.add_argument("--delegation-model")
    initialize.add_argument("--delegation-prompt-sha256")
    initialize.add_argument("--delegation-response-schema-sha256")
    initialize.add_argument("--actor", default="claude-code")

    for name in ("status", "verify"):
        command = commands.add_parser(name, help=f"{name} a workflow")
        common(command)

    submit = commands.add_parser("submit", help="submit the next gate proposal")
    common(submit)
    submit.add_argument("--gate", required=True,
                        choices=[item.value for item in ApprovalGate if item != ApprovalGate.ATTESTATION])
    submit.add_argument("--summary", required=True)
    submit.add_argument("--details", type=Path, required=True, help="JSON object with the proposal details")
    submit.add_argument("--input", action="append", default=[], metavar="NAME=SHA256")
    submit.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH")
    submit.add_argument("--promote", action="append", type=promotion, default=[],
                        metavar="ARTIFACT=DESTINATION[=EXPECTED_SHA256]")
    submit.add_argument("--expected-revision", type=int, required=True)
    submit.add_argument("--actor", default="claude-code")

    cancel = commands.add_parser("cancel", help="cancel a non-terminal workflow")
    common(cancel)
    cancel.add_argument("--note", required=True)
    cancel.add_argument("--expected-revision", type=int, required=True)
    cancel.add_argument("--actor", default="claude-code")

    checkpoint = commands.add_parser("checkpoint", help="record a completed deterministic phase")
    common(checkpoint)
    checkpoint.add_argument("--phase", required=True,
                            choices=("acquisition", "extraction", "validation"))
    checkpoint.add_argument("--expected-revision", type=int, required=True)
    checkpoint.add_argument("--actor", default="claude-code")

    prepare = commands.add_parser("prepare-delegation", help="prepare a policy-routed low-risk item for independent review")
    common(prepare)
    prepare.add_argument("--decision-class", required=True,
                         choices=[DecisionClass.SOURCE_METADATA.value,
                                  DecisionClass.NON_BLOCKING_VARIANT.value])
    prepare.add_argument("--subject-id", required=True)
    prepare.add_argument("--subject", type=Path, required=True)
    prepare.add_argument("--evidence", type=Path, action="append", default=[])
    prepare.add_argument("--summary", required=True)
    prepare.add_argument("--proposer-model", required=True)
    prepare.add_argument("--proposer-request-id", required=True)
    prepare.add_argument("--expected-revision", type=int, required=True)
    prepare.add_argument("--actor", default="claude-code")

    delegated = commands.add_parser("record-delegation", help="record a separate frontier-model verdict")
    common(delegated)
    delegated.add_argument("--item-sha256", required=True)
    delegated.add_argument("--verdict", required=True, choices=[item.value for item in DelegationVerdict])
    delegated.add_argument("--reason", required=True)
    delegated.add_argument("--provider", required=True)
    delegated.add_argument("--requested-model", required=True)
    delegated.add_argument("--observed-model", required=True)
    delegated.add_argument("--prompt-sha256", required=True)
    delegated.add_argument("--response-schema-sha256", required=True)
    delegated.add_argument("--expected-revision", type=int, required=True)

    promote = commands.add_parser("promote", help="promote an approved proposal's snapshotted artifacts")
    common(promote)
    promote.add_argument("--gate", required=True,
                         choices=[item.value for item in ApprovalGate if item != ApprovalGate.ATTESTATION])
    promote.add_argument("--expected-revision", type=int, required=True)
    promote.add_argument("--actor", default="claude-code")

    progress = commands.add_parser("progress", help="record measurable coordinator progress")
    common(progress)
    progress.add_argument("--summary", required=True)
    progress.add_argument("--metric", action="append", type=metric, default=[])
    progress.add_argument("--expected-revision", type=int, required=True)
    progress.add_argument("--actor", default="claude-code")
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        service = AutopilotService(args.packs_dir, args.pack)
        if args.command == "init":
            mode = ReviewMode(args.review_mode)
            if mode == ReviewMode.FAST_TRACK and not (
                    args.delegation_profile and args.delegation_model
                    and args.delegation_prompt_sha256 and args.delegation_response_schema_sha256):
                raise ValueError("fast-track mode needs delegation profile, model, prompt hash and schema hash")
            workflow = service.initialize(args.topic, mode, coordinator(args.actor),
                                          delegation_profile=args.delegation_profile,
                                          delegation_model=args.delegation_model,
                                          delegation_prompt_sha256=args.delegation_prompt_sha256,
                                          delegation_response_schema_sha256=args.delegation_response_schema_sha256)
        elif args.command in {"status", "verify"}:
            workflow = service.status()
        elif args.command == "submit":
            workflow = service.submit(ApprovalGate(args.gate), args.summary, load_object(args.details),
                                      coordinator(args.actor), args.expected_revision, inputs(args.input),
                                      named_paths(args.artifact), args.promote)
        elif args.command == "cancel":
            workflow = service.cancel(coordinator(args.actor), args.note, args.expected_revision)
        elif args.command == "checkpoint":
            action = {"acquisition": service.complete_acquisition,
                      "extraction": service.complete_extraction,
                      "validation": service.complete_validation}[args.phase]
            workflow = action(coordinator(args.actor), args.expected_revision)
        elif args.command == "prepare-delegation":
            workflow = service.prepare_delegation(
                DecisionClass(args.decision_class), args.subject_id, args.subject,
                args.evidence, args.summary, args.proposer_model, args.proposer_request_id,
                coordinator(args.actor), args.expected_revision)
        elif args.command == "record-delegation":
            workflow = service.record_delegated_decision(
                args.item_sha256, DelegationVerdict(args.verdict), args.reason, args.provider,
                args.requested_model, args.observed_model, args.prompt_sha256,
                args.response_schema_sha256, frontier_model(args.observed_model),
                args.expected_revision)
        elif args.command == "promote":
            workflow = service.promote(ApprovalGate(args.gate), coordinator(args.actor),
                                       args.expected_revision)
        else:
            workflow = service.report_progress(coordinator(args.actor), args.summary,
                                               dict(args.metric), args.expected_revision)
        print_result(workflow, args.json)
        return 0
    except (OSError, ValueError, ValidationError, WorkflowConflict, WorkflowNotFound,
            json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
