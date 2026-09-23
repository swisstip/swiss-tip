# Governed knowledge-base autopilot

**Last update:** 23 September 2026

**Status:** The workflow kernel, A1-A6 state model, structured promoted A1 scope, hash-bound proposals and
human decisions, approved-artifact promotion, acquisition/extraction/review/
validation/readiness checkpoints, typed support-work delegation, coordinator CLI and
admin-console control room are implemented and covered by offline focused tests. Claude
still authors scope, catalogue, curation and suite
proposals; there is no Python generator for those semantic artifacts.

This page specifies the executable coordination layer above the deterministic
[knowledge-base pipeline](knowledge-base-pipeline.md). Product intent and the
hackathon demonstration are in
[automated-knowledge-base-builder.md](../product/automated-knowledge-base-builder.md).
The repository boundary decision is
[ADR 0001](decisions/0001-governed-autopilot-boundaries.md).

## 1. Components

| Component | Location | Responsibility |
| --- | --- | --- |
| Workflow contracts | `apps/knowledge-builder/src/swisstip/builder/autopilot/models.py` | Strict Pydantic schemas and policy floors |
| Store | `autopilot/store.py` | Atomic files, inter-process lock, artifact hashes and event verification |
| Service | `autopilot/service.py` | State transitions, checkpoints, delegation and promotion |
| CLI | `autopilot/cli.py` | Coordinator-safe commands; no human approval or attestation command |
| Control room | `apps/admin-console/.../screens/workflow.py` | Verified progress, proposals and human-only actions |
| Claude plugin | `swiss-tip-skills` | Reasoning, web discovery, subagent fan-out and proposal authoring |

The deterministic `Pipeline` remains model-free. Autopilot calls it or binds
its reports; the serving side imports neither autopilot nor the console.

## 2. Files

```text
.local/<pack>/
  drafts/                         coordinator working files, never approved
  autopilot/
    workflow.json                 current committed state
    policy.json                   immutable review mode, budgets and routing
    brief.json                    original topic and coordinator settings
    events.jsonl                  append-only hash chain
    proposals/<gate>/             proposal JSON and snapshotted artifacts
    approvals/<gate>/             human decisions
    delegations/items/            router-approved review packets
    delegations/decisions/        independent frontier verdicts
    promotions/                   receipts for bytes promoted into the pack
    checkpoints/                  reports and inputs bound at phase completion
```

All `ArtifactRef.path` values remain inside `autopilot/`. External reports are
copied into a checkpoint before a transition is committed, so later pipeline
runs do not rewrite history.

## 3. States and gates

| State | Allowed next operation |
| --- | --- |
| `drafting_scope` | Submit A1 `scope` proposal |
| `awaiting_scope_approval` | Human approve, request changes or reject |
| `discovering_sources` | Promote A1 where applicable; submit A2 `catalogue` |
| `awaiting_catalogue_approval` | Human decision |
| `awaiting_download_confirmation` | Promote A2, plan without network, human confirms exact plan and budget |
| `acquiring` | Run acquire/gaps and checkpoint reports |
| `extracting` | Run extract/validate-text, checkpoint validation, submit A3 `exceptions` |
| `awaiting_exception_approval` | Human decision |
| `generating_knowledge` | Submit A4 `knowledge-design` with curation artifacts |
| `awaiting_knowledge_design_approval` | Human decision or policy-authorized delegated sub-decisions |
| `awaiting_fact_review` | Human reviews every served fact and completes checkpoint |
| `generating_tests` | Submit A5 `acceptance` with suites |
| `awaiting_acceptance_approval` | Human decision |
| `validating` | Build, enforce coverage, accept and checkpoint current reports |
| `awaiting_attestation` | Human control room runs G1-G6 as its actor |
| `ready` | Terminal |
| `cancelled`, `failed` | Terminal |

Requesting changes returns to the producer state. Rejecting a proposal cancels
the workflow. Every mutation requires the current workflow revision.

## 4. Transaction and verification rules

Initialization builds a sibling temporary directory and atomically renames it
to `autopilot` while holding `.local/<pack>/.autopilot.lock`. A duplicate
initialization leaves every existing byte unchanged.

Each transition:

1. obtains the inter-process lock;
2. loads `workflow.json` and checks the expected revision;
3. re-hashes policy and every reachable proposal, decision and checkpoint;
4. recomputes every event hash, sequence and `previous_sha256` link;
5. checks the final event's state, revision and workflow-snapshot hash;
6. prepares immutable new artifacts;
7. appends one event and replaces `workflow.json` as the commit marker;
8. rolls back a failed pre-commit append and newly written artifacts.

A stale revision, changed proposal, changed policy, missing artifact, extra
event tail or modified workflow snapshot fails closed.

## 5. Proposal and promotion

Claude writes candidate files only under `.local/<pack>/drafts/`. `submit`
snapshots named artifacts into the proposal. A `PromotionSpec` names the
workspace-relative destination and, when replacing an existing file, its
expected previous SHA-256.

Gate destination allowlists are:

| Gate | Destinations |
| --- | --- |
| A1 scope | `docs/<pack>-acceptance-questions.md` |
| A2 catalogue | `releases/<pack>/sources.json`, `sources.md` |
| A3 exceptions | No direct promotion |
| A4 knowledge design | `curation.yaml`, `curation-coverage.yaml`, `basis-review.md` |
| A5 acceptance | `acceptance.yaml`, `regression.yaml` |

