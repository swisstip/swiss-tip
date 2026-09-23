# Dataset connectors

**Last update:** 23 September 2026

**Status:** steps 1 to 4 of section 9 implemented and tested: the
dataset bundle and its validator (`swisstip.core.datasets`), the
server-connector contract with the reference semantics of the calendar
lookup (`swisstip.core.connector`), the caller-facing additions in
`swisstip.core.contracts`, carried by the schema bundle, the build command
`swisstip-build-dataset` with the `csv-columns` importer
(`swisstip.build.datasets`), the connector process
`apps/calendar-connector`, the conformance test
`scripts/test/connector/check_connector.py`, and the server side:
registration (`swisstip.runtime.connectors`), the offer on `resolve`, the
`lookup` tool and the health entries (`swisstip-server --connector URL`).
Of step 5 the code side exists: the generic connector image
(`docker/calendar-connector`), the compose profile `calendar`, the PyPI
distribution `swisstip-calendar-connector`, the workflow steps that build,
test and publish both, and the `lookup` step kind of the acceptance suite.
In the packs repository the five curation files and their bundles (built
on 23 September 2026), the pack's calendar Dockerfile and the drafted
acceptance cases exist; the cases are not in the suite yet, the images are
not built, nothing is published, and the harness case is open. The
first connector type is the collection calendar, the first datasets are the
five waste-collection calendars of the City of Zurich on
data.stadt-zuerich.ch, and everything else in this document is either a
constraint the first connector must respect or an explicitly deferred
extension (section 10).<br>
**Schema versions proposed:** `swiss-tip-dataset/v1` (the dataset release),
`swiss-tip-connector/v1` (the manifest and the lookup exchange between the
server and a connector). The caller-facing change is additive on
`swiss-tip/v4` (section 6).<br>
**Relation to the other documents:** the knowledge release of
[release-format.md](release-format.md) stays untouched; the tools of
[tool-contracts.md](tool-contracts.md) gain one tool and one optional field;
the cases of [acceptance-gate.md](acceptance-gate.md) gain one step kind;
the two-container setup of [docker/README.md](../../docker/README.md) gains
one optional container.

## 1. Purpose

The knowledge release answers the rule: what organic waste is, how it is
collected in the City of Zurich, what goes into the container. It cannot
answer the instance: on which day the container in postal code 8001 is
emptied next. The instance is not a fact with an excerpt; it is one row of
a table the city publishes as open data once a year.

A dataset connector serves such tables next to the release, under the same
rules the release follows:

- **Facts first, instance second.** A dataset is served only behind a
  concept the release publishes. The caller reaches it through `resolve`,
  which returns the reviewed facts and, next to them, the offer of a
  lookup. A dataset with no concept behind it is not served.
- **Same provenance.** Every lookup result names the publisher, the source
  URL, the licence, the download date and the hash of the file it was
  answered from, the way a fact names its excerpt.
- **Same gaps.** A postal code the dataset does not hold, a date outside
  the published year and a connector that is not running are named gaps,
  not empty lists.
- **Nothing live.** The connector answers from files it carries. No query
  passes to a third party at answer time; the out-of-scope statement of the
  specification (section 11) holds unchanged. Refreshing a dataset is a
  build, like a release.
- **Nothing in the server process.** The connector is a sidecar container,
  like the embedding model. The server does not depend on it: when it is
  absent, every tool of the release keeps working and `lookup` reports the
  absence.

## 2. Shape

Three layers, each as narrow as the first datasets allow:

| Layer | Where | Holds |
| --- | --- | --- |
| Contract | `packages/core` (models), this document | The dataset release format, the connector manifest, the lookup exchange, the caller-facing additions, the conformance test a connector must pass |
| Connector type | `apps/calendar-connector` in this repository | One process that serves datasets of one type with one fixed input schema and one fixed answer shape. The first and only type is `calendar` (section 7) |
| Datasets | `datasets/<pack>/<dataset>/` in the packs repository | The dataset releases: a curation file with the sources and the concept binding, and the bundle with the sources' hashes and the normalised rows the connector serves; the downloaded files stay under `.local/` |

