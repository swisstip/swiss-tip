# Admin console - technical design

**Last update:** 16 September 2026

**Status:** implemented, except the assistant draft of section 4.5, which
waits for the concept-extraction provider port of `TODO.md`, and the live
traffic of section 4.9, which needs the log of a served Streamable HTTP
endpoint saved to a file. What runs, and how to run it, is in the
[console README](../../apps/admin-console/README.md); offline tests cover
the areas of section 10<br>
**Location:** `apps/admin-console` (`swisstip.admin_console`)<br>
**Depends on:** the build-side packages (`swisstip.ingestion`,
`swisstip.extraction`, `swisstip.build`, `swisstip.core`), the runtime
(`swisstip.runtime.service`) and the knowledge builder
(`swisstip.builder.pipeline`)<br>
**Never imported by:** the MCP server. The serving guarantees of section 3.2
of the [functional specification](../product/functional-specification.md)
are untouched by this design

---

## 1. Purpose and position in the pipeline

Without the console, the knowledge expert reads Markdown reading views and
edits `curation.yaml` by hand, and the lead runs the pipeline from the
command line and reads JSON reports. The admin console is a local web
application that puts a graphical interface over exactly those files. It
introduces no new source of truth: every screen reads files
the pipeline already writes, and the console writes only two of them, the
curation file and the source catalogue. Git remains the audit trail and the
release remains the only thing the server reads.

```text
releases/<pack>/sources.json        read, written (screen 2)
<run>/                              run: plan, manifests, gap report      read (screens 2, 3)
<run>/text/                         text dataset: index, records, views   read (screens 4, 5, 6)
releases/<pack>/curation.yaml       read, written (screens 5, 6, 7)
releases/<pack>/release.json        read (screens 7, 8); written only by the build stage
releases/<pack>/build-report.json   read (screens 3, 5)
releases/<pack>/pipeline-report.json read (screens 1, 3)
releases/<pack>/checks.yaml         new: stored tool checks, read and written (screen 8)
.local/<pack>/console/              new: job logs and the call-log aggregate (screens 3, 9)
```

`<run>` is `.local/<pack>/`, outside Git for every pack (`releases/<pack>/`
only when that folder holds a `plan.json`, which no pack does). The
console's own working state is under `.local/<pack>/console/`.

The console starts pipeline stages through `swisstip.builder.pipeline` and
answers tool calls through `swisstip.runtime.service` on a release it loads
itself. It never composes an answer, never edits a statement on its own and
never fetches a page outside the acquire stage.

## 2. Users and modes

| User | Needs | Mode |
| --- | --- | --- |
| Knowledge expert | Read pages, select excerpts, write concepts and facts, review every served fact, check that a fact resolves | `edit` |
| Lead | Run the pipeline, judge dropped and relocated citations, write the manifest text, cut a release, see the working-tree diff before committing | `edit` |
| Evaluator or Swisscom reviewer | See what is covered and try the four tools on the served release | `read-only` |

The mode is a start-up flag (`--read-only`). In read-only mode the write
endpoints of section 6 are not registered at all, the job endpoints of
section 7 are not registered, and screens 2 to 7 render without forms. The
mode is displayed in the page header.

## 3. Rules

1. **Files are the state.** No cache that outlives the process.
   Every list is read from an index file (`text/index.json`, `plan.json`,
   `gap-report.json`, `curation.yaml`, the release manifest), never by
   scanning `documents/`. Section 8 says what is held in memory and how it
   is invalidated.
2. **Two write targets.** `curation.yaml` and `sources.json`, both through
   the existing Pydantic models and the existing `save_curation` and
   catalogue writer, so the console can never write a file the pipeline would
   refuse. Everything else the console shows is produced by a pipeline stage.
3. **No silent writes.** Every write records who did it (section 6), and the
   console never commits. The lead commits from Git after reading the diff
   on the release screen.
4. **The build never edits a statement, and neither does the console on its
   own.** The assistant draft of section 5.5 is an explicit action whose
   result carries the provenance the release format defines for
   assistant-authored text.
5. **A person confirms; a machine never does.** Only the review action of
   screen 6, taken by a named person in edit mode, sets `human-reviewed`.
   Nothing else in the console, the pipeline or the assistant draft can.
6. **Original language as is.** Excerpts are shown in the language of the
   page. A translation aid, when present, sits beside the excerpt, is labelled
   unverified and is never written into a citation or a statement.
7. **Scale by index, not by load.** The full pack has 12,117 records and
   1.25 million blocks, and a KB2 with source sections could hold 140,000
   facts. Every list pages; a record is read only when opened; the review
   queue for automatically derived facts samples rather than enumerates.
