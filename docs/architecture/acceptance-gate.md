# Acceptance gate

**Last update:** 23 September 2026

**Status:** sections 2 to 5 and the `accept` and `ready` stages, the
committed-record test, `--require-ready`, the container and the harness of
section 6 are implemented in `packages/core`, `packages/runtime`,
`packages/build`, `apps/knowledge-builder`, `apps/mcp-server` and
`scripts/test/mock-mcp/`, with a suite and a readiness record for each
committed pack; every claim of the suites quotes its cited excerpt, and
every case carries the answer block the harness runs. Gate G5 is advisory
for both packs until the first graded runs on the current releases exist
(section 9). The console's part of section 6 is planned; section 8 names
what remains.<br>
**Schema versions:** `swiss-tip-acceptance/v1`,
`swiss-tip-acceptance-report/v1`, `swiss-tip-acceptance-answers/v1`,
`swiss-tip-readiness/v1`<br>
**Relation to the other documents:** the cases restate the scenarios of
[user-acceptance-tests.md](../product/user-acceptance-tests.md) and
[wallisellen-user-acceptance-tests.md](../product/wallisellen-user-acceptance-tests.md)
in a machine-readable form; the release they run against is described in
[release-format.md](release-format.md); the tool requests they send follow
[tool-contracts.md](tool-contracts.md).

## 1. Purpose

A release is a set of facts with citations. The build proves that every
citation is an exact excerpt of a saved page and that every hash matches; it
does not prove that the release still answers the questions it was built to
answer, nor that a fact's wording is backed by the excerpt it cites. The
acceptance gate adds that proof: a fixed set of user questions with expected
answers, taken from the acceptance-test documents, that a release must pass
before it counts as ready.

One file per pack holds that set, and the MCP round-trip scripts and the
OpenCode harness take their cases from it, so the expectations of the
knowledge builder, the console, the round trip and the harness cannot drift
apart.

## 2. The acceptance file

`releases/<pack>/acceptance.yaml` holds one case per user question. A case
carries what a person reads (the question as typed, the expected answer, the
trap a generic answer falls into), what the model-free check replays (tool
steps and expected claims) and, later, what the answer-quality check needs.
Models: `swisstip.core.acceptance`; loader: `swisstip.build.acceptance`.

```yaml
schema_version: swiss-tip-acceptance/v1
pack: mvp-zurich
policy:
  as_of: snapshot          # applicability date of every resolve step that names none:
                           # the release's snapshot date, "today", or a date
cases:
- case_id: UAT-3
  label: family reunification for a child over twelve
  spec: docs/product/user-acceptance-tests.md#uat-3-family-reunification-for-a-child-over-twelve
  question: I have had a Swiss B permit since March 2022 and work in Zurich. My son is 13 ...
  expected_answer: The twelve-month deadline for children over twelve ran from the grant ...
  trap: A plain yes with the usual conditions; the twelve-month deadline is missed.
  steps:
  - resolve:
      concept_ids: [family-b, family-deadlines]
      jurisdiction: {canton_code: CH-ZH}
      context: {sponsor_status: b}
      expect_status: {family-b: SUPPORTED, family-deadlines: SUPPORTED}
  claims:
  - claim: Children over twelve must be brought within twelve months
    concept: family-deadlines
    statement_contains: [twelve months]
    excerpt_contains: [innerhalb von zwölf Monaten]
  - claim: Art. 44 permits rather than guarantees a permit
    concept: family-b
    statement_contains: [rather than itself guarantees]
```

