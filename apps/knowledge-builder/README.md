# swisstip-knowledge-builder

**Last update:** 22 September 2026

The build pipeline of a pack in one command: from `releases/<pack>/sources.json`
through the run directory, the text dataset and the curation file to a
validated `release.json`, with a health check the way the server loads it.
Each stage is a function of one of the build-side packages (ingestion,
extraction, build, core); this app only orders them, skips what did not
change and records the outcome. The serving side never imports it.

```shell
./.venv/Scripts/python.exe -m pip install -e apps/knowledge-builder
./.venv/Scripts/python.exe -m unittest discover -s apps/knowledge-builder/tests
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --packs-dir ../swiss-tip-mvp
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --packs-dir ../swiss-tip-mvp --thorough
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --packs-dir ../swiss-tip-mvp --until validate-text --workers 4
```

The governed coordinator above these deterministic stages is
`swisstip-autopilot`. It keeps verified state under
`<packs-dir>/.local/<pack>/autopilot/`; Claude or another coordinator can
initialize, inspect, submit and promote proposals, record measurable progress,
checkpoint deterministic phases and prepare narrow fast-track delegations.
Human approval, fact-review completion and attestation are intentionally absent
from its CLI and belong to the admin console.

```shell
./.venv/Scripts/python.exe -m swisstip.builder.autopilot.cli init \
	--packs-dir ../swiss-tip-mvp --pack <pack> --topic "<topic>" \
	--review-mode full-review
./.venv/Scripts/python.exe -m swisstip.builder.autopilot.cli status \
	--packs-dir ../swiss-tip-mvp --pack <pack> --json
```

Architecture and integrity rules:
[autopilot-workflow.md](../../docs/architecture/autopilot-workflow.md).

The packs live in their own repository,
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp): `--packs-dir`
(or the environment variable `SWISSTIP_PACKS`) names the folder that holds
`releases/<pack>` and the runs under `.local/<pack>`. This checkout holds
no pack.