8. **Offline by default.** The only network request the console can cause
   is the acquire stage with the download flag, behind a confirmation that
   shows the request budget and the etiquette settings of the plan.
9. **Local by default.** Bound to `127.0.0.1` unless told otherwise; section
   9 covers the hosted case.

## 4. Screens

Each screen lists what it reads, what it writes, its layout and its actions.
Field names are those of the files, so that a screen can be checked against
the file it renders.

### 4.1 Packs overview (home)

Reads: every `releases/*/` folder; `sources.json` (source count by authority
level); the run's `summary.json` and `gap-report.json` (targets, saved,
gaps); `text/summary.json` (records, eligible, blocks); `curation.yaml`
(topics, concepts, facts, review statuses, provenance kinds);
`release.json` manifest (`release_id`, `freshness`, counts);
`pipeline-report.json` (last run, per-stage status); `checks.yaml` (last
result of the stored checks).

Writes: nothing.

Layout: one card per pack with four rows.

| Row | Content |
| --- | --- |
| Sources | Catalogue entries by authority level; saved of planned; gaps split into retriable and not retriable |
| Text | Records, eligible records, blocks, dataset date, superseded records since the last build |
| Curation | Concepts and facts; a stacked bar of facts by `review_status`; a stacked bar by `provenance.kind`; the standing-case facts marked present or missing |
| Release | Active `release_id`, `snapshot_date`, days until `stale_from` (red under 14), last pipeline run with its stage strip, last stored-check result |

Actions: open any screen for the pack; start a pipeline run (section 7);
add a pack, which creates `releases/<pack>/sources.json` from the catalogue
template with an empty `sources` list.

### 4.2 Sources and coverage matrix

Reads: `sources.json`; `plan.json` (`targets[]` with attribution);
`gap-report.json` (`gap`, `retriable`, `action` per target); `text/index.json`
(`page_kind`, `status`, `language_declared` per record, joined by
`source_url`).

Writes: `sources.json` through the catalogue model (section 6.2).

Layout, two tabs:

**Catalogue table.** One row per source: `source_id`, `title`,
`authority_level`, `definition.jurisdiction`, `definition.language`,
`priority`, `scan_status`, `canonical_authority`, and three derived columns:
the gap verdict of the source's catalogue target (`none`, the gap class with
its `retriable` flag and `action`), the record status from the text index
(`extracted`, `excluded_source_response` with the page kind,
`no_extractable_text`, or none), and the number of facts citing a record of
this source. A soft error page shows here twice: as the gap class
`soft-error-page` and as `error_page` from the extraction's `page_kind`. Filters on
every column. A row opens the source form: every field of `definition`
(start URL, allowed hosts, allowed path prefixes, canonical authority,
jurisdiction, language), `title`, `authority_level`, `source_kind`,
`priority`, `topic_hints`, `discovery` (method, reference URL, located on),
`scan_status`, `notes`. Validation: unique `source_id`, a start URL inside
its own allowlist, a jurisdiction code of the shape the tool contracts
define, an ISO 639-1 language.

Below the table, the discovered pages of the run grouped by attribution kind
and host, as the gap report's Markdown does, with one action per page:
**promote to catalogue**, which opens the source form prefilled from the
page (URL, host, path prefix, language hint, the attributing source's
authority and jurisdiction) so that a page the crawl found becomes a
catalogue entry with a documented discovery reference.

**Coverage matrix.** Rows are `CH` and the 26 cantons; columns are `de`,
`fr`, `it`, `rm`, `en`. A cell shows the highest stage reached for that
jurisdiction and language and the count behind it:

| Stage | Source of the count |
| --- | --- |
| catalogued | Sources with that `jurisdiction` and `language` |
| saved | Their catalogue targets with an intact snapshot |
| extracted | Eligible records attributed to them, by `language_declared` else `language_hint` |
| curated | Facts with that `jurisdiction` whose evidence is in that language |
| reviewed | The same facts with `review_status: human-reviewed` |

A cell opens the catalogue table filtered to it. The matrix is the KB2
progress board; for the MVP pack it shows one column and two rows filled.

### 4.3 Pipeline runs

Reads: `pipeline-report.json`; the job log of the current run (section 7);
`build-report.json` (`citation_outcomes`, `dropped`, `facts_resolved` with
per-citation `outcome` and `warnings`); the run's `gap-report.json`.

Writes: nothing directly; starts jobs.