Code and data stay in the two repositories they live in today, for the
same reason as the release: the connector knows no dataset by name, and a
dataset is selected by argument. Decision: the connector is an app of this
repository with its own command, its own PyPI distribution and its own
image, not a repository of its own. The contract is young and every change
to it touches core, the server and the connector in one commit; the
publishing, container and test machinery exists here; and a user who wants
only the server never installs or pulls the connector. The app imports
`swisstip-core` only, never the runtime or the server, and the server
reaches it only over the HTTP contract of section 5, so moving it out
later stays a matter of history filtering, should a connector ever need
dependencies, a cadence or contributors of its own (section 10).

Position in the pipeline:

```text
datasets/<pack>/<dataset>/dataset.yaml   what the curator writes: sources, columns, binding
        |  swisstip-build-dataset          downloads the sources (the one step with network),
        |                                  checks their hashes, normalises the rows, validates
        v
datasets/<pack>/<dataset>/dataset.json   the self-contained bundle the connector serves
.local/<pack>/datasets/<dataset>/        the downloaded files: working material, not in Git
```

Serving:

```text
caller --MCP--> swiss-tip server --HTTP--> calendar connector
                 (release.json)             (dataset.json x N)
                 resolve: facts + lookup offer
                 lookup:  forwards, adds release_id, guidance, limitations
```

## 3. The dataset release

One directory per dataset under `datasets/<pack>/` in the packs
repository, next to `releases/<pack>/` and outside it, named by the dataset
ID. The curator writes `dataset.yaml`; the build writes `dataset.json`
beside it and the downloaded files under `.local/<pack>/datasets/<dataset>/`,
where the packs repository keeps every fetched page and working file.
Only the curation file and the bundle are committed: the bundle carries the
rows and the hash of the file they came from, so the file itself is
re-downloadable and verifiable, like a cited page. The pack folder is not
touched, so the pack image, its readiness record and its attestation stay
what they are; the calendar image is built from `datasets/<pack>/`.

### The curation file `dataset.yaml`

