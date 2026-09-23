# ADR 0001: Governed autopilot boundaries

**Last update:** 23 September 2026

## Status

Accepted for the hackathon implementation.

## Context

A user should be able to give Claude Code one high-level topic and receive a
reviewable Swiss TIP knowledge pack. Model work is useful for scope drafting,
source discovery, page reading, classification and test authoring, but a chat
session is not durable state and a prompt is not an authorization boundary.
The existing builder already owns deterministic acquisition, extraction,
release validation, acceptance and readiness.

Three repositories participate:

- `swiss-tip`: code, schemas, deterministic pipeline and admin console;
- `swiss-tip-skills`: distributable Claude Code plugin and subagent briefs;
- `swiss-tip-mvp`: approved knowledge artifacts and pack-specific checks.

## Decision

### Ownership

`swiss-tip` owns every trust-sensitive mechanism:

- workflow, policy, proposal, decision, event, checkpoint, delegation and
  promotion schemas;
- state transitions and invalidation;
- SHA-256 binding, event-chain verification and atomic persistence;
- deterministic risk routing and frontier-review identity checks;
- allowlisted promotion of approved artifacts;
- the admin-console workflow control room and human-only actions;
- source-catalogue boundary enforcement;
- deterministic build, acceptance and readiness gates.

`swiss-tip-skills` owns only the Claude-facing layer:

- the `build-knowledge-base` skill;
- focused scope, scout, reader, classifier, frontier-review and test-author
  agents;
- portable references, templates and place files;
- bootstrap, governed scaffold, doctor and replay helpers;
- thin invocation of `swisstip-autopilot`.

`swiss-tip-mvp` owns only approved pack inputs and generated release artifacts.
It contains no workflow engine and no agent working state.

### State and promotion

Durable workflow state lives under `.local/<pack>/autopilot/` and is ignored by
Git. Incomplete agent output lives under `.local/<pack>/drafts/`. It reaches
`releases/<pack>/` only when:

1. the coordinator snapshots it into a strict proposal;
2. a human or policy-authorized frontier reviewer decides the proposal;
3. `swisstip-autopilot promote` verifies the approved proposal hash;
4. the destination is allowed for that gate;
5. an existing destination has the exact previous hash named by the proposal;
6. a promotion receipt is committed into the hash-chained event log.

Promotion and console writes share a cross-process filesystem lock per pack.
The workflow lock is acquired inside it, so approved pack bytes and their
receipt become visible as one serialized operation to cooperating tools.

`workflow.json` is the commit marker. Every operation verifies the policy,
reachable proposal and decision artifacts, the event sequence and hashes, and
the final workflow snapshot hash before changing state.

### Identity

Claude Code is a coordinator, never a human approver. Its CLI has no approval
or attestation command.

In local console mode, `--actor` is a local asserted identity, not
cryptographic authentication. In hosted mode, the Basic-auth editor identity
is used and consequential workflow POSTs require a same-origin request. The
console derives actor kind and identity server-side.

The local hackathon deployment assumes a cooperative process running under one
OS user. Neither an `Actor` object nor Claude's observed-model field is a
cryptographic credential. An adversarial deployment must run the coordinator
under a separate identity and expose workflow transitions through an
authenticated service; hosted human approval alone does not sandbox a local
coordinator that can edit the same filesystem.

Final attestation runs the existing readiness pipeline with the console actor
and reaches `ready` only when `readiness.json` matches the exact release bytes,
acceptance suite and attestor and contains exactly passed G1 through G6.

### Fast track

Fast track is deny-by-default. Every served fact remains human-reviewed. The current
policy delegates only typed descriptive source metadata and retrieval-only non-blocking
regression variants. Navigation dispositions remain human-routed until a deterministic
predicate can establish that class.

The workflow policy pins the exact frontier-review model identity at A1. A
separate model call records `approve`, `reject` or `escalate` over one immutable typed
item. The decision binds subject, artifact member, evidence, proposer, policy, prompt
and response-schema hashes. Requested and observed model identities must both match the
pinned reviewer identity and differ from the proposer. A2 or A5 records whether the
exact member was applied, omitted or returned to human review; the result cannot alter
fact review status or replace the human gate decision.

## Consequences

- A Claude or console restart does not lose or infer progress.
- Another vendor can replace Claude Code without changing the workflow engine.
- Approved bytes, not conversational summaries, enter a release.
- The plugin can evolve independently but fails closed when the installed
  builder lacks governed-autopilot capabilities.
- Workflow files are local operational records; release and readiness files
  remain the portable serving contract.
- The first implementation does not automate open-ended curation assembly or
  test generation in Python. Claude proposes those artifacts under strict
  schemas and promotion rules; deterministic validators remain authoritative.

## Rejected alternatives

- **Keep state in Claude's conversation.** Not resumable, hash-bound or
  independently inspectable.
- **Put the engine in `swiss-tip-skills`.** Prompt packages must not enforce
  release trust or duplicate upstream schemas.
- **Let Claude write approved pack files directly.** This breaks separation of
  proposal, approval and promotion.
- **Use model confidence as risk.** Confidence is neither deterministic nor a
  policy boundary.
- **Give Claude an approval CLI command.** Prompt instructions are not an
  identity boundary.
