# Container images

**Last update:** 25 September 2026

**Start here:** `docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:mvp-zurich` is the
whole server in one command - the full pack image, with the release, the semantic index and
the embedding model inside, so search is hybrid from the first request and nothing else has
to be installed. The table below is the family of images around it.

The images of the Swiss TIP MCP server with `swisstip-mcp` 0.3.4 from PyPI.
The slim MCP image carries no knowledge release and no model; the MCP
image adds a bundled Ollama with `qwen3-embedding:0.6b`. Two images serve a
release in two containers: a slim release image, the slim MCP image plus one
pack's release, and the embedding sidecar, Ollama and the model without a
server; [compose.yaml](../compose.yaml) at the repository root starts them
together. The lexical image built from this checkout's source, with a pack
supplied as a named build context, is the [root Dockerfile](../Dockerfile).
One more image is a test image, not part of that chain, for showing a
knowledge base without an MCP client: the OpenCode image is an agent and a
browser interface for a server that runs elsewhere - the profile `demo` of
`compose.yaml` starts it beside the slim image and the sidecar.

This repository holds no pack. The packs, their pack images (the MCP
image plus a release and its index), the demo image (a pack image plus the
agent and interface of the OpenCode image), the workflow that builds, tests
and pushes the images that hold a release, and the AWS deployment of the
two-container setup live in the packs repository, [swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp). A command below that names
`releases/<pack>` runs in a checkout of that repository, or in any
directory that holds a pack's files.

| Image | Dockerfile | Build context | Contains |
| --- | --- | --- | --- |
| `swiss-tip-mcp:0.3.4-slim` | [mcp-slim/Dockerfile](mcp-slim/Dockerfile) | the repository root | `swisstip-mcp` 0.3.4 only; the release is mounted on `/srv/swiss-tip` |
| `swiss-tip-mcp:0.3.4` | [mcp/Dockerfile](mcp/Dockerfile) | `docker/mcp` | the slim MCP image, plus CPU-only Ollama 0.34.0 on `127.0.0.1:11434` and `qwen3-embedding:0.6b`; no release |
| `swiss-tip:<pack>` | the pack's Dockerfile in the packs repository | `releases/<pack>` | the MCP image, plus `release.json`, `readiness.json`, `semantic-index.json` and the pack's image `README.md`: the release image, labelled with the release ID and content digest |
| `swiss-tip:<pack>-slim` | [slim/Dockerfile](slim/Dockerfile), one for every pack | `releases/<pack>` | the slim release image: the slim MCP image, plus the pack's `release.json`, `readiness.json`, `semantic-index.json` and its image `README.md` where it has one, with the same labels; no Ollama and no model, so lexical search on its own and hybrid search beside the sidecar |
| `swiss-tip-ollama:qwen3-embedding-0.6b` | [ollama/Dockerfile](ollama/Dockerfile) | `docker/ollama` | the embedding sidecar: `python:3.14-slim`, plus the CPU-only Ollama 0.34.0 and the `qwen3-embedding:0.6b` model copied out of the MCP image, on `127.0.0.1:11434`; no server and no release |
| `swiss-tip-demo:<pack>` | the demo Dockerfile in the packs repository | its directory there | test image: a pack image, plus the OpenCode agent and its web interface on port 4096, configured against the MCP server on the container's loopback, with a proxy for the routes the interface needs and the server does not answer, and a welcome panel with the pack's coverage and sample questions; its configuration, plugin, proxy and start script are those of `docker/opencode` |
| `swiss-tip-opencode` | [opencode/Dockerfile](opencode/Dockerfile) | the repository root | test image: `python:3.14-slim`, plus the OpenCode agent, its web interface on port 4096, and the configuration, plugin, proxy and a generic welcome panel from `docker/opencode`; no server, no release and no model - the MCP server is named by `SWISSTIP_MCP_URL` |
| `swiss-tip-calendar-connector:0.3.4` | [calendar-connector/Dockerfile](calendar-connector/Dockerfile) | the repository root | `swisstip-calendar-connector` 0.3.4 only, the first [dataset connector](../docs/architecture/dataset-connectors.md): HTTP on port 8100 for the MCP server, no dataset; bundles are mounted on `/srv/swiss-tip/datasets`, one subdirectory each |
| `swiss-tip-calendar:<pack>` | the pack's calendar Dockerfile in the packs repository | `datasets/<pack>` | the calendar connector image, plus the pack's dataset bundles; the profile `calendar` of `compose.yaml` starts it beside the server |

Semantic search is hybrid: lexical matching fused with the embedding ranking
of concept IDs in `search`. `resolve` and the facts it returns are
unchanged. The server only accepts a loopback Ollama address, so Ollama
either runs in the same container (the MCP images) or in a container
that shares the server's network namespace (the embedding sidecar beside a
slim image or the slim MCP image).