| Field | Meaning |
| --- | --- |
| `policy.as_of` | Applicability date of every `resolve` step that names none: `snapshot` (the release's snapshot date, the default), `today`, or a date. A suite with `snapshot` passes the same way on every day; staleness is a gate of its own (section 5) |
| `policy.min_runway_days`, `policy.answer_check`, `policy.answer_repetitions`, `policy.answer_pass_rate` | The runway gate G4 (default 14 days) and the answer-quality gate G5: `required`, `advisory` (the default) or `off`, the graded sessions a case needs (3) and the share of them that must pass every criterion (0.8); section 4 |
| `case_id`, `label`, `spec` | The ID the acceptance-test document uses, a short title, and a link to the section of that document |
| `question`, `expected_answer`, `trap` | The user's question verbatim, the answer the document expects, and the mistake a generic answer makes; `trap` is optional |
| `language` | The language of the question as typed (ISO 639 code, `gsw` for Zurich German); optional, required when a search step is translated |
| `blocking`, `quarantine_reason` | A case is blocking by default; `blocking: false` needs a reason, and the case is still run and reported |
| `steps` | Tool calls in order, each either `search` (`query`, the query as the caller sends it: a question in one of the release's query languages as asked, any other question as its key terms translated into the preferred query language, with `translated: true`, since the suite's author prepares the translation a live caller would make; `retrieval: hybrid`, an expectation that holds only with semantic search, which a lexical replay records without judging; `expect_concept`, `within`: the concept must be among the first hits; `jurisdiction`, the user's place as a caller that knows it sends it with the search, and `expect_elsewhere`, the concepts the result must then name in `published_elsewhere` and must not rank, both absent from the suite digest while unset; `expect_strength`: the `match_strength` the result must report, `weak` or `none` for a question outside the release, so an off-topic question is pinned at the search step and not only at resolve; a step carries at least one of the two expectations) or `resolve` (`concept_ids`, `jurisdiction`, `context`, `as_of`, `reviewed_only`, `expect_status` per concept, `expect_gap`, the gap dimension a concept's result must name, so a decline is checked for its reason, and `expect_basis`, the start of a basis label that at least one served fact of the concept must carry, so a case pins that the law, a directive or the authority's own page stays among the served facts: `Federal act: AIG, SR 142.20, Art. 42`; for a jurisdiction given in names, `expect_scope`, the most specific code the request must have run for (`CH-ZH-69`), `expect_not_recognised`, the jurisdiction parts the place register must report as not recognised and no others, an empty list meaning every part was recognised, and `expect_error`, the path of the issue of a request that must be rejected as `INVALID_ARGUMENT`, such as `jurisdiction.city` for a name several municipalities share; a step that expects an error expects nothing else, and a rejected request fails every other step. The three count in the suite's digest only when a step sets them, so a suite without them keeps its digest and its readiness record) or `lookup` (`dataset_id`, `postal_code`, `as_of`, `start`, `end`, `limit`, `expect_status`, `expect_gap`, `expect_first_date`: a dataset a connector serves behind a concept, [dataset-connectors.md](dataset-connectors.md); judged only when the replay is given a connector that serves the dataset, otherwise recorded as not judged, so a pack's readiness never depends on a sidecar, and absent from the digest of a suite without one). Every request is validated against the tool contract when the file is loaded, so a case cannot drift from the API. A case without steps (a boundary probe that must refuse) is checked by the answer-quality check only |
| `claims` | The assertions of the expected answer. Each names its `concept` and holds when one served fact contains every `statement_contains` phrase and, where `excerpt_contains` is given, one of that fact's cited excerpts contains every phrase, verbatim in the source language; `fact_id` pins the fact instead of finding it by wording. Matching ignores case, soft hyphens and whitespace |
| `not_served` | Per concept, phrases its `not_served` list must keep naming, so a caller keeps being told what the release lacks |
| `must_not_serve` | Fact IDs that no step of the case may return: a rule that must not apply to this user, or a fact published for another canton or municipality than the one the case resolves for |
| `answer` | What the answer-quality check expects of a live caller's final answer: `criteria` (the IDs of the general criteria the grader applies, A1 to A10 or C1 to C6 of the acceptance-test documents; a case that carries A10 resolves for one place in every step, which the harness reads as the user's), `must_mention` and `must_not` (name to regular expression; a negated or quoted match is not an assertion), `cites` (URL fragments) and `resolved` (concepts the caller must resolve; a nested list names alternatives). A case without it is not part of gate G5 and is not run by the harness: a decline case, which tests only that the server rejects the request, is checked by the model-free replay alone |

Rules the loader enforces: case IDs are unique; a claim's concept and a
`not_served` concept appear in a `resolve` step of the case; each
`expect_status` concept is requested by its step; a claim states at least a
fact ID or a phrase; a case with a translated search step names its
`language`.

The fields `language`, `translated` and `retrieval` enter the suite digest
only when set, so a suite that does not use them keeps the digest its
readiness record names; every other field counts with its default.

A missing concept fails its case; it is never trimmed from the request.
Rewording a fact breaks the claims that quote it on purpose: the case is
updated in the same commit, so a changed expected answer is visible in
review.

## 3. The model-free check

`swisstip.runtime.acceptance.check_acceptance(service, suite)` replays every
case against a `ReleaseService`, the same code the server answers with, and
returns a report; no model, no network, milliseconds per pack. For every
case it checks, in order: each `search` step finds its concept within the
first hits; each `resolve` step is accepted and returns the expected status and
gap per concept; every claim holds on the served facts and their excerpts; every
`not_served` phrase is still declared; no `must_not_serve` fact was served.
The messages keep the form `CASE (label): what changed`, so a pipeline
failure names the case and the expectation.

The report (`releases/<pack>/acceptance-report.json`) carries the release ID
and content digest, the suite digest (a hash over the parsed suite with its
defaults, so a comment or reformatting of the YAML does not change it, while
a changed expectation does, and so does a new field of the suite model),
the applicability date, one record per case with its steps, claims and
issues, and the lists of failed blocking and quarantined cases. `passed` is
true when no blocking case failed.

## 4. The answer-quality check

The model-free check cannot judge what a calling assistant composes from the
facts. The OpenCode harness under `scripts/test/mock-mcp/` does, with a live
model answering and a grader (a Claude Code subagent or a person) writing
`grade.json` per run:

- The harness builds its cases from the suites (`suite_cases.py`): the
  question, the expected answer, the trap and the `answer` block of every
  case; `wallisellen_cases.py` keeps only what the harness alone knows (the
  CLI name of each case, the prompts, the general criteria texts, the
  strict and tool checks), keyed by `case_id`. A suite case with an `answer`
  block and no harness name fails the harness's own test.
- Every run summary records the release's `content_sha256` (locally from the
  file, remotely from `/health`) and the suite digest.
