# swisstip-mcp-server

**Last update:** 24 September 2026

The Swiss TIP MCP server: the four tools of
[docs/architecture/tool-contracts.md](../../docs/architecture/tool-contracts.md)
over stdio or Streamable HTTP, on one validated knowledge release. It is advertised to clients as
`swiss-tip`. Default lexical serving needs no model, no credentials and no
network. Optional hybrid search uses a local Ollama embedding model;
the release is validated once at startup and any issue stops the
server. The tool semantics live in `packages/runtime`
(`swisstip.runtime.service`); this app only puts them on the wire. The
`initialize` result carries MCP instructions (the release's scope, the
two-call pattern and the languages to search in), and the `search`
description and its `query` field name the same query languages, measured on
the served release (tool-contracts.md, section 4.2).

```shell
./.venv/Scripts/python.exe -m pip install -e packages/core -e packages/runtime -e apps/mcp-server
./.venv/Scripts/python.exe -m unittest discover -s apps/mcp-server/tests
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --release releases/<pack>/release.json --health
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --release releases/<pack>/release.json --print-client-config opencode
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --release releases/<pack>/release.json
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --release releases/<pack>/release.json --transport streamable-http --port 8000
```

The server holds no knowledge of its own: every command names the release
it serves, with `--release` or the environment variable `SWISSTIP_RELEASE`.
The packs of the MVP are published in
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp).

