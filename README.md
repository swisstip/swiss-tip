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

One clone and one command. It needs [uv](https://docs.astral.sh/uv/) and
nothing else: no Python has to be installed, uv fetches Python 3.14 when the
machine has none. uv itself is one line (then open a new terminal):
`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS and Linux,
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
on Windows.

```shell
git clone https://github.com/swisstip/swiss-tip.git && cd swiss-tip
uv run swisstip-quickstart mvp-zurich
```

The command installs the server from this checkout into `.venv`, downloads
the current attested release of the `mvp-zurich` knowledge base from the
packs repository into `.local/packs/mvp-zurich/` (outside Git), and runs the
first tests on it with no model and no further network: the release
validates and its readiness record attests exactly the downloaded file, the
semantic index is bound to that release, the pack's acceptance suite and
regression pack are replayed against it, and a client round trip over MCP
runs against the server. It prints one line per check and ends with the
commands that serve the pack and connect a client:

```shell
uv run swisstip-quickstart mvp-zurich --serve                 # MCP on http://127.0.0.1:8000/mcp, /health beside it
uv run swisstip-mcp --release .local/packs/mvp-zurich/release.json --require-ready --print-client-config opencode
uv run swisstip-quickstart mvp-zurich --no-fetch              # the same checks again, offline
```

`uv run swisstip-quickstart --help` lists the rest: another pack, a commit
or tag of the packs repository, hybrid search beside the embedding sidecar,
and `--url` for the round trip against a running container (the Docker route
below). Details are in the [quickstart README](apps/quickstart/README.md).

Without a clone, the same from the published package `swisstip-quickstart`,
in any directory; the checkout route above tests this source instead:

```shell
uvx swisstip-quickstart mvp-zurich
```

### Only Docker, nothing else

One command, and everything is inside the image: the server, the
`mvp-zurich` release with its readiness attestation and semantic index, and a
CPU-only Ollama with the `qwen3-embedding:0.6b` model, so search is hybrid
(lexical plus embeddings) from the first request. No clone, no Python, no uv,
no API key and no model download.

```shell
docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich
```

The image is about 850 MB and the server answers about 15 seconds after it
starts. `curl -s http://127.0.0.1:8000/health` then reports `"status": "ok"`,
the release ID, a `readiness` status of `ready` and `search.configured_mode`
`hybrid`; any MCP client connects to `http://127.0.0.1:8000/mcp`:

```shell
claude mcp add --transport http swiss-tip http://127.0.0.1:8000/mcp
```

The [other ways to run it](#run-the-server) below are the same release in
different packaging, down to a 165 MB image without the model.

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

Every way of running the server names the release it serves; the server holds
no knowledge of its own. The **full pack image carries everything** and is the
shortest route:

```shell
docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich
```

It bundles the MCP server, the release with its readiness record and semantic
index, and the embedding model, so search is **hybrid** from the first
request. Each `search` result names its `retrieval_mode`: `hybrid`, or
`lexical-fallback` with the reason if the bundled model is ever unreachable.
Use `-p 9000:8000` for another host port, and an immutable tag such as
`mvp-zurich-2026-09-24-v1` instead of the moving tag `mvp-zurich` to pin a
release.

The alternatives serve the same release in different packaging:

| Way | Command | Search | Size |
| --- | --- | --- | --- |
| Full pack image, model inside | `docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich` | hybrid | ~850 MB |
| Two containers: slim server plus the embedding sidecar ([compose.yaml](compose.yaml), no clone of the packs needed) | `SWISSTIP_PACK=mvp-zurich docker compose up -d --wait` | hybrid | ~165 MB + ~830 MB |
| Slim pack image alone, no model | `docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich-slim` | lexical only | ~165 MB |
| From PyPI with [uv](https://docs.astral.sh/uv/), a release directory | `uvx swisstip-mcp --release <packs>/releases/<pack>/release.json --require-ready --transport streamable-http` | lexical; hybrid needs a local Ollama | - |
| From this checkout, after the installation below | `./.venv/bin/python -m swisstip.mcp_server.server --release <packs>/releases/<pack>/release.json --transport streamable-http` (`.venv/Scripts/python.exe` on Windows) | lexical; same note | - |

In the last two, `<packs>` is a clone of the packs repository
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp) (or a directory of
files downloaded from one of its GitHub releases) and `<pack>` is a pack name
in it; the published one is **`mvp-zurich`**. After the quick start above,
`<packs>/releases/<pack>` is `.local/packs/mvp-zurich` of this checkout. For
example:

```shell
git clone https://github.com/swisstip/swiss-tip-mvp ../swiss-tip-mvp
uvx swisstip-mcp --release ../swiss-tip-mvp/releases/mvp-zurich/release.json --require-ready --transport streamable-http
```

Any MCP client connects to `http://127.0.0.1:8000/mcp` (Streamable HTTP, no
authentication), and `/health` shows the release and its review status.
Details, stdio, client configurations and hybrid search with a local
embedding model are in the [server README](apps/mcp-server/README.md) and
under [container images](docker/README.md).

## Install and test

You need Python 3.14 or newer. The tests take about a minute, use no
network and depend on no knowledge base: they run on synthetic fixtures.

```shell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e packages/core -e packages/runtime -e packages/build -e packages/ingestion -e "packages/extraction[office]" -e packages/concepts -e apps/mcp-server -e apps/quickstart -e apps/knowledge-builder -e apps/admin-console -e apps/calendar-connector
for t in packages/*/tests apps/*/tests; do ./.venv/Scripts/python.exe -m unittest discover -s "$t" || break; done
./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py
```

The loop runs every unit test; the last command runs a client round trip
over stdio against the real server on the synthetic release. On macOS or
Linux, use `.venv/bin/`. The checks of the packs themselves (their suites,
reports, readiness records and catalogues) live in the packs repository.

With uv, the same environment comes from `uv.lock`: the root `pyproject.toml`
is a workspace of every component, `uv run` installs the serving side, and
the dependency group `build` adds the pipeline and the console:

```shell
uv sync --group build
for t in packages/*/tests apps/*/tests; do uv run python -m unittest discover -s "$t" || break; done
uv run python scripts/test/mcp/check_wheel.py
```

## Build a knowledge base

The pipeline reads a pack's source catalogue, downloads the pages, extracts
their text, builds the release from the curation file, validates it, replays
the acceptance suite and writes the readiness record. It runs on a packs
directory, a clone of the packs repository:

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --packs-dir <packs>
./.venv/Scripts/python.exe -m swisstip.admin_console.app --packs-dir <packs> --actor "A. Person"
```

The [knowledge builder](apps/knowledge-builder/README.md) describes the
stages, the [admin console](apps/admin-console/README.md) the review
screens, and the [pipeline document](docs/architecture/knowledge-base-pipeline.md)
the route a pack takes from catalogue to served release.

## Source etiquette

Acquisition is operator-triggered and **build-time only**: the MCP server
never crawls at request time, it serves a prebuilt, hashed release. The
downloader (`swisstip-download`) respects `robots.txt` and its crawl delays,
identifies itself, stays within a per-host rate limit and a declared budget,
and **fails closed** when a robots policy cannot be read.

This is the default and the recommended setting. An operator who is
authorised to access a host — a data-sharing agreement, an authoritative
mandate — can override it per run with `--no-obey-robots`; the run then
records `robots_status: "overridden"` in its report so the decision is
auditable rather than silent. The default (`--obey-robots`) leaves the
fail-closed behaviour in place.

```shell
swisstip-download --catalogue releases/<pack>/sources.json --output releases/<pack> --download
swisstip-download --catalogue releases/<pack>/sources.json --output releases/<pack> --download --no-obey-robots
```

Quoted official texts kept in a release remain the property of their
publishers and are reproduced only as cited evidence, see [NOTICE](NOTICE).

## Repository

| Path | Contents |
| --- | --- |
| `apps/` | `mcp-server`, `quickstart` (the [one command](apps/quickstart/README.md) that fetches and tests a pack), `knowledge-builder`, `admin-console`, `calendar-connector` (the first [dataset connector](apps/calendar-connector/README.md)) |
| `packages/` | `core` (release format, validator, tool contracts), `runtime` (the four operations, lexical and hybrid search), `ingestion`, `extraction`, `build`, `concepts` |
| `docs/` | [Functional specification](docs/product/functional-specification.md) and the architecture documents: [release format](docs/architecture/release-format.md), [tool contracts](docs/architecture/tool-contracts.md), [acceptance gate](docs/architecture/acceptance-gate.md), [extraction](docs/architecture/extraction.md), [concept extraction](docs/architecture/concept-extraction.md), [institutions and basis](docs/architecture/institutions-and-provenance-weights.md), [pipeline](docs/architecture/knowledge-base-pipeline.md), [admin console](docs/architecture/admin-console.md), [dataset connectors](docs/architecture/dataset-connectors.md) (proposal) |
| `docker/`, `compose.yaml`, `Dockerfile` | [Container images](docker/README.md): the MCP image, its slim variant without the model, the slim release image, the embedding sidecar and the OpenCode test image; the root Dockerfile builds the server from this source with a pack as build context. The workflow [container-images.yml](.github/workflows/container-images.yml) builds, tests and pushes the images that hold no release; the packs repository builds the ones that hold one |
| `scripts/pypi/` | The PyPI distributions `swisstip-core`, `swisstip-mcp`, `swisstip-quickstart`, `swisstip-calendar-connector` and `swisstip-builder` ([publishing](scripts/pypi/README.md)) |
| `pyproject.toml`, `uv.lock` | The uv workspace of the components, for `uv run` and `uv sync`; each component's own `pyproject.toml` stays the source of its metadata |
| `config/` | Provider profiles of the concepts package |

Contributor conventions are in [AGENTS.md](AGENTS.md).

## Licence

Apache License 2.0, see [LICENSE](LICENSE). Quoted official texts in the
knowledge releases remain the property of their publishers and are
reproduced only as cited evidence, see [NOTICE](NOTICE).