| Stage | Does | Skipped when |
| --- | --- | --- |
| `acquire` | Plans the run from the catalogue (and `sources.md` when present); with `--download` fetches the pages and resolves plugin documents | The run exists for this catalogue and `--download` was not given |
| `gaps` | Writes `gap-report.json` and `gap-report.md` into the run | The report is newer than the run summary and `review-decisions.json` is unchanged |
| `extract` | Builds or updates `<run>/text` | Never; unchanged records are reused inside |
| `validate-text` | Hashes, offsets, index and reading views; with `--thorough` also every saved response | Never |
| `build` | `curation.yaml` to `release.json` with citation relocation and `build-report.json`; verifies every `source_terms` entry against the concept's excerpts (a term the evidence does not contain fails the build) and merges the verified terms into the released aliases; with `question_languages` in the curation, fails for a concept that has no sample question in one of them | Curation, text index and release ID unchanged since the last build; or no curation file |
| `validate-release` | The release against the text dataset (and the run with `--thorough`) | Never |
| `coverage` | Joins the text dataset with the release: every content section of every curation candidate (`curation_candidate` in the text index) must be cited by a fact or dispositioned in `releases/<pack>/curation-coverage.yaml`; writes `curation-coverage.json` and `.md` with the open sections, the findings (stale, expired, unknown out-of-scope entry, new under a prefix rule), a roll-up per catalogue source and, for every section text that recurs on `boilerplate_min_pages` (curation field, default five, floor three) pages of one host, the page where a fact cites it. Fails only when the report is not clean and the curation says `coverage_policy: enforce`; the default `report` writes and goes on | No release, or no text index |
| `health` | Loads the release like the server and reports counts, languages, staleness and review statuses | Never |
| `accept` | Replays the pack's acceptance suite (`releases/<pack>/acceptance.yaml`) against the built release with no model involved, writes `acceptance-report.json` and fails when a blocking case fails: a search that no longer finds its concept, a resolve with another status, a claim of the expected answer that no served fact states (or whose cited excerpt does not contain the quoted phrase), a lost `not_served` item, a fact the case excludes | The pack has no acceptance suite |
| `ready` | Runs the gates of the acceptance gate on the current files (thorough validation with every cited saved response re-hashed, no dropped fact, the suite replayed, the freshness runway, the graded live-caller sessions of `acceptance-answers.json` under the suite's answer policy, the committed acceptance report current) and writes `readiness.json` bound to the bytes of `release.json`; only runs when asked with `--until ready`, and needs `--attested-by` | Never |

The `coverage` stage is what makes a saved-but-forgotten page visible: the
release's own document list is derived from its citations, so validating it
cannot find a page no fact cites. The unit is the section (a heading run with
citable text, `swisstip.extraction.sections`), because a page cited for one
paragraph can still hold the answer to a question two headings above it. A
disposition names a `document_id` (with optional `section_ids`) or a
`url_prefix` rule with its `known_documents`, carries a kind (`out_of_scope`
with the manifest entry it rests on, `duplicate`, `navigation`, `deferred`
with a `reaffirm_by` date), a reason, an author and a date; the ready stage
copies the counts into `readiness.json` as information, not as a gate.
Repeated site boilerplate (a section text on `boilerplate_min_pages`
candidate pages of one host; the curation sets the number, default five,
never below three) is not asked for but traced: the report says on which
page, if any, a fact cites it, so a contact card is served once from the
office's own page and an address the release serves from nowhere is visible
as `cited_nowhere`. The extract stage already marks every such repetition
from two pages up in the text index (`repeated_sections`) and the reading
views (`repeated on N pages` on each block), so readers see it before any
threshold applies. The
per-package command is `swisstip-coverage`
(`python -m swisstip.build.coverage_cli`).

`python -m swisstip.build.source_terms releases/<pack>/release.json` prints,
per concept, the capitalised words and compounds of the cited excerpts as
candidates for `source_terms`; the curator copies the publisher's own terms
into `curation.yaml` and the next build verifies them.

The `accept` stage is the model-free check of the acceptance gate
([docs/architecture/acceptance-gate.md](../../docs/architecture/acceptance-gate.md)).
Each case of a suite is one user question of the pack's acceptance-test
document with its expected answer and trap, the tool requests a live model
sent in a recorded run, and the claims the served facts must carry. The
`mvp-zurich` suite holds UAT-1 to UAT-44 of
`docs/product/user-acceptance-tests.md` (the German cases also fix the
search query as typed; see
`.local/experiments/2026-09-13-standing-cases-exchanges.md`,
`.local/experiments/2026-09-13-edge-case-questions.md` and
`.local/experiments/2026-09-13-multilingual-work-permit.md`); the
`mvp-wallisellen` suite holds the seven primary questions, the tool-request
variants and ten boundary probes of
`docs/product/wallisellen-user-acceptance-tests.md`, the probes fixing the
content the OpenCode run of 15 September 2026 showed missing. `accept` is the
default `--until`, so a pack with a suite does not report success if a
curation or build change silently dropped a fact, weakened its wording, or
broke its jurisdiction or context match; the pack check
`scripts/test/packs/test_committed_packs.py` replays every committed suite
against its committed release as well. It is not one of this package's
tests: the package build depends on no knowledge base. The stage cannot check what a calling LLM
composes from the facts; the UAT harness under `scripts/test/mock-mcp/`
still owns that.

| Option | Effect |
| --- | --- |
| `--from STAGE`, `--until STAGE` | Run a part of the pipeline |
| `--download`, `--retry-failed` | Network in the acquire stage; otherwise no request is made |
| `--workers N` | Download host groups and extraction workers |
| `--scope attributed` or `all` | Extraction scope |
| `--thorough` | Re-hash every saved response in both validation stages |
| `--update-curation` | Write relocated citations and fresh anchors back into `curation.yaml` |
| `--release-id ID` | Name the release; default keeps the current ID when inputs are unchanged and bumps the version (`<pack>-<date>-vN`) when they changed |
| `--attested-by NAME` | The person attesting the release in the `ready` stage; recorded in `readiness.json` |
| `--packs-dir DIR` | The packs repository; required unless `SWISSTIP_PACKS` names it |
| `--run-dir DIR` | Use another run directory; the default is `<packs-dir>/.local/<pack>` (`releases/<pack>` only when it holds a `plan.json`, which no pack does) |

A changed catalogue stops the acquire stage: a run directory is bound to the
catalogue it was planned from, so a new catalogue needs a new run directory.
Every run writes `releases/<pack>/pipeline-report.json` with one record per
stage (status, seconds, counts, error). Exit code 1 when a stage failed, the
build dropped a fact, a blocking acceptance case failed or a readiness gate
failed, 2 on a usage error. A committed release needs its `readiness.json`
(`--from accept --until ready --attested-by "<name>"` after every change to
the release or the suite); the pack check
`scripts/test/packs/test_committed_packs.py` and the
container refuse a release without one, and the server reports a release
without one as a candidate. The per-package commands
(`swisstip-download`, `swisstip-gaps`, `swisstip-extract`,
`swisstip-build-release`, `swisstip-validate-release`) remain available to
run or debug a single stage.