- `--answers FOLDER... --pack <pack>` aggregates the grades of every
  answered run with the server on the pack's current release into
  `releases/<pack>/acceptance-answers.json` (models:
  `AcceptanceAnswers`, `AnswerRecord`): per case the answered and graded
  sessions, how many held the trap and passed every criterion, the models,
  the unsupported claims and the run folders, bound to the release's
  content digest and the suite's digest. Controls, the mock server and runs
  on another release are left out; the two-turn UAT-1 run counts once and
  gets one grading packet with both turns of both scenarios.
- `answer_verdict` reads that file as gate G5 under the suite's policy:
  `answer_check` `required` blocks, `advisory` reports, `off` skips; a case
  meets the policy when it has at least `answer_repetitions` graded
  sessions, the trap held in all of them and at least `answer_pass_rate` of
  them passed every criterion. A blocking answer check is a rate over
  repetitions, never one session's verdict.

The pipeline never calls a model; it only imports the grades. Runs stay
inside the Claude Code window, as the agent-operated direction requires.
The aggregation binds the grades to the release, not to the wording of the
expected answers: a case whose expected answer or trap changed needs its
packets graded again, which nothing enforces.

## 5. Readiness

The `ready` stage of the knowledge builder, run on request
(`swisstip-build <pack> --from accept --until ready --attested-by "<name>"`),
runs every gate on the current files, never on a record inherited from an
earlier run, and writes `releases/<pack>/readiness.json` only when all pass:

| # | Gate | How it is checked | Status |
| --- | --- | --- | --- |
| G1 | The release validates thoroughly | `validate_release` against the text dataset and the run, so every cited saved response is re-hashed | implemented |
| G2 | The build dropped no fact | `build-report.json` names this release ID and lists no dropped fact | implemented |
| G3 | Every blocking case passes the model-free check | the suite is replayed again; quarantined cases are listed in the record | implemented |
| G4 | The release has runway | `stale_from` lies at least `policy.min_runway_days` (default 14) after the attestation date and after the policy date | implemented |
| G5 | The graded live-caller sessions meet the answer policy | `acceptance-answers.json` carries this release's content digest and this suite's digest, and every blocking case with an `answer` block meets the policy (section 4); `advisory` records the shortfall and passes, `off` skips | implemented |
| G6 | The committed acceptance report is current | `acceptance-report.json` carries this release's content digest and this suite's digest and passed | implemented |

Models: `swisstip.core.readiness`. The record holds the pack, the release ID,
`release_sha256` (the SHA-256 of the bytes of `release.json`), the content
and suite digests, the attestation time and name, the verdict and detail per
gate, the case counts with the quarantined IDs, the review-status counts,
the snapshot date, `stale_from` and the runway policy.

