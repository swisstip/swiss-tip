# Swiss TIP OpenCode image: the demo's web interface for a server elsewhere

**Last update:** 21 September 2026

A test image, not a release image. It is the [OpenCode](https://opencode.ai)
agent and its web interface and nothing else: no MCP server, no knowledge
release and no embedding model. The Swiss TIP MCP server it asks runs
elsewhere and is named by `SWISSTIP_MCP_URL`. It is the browser interface of
a pack's demo image as a container of its own, so that it can be switched
on and off beside a running server, or run on a laptop against a hosted
one. The agent's configuration, the plugin that refuses every built-in
tool, the start script, the proxy for the routes the interface misses and
the welcome panel are the files of this directory; a pack's demo image, in
the packs repository [swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp), is a pack image plus these files and the
pack's own welcome panel.

## Beside the server of compose.yaml

[compose.yaml](../../compose.yaml) at the repository root holds it as the
service `demo` behind the profile `demo`, so the server and the embedding
sidecar start without it:

```shell
docker compose up -d --wait                  # the MCP server and the embedding sidecar
docker compose --profile demo up -d --wait   # the same, and the web interface on http://127.0.0.1:4096
docker compose stop demo                     # the interface off; the server is not touched
docker compose --profile demo down           # everything
```

The interface has a network identity of its own and reaches the server by
its service name, `http://swiss-tip:8000/mcp`, the image's default. It does
not join the server's network namespace as the sidecar does: a namespace's
ports are published by the container that owns it, so switching the
interface on or off would recreate the server. The server of a slim image is
bound to every interface and accepts that host name. The host port is
published on `127.0.0.1`, because the interface can spend a model budget;
`SWISSTIP_DEMO_BIND=0.0.0.0` with `OPENCODE_SERVER_PASSWORD` set opens it to
the network, `SWISSTIP_DEMO_PORT` sets the port. The interface's user name
is `opencode`, or `OPENCODE_SERVER_USERNAME`.

## Against a hosted server

```shell
docker run --rm -p 127.0.0.1:4096:4096 \
  -e SWISSTIP_MCP_URL=https://<host>/mcp ghcr.io/swisstip/swiss-tip-opencode
```

When that server sits behind a name and a password, as the AWS deployment
of the packs repository (`deploy/aws`) can put it, add `-e
SWISSTIP_MCP_USERNAME=<name> -e SWISSTIP_MCP_PASSWORD=<password>`. The start
script sends them as basic credentials with its wait for `/health` and
writes them into the agent's configuration as the `Authorization` header of
the `swiss_tip` server, in a file only the container's user can read. A
server that answers 401 ends the container at once with one line that says
whether credentials were missing or refused.

The host that serves the jury then carries the server and the sidecar only,
and a demo session costs it a few tool calls, not an agent. That
deployment can also host this image, behind a password.

## What the start script does with the server's address

It refuses an address that is not an `http` or `https` URL with a path, waits
up to two minutes for `/health` beside that path, and ends with an error
naming the address when there is no answer, so a wrong address is a stopped
container with one log line, not an agent without tools. The answer names
the server's pack. The welcome panel of this image's `welcome.json` is
generic: a title and a note, no coverage text and no sample questions,
because the image knows no pack. A panel written for a pack (its `pack`,
coverage text and sample questions) is the pack's demo image's; on a server
with another pack than the panel's, the start script keeps the note and the
registration of the workspace as the browser's project and leaves the rest
out.

## Variables

Those of the demo image, without `PORT`, and:

| Variable | Effect |
| --- | --- |
| `SWISSTIP_MCP_URL` | the MCP endpoint of the server; default `http://swiss-tip:8000/mcp` |
| `SWISSTIP_MCP_USERNAME`, `SWISSTIP_MCP_PASSWORD` | basic credentials for that endpoint, when it asks for them; both or neither |
| `SWISSTIP_REQUIRE_PASSWORD` | when set, the container ends with an error instead of starting without `OPENCODE_SERVER_PASSWORD`; a hosted setup sets it |

The Docker health status is the web interface's alone. With a password the
interface answers 401 to the check, which sends no credentials, and that
counts as alive. The server reports its own health.

## Build

```shell
docker build -f docker/opencode/Dockerfile -t swiss-tip-opencode .
```

The build context is the repository root, for `LICENSE` and `NOTICE`; the
[ignore file](Dockerfile.dockerignore) admits them and the files of this
directory. The base is `python:3.14-slim`, the base of the basic
image and of the sidecar, with the three libraries the proxy imports at the
versions the basic image resolves. The build fails unless `swiss_tip` is the
configuration's only MCP server and the plugin is loaded, and when a
`swisstip-mcp` or an `ollama` is found in the image.

| Build argument | Meaning |
| --- | --- |
| `PYTHON_IMAGE` | the base; default `python:3.14-slim` |
| `OPENCODE_VERSION`, `OPENCODE_PACKAGE`, `OPENCODE_SHA256` | the OpenCode release; a pack's demo image names the same one |
| `DEMO_MODEL` | the default model |

On GitHub, the workflow
[Container images](../../.github/workflows/container-images.yml) builds,
tests and pushes it with the input `images: all` or `images: opencode`, as
`ghcr.io/<owner>/swiss-tip-opencode:<OpenCode version>` and `:latest`. Its
test needs no pack: the server beside it is the basic image on the
synthetic test release ([docker README](../README.md#build-on-github)). It
is tagged with the OpenCode version, not with a release: a new knowledge
release does not change it.

## Licences

OpenCode is MIT-licensed and is not part of the Swiss TIP release. The
licence of the files of this repository and the notices are in
`/usr/share/doc/swiss-tip/`, and this file is
`/usr/share/doc/swiss-tip/opencode.md` in the image.
