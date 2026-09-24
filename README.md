# Swiss TIP

<p align="center">
  <img src="docs/images/switzerland.gif" alt="Switzerland map" width="720">
</p>

An MCP (Model Context Protocol) server that gives an AI assistant grounded
access to authoritative Swiss public information. It serves a curated,
versioned knowledge base in which every fact is tied to an exact excerpt of
an official page, with a citation. The calling assistant composes the answer
itself; the server never invents one, and when a question is not covered it
says so by name.

## The hackathon

Built for the **[Swiss {ai} Weeks](https://zh.ai-weeks.ch/)** hackathon in Zurich, 24 and 25 September
2026, for the challenge **[Swiss Grounding MCP](https://zh.ai-weeks.ch/challenges/swiss-grounding-mcp)**, set by Swisscom's myAI team.

## Quick start

One command, and Docker is all you need. The image carries everything: the
MCP server, the `mvp-zurich` knowledge base with its readiness attestation and
semantic index, and a CPU-only Ollama with the `qwen3-embedding:0.6b` model,
so search is hybrid (lexical plus embeddings) from the first request. No
clone, no Python, no API key and no model download.

```shell
docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich
```

The first run pulls about 850 MB, roughly three minutes on a normal
connection; the server then answers within about 15 seconds of starting, once
the model is loaded. Check it, and connect any MCP client to
`http://127.0.0.1:8000/mcp` (Streamable HTTP, no authentication):

```shell
curl -s http://127.0.0.1:8000/health
claude mcp add --transport http swiss-tip http://127.0.0.1:8000/mcp
```

A healthy `/health` reports `"status": "ok"`, the release ID, the `readiness`
record with status `ready`, and `search.configured_mode` set to `hybrid`. The
tag `mvp-zurich` always serves the newest attested release of that pack; the
packs repository also publishes a tag per release for pinning an exact one.

To develop the server, run it from source or build a knowledge base, see
[developer setup](docs/developer-setup.md).

## How it works

```text
official page -> saved text -> quoted excerpt -> reviewed fact -> versioned release -> four MCP tools
```

A knowledge base ships as one versioned, hashed release bundle. It is built
from curated facts, each citing an exact excerpt of an official page by URL,
access date and hash, and it is served only after its acceptance and
readiness checks pass. Every fact names its review status, and a caller can
ask for reviewed facts only. The server offers four tools: `get_coverage`,
`search`, `resolve` and `get_evidence`. `resolve` returns a typed status
that says whether the question is covered, whether a detail such as the
canton is missing, or whether the facts are stale. The full design is in the
[functional specification](docs/product/functional-specification.md).

## Coverage at a glance

The server is knowledge-base agnostic; the published MVP knowledge base is
**`mvp-zurich`** ([swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp)).
`get_coverage` returns the live scope, out-of-scope list and limitations of
whatever release is loaded; the snapshot below describes the current one.

- **Subject:** everyday administrative life in Switzerland, for people who
  live here and people moving here, of any nationality.
- **Jurisdictions:** federal rules and arrival registration for **all 26
  cantons**, the full procedures of the **Canton of Zurich** and the **City of
  Zurich**, and waste hand-over in the **City of Lugano**; federal rules still
  apply elsewhere and are served with a caveat.
- **Topics:** residence permits and registration, cantonal migration offices,
  entry and visas, AHV and the pillar system, tax return and tax at source,
  health and accident insurance, unemployment and the RAV, family allowances,
  naturalisation, voting rights, renting, foreign driving licences, customs,
  waste and recycling, integration offers, and City of Zurich services.
- **Languages:** search matches German, English, French and Italian; answers
  are written in the user's own language.
- **Not covered (examples):** fees, appointment availability and processing
  times; benefit amounts and every calculator; eligibility decisions for a
  specific person; asylum, social assistance and debt enforcement; cantons
  other than Zurich and municipalities other than the City of Zurich beyond
  arrival registration. `get_coverage` returns the full list.
- **Sources:** authoritative Swiss pages only — federal (`admin.ch`,
  `fedlex.data.admin.ch`, `ch.ch`), cantonal (`zh.ch`), and municipal
  (`stadt-zuerich.ch`); every fact cites an exact excerpt with its URL, access
  date and hash.

**Current release — `mvp-zurich-2026-09-24-v1`:** 20 topics, 212 concepts and
**1,273 facts, all human-reviewed**, cited to 1,531 excerpts across 336 official
documents; source snapshot 2026-09-23, stale from 2026-11-22. Coverage grows
with each release, so these figures are a snapshot: the running server reports
the live numbers through `get_coverage` and `/health`.

## Run the server

One image, one command, hybrid search:

```shell
docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich
```

The image is the whole server: the release it serves, its readiness
attestation, the semantic index and the embedding model. Nothing else is
needed, and nothing has to be configured. Every `search` result names its own
`retrieval_mode`, which is `hybrid` here.

Other container images exist for narrower cases — a smaller image without the
model, a two-container split with an embedding sidecar, the calendar connector
and a browser demo interface. They serve the same release and are described
under [container images](docker/README.md) and in
[developer setup](docs/developer-setup.md); the command above is the one to
use.

## Source etiquette

Acquisition is operator-triggered and **build-time only**: the MCP server
never crawls at request time, it serves a prebuilt, hashed release. The
downloader respects `robots.txt` and its crawl delays, identifies itself,
stays within a per-host rate limit and a declared budget, and **fails closed**
when a robots policy cannot be read. That is the default; an operator
authorised to access a host can override it per run with `--no-obey-robots`,
and the run records `robots_status: "overridden"` so the decision is auditable
rather than silent. The commands are in
[developer setup](docs/developer-setup.md#source-etiquette).

Quoted official texts kept in a release remain the property of their
publishers and are reproduced only as cited evidence, see [NOTICE](NOTICE).

## Freshness, and what happens when it lapses

Answers come from a **dated snapshot**, never from a live fetch at request
time. For the current release the snapshot date is **23 September 2026**: every
fact says what its official page published on or before that date, and every
citation carries its own `accessed_on` date, so a caller can always tell how
old the evidence is.

The release declares a freshness window of 60 days, which runs out on
**22 November 2026**. The server does not quietly keep serving after that. Once
the window has passed, `resolve` returns the typed status `STALE` instead of
`SUPPORTED`, and says so in its guidance:

> The source snapshot of 2026-09-23 is older than 60 days on 2026-11-23. These
> facts say what the official pages published on 2026-09-23, not what holds on
> 2026-11-23: present them as published on 2026-09-23, do not confirm that
> opening hours, availability, officeholders, rates, contacts or rules still
> apply, and tell the user to check the cited page.

So an assistant is told to present the facts as historical and to send the user
to the source, rather than asserting that they are current. A caller cannot
dodge this by asking for an earlier date: the guidance names that too. The
window is a property of the release, so publishing a fresher release resets it,
and `/health` and `get_coverage` both report the snapshot date and the date the
release goes stale.

This matters most for the values that move: fees, rates, opening hours,
officeholders and deadlines. Two things keep those honest. Amounts and
calculators are **out of scope** by declaration, not by omission — tariff
tables, tax and pension amounts, premiums and every calculator are listed in
`out_of_scope`, which `get_coverage` returns. And where a rate is published,
the statement carries its own effective date rather than presenting it as
timeless. The mortgage reference interest rate reads:

> The mortgage reference interest rate (Referenzzinssatz) that governs rent
> adjustments is 1.25 percent, valid since 2 September 2025; the Federal Office
> for Housing's page, saved on 18 September 2026, states that it remains
> unchanged from 2 September 2026.

## Credentials and the prebuilt index

**No credentials.** The server needs no API key, no token and no account, at
build time or at run time, and it makes no call to any external service while
answering: it serves a local release, and the embedding model runs locally
inside the image. There is nothing to hand over in order to run or test it,
and the repository holds no secrets.

**The prebuilt index ships with the release.** `semantic-index.json` is part
of a pack's release bundle, so every route above already has it: inside the
image, or downloaded with the release. Nothing has to be embedded, crawled or
fetched by hand before the first request. The index is bound to its release by
the release ID, the release content hash, a hash of every embedded text and
the model's own digest; the server refuses an index built for another release
or another model. The script that builds it is in this repository and the
command is in
[developer setup](docs/developer-setup.md#rebuild-the-semantic-index).

## Repository

| Path | Contents |
| --- | --- |
| `apps/` | `mcp-server`, `quickstart` (the [one command](apps/quickstart/README.md) that fetches and tests a pack), `knowledge-builder`, `admin-console`, `calendar-connector` (the first [dataset connector](apps/calendar-connector/README.md)) |
| `packages/` | `core` (release format, validator, tool contracts), `runtime` (the four operations, lexical and hybrid search), `ingestion`, `extraction`, `build`, `concepts` |
| `docs/` | [Developer setup](docs/developer-setup.md) (running from source, tests, building a pack), the [functional specification](docs/product/functional-specification.md) and the architecture documents: [release format](docs/architecture/release-format.md), [tool contracts](docs/architecture/tool-contracts.md), [acceptance gate](docs/architecture/acceptance-gate.md), [extraction](docs/architecture/extraction.md), [concept extraction](docs/architecture/concept-extraction.md), [institutions and basis](docs/architecture/institutions-and-provenance-weights.md), [pipeline](docs/architecture/knowledge-base-pipeline.md), [admin console](docs/architecture/admin-console.md), [dataset connectors](docs/architecture/dataset-connectors.md) (proposal) |
| `docker/`, `compose.yaml`, `Dockerfile` | [Container images](docker/README.md): the MCP image, its slim variant without the model, the slim release image, the embedding sidecar and the OpenCode test image; the root Dockerfile builds the server from this source with a pack as build context. The workflow [container-images.yml](.github/workflows/container-images.yml) builds, tests and pushes the images that hold no release; the packs repository builds the ones that hold one |
| `scripts/pypi/` | The PyPI distributions `swisstip-core`, `swisstip-mcp`, `swisstip-quickstart`, `swisstip-calendar-connector` and `swisstip-builder` ([publishing](scripts/pypi/README.md)) |
| `pyproject.toml`, `uv.lock` | The uv workspace of the components, for `uv run` and `uv sync`; each component's own `pyproject.toml` stays the source of its metadata |
| `config/` | Provider profiles of the concepts package |

Running the server from source, the uv workflow, Python 3.14, the unit tests,
building a knowledge base and rebuilding the semantic index are all in
**[developer setup](docs/developer-setup.md)**. Contributor conventions are in
[AGENTS.md](AGENTS.md).

## Licence

Apache License 2.0, see [LICENSE](LICENSE). Quoted official texts in the
knowledge releases remain the property of their publishers and are
reproduced only as cited evidence, see [NOTICE](NOTICE).
