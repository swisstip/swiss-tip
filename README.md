# Swiss TIP

An MCP (Model Context Protocol) server that gives an AI assistant grounded
access to authoritative Swiss public information. It serves a curated,
versioned knowledge base in which every fact is tied to an exact excerpt of
an official page, with a citation. The calling assistant composes the answer
itself; the server never invents one, and when a question is not covered it
says so by name.

This repository holds the **code**: the server, the knowledge-base pipeline
and the tooling around them. The knowledge bases it serves are published
separately, in [swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp).

## The hackathon

Built for the **Swiss {ai} Weeks** hackathon in Zurich, 24 and 25 September
2026, for the challenge **Swiss Grounding MCP**, set by Swisscom's myAI team:

<https://zh.ai-weeks.ch/challenges/swiss-grounding-mcp>

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

## Run the server

The server holds no knowledge: every way of running it names the release it
serves. A release is a pack directory with `release.json` and
`readiness.json`, from a clone of the packs repository or downloaded from
one of its GitHub releases.

With [uv](https://docs.astral.sh/uv/), the published package from PyPI:

```shell
uvx swisstip-mcp --release <packs>/releases/<pack>/release.json --require-ready --transport streamable-http
claude mcp add --transport http swiss-tip http://127.0.0.1:8000/mcp
```

With Docker, the basic image with the pack mounted, or a pack's own image
from the packs repository:

```shell
docker run --rm -p 8000:8000 -v "<packs>/releases/<pack>:/srv/swiss-tip:ro" ghcr.io/swisstip/swiss-tip-mcp:latest
```

From this checkout, after the installation below:

```shell
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --release <packs>/releases/<pack>/release.json --transport streamable-http
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
./.venv/Scripts/python.exe -m pip install -e packages/core -e packages/runtime -e packages/build -e packages/ingestion -e "packages/extraction[office]" -e packages/concepts -e apps/mcp-server -e apps/knowledge-builder -e apps/admin-console
for t in packages/*/tests apps/*/tests; do ./.venv/Scripts/python.exe -m unittest discover -s "$t" || break; done
./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py
```

The loop runs every unit test; the last command runs a client round trip
over stdio against the real server on the synthetic release. On macOS or
Linux, use `.venv/bin/`. The checks of the packs themselves (their suites,
reports, readiness records and catalogues) live in the packs repository.

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

## Repository

| Path | Contents |
| --- | --- |
| `apps/` | `mcp-server`, `knowledge-builder`, `admin-console` |
| `packages/` | `core` (release format, validator, tool contracts), `runtime` (the four operations, lexical and hybrid search), `ingestion`, `extraction`, `build`, `concepts` |
| `docs/` | [Functional specification](docs/product/functional-specification.md) and the architecture documents: [release format](docs/architecture/release-format.md), [tool contracts](docs/architecture/tool-contracts.md), [acceptance gate](docs/architecture/acceptance-gate.md), [extraction](docs/architecture/extraction.md), [concept extraction](docs/architecture/concept-extraction.md), [institutions and basis](docs/architecture/institutions-and-provenance-weights.md), [pipeline](docs/architecture/knowledge-base-pipeline.md), [admin console](docs/architecture/admin-console.md) |
| `docker/`, `compose.yaml`, `Dockerfile` | [Container images](docker/README.md): the basic server image, the semantic image, the slim release image, the embedding sidecar and the OpenCode test image; the root Dockerfile builds the server from this source with a pack as build context |
| `scripts/pypi/` | The three PyPI distributions `swisstip-core`, `swisstip-mcp` and `swisstip-builder` ([publishing](scripts/pypi/README.md)) |
| `config/` | Provider profiles of the concepts package |

Contributor conventions are in [AGENTS.md](AGENTS.md).

## Licence

Apache License 2.0, see [LICENSE](LICENSE). Quoted official texts in the
knowledge releases remain the property of their publishers and are
reproduced only as cited evidence, see [NOTICE](NOTICE).