| Option | Effect |
| --- | --- |
| `--release FILE` | Release to serve; required unless `SWISSTIP_RELEASE` names it |
| `--transport stdio\|streamable-http` | Default `stdio`; `streamable-http` serves MCP on `/mcp`, the `--health` payload on `/health` and a short index on `/` |
| `--host HOST` | Interface for `streamable-http`; default `127.0.0.1`, where the endpoint accepts only loopback `Host` and `Origin` headers |
| `--port PORT` | Port for `streamable-http`; default `$PORT`, else 8000 |
| `--health` | Load and validate the release, print counts, jurisdictions, languages, freshness, review statuses and `readiness` (`ready` with the attestation of the release's `readiness.json`, or `candidate` with the reason), exit 0; exit 2 with the reason when the release does not validate |
| `--require-ready` | Refuse to serve, or to report healthy, a release whose `readiness.json` (next to it, written by the knowledge builder's `ready` stage) does not name exactly this file; without the flag a candidate is served with a warning on stderr. The container image runs with it |
| `--print-client-config [generic\|opencode]` | Print a client configuration that starts this server from this checkout |
| `--url URL` | With `--print-client-config`: print a configuration for a remote Streamable HTTP endpoint instead |
| `--log-level LEVEL` | Default `INFO`; one line per call on stderr with tool, status, bytes, latency and release ID |
| `--semantic-index FILE` | Enable optional hybrid search with a validated embedding index for this release |
| `--ollama-url URL` | Local loopback origin; default `http://127.0.0.1:11434` |
| `--semantic-timeout SECONDS` | Local request timeout; default 10 |
| `--semantic-min-score SCORE` | Minimum semantic cosine similarity; experimental default 0.5 |
| `--semantic-candidates N` | Semantic candidates before fusion with lexical ranks; default 10 |
| `--connector URL` | A dataset connector to register (repeatable; default `SWISSTIP_CONNECTORS`, comma-separated): its manifest is read once at startup, every dataset that stands behind a concept of the served release is bound to it, `resolve` then offers the dataset in `lookups` and the fifth tool `lookup` is listed. An unreachable connector is logged, probed again on every `/health` request, and never delays the release's own tools; the health payload lists every connector with its status and the datasets registered and rejected. See [docs/architecture/dataset-connectors.md](../../docs/architecture/dataset-connectors.md) |

## Fetch a pack and run the first tests

`swisstip-quickstart <pack>` ([apps/quickstart](../quickstart/README.md))
fetches a published pack from the packs repository, verifies it against its
readiness record and runs the first tests on it, the round trip of this
server among them (`swisstip.mcp_server.roundtrip`, shared with
`scripts/test/mcp/check_wheel.py`); `--serve` then serves the pack with this
server. From its published package, `uvx swisstip-quickstart <pack>` needs
no clone.

## Run from PyPI with uv

The published package `swisstip-mcp` serves the release of a checkout
without an installation and without Docker:
[uv](https://docs.astral.sh/uv/)'s `uvx` fetches the server and a matching
Python on first use. The package contains no knowledge, so `--release` is
required ([scripts/pypi/README.md](../../scripts/pypi/README.md)); the data
is the pack directory of a clone, or `release.json` and `readiness.json`
downloaded from the repository's GitHub release of that knowledge base.

```shell
uvx swisstip-quickstart <pack>
uvx swisstip-mcp --release releases/<pack>/release.json --require-ready --health
uvx swisstip-mcp --release releases/<pack>/release.json --require-ready --transport streamable-http
claude mcp add swiss-tip -- uvx swisstip-mcp --release /absolute/path/to/releases/<pack>/release.json --require-ready
```

The first line needs no clone at all: the quickstart of the section above,
from its own package `swisstip-quickstart`, fetches the pack into
`.local/packs/<pack>` of the current directory, runs the tests and prints
the commands below with `uvx` in front. The third line serves `http://127.0.0.1:8000/mcp` with
`/health` beside it, like the container. The fourth gives a client a stdio
server of its own; the path is absolute because the client chooses the
working directory. Other clients take the same command and arguments in
their `mcpServers` entry.

Search is lexical. Hybrid search needs an Ollama with the index's model on a
loopback address. Without an installed Ollama, the embedding sidecar image
([docker/README.md](../../docker/README.md)) supplies it, with its port
published on the host's loopback address only:

```shell
docker run -d --rm --name swiss-tip-embeddings -p 127.0.0.1:11434:11434 -e OLLAMA_HOST=0.0.0.0:11434 \
  ghcr.io/swisstip/swiss-tip-ollama:qwen3-embedding-0.6b
uvx swisstip-mcp --release releases/<pack>/release.json --require-ready \
  --semantic-index releases/<pack>/semantic-index.json --transport streamable-http
```

Where an installed Ollama already holds port 11434, publish another port
(`-p 127.0.0.1:11435:11434`) and name it with
`--ollama-url http://127.0.0.1:11435`.

Tested on 17 September 2026 on Windows with uv 0.12.15 and `swisstip-mcp`
0.2.1 on release `mvp-zurich-2026-09-16-v3`: `--require-ready --health`
reported the release ready, 20 s on first use for the download and 6 s
after it; over Streamable HTTP the round trip of `check_server.py --url`
had 0 failures with lexical search and, with the sidecar published on port
11435, 0 failures with `--require-hybrid` and all 19 searches hybrid; a
stdio session started through `uvx` listed the four tools and answered a
search. The test set `UV_CACHE_DIR` to a directory whose path is as long as
the default cache's (`%LOCALAPPDATA%\uv\cache`); a first attempt with a
cache under a path of about 150 characters failed on Windows, because a
nested package file then passes the 260-character path limit.

## Streamable HTTP and the container image

```shell
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --transport streamable-http --port 8000
./.venv/Scripts/python.exe scripts/test/mcp/check_server.py --url http://127.0.0.1:8000/mcp
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --print-client-config opencode --url http://127.0.0.1:8000/mcp
```

The endpoint is stateless and answers with JSON rather than an event stream:
every request stands alone, so any number of replicas can serve it without
session affinity, and the server sends no notifications of its own. The
per-call log line is the same as over stdio; uvicorn writes no access lines.
The endpoint requires no authentication: it serves a read-only release of
public information, and a hosted endpoint relies on its host's instance and
budget limits.

The root [Dockerfile](../../Dockerfile) packages this app with
`packages/core`, `packages/runtime` and a pack's `release.json` at
`/srv/swiss-tip/release.json` with its `readiness.json`, on
`python:3.14-slim`, as user 10001, with none of the build's working files
(fetched pages, text records), no builder, console or Ollama; beside the release it carries `LICENSE`, `NOTICE`
and the pack's user-facing README. The
build runs `--require-ready --health` on the copied release and fails when
it does not validate, has no readiness record naming exactly that file, or
is not the release its build arguments name; the entry point runs with
`--require-ready` as well, and `build_image.py` refuses a candidate before
the build starts. The image README quotes the
release's scope, counts and cited documents, so a new release updates it in
the same change, like COVERAGE.md.
[scripts/container/build_image.py](../../scripts/container/build_image.py)
reads the release ID and content digest from the release, passes them as
build arguments and labels, and tags the image with both and `latest`:

```shell
python scripts/container/build_image.py --image swiss-tip
docker run --rm -p 8000:8000 swiss-tip:latest
docker run --rm -i swiss-tip:latest --transport stdio
docker run --rm swiss-tip:latest --health
```

The entry point starts `streamable-http` on `0.0.0.0` and `$PORT` (8000 in
the image); arguments after the image name are appended, so the last two
lines serve stdio to a local client and print the health report. The image
declares a `HEALTHCHECK` on `/health`. The release image published on GitHub
is built by the manual workflow "Container images" of the packs repository
from the MCP image of [docker/](../../docker/README.md): it runs
the round-trip check against the running container and pushes the tested
tags to `ghcr.io/<owner>/swiss-tip` (`<release_id>`, `content-<digest>`,
`<pack>`). The same workflow builds a slim release image without Ollama
(`<release_id>-slim`, `<pack>-slim`). The images without a release, among
them the embedding sidecar `ghcr.io/<owner>/swiss-tip-ollama`, which
supplies the model from a second container, are built by this repository's
[container-images.yml](../../.github/workflows/container-images.yml);
[compose.yaml](../../compose.yaml) starts the slim image and the sidecar
together. The lexical image of the root Dockerfile is built locally with
`build_image.py`; its `--push`, from a clean checkout after
`docker login ghcr.io`, must name another image with `--image`, because the
default name is the release image's. A new
package on GitHub Container Registry is private until its visibility is
changed in the package settings, so each package under `ghcr.io/swisstip`
is made public after its first push.
The package page on GitHub shows the image's
`org.opencontainers.image.description` label (plain text, at most 512
characters) under the package name. A container package has no README of its
own: a package linked to a repository through
`org.opencontainers.image.source` shows that repository's README instead, so
`build_image.py` sets the repository URL only as
`org.opencontainers.image.url`, and the package is not linked to this
repository (the link is removed in the package settings under "Repository
source"). The image README is `/srv/swiss-tip/README.md` inside the image.

## Testing the published image

```shell
./.venv/Scripts/python.exe scripts/test/container/run_image_test.py
./.venv/Scripts/python.exe scripts/test/container/run_image_test.py --live
./.venv/Scripts/python.exe scripts/test/container/run_image_test.py --require-public --live
```

[run_image_test.py](../../scripts/test/container/run_image_test.py) tests the
image as a registry serves it, not the checkout's server: it checks whether
the tag can be pulled without a login, pulls
`ghcr.io/swisstip/swiss-tip:mvp-zurich`, the pack's moving tag (`--image` for
a release's tag), starts it on a free loopback port and then uses the checkout
only for its clients. It checks the release labels against `/health`, runs
the checks of `check_server.py --url`, confirms from the container's own
log that the calls reached it, and connects OpenCode to the endpoint; `--live`
adds the standing cases through OpenCode (`--case` to choose). Anonymous
access fails the run only with `--require-public`; the harness's assessment
is recorded and never fails it. Output goes to
`.local/container-image-test/run-<timestamp>/`. The run of 15 September 2026
against release `mvp-zurich-2026-09-15-v3` is
[recorded](../../.local/experiments/2026-09-15-container-image-release-v3.md);
the run of 18 September 2026 against `mvp-zurich-2026-09-18-v10`, built on
`swisstip-mcp` 0.2.4, passed every step with `--require-public`, among them
the checks that send the jurisdiction in names (summary under
`.local/container-image-test/run-20260918-155955/`).

## Optional semantic search

A pack's index (`releases/<pack>/semantic-index.json`) contains one vector
per concept of its release, built with an Ollama embedding model such as
`qwen3-embedding:0.6b`, and is bound to that release and the exact model
digest recorded in the index. Use it without rebuilding the document vectors:

```shell
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --release releases/<pack>/release.json --semantic-index releases/<pack>/semantic-index.json
./.venv/Scripts/python.exe -m swisstip.mcp_server.server --release releases/<pack>/release.json --semantic-index releases/<pack>/semantic-index.json --print-client-config opencode
```

Local Ollama must be running with the exact model the index was built with,
installed for query embeddings. Model weights are separate from the
index, and a matching model tag with a different digest is
incompatible. Lexical search remains the default when `--semantic-index`
is omitted. The [runtime README](../../packages/runtime/README.md) records
the format, benchmarks an index and describes regeneration after
release or model changes; experimental rebuilds go under `.local/`.

The printed configuration preserves the semantic options. The knowledge
release and all `resolve` results are unchanged by this option: embeddings
only rank concept IDs during `search`. Inference uses a worker thread so a
local embedding request does not block the stdio event loop.

Missing, incompatible or tampered indexes, an unavailable local model and
failed embedding calls fall back to lexical search. Every search identifies
the actual `retrieval_mode` and `fallback_reason`. `--health` validates the
release and local index without making a model request; its
`model_readiness: not-probed` is explicit. Every semantic query verifies the
installed model digest before embedding. Models are never downloaded by the server.

## Self-check without a model

```shell
./.venv/Scripts/python.exe scripts/test/mcp/check_server.py
```

Starts the server as a subprocess and runs its checks through the MCP client
library (with `--url`, against a running Streamable HTTP endpoint instead,
plus a check on its `/health` route): tool listing, coverage root and topic pages, search and resolve for
the two standing cases (Czech-citizen registration in Zurich, a third-country
national's work permit asked in German), the Swiss
citizen's family question in English, Standard German and Zurich German, the
German forms of the separation and social-assistance questions, the
notification concepts of UAT-1e and 1l, jurisdiction containment, context
gaps, staleness, evidence reads and typed errors.

```shell
./.venv/Scripts/python.exe scripts/test/mcp/check_wallisellen.py
./.venv/Scripts/python.exe scripts/test/mcp/check_wallisellen.py --url http://127.0.0.1:8000/mcp
```

The same kind of round trip on `releases/mvp-wallisellen/release.json`: the
jurisdiction variants of W-UAT-2, `STALE` for the museum and the city
president, `reviewed_only`, the guidance for each status with its disclosures,
`not_served`, the emergency numbers and naturalisation requirements, and
search for every primary acceptance question. A container serves another
release through read-only mounts over its release file and its readiness
record (the entry point runs with `--require-ready`):

```shell
docker run --rm -p 127.0.0.1:8000:8000 \
  -v "$PWD/releases/<pack>/release.json:/srv/swiss-tip/release.json:ro" \
  -v "$PWD/releases/<pack>/readiness.json:/srv/swiss-tip/readiness.json:ro" swiss-tip:latest
```

## Caller proof through OpenCode

```shell
./.venv/Scripts/python.exe scripts/test/mock-mcp/run_opencode_test.py --server real --check-connection
./.venv/Scripts/python.exe scripts/test/mock-mcp/run_opencode_test.py --server real --live
./.venv/Scripts/python.exe scripts/test/mock-mcp/run_opencode_test.py --server real --live --case german-work-permit
./.venv/Scripts/python.exe scripts/test/mock-mcp/run_opencode_test.py --server real --live --case swiss-german-family-permit
./.venv/Scripts/python.exe scripts/test/mock-mcp/run_opencode_test.py --server real --url http://127.0.0.1:8000/mcp --live
```

With `--url` the harness configures OpenCode with a `remote` MCP entry for a
running endpoint (a container or the hosted server) instead of a local
process.

The harness is described in
[scripts/test/mock-mcp/README.md](../../scripts/test/mock-mcp/README.md); the
recorded runs are under `.local/experiments/`.

## Claude Desktop or another MCP client

`--print-client-config` prints the `mcpServers` entry with the absolute paths of
this checkout's interpreter and release. Set `PYTHONIOENCODING=utf-8` and
`PYTHONUTF8=1` in the server environment on Windows, as the printed
configuration does.