Promotion is idempotent, refuses another pack or arbitrary path, refuses an
unexpected existing file, and writes a receipt bound to the proposal hash.
The A2 receipt must contain both catalogue files, A4 must contain curation and
coverage dispositions, and A5 must contain both suites. Promotion, console
writes and pipeline jobs share `.local/<pack>/.pack-write.lock`, so hash
checks, pack-file replacement and receipt commit are serialized across Claude
and console processes. A recoverable transaction marker stores prior bytes;
the next pack writer rolls back an interrupted promotion, including an event appended
before `workflow.json` replacement and its orphan receipt, or finalizes cleanup when its
workflow receipt committed.
Downstream network confirmation, fact-review completion and validation require
the current A2, A4 and A5 proposals respectively to have promotion receipts.

## 6. Source boundary and budgets

`sources.md` is executable input: every Markdown link is a target. Planning
therefore accepts only HTTPS links attributable to a selected `sources.json`
entry's approved host and path prefix. Each target receives its own redirect
hosts and path prefixes; it cannot redirect through the union of another
source's permissions. An HTTPS request may never redirect to HTTP.

A2 confirmation binds `plan.json`, `plugin-plan.json`, `sources.json`, target
count, hosts and the policy budget. Governed mode currently refuses source
plugins and supplements because their requests, hosts and bytes are not part of
the approved root plan. Completion re-hashes the confirmed plan and catalogue,
binds `summary.json` and `gap-report.json`, and refuses recorded requests or
saved bytes above the approved aggregate budget.
The current downloader still enforces response and per-target limits. A true
cross-worker live aggregate kill-switch is future work; post-run excess fails
the workflow and cannot advance.

## 7. Review modes

### Full review

`complete_fact_review` loads current curation, checks its provenance, rebuilds
the release and enforced coverage from those exact reviewed bytes, then
snapshots curation, release, build and coverage reports. Validation refuses if
any of those bytes changes later.

### Fast track

Every served fact remains human-reviewed. Fast track delegates only typed descriptive
source metadata (`source_id`, `title`, `notes`) and retrieval-only non-blocking
regression variants. Navigation dispositions remain human-routed until a deterministic
navigation predicate can prove that class.

`prepare-delegation` is allowed only while drafting the owning A2 or A5 gate. It parses
and canonically hashes the typed artifact member, snapshots subject and evidence files,
binds the proposing model identity and request ID, and refuses any human-routed class.

A separate frontier response records `approve`, `reject` or `escalate`, with
provider, prompt hash, response-schema hash, and requested/observed model. The
policy pins the exact reviewer identity at A1. The proposing and reviewing model
identities must differ and each item has one final verdict. The later proposal records
the item and decision references and whether the exact member was applied, omitted, or
returned to human review. Exact rejected members cannot be submitted. Changed members
lose delegated approval and remain visible for human review. A delegated decision cannot
change fact review status or cross a gate itself.

## 8. Human identity and control room

The control room is `/packs/<pack>/workflow`. It shows only persisted values: state,
revision, gate status, verified events, proposal details and delegated-support handling.
Pending proposal artifacts have hash-addressed complete text views and exact-byte
downloads; arbitrary workflow artifacts are not exposed. The UI does not invent an
overall completion percentage.

Human decisions derive actor identity server-side:

- local mode: `local-asserted`, from `--actor`;
- hosted mode: authenticated editor username, with same-origin enforcement on
  workflow POSTs.

Local mode is a cooperative single-user threat model. A process running as
the same OS user can call Python APIs, edit local files or assert a model name;
the hash chain detects later byte changes but is not a signature or a sandbox.
Use hosted mode behind TLS and isolate the coordinator under another OS or
service identity when approvals must resist a malicious coordinator. Claude
Code subscription responses carry observed identity metadata, not a
cryptographic provider signature.

The console records approval, download confirmation, fact-review completion
and attestation in both the workflow event chain and its existing audit log.
Read-only mode registers no workflow write routes.

A6 displays release digest, counts, review mix, coverage, acceptance failures
and quarantines, limitations and exact file hashes. It re-hashes the validation
checkpoint, runs readiness as the console actor, and accepts only a record with
exactly passed G1 through G6.

## 9. CLI

```shell
swisstip-autopilot init ...
swisstip-autopilot status ...
swisstip-autopilot verify ...
swisstip-autopilot submit ...
swisstip-autopilot promote ...
swisstip-autopilot checkpoint --phase acquisition|extraction|validation ...
swisstip-autopilot prepare-delegation ...
swisstip-autopilot record-delegation ...
swisstip-autopilot progress ...
swisstip-autopilot cancel ...
```

There is deliberately no `decide`, `confirm-download`, `complete-fact-review`
or `attest` CLI command. Those operations require a human control-room actor.

## 10. Validation

Focused offline tests cover:

- duplicate initialization and stale revisions;
- proposal, policy and workflow tampering;
- event-chain and snapshot verification;
- full-review and fast-track policy floors;
- approved artifact promotion and path refusal;
- A1 through A3 with real files and no network;
- control-room rendering, approval, read-only mode and workflow-only packs;
- `sources.md` attribution and target-specific boundaries;
- Claude plugin scaffolding and semantic config against upstream loaders.

The full package and console suites remain the regression check for shared
behavior. A live Claude session and public web acquisition are pilot tests,
not offline unit tests.

## 11. Current limitations

- Scope, catalogue, curation and suite semantics are authored by Claude, not
  generated by a Python planner. They are still schema-checked and promoted by
  approved hash.
- Fast-track routing is intentionally narrow and covers support work only;
  every served fact remains human-reviewed.
- There is no automatic loop that answers `assistant_exchange` requests;
  Claude follows the plugin skill and records responses.
- There is no live-caller harness in the plugin.
- Aggregate network budgets are checked at plan and completion; a live global
  byte/request kill-switch remains planned.
- Local actor and model identities are audit assertions. Cryptographic identity
  and hostile-process isolation require a separately deployed coordinator and
  authenticated approval service.
