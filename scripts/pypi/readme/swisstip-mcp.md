# swisstip-mcp

**Last update:** 20 September 2026

The Swiss TIP MCP server gives an AI assistant grounded access to
authoritative Swiss public information. It serves a curated, versioned
knowledge release: facts, each tied to an exact excerpt of an official page
with a citation, and an explicit statement of what is not covered. The
assistant composes the answer; the server never invents one.

Four read-only tools: `get_coverage`, `search`, `resolve`, `get_evidence`.
Transports: stdio, and Streamable HTTP on `/mcp` with a `/health` route.
Serving needs no model, no credentials and no network.

## Knowledge releases

The package contains no knowledge. A knowledge release is two files,
`release.json` and `readiness.json`, published with the GitHub releases of
the packs repository, [swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp).
Download both into one directory; the server validates every hash
and span of `release.json` before it serves, and `--require-ready` refuses a
release without a matching readiness record.

## Use

Python 3.14 or newer. With [uv](https://docs.astral.sh/uv/), `uvx` fetches
the server and a matching Python on first use:

```bash
uvx swisstip-mcp --release ./kb/release.json --require-ready --health
uvx swisstip-mcp --release ./kb/release.json --require-ready
uvx swisstip-mcp --release ./kb/release.json --transport streamable-http --port 8000
```

Claude Code:

```bash
claude mcp add swisstip -- uvx swisstip-mcp --release /path/to/kb/release.json --require-ready
```

Other MCP clients (Claude Desktop, Cursor and similar):

```json
{ "mcpServers": { "swisstip": { "command": "uvx",
  "args": ["swisstip-mcp", "--release", "/path/to/kb/release.json", "--require-ready"] } } }
```

`swisstip-mcp --release <file> --print-client-config opencode` prints a
configuration for OpenCode. `swisstip-server` is the same command under its
earlier name.

## Container

```dockerfile
FROM python:3.14-slim
RUN pip install --no-cache-dir swisstip-mcp
COPY kb/release.json kb/readiness.json /srv/swiss-tip/
ENTRYPOINT ["swisstip-mcp", "--release", "/srv/swiss-tip/release.json", "--require-ready", "--transport", "streamable-http", "--host", "0.0.0.0"]
```

## Related packages

- `swisstip-core`: the release format, validator and tool operations.
- `swisstip-builder`: the workstation tools that build a knowledge release.

Licence: Apache-2.0.
