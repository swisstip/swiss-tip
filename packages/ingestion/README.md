# swisstip-ingestion

**Last update:** 20 September 2026

Build-side source acquisition for Swiss TIP: download the exact pages of a
source catalogue, resolve Fedlex legal texts to dated documents, and report
which sources are still missing and why. The serving side never imports this
package.

The code is a simplified copy of the SwissTIP ingestion package. The bounded
crawler (`crawler.py`) and the run layout are unchanged, so a run directory
produced here is readable by tooling written for the original runs. Left out:
the depth-crawling scan CLI, third-party plugin discovery through entry points,
and the normalization binding for extraction.

## Install and test

```shell
./.venv/Scripts/python.exe -m pip install -e packages/ingestion
./.venv/Scripts/python.exe -m unittest discover -s packages/ingestion/tests
```

Standard library only. Tests make no network requests.

## Download a pack's sources

Plan first (no requests), then download into the same directory:

```shell
./.venv/Scripts/python.exe -m swisstip.ingestion.download_cli --catalogue releases/<pack>/sources.json --output releases/<pack>
./.venv/Scripts/python.exe -m swisstip.ingestion.download_cli --catalogue releases/<pack>/sources.json --output releases/<pack> --download
```

For the full pack, `--markdown releases/<pack>/sources.md` adds every
explicit link of the human-readable catalogue as a target; the 59 registry
seeds are a subset of those 115 links.

| Option | Effect |
| --- | --- |
| `--set NAME`, `--source ID` | Select a named scan set or explicit source IDs from the registry; default: every source |
| `--download` | Make requests. Without it only `plan.json`, the copied catalogue and an all-pending summary are written |
| `--retry-failed` | Make a new attempt for every target without an intact saved response, and for every saved response that is an error page served with HTTP 200 |
| `--include-not-ready` | Also fetch registry seeds whose `scan_status` is not `ready` (they are planned but skipped by default) |
| `--workers N` | Host groups downloaded in parallel, 1 to 4; requests to one host stay sequential with the robots delay |
| `--transport curl` | Fetch through the native curl binary (system certificate store); the crawler still checks every redirect |
| `--no-source-plugins` | Only snapshot the listed URLs; skip Fedlex resolution |

A run directory is bound to the catalogue bytes it was planned from; a changed
catalogue needs a new directory. Saved responses are reused after a hash check.
Every target is fetched at depth zero as
`Mozilla/5.0 (compatible; SwissTIPDemoCrawler/0.1)`, with robots.txt
groups matched by the `SwissTIPDemoCrawler` token, a 25 MB
response cap, redirects limited to the plan's host allowlist and the public
network only. The exit code is nonzero while any target or plugin document is
unsaved.

## Run layout

```text
plan.json                    targets (URL, sha256 ID, references, registry entries), catalogue hash
catalogue.json, catalogue.md the catalogue files as they were read
plugin-plan.json             which targets a source plugin will resolve
pages/<url-sha256>/attempt-NNN/response.html|pdf|txt   raw response bytes
pages/<url-sha256>/attempt-NNN/manifest.json           outcome of that attempt
pages/<url-sha256>/latest.json                         outcome of the last attempt
fedlex-documents/            dated HTML and PDF per Fedlex ELI page, with metadata responses
summary.json, README.md      current outcome per target
```

A saved HTML response can be a navigation page or a JavaScript application
shell; `review_flags` in the manifest mark the obvious cases. Fedlex ELI pages
are such shells, which is why the bundled `fedlex` plugin resolves each of them
through the public JOLux SPARQL endpoint to the consolidated version in force
on the plan date and saves its HTML and PDF under `fedlex-documents/`.

## Gap report

```shell
./.venv/Scripts/python.exe -m swisstip.ingestion.gap_report --run releases/<pack>
```

