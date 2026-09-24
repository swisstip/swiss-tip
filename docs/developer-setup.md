# Developer setup

## First step: run it with Docker

Before setting anything up locally, run the server as it is published. One
command, and everything is inside the image: the MCP server, the `mvp-zurich`
knowledge base with its readiness attestation and semantic index, and a
CPU-only Ollama with the `qwen3-embedding:0.6b` model, so search is hybrid
from the first request.

```shell
docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich
```

It needs no clone, no Python, no uv, no API key and no model download. The
first run pulls about 850 MB, roughly three minutes on a normal connection,
and the server answers on `http://127.0.0.1:8000/mcp` about 15 seconds after
starting. This is the fastest way to see what the server does, to try it from
an MCP client, and to have something known-good to compare against when a
local build behaves differently. The [README](../README.md) covers it and the
two smaller image variants.

## Then, if you are developing

Use the local toolchain when you are changing the code, running the unit
tests, building or curating a knowledge base, or rebuilding a semantic index —
none of which the published image can do. That means uv and Python 3.14, as
below. The rule of thumb:

| You want to | Use |
| --- | --- |
| Use, demo or evaluate the server | **Docker**, above |
| Change the server or the pipeline, run the tests | **uv** and Python 3.14, below |
| Serve a pack you are building locally | **uv** or the checkout, pointing `--release` at your pack |
| Build a pack, curate facts, review in the console | **uv** with the `build` dependency group |
| Rebuild a semantic index | **uv** plus a local Ollama with the model |

## Quick start with uv

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
and `--url` for the round trip against a running container. Details are in
the [quickstart README](../apps/quickstart/README.md).

Without a clone, the same from the published package `swisstip-quickstart`,
in any directory; the checkout route above tests this source instead:

```shell
uvx swisstip-quickstart mvp-zurich
```

## Serve a release from source or from PyPI

A release is a pack directory holding `release.json`, `readiness.json` and
`semantic-index.json`. In the commands below `<packs>` is a clone of the packs
repository [swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp) (or the
same files downloaded from one of its GitHub releases) and `<pack>` is a pack
name in it; the published one is `mvp-zurich`. After the quick start above,
`<packs>/releases/<pack>` is `.local/packs/mvp-zurich` of this checkout.

```shell
git clone https://github.com/swisstip/swiss-tip-mvp ../swiss-tip-mvp
uvx swisstip-mcp --release ../swiss-tip-mvp/releases/mvp-zurich/release.json --require-ready --transport streamable-http
./.venv/bin/python -m swisstip.mcp_server.server --release ../swiss-tip-mvp/releases/mvp-zurich/release.json --transport streamable-http
```

Use `.venv/Scripts/python.exe` instead of `.venv/bin/python` on Windows. Both
serve **lexical** search on their own: hybrid needs an Ollama with
`qwen3-embedding:0.6b` on the loopback address, and `--semantic-index
<packs>/releases/<pack>/semantic-index.json` passed to the server. The
[server README](../apps/mcp-server/README.md) covers stdio, client
configurations and the semantic options; the Docker images in the
[README](../README.md) bundle the model instead.

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

The [knowledge builder](../apps/knowledge-builder/README.md) describes the
stages, the [admin console](../apps/admin-console/README.md) the review
screens, and the [pipeline document](architecture/knowledge-base-pipeline.md)
the route a pack takes from catalogue to served release.

## Rebuild the semantic index

`semantic-index.json` ships inside a release, so nothing has to be embedded
before serving it. It is rebuilt from the release with the code that built
it, against a local Ollama serving the model:

```shell
./.venv/bin/python -m swisstip.runtime.search_cli index \
    --release <packs>/releases/<pack>/release.json \
    --output <packs>/releases/<pack>/semantic-index.json \
    --timeout 3600
```

`--model` selects another embedding model (default `qwen3-embedding:0.6b`)
and `--base-url` another Ollama address. Every concept of the release is
embedded in one request, so raise `--timeout` well above its default of 10
seconds: a 212-concept release takes about 15 minutes on a CPU and prints
nothing until it finishes. The `benchmark` subcommand of the same CLI
measures concept retrieval against a case set.

Building the index is a step of its own. The knowledge-builder pipeline does
not create it, it checks it, and its `ready` stage refuses a release whose
committed index or attested hybrid run does not match, so a new release means
rebuilding the index before the pack is attested. Embedding is not
bit-reproducible: a rebuild of the same release with the same model produces
an equivalent index with a different `content_sha256`, which is why a
rebuilt index needs the pack to be re-attested.

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
