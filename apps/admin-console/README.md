# Swiss TIP admin console

**Last update:** 20 September 2026

A local web interface over the files of a pack: the knowledge expert reads
pages, selects excerpts, writes concepts and facts and confirms every served
fact; the lead runs the pipeline, judges relocated citations, cuts a release
and reads the working-tree diff before committing. The design is
[docs/architecture/admin-console.md](../../docs/architecture/admin-console.md);
this README says how to run what is implemented.

The console introduces no new source of truth. Every screen
reads a file the pipeline already writes, and the console writes only three:
`releases/<pack>/curation.yaml`, `releases/<pack>/sources.json` and
`releases/<pack>/checks.yaml`. It never commits; Git remains the audit trail.
The MCP server never imports this package, so the serving guarantees of the
functional specification are untouched.

## Install and run

```shell
./.venv/Scripts/python.exe -m pip install -e apps/admin-console
./.venv/Scripts/python.exe -m unittest discover -s apps/admin-console/tests
./.venv/Scripts/python.exe -m swisstip.admin_console.app --packs-dir ../swiss-tip-mvp --actor "Anna Meier"
```

The packs live in their own repository,
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp): `--packs-dir`
(or the environment variable `SWISSTIP_PACKS`) names the folder that holds
`releases/<pack>` and `.local/<pack>`. The release screen runs `git status`
and `git diff` in that repository, never in this checkout.

On macOS or Linux replace `.venv/Scripts/` with `.venv/bin/`. The console
binds to `127.0.0.1:8765`; open <http://127.0.0.1:8765>. The build-side
packages must be installed first (`packages/core`, `packages/ingestion`,
`packages/extraction`, `packages/build`, `packages/runtime`,
`apps/knowledge-builder`).

| Option | Effect |
| --- | --- |
| `--actor NAME` | The name recorded on every write and written into `reviewed_by` when a fact is confirmed |
| `--read-only` | Registers no write and no job route at all; the evaluator view |
| `--packs-dir DIR` | The packs repository; required unless `SWISSTIP_PACKS` names it |
| `--run-dir PACK=DIR` | Run directory of a pack; default `.local/<pack>` (`releases/<pack>` only when it holds a `plan.json`, which no pack does). Job logs, audit and call logs are under `.local/<pack>/console/` |
| `--server-log FILE` | A served release's log file for the operations screen |
| `--host`, `--port` | Bind elsewhere; a non-local host without `--auth-file` prints a warning |
| `--auth-file FILE` | HTTP basic users for a hosted instance |
| `--hash-password PW` | Print the SHA-256 for an `--auth-file` line and exit |

## Screens

| Path | Screen | Writes |
| --- | --- | --- |
| `/` | Packs overview: sources, text, curation and release per pack | a new pack's `sources.json` |
| `/packs/<pack>/workflow` | Governed A1-A6 state, live progress, proposals, verified events and human checkpoints | workflow decisions and checkpoint records under `.local/` |
| `/packs/<pack>/sources` | Catalogue table with acquisition review and record columns, discovered pages, coverage matrix | `sources.json` |
| `/packs/<pack>/runs` | Stage strip, job log, run history, run form, relocation review | starts jobs, `curation.yaml` |
| `/packs/<pack>/documents` | Document list, reading view with block selection, diff of a superseded record | nothing |
| `/packs/<pack>/workbench` | Pack, topic, concept and fact forms | `curation.yaml` |
| `/packs/<pack>/review` | Review queue and sample mode | `curation.yaml`, `checks.yaml` |
| `/packs/<pack>/release` | Manifest text, build, release diff, caller preview, working tree | starts the build job |
| `/packs/<pack>/sandbox` | The four tools over a chosen release, stored checks | `checks.yaml` |
| `/packs/<pack>/operations` | Health, traffic, gap feed, freshness | `calls.jsonl`, starts a refresh |
| `/packs/<pack>/audit` | Every write with its actor, hashes and reason | nothing |

The sources overview and document lists distinguish outstanding work from
human-reviewed scope approvals in the run's `review-decisions.json` and
acquisition report. Approved items appear as accepted scope, while their
recorded acquisition and extraction statuses remain available. Scope approval
does not change the review status of a fact or the coverage matrix's reviewed
stage.

The workflow screen also discovers a pack that exists only under
`.local/<pack>/autopilot/`, before an approved catalogue has been promoted.
It polls the verified state every two seconds, shows only persisted progress
metrics, and records approvals with the console actor. In local mode that actor
is asserted with `--actor`; hosted mode uses the authenticated editor and
requires same-origin workflow form submissions. Claude's coordinator CLI has
no approval or attestation operation.

