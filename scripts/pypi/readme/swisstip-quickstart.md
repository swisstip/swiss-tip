# swisstip-quickstart

**Last update:** 24 September 2026

The one command that serves and tests a published Swiss TIP knowledge pack
on your machine. It fetches the pack from its public repository, verifies
every file against the pack's readiness record, and runs the first tests
with no model and no further network: the release validates, the semantic
index is bound to it, the pack's acceptance suite and regression pack are
replayed against it, and a client round trip runs over MCP against the
server. It ends with the commands that serve the pack and connect an MCP
client.

## Use

Python 3.14 or newer. With [uv](https://docs.astral.sh/uv/), `uvx` fetches
the command and a matching Python on first use, in any directory:

```bash
uvx swisstip-quickstart mvp-zurich                # fetch into ./.local/packs/mvp-zurich, check, test
uvx swisstip-quickstart mvp-zurich --serve        # then serve on http://127.0.0.1:8000/mcp
uvx swisstip-quickstart mvp-zurich --no-fetch     # the checks and tests again, offline
uvx swisstip-quickstart --url http://127.0.0.1:8000/mcp   # the round trip against a running server
```

The pack is the argument; the packs of the MVP are published in
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp), and `--ref`
names a commit or tag of it. `--help` lists the rest.

## Related packages

- `swisstip-mcp`: the MCP server the quickstart tests and serves with.
- `swisstip-core`: the release format, validator and tool operations.

Requires `swisstip-core` and `swisstip-mcp` at the same version. Licence:
Apache-2.0.