| Field | Meaning |
| --- | --- |
| `dataset_id` | `[A-Za-z0-9][A-Za-z0-9._-]*`, unique within the pack: `zurich-waste-bioabfall` |
| `type` | The connector type: `calendar` |
| `title` | English: `Organic waste collection days, City of Zurich` |
| `label` | The label every row of this dataset is served with, English: `Organic waste (Bioabfall)` |
| `pack`, `concept_id` | The binding: the pack and the concept the dataset is served behind. A dataset has exactly one binding; a concept may have several datasets |
| `jurisdiction` | The municipality the rows are published for: `CH-ZH-261`. The connector serves the dataset only for a request whose place is contained in it |
| `publisher`, `publisher_url` | `Entsorgung + Recycling Zürich`, the portal page of the dataset |
| `licence` | The licence the portal states: `CC0-1.0` |
| `refresh` | `bundled`: the rows are what the build downloaded; a newer file means a new build. The only value now (section 10 names the other) |
| `sources` | One entry per file: `url` (the portal's download URL, redirects followed), `sha256`, `bytes` and `downloaded_on` as the build recorded them. An entry without a hash is a first download; the build fills it in and the curator commits it. A later build that finds a different hash fails, so an upstream change is a deliberate refresh |
| `importer` | `name` `csv-columns`, with `columns`: which column holds the postal code, the date and, optionally, the location. The delimiter, the encoding (UTF-8 with or without a byte-order mark) and the date format (ISO, `dd.mm.yyyy`, `dd/mm/yyyy`) are detected; a repeated row is dropped and counted in the build report |
| `period` | `start`, `end`: the calendar dates the file is published for, from the portal's description: `2026-01-01` to `2026-12-31`. Every row must fall inside |
| `notes` | Free text for the curator; not served |

The five Zurich datasets differ only in `dataset_id`, `title`, `label`,
`concept_id`, the source URL and, for the hazardous-waste van, the location
column. What the portal publishes, as read on 23 September 2026:

| Dataset on data.stadt-zuerich.ch | Columns | Rows in 2026 | Concept |
| --- | --- | --- | --- |
| `entsorgungskalender_bioabfall` | `PLZ`, `Abholdatum` | 1,253 | `city-zurich-organic-paper-cardboard` |
| `entsorgungskalender_papier` | `PLZ`, `Abholdatum` | 622 | `city-zurich-organic-paper-cardboard` |
| `entsorgungskalender_karton` | `PLZ`, `Abholdatum` | 1,243 | `city-zurich-organic-paper-cardboard` |
| `entsorgungskalender_kehricht` | `PLZ`, `Abholdatum` | 1,399 | `city-zurich-household-waste` |
| `entsorgungskalender_sonderabfall` | `PLZ`, `Station`, `Abholdatum` | 30 | `city-zurich-hazardous-waste` |

All five: licence CC0, publisher Entsorgung + Recycling Zürich, one CSV per
year, ISO dates, 24 postal codes, no row finer than a postal code, no
duplicate rows, about 25 KB each. The portal's download URL answers with a
redirect to the file, so the build follows redirects before hashing.

### The bundle `dataset.json`

One JSON object, self-contained like `release.json`: the connector needs
no other file to serve it.

| Part | Content |
| --- | --- |
| `manifest` | Every field of the curation file except `importer` and `notes`, plus `schema` (`swiss-tip-dataset/v1`), `dataset_version` (`<downloaded_on>-v<n>`, the way a release ID is formed), `created_at`, `postal_codes` (sorted, as found in the rows), `row_count`, `period` and `content_sha256` over the canonical JSON of `rows` |
| `rows` | The normalised rows, sorted by postal code and date: `postal_code` (four digits), `date` (ISO), optional `location` (free text as published, `8001, Neumarkt: Parkplatz am Hirschengraben 13 (vor kantonalem Obergericht)`) |

### Build and validation

`swisstip-build-dataset --packs-dir <checkout> --pack <pack> --dataset
<dataset_id>`, a command of `packages/build` that takes the packs directory
the way the knowledge builder does, is the only step that reaches the
network. A source file at hand under `.local/<pack>/datasets/<dataset>/`
whose hash matches the pin is used as it is, so a rebuild needs no
network; a missing one is downloaded (`--offline` fails instead); one whose
bytes differ from the pin stops the build, and `--refresh` accepts the
publisher's new file and re-pins it. The build records the pins of a first
download in the curation file, runs the importer, validates and refuses to
write a bundle that does not validate. The dataset version is
`<latest downloaded_on>-v<n>`, `n` from `--version`, so a rebuild of the
same download gets a new version without a new download. The
connector runs the same validator at startup and fails closed for that
dataset only; the other datasets it holds keep being served.

The validator checks, in this order: the schema; the hashes of the
downloaded files against the manifest; that every row parses, that every date lies inside
`period`, that every postal code has four digits, that no row is repeated;
that `content_sha256` matches; that the binding names a pack. Whether the
bound concept exists is checked by the server at registration (section 5),
because the connector does not read the release. No check ties a postal
code to the municipality: the register of the release holds municipalities,
not postal codes, and the datasets are trusted on the portal's word that
their rows belong to the city. The manifest says so in `limitations`.

There is no human review of rows and no attestation of a dataset. A
thousand dates are not reviewed by reading them; the hash and the
validator are the whole gate, and every lookup result says so.

## 4. The connector process

`apps/calendar-connector`: `swisstip-calendar-connector --dataset <dir>`
(repeatable, or `SWISSTIP_DATASETS` as a list of directories), one HTTP
process on port 8100. It loads every bundle, validates it, and serves three
routes. It has no MCP endpoint and is never reached by a caller directly.

| Route | Purpose |
| --- | --- |
| `GET /manifest` | The connector manifest (section 5): what it serves and how to ask |
| `POST /lookup` | One lookup (section 5) |
| `GET /health` | `status`, `schema`, `connector_id`, and one line per dataset: `dataset_id`, `dataset_version`, `status` (`ok`, `invalid` with the validator's first message), `period`, `row_count`, `expires_on` (the `period.to`), `expired` (true once today is past it) |

The container `swiss-tip-calendar:<pack>-<dataset_version>` is built in
the packs repository on a generic `swiss-tip-calendar-connector` image
from this repository, the way pack images are built on the MCP image, and
carries the pack's dataset directories. In `compose.yaml` it is a fourth
service that joins the server's network namespace like the embedding
sidecar, so the server reaches it on `127.0.0.1:8100` and nobody else does:

```yaml
  calendar:
    profiles: ["calendar"]
    image: ${SWISSTIP_REGISTRY:-ghcr.io/swisstip}/swiss-tip-calendar:${SWISSTIP_PACK}
    network_mode: "service:swiss-tip"
```

The server takes `--connector http://127.0.0.1:8100` (repeatable) or
`SWISSTIP_CONNECTORS`, comma-separated; `compose.yaml` passes the variable
through, so `SWISSTIP_CONNECTORS=http://127.0.0.1:8100 docker compose
--profile calendar up -d --wait` is the whole setup. Without it, nothing of
this document is active: the tool list has no `lookup`, `resolve` offers
nothing, and the health payload says `connectors: []`.

## 5. The internal contract: server and connector

`swiss-tip-connector/v1`. Both directions are strict JSON objects that
reject unknown fields, modelled in `packages/core` next to the tool
contracts and served by the connector app. The conformance test of section
8 runs against these shapes, so a connector written elsewhere, in another
language, can prove it speaks them.

### Manifest, `GET /manifest`

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | string | `swiss-tip-connector/v1` |
| `connector_id` | string | `calendar-connector` |
| `types` | list of string | The connector types this process serves: `["calendar"]` |
| `datasets` | list of DatasetSummary | One per bundle loaded, valid or not |

DatasetSummary:

| Field | Type | Meaning |
| --- | --- | --- |
| `dataset_id`, `dataset_version`, `type`, `title`, `label` | string | From the bundle's manifest |
| `pack`, `concept_id`, `jurisdiction` | string | The binding |
| `publisher`, `publisher_url`, `licence` | string | Provenance, served on every result |
| `sources` | list of Source | `url`, `sha256`, `downloaded_on`, `bytes` |
| `period` | Period | `start`, `end`, both inclusive |
| `postal_codes` | list of string | The codes the rows hold, so the server can answer a code outside them without a round trip |
| `row_count` | integer | |
| `requires` | list of string | The input fields a lookup must carry: `["postal_code"]` for `calendar` |
| `accepts` | list of string | The optional input fields: `["start", "end", "limit"]` |
| `status` | string | `ok` or `invalid` |
| `issue` | string or absent | The validator's first message when `invalid` |
| `limitations` | list of string | The dataset's own lines: that rows are not reviewed, that postal codes are trusted from the portal, the licence line |

### Registration

At startup, and again on every health probe while a connector is
unreachable, the server fetches the manifest and, for each dataset:

1. Rejects it when `type` is not one the server knows (`calendar`) or the
   `schema` is not one it speaks; logs one line.
2. Rejects it when `pack` is not the pack of the served release, or
   `concept_id` is not a concept of the release, or `jurisdiction` is not
   contained in one of the concept's jurisdictions; logs one line naming
   the concept. A dataset must stand behind a published concept.
3. Registers it as a lookup offer on that concept.

A connector that cannot be reached is logged and probed again; the server
starts without it. The health payload of the server gains
`connectors`: one entry per configured URL with `url`, `status`
(`ok`, `unreachable`, `manifest_invalid`), `connector_id`, and the
datasets registered and rejected with their reasons.

### Lookup, `POST /lookup`

Request:

| Field | Type | Meaning |
| --- | --- | --- |
| `dataset_id` | string | One dataset of the manifest |
| `postal_code` | string | Four digits |
| `start` | date or null | First date to return; the server sends its `as_of`; null means the start of the published period |
| `end` | date or null | Last date to return, inclusive; null means the end of the published period |
| `limit` | integer | 1 to 60; the server passes the caller's value or its default |

Response:

| Field | Type | Meaning |
| --- | --- | --- |
| `dataset_id`, `dataset_version` | string | |
| `status` | string | `SUPPORTED` when at least one row is returned, else `OUT_OF_COVERAGE` |
| `events` | list of Event | `date`, `label` (the dataset's label), optional `location`; sorted by date, at most `limit` |
| `truncated` | boolean | More rows than `limit` fell into the range |
| `provenance` | Provenance | `publisher`, `publisher_url`, `licence`, `sources` (as in the manifest), `period`, `dataset_version` |
| `gaps` | list of Gap | `dimension`, `message`, `published_values` (section 6 names the dimensions) |

Errors: a malformed request (a postal code that is not four digits, an
unknown `dataset_id`, `end` before `start`) is an HTTP 400 with `{"code":
"INVALID_ARGUMENT", "message": ..., "path": ...}`; the server turns it
into a `ToolError` of the same code. Anything else (the connector down, a
timeout, an unexpected status) becomes the `connector_unavailable` gap on
the caller's result, never an error, because the release is unaffected.

The connector composes nothing: no guidance, no limitations of the
release, no release ID. Those are the server's, added on the way out.

## 6. The caller-facing change

Additive on `swiss-tip/v4`: one optional field, one tool, four gap
dimensions. `search`, `get_coverage` and `get_evidence` do not change, and
a caller that ignores the new field sees the tools it knows. The schema
bundle carries `lookup` next to the four tools; the server lists it only
while a connector is registered, from a tool table of its own.

### `resolve`: the offer

ConceptResolution gains `lookups`, a list of LookupOffer, absent when
empty:

| Field | Type | Meaning |
| --- | --- | --- |
| `dataset_id` | string | What to send to `lookup` |
| `type` | string | `calendar` |
| `title`, `label` | string | `Organic waste collection days, City of Zurich` |
| `jurisdiction` | string | `CH-ZH-261` |
| `requires` | list of string | `["postal_code"]` |
| `accepts` | list of string | `["start", "end", "limit"]` |
| `period` | Period | The published range, so the caller can tell the user before asking for a postal code that next year is not published yet |
| `publisher` | string | |

The offer appears only when the concept resolved `SUPPORTED` or `STALE`
for a place the dataset's jurisdiction contains: a caller that asked for
Winterthur gets the Winterthur answer of the concept and no Zurich
calendar. When an offer is present, `guidance_for_caller` adds one
sentence: a calendar of dates exists for this concept; to name a date, ask
the user for the four-digit postal code and call `lookup`; do not guess a
date from the facts. Nothing else changes: `resolve` never calls the
connector, so its latency, its statuses and its regression pack are
untouched by a connector that is slow or down.

### `lookup`

Registered only when at least one dataset is registered; annotated
`readOnlyHint` like the others.

Request:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dataset_id` | string | required | From a `resolve` offer |
| `postal_code` | string | required | Four digits, as the user gave it |
| `as_of` | date or null | today | The date "next" counts from |
| `start`, `end` | date or null | `as_of`, end of the period | The range wanted: a month, a week, or nothing for "the next dates" |
| `limit` | integer | 3 | 1 for the next date, up to 60 for a whole year |
| `release_id` | string or null | | As on the other tools |

Result, LookupResult:

| Field | Type | Meaning |
| --- | --- | --- |
| `release_id` | string | The release the offer came from |
| `dataset_id`, `dataset_version`, `type`, `label` | string | |
| `status` | Status | `SUPPORTED` or `OUT_OF_COVERAGE` |
| `as_of` | date | |
| `events` | list of Event | `date`, `label`, optional `location` |
| `truncated` | boolean | |
| `provenance` | Provenance | Publisher, portal URL, licence, sources with hashes and download dates, period |
| `gaps` | list of CoverageGap | |
| `guidance_for_caller` | string | On `SUPPORTED`: state the dates as published by the publisher for that postal code, name the publisher, and say the rows are not reviewed statements; on `OUT_OF_COVERAGE`: say what the gap says and do not derive a date from the rule |
| `limitations` | list of string | The release's two standing lines, then the dataset's own |

New gap dimensions, added to the shared `CoverageGap` so a caller reads
one vocabulary:

| `dimension` | When | `published_values` holds |
| --- | --- | --- |
| `postal_code_not_covered` | The dataset holds no row for the code | The codes it holds |
| `period_not_published` | The range asked for lies wholly outside the published period, typically a date in the next year before its file exists | The published period as two dates |
| `no_dates_in_range` | The postal code is held and the range overlaps the period, but no date of the dataset falls into it: the hazardous-waste van has passed for the year | The published period as two dates |
| `connector_unavailable` | The connector did not answer, or answered something other than the contract | Empty; the message names the connector and says the release is unaffected |

An unknown `dataset_id` is a `ToolError` `INVALID_ARGUMENT` listing the
registered ones, like an unknown concept ID on `get_evidence`; a lookup
for a dataset whose connector was registered and has since gone is the
`connector_unavailable` gap.

### Example

`resolve` for `city-zurich-organic-paper-cardboard` in Zurich returns its
facts and citations as today, and in the concept's result:

```json
"lookups": [
  {"dataset_id": "zurich-waste-bioabfall", "type": "calendar",
   "title": "Organic waste collection days, City of Zurich", "label": "Organic waste (Bioabfall)",
   "jurisdiction": "CH-ZH-261", "requires": ["postal_code"], "accepts": ["start", "end", "limit"],
   "period": {"start": "2026-01-01", "end": "2026-12-31"}, "publisher": "Entsorgung + Recycling Zürich"},
  {"dataset_id": "zurich-waste-papier", "...": "..."},
  {"dataset_id": "zurich-waste-karton", "...": "..."}
]
```

`lookup` with `{"dataset_id": "zurich-waste-bioabfall", "postal_code":
"8001", "as_of": "2026-09-23", "limit": 3}`, values as published in the
2026 file read on 23 September 2026:

```json
{
  "release_id": "mvp-zurich-2026-09-22-v5",
  "dataset_id": "zurich-waste-bioabfall", "dataset_version": "2026-09-23-v1",
  "type": "calendar", "label": "Organic waste (Bioabfall)",
  "status": "SUPPORTED", "as_of": "2026-09-23",
  "events": [
    {"date": "2026-09-28", "label": "Organic waste (Bioabfall)"},
    {"date": "2026-10-05", "label": "Organic waste (Bioabfall)"},
    {"date": "2026-10-12", "label": "Organic waste (Bioabfall)"}
  ],
  "truncated": true,
  "provenance": {
    "publisher": "Entsorgung + Recycling Zürich",
    "publisher_url": "https://data.stadt-zuerich.ch/dataset/entsorgungskalender_bioabfall",
    "licence": "CC0-1.0",
    "sources": [{"url": "https://data.stadt-zuerich.ch/dataset/entsorgungskalender_bioabfall/download/entsorgungskalender_bioabfall_2026.csv",
                 "sha256": "6040ce9917e5c8e0b6af5416a404d0df0bb0ee62ef4b965845dbeb6731802c0d",
                 "bytes": 23830, "downloaded_on": "2026-09-23"}],
    "period": {"start": "2026-01-01", "end": "2026-12-31"}
  },
  "gaps": [],
  "guidance_for_caller": "State the dates as published by Entsorgung + Recycling Zürich for postal code 8001; they are rows of the publisher's open-data file, not reviewed statements. Name the publisher.",
  "limitations": ["...the release's two lines...",
                  "Calendar rows are taken from the publisher's file unchanged and are not human-reviewed; the postal codes are the publisher's."]
}
```

The same request with `"postal_code": "8304"` returns `OUT_OF_COVERAGE`
with `postal_code_not_covered` and the 24 codes; with `"start":
"2027-01-04"` it returns `period_not_published` with the two dates of the
2026 period.

### The call pattern

Three calls for "When is the next organic waste collection in 8001?":
`search` with the question, `resolve` with the concept (which returns the
rule and the offer), `lookup` with the postal code. A caller that already
knows the postal code from the conversation makes the same three calls
without asking; one that does not asks the user once, after `resolve`,
because the offer says what is required.

## 7. The `calendar` type

The only connector type. It fixes what section 5 and 6 leave open:

- **Input:** a four-digit postal code and a date range. Nothing finer
  (street, area) and nothing coarser (a municipality), because the first
  datasets are keyed by postal code and a type is as narrow as its data.
- **Row:** a date, the dataset's label, an optional location. A dataset
  holds one kind of event, so the label is the dataset's, not the row's:
  three datasets with three labels rather than one with a type column,
  because the portal publishes them that way and a binding is one concept
  per dataset.
- **Semantics of the range:** `start` inclusive, `end` inclusive, dates
  compared as calendar dates in the publisher's calendar; no time of day,
  no time zone. "Next" is `limit` 1 from `as_of`.
- **Importer:** `csv-columns` reads one CSV with named columns into rows.
  It is the only importer. A portal that publishes another shape gets a
  second importer name in the curation file, not a change of the type.

## 8. Quality

- **Unit tests** in `packages/core`, `packages/build` and
  `apps/calendar-connector` on fixture files under their `tests/fixtures`,
  as every test here: a five-row CSV, a bundle built from it, a manifest,
  a lookup. No test reads a pack or the network.
- **Conformance test:** `scripts/test/connector/check_connector.py <url>`
  fetches the manifest, validates it against the models, sends one lookup
  per dataset for its first postal code, one for a code it does not hold,
  one outside the period, and one malformed request, and checks the four
  answers against section 5. A connector written in another language passes
  it or is not a connector. The packs repository runs it on the built
  calendar image.
- **Acceptance cases**, in the packs repository: a third step kind,
  `lookup` (`dataset_id`, `postal_code`, `as_of`, `start`, `end`, `limit`,
  `expect_status`, `expect_gap`, `expect_first_date`), validated against the
  tool contract like the others and implemented in `swisstip.core.acceptance`
  and the replay of `swisstip.runtime.acceptance`; cases for the next
  organic-waste date in 8001 on a fixed `as_of`, a postal code outside the
  city, a date in the next year, drafted in the packs repository's
  `datasets/README.md`. A lookup step is judged only when the replay's
  service has a registered connector serving the dataset; without one it is
  recorded as not judged, not as failed, so a pack's readiness does not
  depend on a sidecar. The knowledge builder's `accept` stage does not take
  a connector yet, so there every lookup step is unjudged.
- **Harness case**: "When is the next organic waste collection in 8001?"
  against the demo image with the calendar container, graded on whether
  the answer states the date the dataset holds and names the publisher.
- **Regression pack:** no change; `search` is untouched.

## 9. Order of work

1. Models in `packages/core`: dataset bundle, manifest, lookup exchange,
   the caller-facing additions; the schema bundle regenerated; tests.
   Implemented.
2. `swisstip-build-dataset` in `packages/build` with the `csv-columns`
   importer and the validator; tests on fixtures. Implemented.
3. `apps/calendar-connector`; the conformance test. Implemented.
4. Server: `--connector`, registration, the offer on `resolve`, the
   `lookup` tool, the health entries; tests with a connector stub.
   Implemented.
5. Packs repository: five `dataset.yaml`, the bundles, the calendar
   image, the compose service, the acceptance cases, the harness case.
   The code side (generic image, compose profile, distribution, workflow
   steps, the `lookup` step kind) and the pack's curation files, bundles
   and calendar Dockerfile are implemented; the acceptance cases are
   drafted and not in the suite, the images are unbuilt, the harness case
   is open.
6. Documentation: this document's status line, the tool contracts (the
   field, the tool, the four gaps), the docker README, the specification's
   delivery section.

Steps 1 to 4 are code and ship without any dataset; step 5 is data and
ships without a code change once the packages are published.

## 10. Deferred, by decision

What the first connector does not do, and what would change if it did. None
of it is designed beyond this list, so that nothing here is built for a
case that does not exist yet.

- **A second connector type** (`places` for the collection points and the
  mobile recycling yard of the same portal, with opening hours and
  coordinates; `tariff` for fee tables). Each is a new fixed input and
  answer shape and a new value of `type`; the manifest, registration,
  offers and gaps are shared. A type is added when a dataset needs it.
- **A second refresh policy** (`scheduled`: fetched at runtime on an
  interval, with `fetched_on` and a staleness threshold on every result)
  for data that changes faster than a build cycle. Road closures would need
  it, and also a concept to stand behind, which the pack does not publish;
  until both exist, they are out.
- **Live services.** A connector that passes a query to a third party at
  answer time contradicts the out-of-scope statement of the specification
  and is not a dataset connector. It would be a different product feature
  with its own decision.
- **Generic datasets.** A connector that serves arbitrary tables with a
  query over their columns would give up the typed gaps and the fixed
  answer shape that make a lookup result as safe to relay as a fact. Not
  planned.
- **A postal-code register.** Checking that a postal code belongs to the
  municipality of the binding, and turning a postal code into a place for
  `resolve`, need the official postal-code list. Until then a postal code is
  the dataset's word, and `resolve` keeps taking the municipality.
- **A connectors repository.** When a connector needs dependencies the
  server does not have, a release cadence of its own, or contributors who
  should not touch the server, its code moves to a repository of its own,
  `swiss-tip-connectors`; the contract and the conformance test stay here.
  Not before one of the three is the case.
- **Other cities.** A city whose portal publishes the same shape is five
  more curation files in the packs repository and nothing else; one with
  another shape is one more importer name. Neither is planned before the
  Zurich datasets are served.