Layout: the seven stages as a horizontal strip, each with `status` (`ran`,
`skipped`, `failed`), `seconds`, the stage's counts and, for a skipped
stage, its `reason`. Below the strip, the streaming log of the running job,
then the run history (one line per past `pipeline-report.json`, kept under
`.local/<pack>/console/runs/` when a run finishes, so the report the
pipeline overwrites is not lost).

Run form: `from` and `until` stage, `workers`, `scope`, `thorough`,
`update_curation`, `release_id` (default from `next_release_id`), `download`
and `retry_failed`. Choosing `download` expands a confirmation that lists the
hosts of the plan, the per-host delay, the response cap and the number of
targets that would be requested, and requires a typed confirmation of the
pack name. A changed catalogue is detected before the run starts (the
catalogue hash of `plan.json` against `sources.json`) and offered as "plan a
new run directory" rather than failing inside the stage.

**Relocation review.** After a build stage, a panel lists every citation
whose outcome is not `same-snapshot`, grouped by outcome:

| Outcome | Shown | Actions |
| --- | --- | --- |
| `same-text` | Fact, document, old and new raw hash | none needed; listed for the record |
| `moved` without warnings | Old and new block IDs and offsets | none needed |
| `moved` with `context-changed`, `neighbourhood-changed` or `blocks-restructured` | Old and new heading path, the excerpt, the neighbouring blocks that changed | open in the review queue (screen 6), where the fact waits for re-confirmation |
| `ambiguous` | Every candidate range with its heading path | pick one candidate, which rewrites the citation's block range and anchor; or drop the fact |
| `changed` | The old excerpt and the closest new blocks with the similarity ratio | open the reading view at the closest block to re-cite; or drop the fact with a note |
| dropped for a missing record | The source URL and the gap verdict | open the source in screen 2 |

Picking a candidate or dropping a fact is a curation write (section 6.1);
the build is then re-run for the pack.

### 4.4 Documents and reading view

Reads: `text/index.json`; `text/documents/<id>.json` for the opened record;
`text/unavailable.json` and `errors.json`; `curation.yaml` to mark cited
blocks.

Writes: nothing; the selection action opens a curation form (screen 5).

**Document list.** One row per index entry: `title`, `source_url`,
`language_declared` / `language_hint`, `representation`, `page_kind`,
`status`, `eligible_for_processing` with `exclusion_reasons`,
`attribution_kind` and `source_ids`, `retrieved_at`, `blocks`,
`text_characters`, `superseded`, `imported`, and two derived columns: the
number of facts citing the record and whether it is the
`preferred_representation` of its group. Filters on every column; a search
over `title` and `source_url`. Records with `identical_text_records` show a
link count; unavailable targets and extraction failures are separate tabs.

**Reading view.** The record's header (title, source and document URL,
version URI, attribution, languages, retrieval date and attempt, raw and
content hash, warnings, `imported_text` when present), then one line per
block: block number in the margin, `kind`, text indented by the depth of
`heading_path`, and marks for `region`, `furniture` labels, `explicit_hidden`
and `is_footnote`. Tables render as tables with header cells and spans.
Three toggles: hide furniture (navigation, banner, footer, breadcrumb,
share, cookie notice, language menu, related links, anchor navigation,
scroll to top, chat link, link-only items), hide hidden blocks, show raw
`dom_path` and links. Blocks already cited by a fact carry the fact IDs in
the margin and a link to the fact.

Selecting a range of consecutive blocks shows the excerpt as the build would
cut it and one action, **cite**: either add the range as evidence to an
existing fact chosen from a search box, or create a new fact under a chosen
concept with the citation prefilled (`document_id`, `first_block`,
`last_block`). Selection is by clicking the first and last block or by
typing block numbers, because the expert's current workflow is block numbers
from the Markdown view.

**Diff view.** A superseded record opens beside its successor (the record
with the same `source_url` that replaced it): blocks aligned by
`text_sha256`, unchanged blocks collapsed, added and removed blocks
coloured, and every citation into the old record listed with its relocation
outcome from the last build report. This is the screen a refresh is judged
on.

### 4.5 Curation workbench

Reads: `curation.yaml`; `text/index.json` for document titles; the record of
each cited document for the excerpt preview; `build-report.json` for each
citation's last outcome.

Writes: `curation.yaml` (section 6.1).

Layout: a left tree of topics and concepts with fact counts and a review
progress mark; the right pane is one of four forms.

**Pack form.** `title`, `scope_statement`, `out_of_scope` (list),
`out_of_scope_response`, `limitations` (list), `freshness_max_age_days`,
`publishers` (host to name, the fallback when no institution rule matches
a cited host; a host cited by a record without a publisher or an institution
rule is flagged, because the build stops on it), `institutions`,
`page_basis` and `ranking_policy` (the registry of publishers, the default
basis of a page's excerpts and the ranking policy of
[institutions-and-provenance-weights.md](institutions-and-provenance-weights.md),
each as a YAML block validated through the curation models before the dry
build), `context_fields` (name, `enum`,
`description`; a field in use by a condition cannot be removed and an enum
value in use cannot be renamed without a confirmation that lists the facts).