Each Dockerfile has a `Dockerfile.dockerignore` next to it, so a release
image's build context is the files it serves (`release.json`,
`readiness.json`, `semantic-index.json` and the image README where the pack
has one), not the build's working files (fetched pages and text records).
The context can also be a directory with the same
files downloaded from a GitHub release. The slim MCP image is the exception: it
builds from the repository root with only `LICENSE` and `NOTICE` admitted,
and copies them to `/usr/share/doc/swiss-tip/`, outside the release mount
point, so every image built on it carries them.

## Platforms

Every image that serves - both MCP images, the pack and slim release
images, the sidecar and both calendar images - is published for
`linux/amd64` and `linux/arm64` under the same tag, and Docker pulls the
one for its machine: an Apple silicon Mac runs them natively, without
emulation. The Ollama runtime in them has a CPU backend for each x86 and Arm
generation. The two OpenCode test images (`swiss-tip-opencode` and
`swiss-tip-demo`) are `linux/amd64` only, as they carry the linux-x64
OpenCode binary; they are no part of the server.

A local build is for the machine's own platform. Both platforms at once
need the containerd image store (Docker Desktop: Settings, General, "Use
containerd for pulling and storing images") and `--platform
linux/amd64,linux/arm64`; the other platform is built under emulation. The
arm64 variant, built and started under emulation on an amd64 machine on
25 September 2026, passed the pack image's build checks (the four check
searches hybrid, with the same top concepts as on amd64, and 15 of 15
calendars registered) and came up healthy with `--semantic-timeout 120`
after about 150 s. Under emulation one embedding takes 7 to 23 s, against
0.2 to 0.4 s natively, which is why the pack image's check search waits
120 s per call when the target platform is not the build platform, and why
an emulated start needs the longer timeout: the server checks one
embedding before it serves.

## Build

The order matters: each image builds on the previous one.

```shell
docker build -f docker/mcp-slim/Dockerfile -t swiss-tip-mcp:0.3.4-slim .
docker build -t swiss-tip-mcp:0.3.4 docker/mcp
docker build -f docker/opencode/Dockerfile -t local/swiss-tip-opencode:latest .
docker build -f docker/calendar-connector/Dockerfile -t local/swiss-tip-calendar-connector:0.3.4 .
docker build -t local/swiss-tip-ollama:qwen3-embedding-0.6b docker/ollama
docker build -f docker/slim/Dockerfile --build-arg PACK=<pack> -t local/swiss-tip:<pack>-slim <packs>/releases/<pack>
```

The last two are the two-container setup: the sidecar comes out of the
MCP image and a slim image builds on the slim MCP image, so neither needs
a pack image. The OpenCode image needs no other image. `compose.yaml` takes
its images from one prefix, which `SWISSTIP_REGISTRY=local SWISSTIP_PACK=<pack>
docker compose up -d --wait` points at these builds. The pack images and the
demo image are built in the packs repository, on the MCP image and on
the files of `docker/opencode`.

The MCP image's build pulls the model and fails unless its manifest digest is
`ac6da0dfba84...`, the digest the packs' indexes were built with
(build argument `EMBEDDING_MODEL_DIGEST`). A pack build fails when the
release does not validate or has no matching readiness record, and when a
check search on the bundled model does not run hybrid, for example with an
index for another release or another model digest. The check prints each
query's retrieval mode, latency and top concepts. The sidecar build pulls
nothing: it copies the Ollama runtime, the model and the start check out of
the MCP image, starts Ollama once, and fails unless the copied model has
that digest, loads, and the runtime is the named Ollama version. A slim build
fails like a pack build on the release, and when the semantic index was not
built for exactly that release; it runs no search, because it has no model.

| Image | Build arguments |
| --- | --- |
| slim MCP | `PYTHON_IMAGE`, `SWISSTIP_MCP_VERSION`, `PACKAGE_INDEX` (another index than PyPI to take `swisstip-mcp` from, with PyPI behind it for the dependencies; empty is PyPI) |
| MCP | `BASE_IMAGE`, `OLLAMA_IMAGE`, `EMBEDDING_MODEL`, `EMBEDDING_MODEL_DIGEST` |
| slim | `BASE_IMAGE` (the slim MCP image), `PACK` (for the labels), `RELEASE_ID`, `RELEASE_CONTENT_SHA256`, `REVISION` (the release ID and digest, when given, must match `release.json`) |
| sidecar | `MCP_IMAGE`, `PYTHON_IMAGE`, and `EMBEDDING_MODEL`, `EMBEDDING_MODEL_DIGEST`, `OLLAMA_VERSION`, which name what the MCP image holds and are checked against it, not applied |
| OpenCode | `PYTHON_IMAGE`, `OPENCODE_VERSION`, `OPENCODE_PACKAGE`, `OPENCODE_SHA256`, `DEMO_MODEL`; a pack's demo image names the same OpenCode release |
| calendar connector | `PYTHON_IMAGE`, `SWISSTIP_VERSION`, `PACKAGE_INDEX`, as for the slim MCP image |

## Build on GitHub

Two workflows named "Container images" build the images, each on one
runner, test them and push them to `ghcr.io/<owner>/`. The one of this
repository, [container-images.yml](../.github/workflows/container-images.yml),
builds the images that hold no release: both MCP images, the sidecar and
OpenCode. The one of the packs repository builds the images that hold one,
on the images pushed from here: the pack images, the slim images (with
[slim/Dockerfile](slim/Dockerfile) and `compose.yaml` of a checkout of this
repository) and the demo image. Both run by hand only (Actions, "Container
images", "Run workflow").

| Input | Values |
| --- | --- |
| `images`, here | `all` (both MCP images, sidecar, OpenCode, calendar connector), `mcp-slim`, `mcp`, `ollama` (the sidecar), `opencode`, `calendar` |
| `images`, in the packs repository | `all` (every pack, then the demo), `packs`, a pack's name, `demo` |
| `swisstip_mcp_version` | the `swisstip-mcp` version, which is the tag of both MCP images: built here, pulled there; default `0.3.4` |
| `package_index` | `pypi`, or `testpypi` to rehearse a version that is not released yet; in the packs repository it names where the check client comes from |
| `code_ref`, in the packs repository | the branch, tag or commit of this repository whose slim Dockerfile and `compose.yaml` are used; default `main` |
| `push` | push the tested images; off builds and tests only |

| Image | Tags in `ghcr.io/<owner>/` |
| --- | --- |
| slim MCP | `swiss-tip-mcp:<version>-slim`, and `swiss-tip-mcp:latest-slim` from the default branch, from PyPI, for a final version |
| MCP | `swiss-tip-mcp:<version>`, and `swiss-tip-mcp:latest` under the same rule. Both variants are one package, and the `-slim` suffix sits in the tag, as it does for the release images |
| pack (the release image) | `swiss-tip:<release_id>`, `swiss-tip:content-<first 12 hex digits of the content digest>`, `swiss-tip:<pack>` (the pack's moving tag; no `latest`, so a pull names a release or a pack) |
| slim (the release image without Ollama) | `swiss-tip:<release_id>-slim`, `swiss-tip:<pack>-slim` (the moving tag `compose.yaml` names), in the package of the pack image |
| sidecar | `swiss-tip-ollama:qwen3-embedding-0.6b` (the model tag with a hyphen, the tag `compose.yaml` names), `swiss-tip-ollama:qwen3-embedding-0.6b-<first 12 hex digits of the model digest>` |
| demo (the test image) | `swiss-tip-demo:<release_id>`, `swiss-tip-demo:<pack>`, and `swiss-tip-demo:latest` from the default branch (unlike the release image: the demo is built on one pack, so `latest` names it) |
| OpenCode (the test image without a server) | `swiss-tip-opencode:<OpenCode version>` and `swiss-tip-opencode:latest`, the tag `compose.yaml` names, both from any branch: the image carries no release and no `swisstip` package, so neither tag is a release pointer; it holds no release, so a new release does not change it |
| calendar connector | `swiss-tip-calendar-connector:<version>`, and `swiss-tip-calendar-connector:latest` under the MCP image's rule; tested with the synthetic bundle of `apps/calendar-connector/tests/fixtures` mounted and the conformance test `scripts/test/connector/check_connector.py --url` |
| calendar (a pack's connector image) | `swiss-tip-calendar:<pack>`, the tag `compose.yaml` names, and `swiss-tip-calendar:<pack>-<dataset version>`, built in the packs repository on the calendar connector image |

Within a run, an image built earlier is the base of the next one; an image
not selected is pulled from `ghcr.io`. So the first run is `all` with `push`
on here, then `all` in the packs repository, and a later run there naming
one pack rebuilds only that pack on the pushed MCP image. A new
`swisstip-mcp` version is a run here and then a run there. The packs
repository's workflow pulls with its own token, so it must be able to read
`swiss-tip-mcp` and `swiss-tip-ollama`: the packages
are public, or each names the packs repository under "Manage Actions access"
in its package settings.

A version that is not on PyPI yet is rehearsed with `package_index:
testpypi`. The slim MCP image then takes `swisstip-mcp` from TestPyPI, with
PyPI as the second index for the dependencies, which are not published on
TestPyPI; every other image is built on it and installs nothing.

The branch a run was started from ("Use workflow from") decides two things,
in both repositories:

- **The default branch builds from PyPI only.** `package_index: testpypi`
  ends a run started from it with an error, so nothing built on the default
  branch ever comes from a package that no tag published. A rehearsal is
  started from another branch.
- **`latest` is pushed from the default branch only**, and here only from
  PyPI and for a final version (digits and dots, so not `0.3.0rc1`). It is
  what a pull without a tag gets, so it names a release. Every other run
  pushes its version tags and leaves `latest` where it is.

The second rule covers the images that carry a `swisstip` package. The
OpenCode image carries none and holds no release: `package_index` never
reaches its build, and the version above neither tags it nor enters it. Its
`latest` is a moving pointer to a test image, as the sidecar's model tag is,
so it is pushed from every branch and `compose.yaml` can always pull it. The
demo image is the other way round, because it carries a release: its
`latest` follows the rule.

The version tags are pushed as usual, so the packs repository rehearses on
them by naming the same version, with `package_index: testpypi` there too,
which only tells its check client where to find that version.

No test here reads a pack. The release served is the synthetic test release
of `apps/mcp-server/tests/fixtures` with a test readiness record, written by
[synthetic_pack.py](../scripts/test/container/synthetic_pack.py). Tests
before the push, here: the slim MCP image with that release mounted must pass
the round trip of the package build
([check_wheel.py](../scripts/test/mcp/check_wheel.py) `--url`), which also
shows that the image accepted the readiness record, because it serves with
`--require-ready`; the MCP image's build pulls the model and checks its digest;
the sidecar on its own must report healthy, which means the model is loaded.

A pack selection in the packs repository builds both release images of the
pack, the pack image and the slim image. Tests before the push, there: for
each pack image its build check, `search.configured_mode: hybrid` in
`/health` and, for the pack that has one, the round trip; each slim image is
started with the sidecar of `ghcr.io` by
`docker compose up -d --wait` on this repository's `compose.yaml`, must
report `search.configured_mode: hybrid`, must run a check search hybrid from
inside the server's container (`check_semantic.py`, so the sidecar answers
on loopback with the index's model digest) and, for that pack, must pass
the round trip with `--require-hybrid`, which fails on any search that is
not hybrid. The images of a private repository are private packages: pulling
them needs `docker login ghcr.io` with a token with `read:packages`, or the
package made public in its settings; `swiss-tip-ollama` is a package of its
own and starts private like every new package. The lexical image of the root
Dockerfile has no workflow; it is built locally with
`scripts/container/build_image.py --pack-dir <packs>/releases/<pack>`, whose
default image name `swiss-tip` is the release image's, so a local push must
name another image (`--image`).

The demo image is a test image, not the product. `all` in the packs
repository builds it last, on the pack image of the same run; `demo` alone
builds it on the pack image of `ghcr.io`, so that pack must have been pushed
before.
Its test starts the container and reads the interface's own routes with
[check_interface.py](opencode/check_interface.py): one project to open a
session in, `swiss_tip` connected, a default model set, the welcome panel
with its questions; then again with `OPENCODE_SERVER_PASSWORD`, where the
container must report healthy and the interface must answer 401 without
credentials. No question is asked, which would spend a free model's budget
and make the run depend on a provider. Only the layers OpenCode and Git add
are new bytes in the registry; the rest are the pack image's.

The OpenCode image is a test image too. `all` here builds it last and
`opencode` alone builds it; it has no base among the other images. It is
tested as `compose.yaml` runs it, with `docker compose --profile demo up -d
--wait`, with the server replaced by the slim MCP image of the same run or of
`ghcr.io` on the synthetic release
([compose.synthetic.yaml](../scripts/test/container/compose.synthetic.yaml));
search is lexical there, so the sidecar is left out. The check is the same,
with and without a password and with `--questions none`, since the generic
panel carries none, and the run fails if switching the interface on
restarted the server's container. Its package `swiss-tip-opencode` starts
private like every new package.

## Run the slim MCP image

Mount a release directory with `release.json` and `readiness.json`; the
server refuses a release without a matching readiness record. Arguments
after the image name are added to the server's command line.

Lexical search:

```shell
docker run --rm -p 8000:8000 -v "$PWD/releases/<pack>:/srv/swiss-tip:ro" swiss-tip-mcp:0.3.4-slim
docker run --rm -i -v "$PWD/releases/<pack>:/srv/swiss-tip:ro" swiss-tip-mcp:0.3.4-slim --transport stdio
```

Hybrid search with the embedding sidecar: it joins the server's network
namespace, so both share `127.0.0.1`, and brings the model with the digest
the packs' indexes were built with.

```shell
docker run -d --name swiss-tip -p 8000:8000 \
  -v "$PWD/releases/<pack>:/srv/swiss-tip:ro" swiss-tip-mcp:0.3.4-slim \
  --semantic-index /srv/swiss-tip/semantic-index.json
docker run -d --name swiss-tip-embeddings --network container:swiss-tip swiss-tip-ollama:qwen3-embedding-0.6b
```

Any other Ollama container serves the same way, in either direction of the
join (with the server joining Ollama's namespace, the port is published on
the Ollama container); in Kubernetes, both containers in one pod. Its model
must have the digest the index was built with (`ollama list` shows the first
twelve digits as its ID, `ac6da0dfba84` for the packs' indexes);
otherwise every search falls back to lexical and says so. It should run with
`OLLAMA_KEEP_ALIVE=-1`, as the sidecar does, which keeps the model loaded
after the first query; without it Ollama unloads it after five minutes and
the next query pays the load time again, against the server's ten-second
`--semantic-timeout`.

When `--semantic-index` is given and no Ollama answers, the server still
serves: every search reports `retrieval_mode: lexical-fallback` with the
reason.

## Run a pack image

A pack image is built in the packs repository on the MCP image; its
entrypoint is this directory's `with-ollama` (docker/mcp).

```shell
docker run --rm -p 8000:8000 swiss-tip:<pack>       # MCP on http://127.0.0.1:8000/mcp, /health beside it
docker run --rm -i swiss-tip:<pack> --transport stdio
```

A pack image with datasets (`mvp-zurich`) also carries the calendar
connector and its bundles: its entrypoint starts `with-calendar` first,
which runs the connector on the container's loopback address, waits for it
and registers it through `SWISSTIP_CONNECTORS`, so the server lists
`lookup` from the first request; `-e SWISSTIP_CONNECTORS=` leaves it out.

At start, `with-ollama` starts Ollama, checks the model digest and loads the
model, which stays loaded (`OLLAMA_KEEP_ALIVE=-1`); then the server starts.
If Ollama does not become ready, the server still starts and every search
reports `retrieval_mode: lexical-fallback` with the reason. `/health` shows
`search.configured_mode: hybrid` when the index loaded.

Ollama runs with cloud features off (`OLLAMA_NO_CLOUD=1`), and serving needs
no network: the pack images answer hybrid searches over stdio with
`--network none`. Its log is filtered to its own structured lines and
errors; set `SWISSTIP_OLLAMA_LOG=all` for everything, including llama-server.

## Run a slim image with the embedding sidecar

The release and the hybrid search of a pack image in two containers: the
server in a slim release image, Ollama with the model in the sidecar.
[compose.yaml](../compose.yaml) at the repository root starts both from
`ghcr.io`; the file is all it needs, so it also works without a clone.

```shell
docker compose up -d --wait         # MCP on http://127.0.0.1:8000/mcp, /health beside it
docker compose logs -f swiss-tip    # one line per tool call
docker compose down
```

`--wait` returns when the server answers and the model is loaded.
`SWISSTIP_PACK=<pack>` names the pack to serve and is required, since this
repository holds no pack; `SWISSTIP_PORT` sets the host port and
`SWISSTIP_REGISTRY` another image prefix than `ghcr.io/swisstip`. The same
without Compose:

```shell
docker run -d --name swiss-tip -p 8000:8000 ghcr.io/swisstip/swiss-tip:<pack>-slim \
  --semantic-index /srv/swiss-tip/semantic-index.json
docker run -d --name swiss-tip-embeddings --network container:swiss-tip \
  ghcr.io/swisstip/swiss-tip-ollama:qwen3-embedding-0.6b
```

How the two are connected:

- **One network namespace.** The sidecar joins the server's
  (`network_mode: "service:swiss-tip"`, `--network container:swiss-tip`), so
  the server finds the model on `127.0.0.1:11434`, the only kind of address
  it accepts, with its default `--ollama-url`. The sidecar publishes no
  port and Ollama binds to the loopback address, so the model answers
  neither the host nor another container on the same Docker network.
- **The server does not depend on the sidecar.** It owns the namespace and
  the published port and starts first. While the model loads, and when the
  sidecar is stopped, every search reports `retrieval_mode:
  lexical-fallback` with the reason and is served; when the sidecar is back,
  the next search is hybrid. The other way round the namespace is lost:
  `docker compose restart swiss-tip` restarts the sidecar with the server,
  while after a plain `docker restart swiss-tip` the sidecar must be
  restarted too, and until then search falls back.
- **Without the sidecar** a slim image is a lexical server: started without
  `--semantic-index`, `/health` reports `search.configured_mode: lexical`
  and no search names a fallback.
  `docker run --rm -p 8000:8000 ghcr.io/swisstip/swiss-tip:<pack>-slim`.
- **The model.** At start the sidecar checks that its model has the digest
  the packs' indexes name, loads it, and keeps it loaded
  (`OLLAMA_KEEP_ALIVE=-1`); a model that fails the check ends the container
  with an error. Its Docker health status means "the model is loaded". It
  runs with cloud features off (`OLLAMA_NO_CLOUD=1`), as an unprivileged
  user, with the log filter of the pack images (`SWISSTIP_OLLAMA_LOG=all`
  for everything). The tag with the model digest pins the model an index
  needs.
- **stdio.** A client that starts the server as a local process starts one
  container, so hybrid search over stdio is the pack image's; a slim image
  serves stdio with lexical search.
- **A server outside Docker.** For a server on the host, for example
  `uvx swisstip-mcp` from PyPI, the sidecar publishes its port on the host's
  loopback address instead of joining a namespace:
  `docker run -d --rm -p 127.0.0.1:11434:11434 -e OLLAMA_HOST=0.0.0.0:11434 ghcr.io/swisstip/swiss-tip-ollama:qwen3-embedding-0.6b`.
  Inside the container Ollama then listens on every interface, and the
  publication on `127.0.0.1` keeps it off the network
  ([server README](../apps/mcp-server/README.md#run-from-pypi-with-uv)).

## Run the demo image

A test image for showing a knowledge base without an MCP client: a pack
image with the OpenCode agent and its web interface, built in the packs
repository from the files of `docker/opencode`. Details, variables and
licences are in its README there, which the image carries as
`/usr/share/doc/swiss-tip/demo-opencode.md`.

```shell
docker run --rm -p 127.0.0.1:4096:4096 swiss-tip-demo:<pack>   # GUI on http://127.0.0.1:4096
```

The start script starts the MCP server of the pack image on the container's
loopback, waits until it answers `/health`, starts `opencode web` on the
loopback too, and publishes port 4096 through a small proxy: the web
interface is the desktop client's and asks for routes under `/api` that the
standalone server does not answer, among them the project list, the MCP
list and the default model, so without them its project list is empty, "New
session" is disabled, no model and no MCP server are shown and opening a
session fails. The proxy answers them from the server's own routes without
the prefix and passes everything else through, and the workspace is a Git
repository so that it is a project at all; the image README has the details. The baked
configuration `/etc/swiss-tip/opencode.json` gives the agent the loopback
MCP server as its only tool source and loads a plugin that refuses every
built-in tool call (no shell, no file access, no web fetch), with the
`residence-assistant` prompt of the acceptance runs. The tools are refused
rather than turned off because the OpenCode Zen free tier rejects a request
without them; the image README has the details. The build fails if that
configuration stops naming a loopback MCP server or stops loading the plugin.

Two differences from the pack images. The image needs the network at run
time: only the knowledge base is local, and the model is called over the
internet. And it can spend a model budget, so the port is published on
`127.0.0.1` above; on a shared network set `OPENCODE_SERVER_PASSWORD`. It
carries no credentials: without a key OpenCode uses the public OpenCode Zen
key, which leaves the free models enabled, and the default model
`opencode/ling-3.0-flash-fin-free` is the one the acceptance runs used, with
the same rate limits.

With `OPENCODE_SERVER_PASSWORD` the agent answers every request without
credentials with 401, its own health route included. The start script takes
that answer as the agent being up, the proxy sends the password with its two
calls at start and with no visitor's request, and the image's health check
([opencode_health.py](opencode/opencode_health.py)) sends none and takes
401 as alive.

On GitHub the packs repository's workflow builds, tests and pushes it with
`all` or `demo`; see above.

## Run the OpenCode image beside the server

The same agent and web interface in a container of its own, for the server
of the two-container setup. It is the service `demo` of
[compose.yaml](../compose.yaml), behind the profile `demo`, so the server
and the sidecar start without it. Details are in its
[README](opencode/README.md).

```shell
docker compose --profile demo up -d --wait   # the server, the sidecar, and the GUI on http://127.0.0.1:4096
docker compose stop demo                     # the GUI off; the server is not touched
docker compose --profile demo down           # everything
```

- **Its own network identity.** It reaches the server as
  `http://swiss-tip:8000/mcp` on the Compose network and publishes its own
  port, so it comes and goes without the server being recreated. Joining the
  server's namespace, as the sidecar does, would put port 4096 on the
  server's container. The server of a slim image is bound to every
  interface and accepts that host name.
- **Any Swiss TIP server.** `SWISSTIP_MCP_URL` names it:
  `docker run --rm -p 127.0.0.1:4096:4096 -e SWISSTIP_MCP_URL=https://<host>/mcp ghcr.io/swisstip/swiss-tip-opencode`
  runs the interface on a laptop against a hosted server. The start script
  refuses what is not an `http` or `https` address, waits for `/health`
  beside it and ends with an error when there is no answer. For a server
  behind a name and a password, `SWISSTIP_MCP_USERNAME` and
  `SWISSTIP_MCP_PASSWORD` are sent as basic credentials, by the wait and by
  the agent.
- **The pack.** `/health` names the server's pack. The welcome panel of
  this image is generic (a title and a note); a pack's demo image carries
  the panel with that pack's coverage text and sample questions.
- **The port.** It is published on `127.0.0.1`, because the agent can spend
  a model budget. `SWISSTIP_DEMO_BIND=0.0.0.0` opens it to the network and
  wants `OPENCODE_SERVER_PASSWORD` set with it; `SWISSTIP_DEMO_PORT` sets the
  host port. `OPENCODE_SERVER_USERNAME`, `OPENCODE_API_KEY` and
  `SWISSTIP_DEMO_MODEL` are passed on when they are set where Compose runs.
- **A name and a password for the MCP endpoint.** The server has none of its
  own. A proxy in front of it asks for them: the AWS deployment of the packs
  repository (`deploy/aws`) puts Caddy there, with `SWISSTIP_MCP_USERNAME`
  and `SWISSTIP_MCP_PASSWORD`, on AWS or on any host with Docker.

## Measured

Measured on 15 September 2026, the rows of the slim image and the sidecar on
17 September 2026 and those of the OpenCode image on 18 September 2026, on
one Windows laptop (Intel Core i7-13700H,
Docker Desktop, no GPU in the containers). Image sizes are those
`docker images` lists; what a pull downloads is less than half of it (about
700 MB for a pack image, 683 MB for the sidecar, 62 MB for a slim image):

| Measure | Value |
| --- | --- |
| Image size: slim MCP | 248 MB |
| Image size: MCP and each pack image | 1.6 GB |
| Image size: slim release image | 254 MB (`mvp-zurich`), 251 MB (`mvp-wallisellen`) |
| Image size: embedding sidecar | 1.54 GB |
| Image size: OpenCode | 600 MB |
| Memory of a running pack container | 1.2 GiB |
| Memory of the two containers: slim server, sidecar | 63 MiB, 1.2 GiB |
| Time from `docker run` to a healthy `/health`: slim MCP, pack | about 1 s, about 4 s |
| Time of `docker compose up -d --wait` until the server answers and the model is loaded, images present | about 7 s |
| Time of `docker compose --profile demo up -d --wait` on the running server until the web interface is healthy | about 20 s |
| `search` latency, lexical | 22 to 43 ms per query, through MCP |
| `search` latency, hybrid, model loaded | 73 to 390 ms per query |
| `search` latency, hybrid, slim image with the sidecar, model loaded | 151 to 464 ms per query, through MCP |
| First hybrid query with a separate Ollama that has not loaded the model | 2.0 s |
| First hybrid query after the sidecar was stopped and started again | 1.7 s |
| Semantic index build for the 28 Wallisellen concepts | 29 s |

These numbers come from one machine and do not predict a hosted instance.

## Tests run against the images

- `scripts/test/mcp/check_server.py --url`, the Zurich round trip, with
  0 failures against: the Zurich pack image (hybrid), the slim MCP image with
  the Zurich release mounted (lexical), and the slim MCP image joined to a
  separate `ollama/ollama:0.34.0` container (hybrid).
- `search` through MCP over Streamable HTTP and over stdio on both packs, in
  German, English, French, Italian and Chinese; the pack images and the
  lexical slim MCP image with `--network none`.
- The slim MCP image with `--semantic-index` and no Ollama: `lexical-fallback`
  with its reason. Without a mounted release: exits with an error naming
  the missing file.
- `scripts/test/mcp/check_wallisellen.py --url`, the Wallisellen round trip,
  with 0 failures against a local build of the Wallisellen pack image on
  release `mvp-wallisellen-2026-09-16-v3` (hybrid; the check that the bare
  municipality name matches nothing applies to lexical matches there, since
  the embedding model ranks every concept of the pack near that name).
- The two-container setup on 17 September 2026, on local builds: the slim
  images of `mvp-zurich-2026-09-16-v3` and `mvp-wallisellen-2026-09-16-v3`
  on `ghcr.io/bobrovsky420/swiss-tip-mcp:0.2.1`, and the sidecar out of
  `ghcr.io/bobrovsky420/swiss-tip-semantic:0.2.1` (the names those images
  had that day, before the renaming of 21 September 2026). With
  `docker compose up -d --wait` on `compose.yaml`:
  `check_server.py --url --require-hybrid` with 0 failures and all 19
  searches hybrid, and with `SWISSTIP_PACK=mvp-wallisellen`
  `check_wallisellen.py --url` with 0 failures. The same Zurich round trip
  with 0 failures against the two `docker run` commands and against the
  slim MCP image with the release mounted and the sidecar joined. The Zurich
  slim image alone: `search.configured_mode: lexical`, the round trip with 0
  failures, and `--require-hybrid` failing, as it must. With the sidecar
  stopped a search reported `lexical-fallback` with the reason and was
  served; started again, the next search was hybrid. From another container
  on the Compose network the server's port answered and port 11434 was
  refused. `docker compose restart swiss-tip` restarted the sidecar too and
  search stayed hybrid; after a plain `docker restart` of the server, search
  fell back until the sidecar was restarted. The workflow's test of the slim
  images (Compose on the run's images, `check_semantic.py` from inside the
  server's container, the round trip) passed as a local run of the same
  commands for both packs. Not tested: the new workflow steps on GitHub, and
  a pull of these images from `ghcr.io`, where they exist only after a
  workflow run.
- The demo image on 16 September 2026, built on
  `ghcr.io/bobrovsky420/swiss-tip:mvp-zurich-2026-09-15-v3` (1.85 GB): the
  web interface answered on port 4096 about 8 s after `docker run`, the
  container reported healthy, `GET /mcp` reported `swiss_tip connected`, and
  the Zurich registration question asked in German through the agent's API
  was answered with 15 September 2026 from two `swiss_tip_search` calls and
  one `swiss_tip_resolve` (scope `CH-ZH-261`, `SUPPORTED`, release
  `mvp-zurich-2026-09-15-v3`) on the free default model. One question on one
  day is a smoke test, not an acceptance run. The web interface was then
  opened in a browser on the same machine: a session opened, the model and
  the `swiss_tip` server were shown as connected, and a question was
  answered. It took the Git workspace and the proxy to get there. Without
  them the interface has no project, its "New session" control renders
  disabled, and the calls it makes for the MCP list, the default model and
  the state of the working tree fail.

- The OpenCode image on 18 September 2026, a local build, beside
  `ghcr.io/bobrovsky420/swiss-tip:mvp-zurich-slim` (release
  `mvp-zurich-2026-09-18-v10`) and the sidecar of `ghcr.io`, with
  `docker compose --profile demo up -d --wait` on `compose.yaml`: all three
  containers healthy, `check_interface.py` passed without a password and,
  after the interface was recreated with one, with it (401 without
  credentials), and the server's container kept its start time through both.
  The Zurich registration question asked through the agent's API was
  answered with both limits from two `swiss_tip_search` calls and one
  `swiss_tip_resolve` (`SUPPORTED`), which the server logged as calls of the
  release; one question is a smoke test, not an acceptance run. With
  `SWISSTIP_PACK=mvp-wallisellen` the panel was reduced to its title and
  note, with no questions. `SWISSTIP_MCP_URL=ftp://nowhere` and
  `SWISSTIP_REQUIRE_PASSWORD=1` without a password each ended the container
  with one error line. The workflow's step for the image passed as a local
  run of the same commands. The demo image, rebuilt with the shared start
  script on `ghcr.io/bobrovsky420/swiss-tip:mvp-zurich-2026-09-18-v10` and
  started with `OPENCODE_SERVER_PASSWORD`, reported healthy and passed the
  same check; before this change it never opened port 4096 with a password,
  because the start script waited for a 200 from the agent's health route,
  which answers 401 then. With `SWISSTIP_MCP_USERNAME` and
  `SWISSTIP_MCP_PASSWORD` against a server behind Caddy's basic credentials
  (the AWS deployment of the packs repository, "Tested and not tested") the
  image reported `swiss_tip connected`, and ended with one error line when
  they were missing, refused or incomplete. Not tested: the new workflow steps on GitHub, a
  pull of `swiss-tip-opencode` from `ghcr.io`, where it exists only after a
  workflow run, and the web interface of this image in a browser.

- The `package_index` path on 21 September 2026, locally: the slim MCP image
  built with `SWISSTIP_MCP_VERSION=0.3.0rc1` and
  `PACKAGE_INDEX=https://test.pypi.org/simple/` carried `swisstip-mcp` and
  `swisstip-core` 0.3.0rc1, reported the synthetic release as ready and
  passed `check_wheel.py --url` with 0 failures; a build without the
  argument still took 0.2.5 from PyPI. The workflow's tag logic was
  exercised on its own over every combination of branch, index and version:
  `latest` is pushed for the default branch with PyPI and a final version
  and in no other case, and a run started from the default branch with
  `testpypi` ends with an error. Not tested: the workflow's TestPyPI path on
  GitHub, and a pack image built on a rehearsed base.
- The tests of this repository's workflow on 21 September 2026, as a local
  run of the same commands on local builds with `swisstip-mcp` 0.2.5: the
  slim MCP image with the synthetic pack mounted reported the release `ready`
  and passed `check_wheel.py --url` with 0 failures; the OpenCode image,
  started by `docker compose --profile demo up -d --wait swiss-tip demo`
  with `compose.synthetic.yaml`, passed `check_interface.py --questions
  none` without a password and, recreated with one, with it (401 without
  credentials), the server's container kept its start time, and the sidecar
  was not started. Not tested: either workflow on GitHub after the split,
  and the packs repository's pulls of the slim MCP image, the MCP image
  and the sidecar from `ghcr.io` with its own token.

## Rebuilding an index

A semantic index must be rebuilt when the release changes or the model
digest changes. Build it with the MCP image, so that it matches the
model it is served with:

```shell
docker run --rm -v "$PWD/releases/<pack>:/kb" swiss-tip-mcp:0.3.4 \
  python -m swisstip.runtime.search_cli index \
  --release /kb/release.json --output /kb/semantic-index.json --timeout 600
```

`--timeout 600` is needed on CPU: the index sends every concept in one
embedding batch, which takes longer than the ten-second default.