A release is ready only when a `readiness.json` next to it names exactly its
file, by `release_sha256`, with every gate passed; otherwise it is a
candidate. The file digest rather than the content digest binds the record,
because the content digest leaves the manifest out: a hand-edited
limitation, scope statement or freshness window would keep it. Any change to
the release file therefore invalidates readiness by itself, and the build
side (the committed-record test) also requires the record's suite digest to
be the current suite's, so a changed suite needs the gate run again. There
is no status field to reset. The server checks the file digest only; it has
no suite.

Readiness does not require human-reviewed facts: the review counts are
recorded and stated in `LIMITATIONS.md`, so a pack of unreviewed facts can be
ready with that limitation visible. A per-pack `require_human_review` policy
flag is the place to tighten this.

The record also carries `coverage`, the counts of the pipeline's `coverage`
stage when `curation-coverage.json` is the report of this release's content
digest: how many candidate records and content sections the text dataset
holds, how many a fact cites, how many a curator dispositioned in
`curation-coverage.yaml`, how many are neither, and whether the report is
clean. It is information, not a gate: whether an unclean report stops the
build is the curation's `coverage_policy` (`report`, the default, or
`enforce`), decided per pack, so that a pack with history writes its
dispositions before the stage can fail it, and a frozen pack is not broken by
a rule it never had. Promoting it to a gate G7 is the step after `mvp-zurich`
is clean under `enforce`.

## 6. Where the gate is enforced

| Where | Behaviour | Status |
| --- | --- | --- |
| Pipeline | The `accept` stage (the default `--until`) replays the suite, writes the report and fails on any blocking case; a pack without a suite is skipped and says so | implemented |
| Pack check | `scripts/test/packs/test_committed_packs.py` (run by `knowledge-bases.yml`, not by the package build, which depends on no knowledge base) replays every committed pack's suite against its committed release, and fails when a committed `release.json` has no suite; a changed release cannot be committed without its cases passing | implemented |
| Pipeline `ready` stage | Gates G1 to G6 on the current files, `readiness.json` bound to the release bytes; needs `--attested-by` | implemented |
| OpenCode harness | Cases from the suites, the content digest in every run record, `--answers` writing `acceptance-answers.json` | implemented |
| Unit test | The same test fails when a committed `release.json` has no `readiness.json` naming its bytes and the current suite | implemented |
| MCP server | `--require-ready` refuses to start, or to report healthy, without a matching record; without the flag it serves a candidate with a warning, and `--health` and `/health` carry `readiness` (`ready` with the attestation, or `candidate` with the reason) | implemented |
| Dockerfile | Copies `readiness.json` next to the release, runs the build-time `--health` and the entry point with `--require-ready`, so no image is built from or serves a candidate; `build_image.py` refuses a candidate before the build starts | implemented |
| Regression pack | `scripts/test/regression/run_regression.py` replays `acceptance.yaml` and `regression.yaml` lexically and with hybrid search into `regression-report.json`; the committed-pack test replays both files lexically and requires the committed report, with its hybrid run, to name the current release and suites (section 10) | implemented |
| Admin console | The release screen shows readiness and the gate table; the sandbox saves its checks into `acceptance.yaml`, and `checks.yaml` goes away | planned |
| `COVERAGE.md`, `LIMITATIONS.md` | Take the attested case counts and the quarantined cases from `readiness.json` | planned |

## 7. Layering

```text
packages/core      acceptance.py   models, the suite digest, the answers file and answer_verdict (gate G5);
                                   text.py, the phrase normalisation
                   readiness.py    the readiness record and readiness_status(release_path)
packages/runtime   acceptance.py   check_acceptance over ReleaseService (no YAML: the container has no PyYAML)
packages/build     acceptance.py   load and save the YAML file; load_regression combines a pack's two suites
apps/knowledge-builder             the accept stage, the coverage stage (swisstip.build.coverage, informational in
                                   readiness) and the ready stage with gates G1 to G6
apps/mcp-server                    --require-ready, the readiness field and the content digest of the health payload
scripts/test/mock-mcp              suite_cases.py builds the harness cases from the suites; --answers aggregates
                                   the grades into acceptance-answers.json
scripts/test/mcp                   the same steps through the MCP transport (planned)
scripts/test/regression            run_regression.py replays the regression pack lexically and hybrid (section 10)
apps/admin-console                 reads and writes the suite, shows readiness (planned)
```