**Topic form.** `topic_id`, `title`, `description`.

**Concept form.** `concept_id` (immutable after creation; renaming is a
separate action that rewrites `fact_ids` and the check file), `topic_id`,
`label`, `description`, `aliases`, `questions`, `required_context` (chosen
from `context_fields`), `required_user_facts` (name, status, instruction),
`decision_rule` (description, steps), `notes`. A side panel shows the
concept as `search` would rank it: the terms of `label`, `aliases`,
`questions` and `description` after the runtime's tokenizer, so the expert
sees which words a query can hit. Missing aliases and a description equal
to the first statement are shown as warnings on the tree node.

**Fact form.** `fact_id`, `statement`, `language`, `jurisdiction` (picker
listing `CH`, the cantons and, for a chosen canton, the municipalities the
release already knows; the containment rule is shown as a sentence:
"answers for CH-ZH and its municipalities, not for other cantons"),
`condition` (one row per required context field with its enum as a select),
`valid_from`, `valid_through`, `provenance` (`kind`, `review_status`,
`author`, `source`, `reviewed_on`, `notes`; `review_status` is read-only
here and changes only in screen 6), and the evidence list. Each evidence row
shows `document_id` with the record title, `first_block` to `last_block`,
the excerpt from the anchor, the anchor state (no anchor yet; anchored and
matching the current record; relocated with warnings; broken, from the last
build report), the basis the build would state for the citation with the
rule that produced it, and actions: open in the reading view at the block,
set the basis (kind, level, norm, `refers_to`; an empty kind returns the
citation to the page default, and an act, ordinance, treaty or directive
without a norm is refused by the dry build), adjust the range, remove. A
fact needs at least one citation and the form refuses to save without one,
as the model does.

**Draft with assistant.** On a fact whose statement is empty, a button sends
the excerpt, the concept label and the pack's context fields to the
configured provider (the provider layer of the concept-extraction port in
`TODO.md`; until it exists the button is absent) and fills `statement` with
the proposal, sets `provenance.kind: curated-statement`,
`review_status: assistant-authored-unreviewed`, `author` to the model name
and a note with the prompt version. The expert can edit before saving; the
status stays as set until a person reviews in screen 6.

**Save.** Every form validates through the curation models on the server,
shows Pydantic errors next to the field, then runs a dry build (section 6.1)
and shows dangling references, conditions outside an enum, a jurisdiction
that does not match the concept's, and citations that no longer resolve,
before writing. Save writes the whole file through `save_curation`, so the
file keeps the key order and formatting the pipeline expects.

### 4.6 Review queue

Reads: `curation.yaml`; the cited records for the excerpt context;
`build-report.json` for relocation warnings; `checks.yaml` to know which
facts the stored checks expect.

Writes: `curation.yaml`, `provenance` only (section 6.1).

The queue lists facts, one card at a time with a list at the side, ordered
by priority:

1. Facts expected by a stored check (the standing cases) that are not
   `human-reviewed`.
2. Facts whose last citation outcome carried a warning, or whose review
   status is `human-reviewed` but whose citation anchor no longer matches
   the current record (the relocation cleared it, section 4.3).
3. Every other fact that is not `human-reviewed`, by concept order.
4. Model candidates (`provenance.kind: model-candidate`), by concept.

Filters: `review_status`, `provenance.kind`, jurisdiction, language,
concept, document, "cited by a check", basis (the kind of the fact's
strongest basis as the build states it, or "none" for a fact whose cited
page has no institution).

**Card.** Left: the statement, the fact's jurisdiction and condition, the
basis the build states on the fact (the strongest of its citations), the
provenance with `author` and `source`, and the concept's label. Right: the
excerpt highlighted inside its page context (the blocks under the same
heading, from the record), the `heading_path`, the basis of the citation
with the rule that produced it (the citation's own, a page rule, the
default) and the publisher's name and level, the source URL as a link to
the live page for a comparison, `accessed_on`, the language, and the anchor
state. The basis is computed with the build's own functions over the pack's
text dataset, so the card shows what the next build serves. For a relocated citation, the warning and the old excerpt beside the
new one.

**Actions** (keyboard: `y` confirm, `e` edit, `n` reject, `f` flag, `j`/`k`
next and previous, `x` select the card for a bulk action):