Every identifier is a link: a `document_id` opens the reading view, a
`fact_id` the fact form, a `concept_id` the concept form, a `source_id` the
source form.

## What a write goes through

`curation.yaml` is written through the curation models and `save_curation`;
`sources.json` through the ingestion package's `save_source_catalog`. A
curation write loads the file, applies the change, validates the model, runs
the release build into memory against the text dataset, and only then saves
and appends to `.local/<pack>/console/audit.jsonl`. A write that would drop a
fact is refused, except where the relocation review or the review queue drops
one on purpose with a note. A form carries the SHA-256 of the file it loaded;
a write against a changed file is refused and names who wrote last.

Only the confirm action of the review queue, taken by a named person in edit
mode, sets `review_status: human-reviewed`, card by card or in bulk; it writes
`reviewed_on` and `reviewed_by` and refreshes the citation anchor from the
current record, so a confirmation is bound to the exact bytes that were read.
Editing a confirmed statement clears the status again.

The bulk action confirms, flags or rejects several facts in one write: the
facts ticked in the queue list (select all on the page, or `x` on a card), or
every card matching the current filter across pages. Flag and reject need a
comment; a confirmation may carry one. It is all or nothing, with one dry build
and one audit entry naming every fact, and a fact confirmed together with
others gets a `review: confirmed in a bulk review of N facts` note, so a group
confirmation stays distinguishable from a card-by-card one.

## Jobs and the network

A pipeline stage or a build runs as a thread whose log is written to
`.local/<pack>/console/jobs/<job_id>.log`; the page polls and tails it. One
job per pack at a time, and a job that writes takes the same lock as a form
save. A restart marks jobs that were still running as `interrupted`.

The acquire stage with the download flag is the only action that makes a
network request. It is confirmed on every start behind a panel that lists the
hosts, the request budget and the crawl-profile etiquette, and it requires
the pack name to be typed. There is no "always allow".

## Hosted mode

```shell
./.venv/Scripts/python.exe -m swisstip.admin_console.app --hash-password "the password"
printf 'anna:<sha256>:editor\nevan:<sha256>:reader\n' > .local/console-users.txt
./.venv/Scripts/python.exe -m swisstip.admin_console.app --host 0.0.0.0 --auth-file .local/console-users.txt
```

Every page then needs HTTP basic credentials, the actor of a write is the
authenticated user, and only users listed as `editor` may write. Keep the
password file outside Git (`.local/` is ignored). No secret is rendered on
any page.

## Technology

FastAPI with Jinja templates and HTMX for the partial updates, served by
`uvicorn`, PyYAML for the checks file. No JavaScript build step and no Node
toolchain: `static/htmx.min.js` is vendored and `static/console.js` is about
150 lines for the block-range selection, the review-queue keyboard
(`y` confirm, `e` edit, `n` reject, `f` flag, `j`/`k` next and previous, `x`
select for a bulk action) and the bulk selection, which it keeps in
`sessionStorage` per pack and filter across the page load of every card.
Every page is a server render, which is what the tests assert on.

```text
src/swisstip/admin_console/
  app.py        application factory, mode flag, hosted authentication, CLI
  data.py       the file readers of section 8, cached by hash
  writes.py     write_curation, write_catalogue, write_checks, the audit log
  jobs.py       the job runner, log tailing, run and release archives
  checks.py     the checks file model, the seeded standing cases, the runner
  calls.py      the server log-line parser and the traffic aggregate
  screens/      one module per screen, each with a read and a write router
  views/        Jinja templates, one folder per screen
  static/       one stylesheet, htmx, console.js
```

The design document lists the routes under `app.py`; the implementation keeps
the named modules and puts the routes of each screen in `screens/<screen>.py`
so that a screen's routes and its templates sit side by side.

## Tests

`./.venv/Scripts/python.exe -m unittest discover -s apps/admin-console/tests`
runs the tests on a synthetic pack of three documents, two concepts, four
facts and one stored check, built by the real pipeline in `setUpClass`. No
network, no browser. The areas are those of section 10 of the design: data,
sources, reading view, workbench, review, relocation, release, sandbox, jobs,
modes and calls.

## Not implemented

- **Assistant draft.** The draft button of section 4.5 needs the provider
  layer of the concept-extraction port in [TODO.md](../../TODO.md). Until it
  exists the button is absent, as the design asks: the console never writes a
  statement on its own.
- **Live server traffic.** Over stdio the server's log line goes to stderr
  inside the client and is not collected. The Streamable HTTP endpoint writes
  the same line to stderr, which a container host keeps, so the operations
  screen shows only the calls made in the sandbox until that log is saved to
  a file and passed with `--server-log`. The parser already accepts the two additive keys the gap feed
  needs (`gap=` and `hits=`), so the format can grow without this package
  changing.