The serving side imports nothing from the build side; the server reads
`readiness.json` and nothing else of the gate.

## 8. What remains

The gate itself is complete. What is left lies outside it: the first graded
runs on the current releases (then `answer_check: required` for
`mvp-zurich`, section 9), the console's part of section 6, the MCP
round-trip scripts replaying the suite, and gate G7, curation coverage,
which section 5 describes as information until `mvp-zurich` is clean under
`coverage_policy: enforce`.

Two limits of a phrase check stay: a flattened table is read as text (the
2026 rate is matched as `93 %` near `Politische Gemeinde`, not as a column),
and a statement no claim covers is not checked at all.

## 9. Decisions taken

- The answer-quality check is to be `required` for `mvp-zurich` (the default
  release, shipped in the image) and `advisory` for `mvp-wallisellen`. Both
  suites say `advisory` while no graded run on the current releases exists:
  a `required` policy without one would leave the repository with a release
  that cannot be attested and a failing committed-record test. The flip to
  `required` is the commit that adds the first `acceptance-answers.json` for
  `mvp-zurich`.
- Readiness does not require human review (section 5).
- The server refuses a candidate inside the container and only warns
  locally, so a candidate stays easy to serve during curation.

## 10. Regression pack

The acceptance suite restates the acceptance-test documents: a few cases,
each with an expected answer, a trap and claims, and every one of them a
readiness gate. A regression pack adds breadth: many plain questions that
must find their concept and resolve, and questions outside the release that
must be declined, in the languages users write in.

| Item | What it is |
| --- | --- |
| `releases/<pack>/regression.yaml` | The questions beyond the acceptance-test document, in the suite format of section 2. Not a readiness gate: its digest is not in `readiness.json`, so adding a case needs no attestation |
| The pack | `acceptance.yaml` and `regression.yaml` replayed together (`swisstip.build.acceptance.load_regression`), the acceptance cases first, under the acceptance suite's policy; a UAT case is referenced, never copied, and a case ID may appear in one file only |
| `releases/<pack>/regression-report.json` | Schema `swiss-tip-regression-report/v1`: the release's content digest, the digests of both suites, and one run per retrieval mode (`lexical`, `hybrid`), each with the pass count, the failed blocking cases, the quarantined cases still failing and those now passing, the number of hybrid-only steps a lexical run did not judge, and per case its issues and the query, hits and match strength of every search |
| `releases/<pack>/test-cases.md` | The readable page of the pack, rendered by `swisstip.build.case_catalogue` from the two suites, the report and `acceptance-answers.json`: every group of cases opens with an overview table (one row per case: question, language, status, outcome per retrieval mode, live-caller grades, a link to the case), then every case with its question, expected answer, trap, steps, claims, facts that must not be served and quarantine reason, followed by its latest results per retrieval mode (outcome, first hits, match strength, issues) and, for an acceptance case, its graded live-caller sessions with the release they ran on |
| `scripts/test/regression/run_regression.py` | Writes the report and the page (`--render-only` rewrites the page alone from the committed files). The hybrid run uses the release's `semantic-index.json` and the local Ollama model it names, as the container does; it exits 2 when semantic search is unavailable or a query fell back to lexical search, and 1 when a blocking case fails |
| Committed-pack test | Replays the pack lexically with no blocking issue, and requires the committed report to name the current release and both suites, to hold a hybrid run and to have passed, and the committed page to equal the page the committed files render |

Rules of the file:

- `Q-` cases are plain questions: the search step expects the concept among
  the first three hits with a `strong` match, the resolve step `SUPPORTED`.
  `OOS-` cases are declined: a topic the release does not publish at search
  (`weak` or `none`) and at resolve (`concept_not_published`), a covered
  topic asked for another place at resolve alone
  (`jurisdiction_not_covered`), with the concepts that still apply there
  expected `SUPPORTED`. `UAT-<n>-T` is a variant of a UAT case whose question
  is in a language the release does not advertise, with the translated
  search a caller would send.