| Action | Effect on the fact |
| --- | --- |
| Confirm | `review_status: human-reviewed`, `reviewed_on: today`, `reviewed_by: <reviewer>` (an additive field on `Provenance`, section 6.3); the anchor is refreshed from the current record so the confirmation is bound to the exact bytes reviewed |
| Edit | Opens the fact form (screen 5) in place; saving an edited statement keeps the status unreviewed, so an edit is followed by a confirm |
| Reject | Removes the fact from the concept (and the concept, when it was the last fact), with a required note stored under `.local/<pack>/console/rejected.jsonl` with the fact as it was, the reviewer and the reason; the curation file records nothing about rejected facts, as today |
| Flag | Adds a note to `provenance.notes` prefixed with `flag:` and the reviewer, and leaves the status unchanged; flagged facts sort to the top for the lead |
| Accept candidate | For a `model-candidate`: sets `kind: curated-statement` and `review_status: human-reviewed` with the reviewer, after the statement has been shown editable; the model's authorship stays in `author` and `source` |
| Bulk | Confirm, flag or reject several facts at once: the facts ticked in the side list (select all on the page, `x` on a card) or every card matching the current filter on every page. Flag and reject need a comment, confirm takes an optional one as a `review:` note. One write, one dry build and one audit entry listing every fact, so it is all or nothing; a reject asks first. Each fact is changed exactly as its single action would change it, anchors refreshed; a fact confirmed with others also gets `review: confirmed in a bulk review of N facts`, because a group confirmation is weaker evidence of reading than a card-by-card one and the lead should be able to tell them apart. Not offered in sample mode |

A progress bar per pack shows reviewed facts of all facts, and a second one
for the facts the stored checks expect. Both numbers appear on the pack card.

For a pack whose facts are `automatically-derived-unreviewed` in the tens of
thousands (KB2 source sections), the queue offers a **sample** mode: a fixed
random sample per document (seeded by `content_sha256`, so it is stable),
reviewed as spot checks whose result is recorded in `checks.yaml` as a
sample verdict per document, not as per-fact review statuses. The
limitations text of the release says so.

### 4.7 Release

Reads: `curation.yaml` (manifest fields); `release.json` and the previous
release (kept as `.local/<pack>/console/releases/<release_id>.json` when a
build finishes, since Git holds only the current one); `build-report.json`;
the validator's output; the runtime service for the coverage preview; the
Git working tree (through the `git` executable, read-only).

Writes: `curation.yaml` for the manifest fields (through the pack form of
screen 5); starts the build, validate-release and health stages.

Layout, top to bottom:

**Manifest text.** The pack form fields of screen 5 that end up in the
manifest, edited here with a live character count for `scope_statement`
and `out_of_scope_response`, because both are returned on every coverage
root call.

**Build.** `release_id` (default from `next_release_id`), the
`update_curation` flag, a build button that runs the build, validate-release
and health stages as one job. The result shows the validator's issue list
(empty on success), the health counts, and the `citation_outcomes` summary
with a link to the relocation review.

**Diff against the previous release.** Concepts added and removed; facts
added, removed, reworded (statement changed) and re-cited (evidence
offsets, hashes or document changed); documents added and removed; manifest
fields changed; review-status counts before and after. A reworded fact shows
both statements. The diff is computed from the two release files, not from
the curation file, so it shows what callers will see change.

**Caller preview.** The coverage root as `get_coverage` returns it, with its
size in bytes against the 2 KB target of the tool contracts and a warning
above it; each topic page with its size; the generated `COVERAGE.md` and
`LIMITATIONS.md` text as the generator would write them.

**Working tree.** `git status` and `git diff --stat` for `releases/<pack>/`,
and the subject-line convention from `AGENTS.md` shown as a hint. There is
no commit button. The lead commits from Git.

### 4.8 Tool sandbox

Reads: the release chosen at the top (the pack's `release.json` or any
release file under `.local/<pack>/console/releases/`); `checks.yaml`.

Writes: `checks.yaml` (section 6.4).

Four panels, one per tool, each with a request form generated from the
contract models and a response pane that shows the result as the caller
gets it (the JSON) and as a rendered view:

| Tool | Form | Rendered view |
| --- | --- | --- |
| `get_coverage` | `parent_id` (select from the root's topics) | Scope statement, out-of-scope list, jurisdictions, languages, freshness, topic table; on a topic, the concept table with aliases and required context |
| `search` | `query`, `limit` | Ranked hits with `score` and `matched_on`, each linking to the concept in screen 5 |
| `resolve` | `concept_ids` (multi-select with search), jurisdiction (country, canton, municipality), `as_of` date, `context` (one input per context field of the chosen concepts, with enums) | Per concept: the status badge, the named gap for `NEEDS_CONTEXT` and `OUT_OF_COVERAGE` with its published values, the facts with statement, jurisdiction, condition and validity, each with its citations; a `STALE` result shows the freshness dates |
| `get_evidence` | `evidence_ids` (from the last resolve result) | The excerpts with URL, publisher, access date, offsets and hashes |

Every call is logged like a server call (tool, status, bytes, latency) into
the sandbox's own log so the expert sees response sizes.

**Stored checks.** A check is a name, a tool, a request and expectations:
for `resolve`, the expected status per concept and the fact IDs that must be
present; for `search`, concept IDs that must appear in the top N. The two
standing cases (the Czech-citizen registration and the third-country
national's work permit) are the first two checks, seeded from the assertions of
`scripts/test/mcp/check_server.py`. A "run all checks" button runs them
against the chosen release and shows pass or fail per expectation; the
result and time are written into `checks.yaml` and shown on the pack card.
A "save as check" button on any resolve or search result creates a new
check from it. `check_server.py` can read the same file later so the
console and the self-check share one list; that change is outside this
design.

### 4.9 Operations

Reads: the server's log lines (`tool=... status=... bytes=... ms=... release=...`)
from a log file the operator points the console at, or from the sandbox's
own log; `.local/<pack>/console/calls.jsonl` as the parsed aggregate.

Writes: `calls.jsonl` (append only, parsed from the log).

Over stdio the server runs inside the client and its stderr is not
collected. The Streamable HTTP endpoint writes the same lines to stderr, which
a container host keeps (`docker logs` locally), so this screen stays empty
until the operator saves that log to a file and points the console at it.
The screen is specified now so the log line format is not changed without it
in mind.

| Panel | Content |
| --- | --- |
| Health | The `--health` output of the served release: counts, jurisdictions, languages, freshness, review statuses; days until `stale_from`; the release ID the log lines report against the release ID on disk (a mismatch means a running server serves an old release) |
| Traffic | Calls per tool per hour or day; status mix per tool; bytes and latency at the median, 95th and 99th percentile per tool; tool-error count by error code |
| Gap feed | The most frequent `OUT_OF_COVERAGE` gaps by jurisdiction and by concept; the most frequent `NEEDS_CONTEXT` fields; search queries with no hit and queries whose best hit was not resolved afterwards; each with a count and a "create concept" or "add alias" link into screen 5 |
| Freshness | Cited documents by `accessed_on`; a refresh button that starts the acquire stage with the download flag (same confirmation as screen 3) followed by extract, validate-text and build with `update_curation`, and then opens the relocation review |

The gap feed requires the server to log the gap fields, which today's log
line does not carry. The additive change is one more key on the line,
`gap=<field or jurisdiction>` when the status is not `SUPPORTED`, and
`hits=<n>` on search calls. Request payloads are never logged; a query
string is logged only when it produced no hit, truncated to 80 characters.

## 5. Cross-cutting behaviour

### 5.1 Navigation

Every identifier is a link: a `document_id` opens the reading view, a
`fact_id` opens the fact form, a `concept_id` opens the concept form, a
`source_id` opens the source form, a block ID opens the reading view at the
block. The pack is part of every URL (`/packs/<pack>/...`), so a page can be
bookmarked and shared between the expert and the lead.

### 5.2 Concurrency

One person edits a pack at a time. A write carries the SHA-256 of the file
as the form loaded it; a write against a changed file is refused with a
message naming who wrote last (from the audit log of section 6.5) and
offers to reload. A job that writes (`build` with `update_curation`, the
relocation review's picks) takes the same lock as a form save; a form save
is refused while such a job runs.

### 5.3 Language

The interface is English, as the statements and labels are. Content is
shown in the language of the record with the language code beside it.
Right-to-left and CJK content render as the browser renders them; no
transliteration.

### 5.4 Accessibility

Keyboard operation for the review queue and the reading view selection;
status conveyed by text and icon, never by colour alone; tables with header
cells; no interaction that needs a hover.

## 6. Writes

### 6.1 Curation

All curation writes go through one function,
`write_curation(pack, mutate, actor, reason)`:

1. Load `curation.yaml` through `load_curation` (the file must validate; a
   file that does not is shown as an error and the console is read-only for
   that pack until it is fixed by hand).
2. Apply `mutate` to the model in memory.
3. Validate the model again.
4. Dry build: `build_release` against the text dataset into memory, with the
   current release ID; a `BuildError` or a dropped fact is reported and the
   write is refused, except for the relocation review's explicit drop.
5. Write through `save_curation`, then append the audit record (section
   6.5).

The dry build takes about a second for the MVP pack. For a pack where it
takes longer than five seconds the console runs the dry build on the facts
touched by the mutation only and says so in the save confirmation.

### 6.2 Catalogue

`write_catalogue(pack, mutate, actor, reason)` mirrors 6.1 with the
catalogue model of the ingestion package and the planning check of the
pipeline (a changed catalogue means the next run needs a new run directory;
the write shows this warning). No dry download.

### 6.3 Provenance fields

The review actions need one field the release format does not have:
`reviewed_by` on `Provenance`, optional, a person's name. It is additive to
`swiss-tip-release/v1` and `swiss-tip-curation/v1` and validated as
non-empty when `review_status` is `human-reviewed`. The release format
document gets one line in its provenance table.

### 6.4 Checks file

`releases/<pack>/checks.yaml`, schema `swiss-tip-checks/v1`:

```yaml
schema_version: swiss-tip-checks/v1
pack: mvp-zurich
checks:
  - check_id: czech-registration
    title: Czech citizen starting work in Zurich, registration deadline
    tool: resolve
    request: {concept_ids: [...], jurisdiction: {canton_code: CH-ZH}, as_of: "2026-09-12", context: {population: eu_efta}}
    expect:
      status: {zh-eu-registration: SUPPORTED, ...}
      fact_ids: [zh-eu-registration-1, ...]
  - check_id: search-registration-zh
    tool: search
    request: {query: register stay municipal authority Zurich, limit: 5}
    expect: {concept_ids_in_top: [zh-eu-registration]}
last_run:
  release_id: <release-id>
  at: "2026-09-13T10:00:00+00:00"
  results: {czech-registration: pass, search-registration-zh: pass}
samples: {}     # document_id -> {reviewed_by, on, blocks_checked, verdict}
```

The `request` objects are validated against the contract models, so a check
cannot drift from the contract.

### 6.5 Audit log

`.local/<pack>/console/audit.jsonl`, one line per write: time, actor, file,
SHA-256 before and after, the reason string of the action ("confirm fact
zh-eu-registration-1", "promote page ... to catalogue"), and the IDs
touched. The actor is the name given at start-up (`--actor`) or, when
hosted, the authenticated user. The log is outside Git; the Git history
holds the file-level record.

## 7. Jobs

Pipeline stages and the check run are jobs. A job is a thread in the
console process running `Pipeline(...).run(...)` with `log` redirected to
`.local/<pack>/console/jobs/<job_id>.log`; the page polls a status endpoint
and tails the log. One job per pack at a time. A job survives a page
reload but not a console restart; a restart marks running jobs as
`interrupted` and the pipeline report says what ran. The download flag
is the only job that touches the network and requires the confirmation of
section 4.3 on every start; there is no "always allow".

## 8. Data access

`swisstip.admin_console.data` holds one class per file family, each with a
`load()` that reads the file and remembers its SHA-256, and a `current()`
that re-reads only when the file's size or mtime changed and the hash
differs. Record files under `documents/` are read on demand and not
retained. The text index of the full pack (12,117 entries) is held in memory
once loaded, about 20 MB; lists over it are paged server-side in steps of
100.

Cited excerpts shown in lists (workbench, review queue) come from the
citation's `anchor.excerpt`, so a list of every fact of a pack opens no
record. The record is read when a card opens, to render the context and
check the anchor against the current block hashes.

## 9. Application layout, technology, security

```text
apps/admin-console/
  pyproject.toml                  swisstip-admin-console; script swisstip-admin
  README.md
  src/swisstip/admin_console/
    app.py                        FastAPI application factory, mode flag, hosted auth, CLI
    data.py                       file readers of section 8
    writes.py                     write_curation, write_catalogue, write_checks, audit log
    jobs.py                       job runner and log tailing
    checks.py                     checks file model, runner
    calls.py                      log-line parser and aggregate
    screens/                      one module per screen: a read router and a write router
    views/                        Jinja templates, one folder per screen
    static/                       one stylesheet, htmx, no build step
  tests/                          unittest on a synthetic pack; no network
```

As built, the routes of each screen live in `screens/<screen>.py` rather than
in `app.py`, so that a screen's routes sit beside its `views/<screen>/`
templates; `app.py` is the factory that registers the read routers always and
the write routers only in edit mode.

Technology: FastAPI with Jinja templates and HTMX for the partial updates
(polling, form validation, the review queue), served by `uvicorn`, `PyYAML`
for the checks file. No JavaScript build step, no Node toolchain, no
JavaScript framework; the only scripts are HTMX and under a hundred lines
for the block-range selection and the keyboard shortcuts. The reasons: one
Python app in the same test runner as the rest, no second package manager,
and every page is a server render that the tests can assert on.

Dependencies are pinned like the other packages. The console installs with
`pip install -e apps/admin-console` after the build-side packages.

Security: bound to `127.0.0.1:8765` by default, no authentication, one actor
name from the command line. For a hosted instance (the evaluator's read-only
view, or a shared expert workstation): `--host`, HTTP basic authentication
from a password file outside Git (`--auth-file`), the actor taken from the
authenticated user, and write endpoints only for users listed as editors.
No secret is ever rendered; the provider key of the assistant draft is read
from the environment and never shown. The `git` calls are read-only
(`status`, `diff`, `log`).

## 10. Tests

All on a synthetic pack under `tests/fixtures/` with three documents, two
concepts, four facts and one stored check, built by the existing pipeline in
`setUp`; no network, no browser. `unittest` with FastAPI's test client.

| Area | Tests |
| --- | --- |
| Data | Index reload on file change; record read on demand; paging |
| Sources | Source form validation (allowlist, jurisdiction, language); promote a discovered page; catalogue write refused when the model rejects it; changed-catalogue warning |
| Reading view | Blocks, marks and tables rendered; furniture toggle; cited blocks marked; range selection to excerpt equals the build's cut |
| Workbench | Each form saves through the models; a condition outside an enum is refused; a fact without evidence is refused; a save that would drop a fact is refused; publisher missing is flagged |
| Review | Confirm sets status, date and reviewer and refreshes the anchor; edit clears status; reject moves the fact to the rejected log; accept candidate; queue order; sample mode is stable |
| Relocation | Each build outcome renders; picking a candidate rewrites the citation; drop with a note |
| Release | Diff between two releases; coverage root size warning; working-tree panel with a fake `git` |
| Sandbox | Each tool form round-trips through the contract models; checks run and record results; save-as-check |
| Jobs | One job per pack; log tail; interrupted on restart; download needs confirmation |
| Modes | Read-only registers no write or job route; hosted mode requires authentication for writes |
| Calls | Log-line parser, percentiles, gap feed |

## 11. Build order

Steps 1 to 6 are built. Step 7 renders the sandbox's own call log, and a
saved server log when the console is pointed at one (section 4.9); the gap
feed waits for the additive log keys.

| Step | Delivers | Closes |
| --- | --- | --- |
| 1 | Skeleton, packs overview, data layer, read-only reading view | A shared way to read the text dataset |
| 2 | Review queue with confirm, edit, flag, reject; `reviewed_by` field | The review of every KB1 fact |
| 3 | Curation workbench, range selection to citation | Descriptions and aliases for concepts without them; new facts without hand-editing YAML |
| 4 | Tool sandbox with stored checks | The expert verifies a change before the harness runs |
| 5 | Sources table, promote, coverage matrix | KB2 progress; soft error pages; pages missing from a catalogue |
| 6 | Pipeline runs with the relocation review, release diff and caller preview | A refresh becomes a reviewed event rather than a report to read |
| 7 | Operations, once the HTTP transport writes a log | The gap feed as the curation backlog |

Steps 1 to 4 are the expert's tool; steps 5 to 7 are the KB2 and operations
tooling.

## 12. Out of scope

- Any change to the MCP server, the release format beyond `reviewed_by`, or
  the tool contracts.
- Committing to Git from the console.
- Multi-user editing of one pack at the same time.
- Machine translation shown as content; OCR; crawling beyond the acquire
  stage.
- A public-facing browse site for the knowledge base; the read-only mode is
  for evaluators, not for the public.
- The concept-extraction pipeline itself; the console only consumes its
  candidates once `TODO.md`'s port exists.

## 13. Open questions

- ~~Does the reviewer's name belong in the committed curation file
  (`reviewed_by`), or only in the audit log with a reviewer ID in the file?~~
  Settled as this design proposed: `reviewed_by` is an optional field of
  `Provenance`, validated as non-empty when `review_status` is
  `human-reviewed`, in both `swiss-tip-release/v1` and
  `swiss-tip-curation/v1`. The audit log keeps the write record as well.
- Should `check_server.py` read `checks.yaml` so the console and the
  self-check share one list, or stay independent with its hardcoded
  checks?
- For KB2 source sections, is a per-document sample verdict enough for the
  limitations text, or does the release need a per-fact `sampled` mark?
- Does the hosted read-only instance for Swisscom need to exist for the
  evaluation window, or is the tool sandbox covered by the MCP client they
  bring?