Writes `gap-report.json` and `gap-report.md` into the run. Every target gets a
gap class and a verdict on whether a plain retry can close it: transient
network, rate limit, server error and robots fetch failures are retriable;
access denial, DNS failure, dead links and out-of-scope redirects need a
catalogue change or an operator decision; a source marked
`needs_access_review` is never retriable automatically. A saved response
whose title or level-1 heading is an error text ("Error Page (404)", "Seite
nicht gefunden") is the retriable gap `soft-error-page`: `www.ch.ch`, for
example, sends its error page with HTTP 200 to any User-Agent that does not
begin like a browser's. Saved shells that the
plugin resolved and browser-rendered captures are listed as notes, not gaps.

### Human approval of the current scope

`review-decisions.json` in a run records human-approved acquisition and
extraction exceptions. Each decision names its reviewer, date and reason,
uses `review_status: human-reviewed` and `decision: approved`, and identifies
the observed URL or text record by a fingerprint. The file is bound to the
run's catalogue hash. It accepts the recorded scope; it does not review a
fact, create source evidence or change extraction eligibility.

Rebuilt reports apply these decisions automatically. Approved items have
`disposition: approved`, are absent from outstanding counts and retry lists,
and appear as approved in the admin console. The original download status,
classification and snapshot metadata remain in the report's target rows.
A changed observation requires its own decision. The decisions file can be
removed or edited to reopen an item. `review_decisions.py` provides the
fingerprint and loading helpers; its tests use temporary local runs.

## Place register

```shell
./.venv/Scripts/python.exe -m swisstip.ingestion.places --output config/places/ch-register.json
```

`swisstip-places` reads the register of municipalities of the Federal
Statistical Office (Amtliches Gemeindeverzeichnis) from the snapshot endpoint
of its application of the Swiss municipalities, which returns every canton,
district and municipality valid on a day as CSV, and writes the place file
the release build embeds: the 26 cantons and the municipalities with the
codes the releases use (`CH-ZH`, `CH-ZH-261`: the canton's abbreviation and
the BFS number), the official names, the URL, the access date and the hash of
the response. Districts are dropped. A response that is not the register (no
such columns, not 26 cantons, a municipality on no canton, a code twice) is
refused and nothing is written. `--snapshot FILE --accessed-on DATE` reads a
saved response instead of fetching; the tests use a synthetic snapshot.
Municipalities merge, mostly on 1 January: fetch the register again then and
rebuild the releases. What the build does with the file, and the hand-written
aliases beside it, is in
[docs/architecture/release-format.md](../../docs/architecture/release-format.md).

## Discovered pages

The downloader fetches catalogue URLs only. Pages that a link-following crawl
found can still join a run as *discovered targets*: they share the `targets`
list of `plan.json` and the page layout, and carry an `attribution`:

| Kind | Meaning |
| --- | --- |
| `in-scope` | The URL lies inside the host and path allowlist of a catalogue source; `source_ids` names the sources |
| `language-variant` | Reached through a published language link (hreflang, language menu) from an in-scope or catalogue page, transitively; inherits the sources of the linking page |
| `out-of-scope` | Everything else the crawl saved; only with scope `all` |

`summary.json` and the gap report count catalogue and discovered targets
separately; the Markdown reports summarise discovered pages by kind and host
instead of listing every page. The attribution logic is in `discovery.py`
and `catalog.attribute_url`.

## Imported runs

The pack runs (`releases/mvp-zurich/`, committed, and `.local/swiss-residence/`)
were not downloaded by this package but copied
from the runs of the original SwissTIP tooling with a one-time script, kept
outside Git under `.local/scripts/`. `import-ledger.json` in each run lists
every copied file with its source path and hash. Discovered targets were
added to `plan.json` through `discovery.select_discovered`.

An imported plugin archive (a `*-documents/plan.json` without a plugin
identity) is kept as it is while the catalogue names only sources it already
holds. When the catalogue gains sources the same plugin resolves, the runner
adopts the archive in place: the archived documents become the recorded
resolutions of their source pages and are never fetched again, the plan
takes the plugin identity and the current catalogue hash, `adopted_archive`
keeps the hash the archive was imported under, and only the new sources are
resolved. The draft-2 run of `mvp-zurich` (15 September 2026) was seeded
this way from the draft-1 run: `import-ledger.json` lists every copied page,
Fedlex document and text record with its hash.