- `XC-` and `XM-` cases are asked from another canton and from a Zurich
  municipality other than the city. The search step expects the concept
  that applies at the user's place among the first three hits, although
  the step sends no `jurisdiction` and a Zurich concept may rank above it (a
  search step can send one and name the concepts expected in
  `published_elsewhere` with `expect_elsewhere`; no committed case does yet). The
  resolve step is sent for the user's place and pins the split: the federal
  facts (in `XM-` also the cantonal facts) `SUPPORTED` with a claim on the
  fact the answer needs, the user's own canton from SEM's directory where an
  office is asked for, and every Zurich concept a caller might request
  alongside `OUT_OF_COVERAGE` with `jurisdiction_not_covered`; a served
  concept of a topic that holds a Zurich level must carry the caveat
  `more_specific_jurisdiction_not_published`, and `must_not_serve` names the
  Zurich facts no step may return. A question
  whose whole subject is published for Zurich only is an `OOS-` case.
- `N-EN-` and `N-DE-` cases follow the `Q-` rules with a question written
  the way people type it: `T` with typos, ASCII umlauts or no capitals, `J`
  with jargon, expat slang, Germany-German vocabulary, outdated office names
  or Zurich German (language `gsw`, sent as typed), `A` with abbreviations.
- `PN-` cases send the jurisdiction in names (`country`, `canton`, `city`):
  `expect_scope` pins the code the place register made of a name,
  `expect_not_recognised` a part it does not know (a quarter of a city), and
  `expect_error: jurisdiction.city` a name several municipalities share or a
  city named with the wrong canton.
- `CTX-` cases pin context routing at resolve: a question that does not
  reveal what a concept needs (`NEEDS_CONTEXT`); a user in a population,
  sponsor status or insurance situation a concept does not serve
  (`OUT_OF_COVERAGE` with `context_not_covered`), next to the concept that
  does apply, with `must_not_serve` naming the facts of the branch that does
  not; and a City of Zurich concept requested for the canton when the
  municipality is not known (`jurisdiction_not_covered`).
- `DATE-` cases resolve for the date the user asks about (`as_of`). A fact
  is not served before its `valid_from` or after its `valid_through`, and a
  concept whose facts are all dated later is `OUT_OF_COVERAGE` with
  `date_outside_coverage`. A date after the release's `stale_from` makes
  every status `STALE`, so a case about such a date pins the facts served
  and not served and no status, and holds on the next release too.
- A case asks for something no other case asks for: a fact, a situation, a
  context branch or a place. The same need in other words, in another
  language, with typos, or from another town is not a new case, unless the
  variation itself is what the case tests and no other case asks it.
- A question in a query language of the release (`get_coverage`
  `query_languages`) is sent as asked; any other is sent as the key terms
  its author translated into the preferred language, marked
  `translated: true`, with the case's `language` naming the question's. The
  key terms carry the question's words only, never words of the answer.
- A search that passes only with semantic search is marked
  `retrieval: hybrid`, so the lexical test stays meaningful for the rest.
- A case that fails in the hybrid run is committed with `blocking: false`
  and a `quarantine_reason` that names the defect and the release it was
  measured on. The report lists a quarantined case that starts passing, so
  it can be made blocking; a blocking case that fails stops the commit.

`mvp-zurich` carries the first pack. Its plain questions are asked in
English and German as typed and, as translated key terms, in French,
Italian, Spanish, Portuguese, Polish, Romanian, Hungarian, Turkish,
Albanian, Serbian, Ukrainian, Arabic, Tamil, Russian and Hindi, besides
`UAT-7-T` in Zurich German. The office-contact cases pin addresses, opening
and telephone hours and the negative statements the pages make (no e-mail
address, closed days, a location the Road Traffic Office does not have) as
claims on the served fact; the daily-life cases (waste, parking, vehicles,
dogs, school holidays, the tax return, the radio and television fee,
emergencies) follow the cases per fact of the earlier topics. More than
half of the plain and noisy cases and most context and date cases carry a
claim on the fact that answers the question, so a reworded or dropped fact
fails the case and not only a lost concept. The cross-jurisdiction cases (`XC-`, `XM-`, and the `OOS-` declines
of Zurich-only subjects asked elsewhere) come from most other cantons and
from Zurich municipalities around the city, some from two places at once
(living in Schwyz and working in Zurich, moving from Zurich to Bern); they
accompany UAT-35 to UAT-44 of the acceptance suite. The quarantined cases
name defects of the release: plain and noisy questions whose concept ranks
below the first three hits or reads weak, off-topic questions that read
strong, and a fact served to a group its statement excludes. The case
totals, the pass counts per mode and the quarantined cases are in the
committed `regression-report.json`; `run_regression.py` prints them.
