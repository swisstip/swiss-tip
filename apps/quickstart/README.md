# swisstip-quickstart

**Last update:** 24 September 2026

The one command a first-time user needs to serve and test a published pack:
`swisstip-quickstart <pack>` fetches the pack from the packs repository,
verifies it against its readiness record and runs the first tests on it,
with the server of `apps/mcp-server`. Published as `swisstip-quickstart`,
so `uvx swisstip-quickstart <pack>` runs it without an installation and
without a clone; in a checkout, `uv run swisstip-quickstart <pack>` runs it
on the checkout's own code. This package knows no pack by name: the pack is
the argument, or `SWISSTIP_PACK`, the name `compose.yaml` takes as well.

```shell
./.venv/Scripts/python.exe -m pip install -e packages/core -e packages/runtime -e apps/mcp-server -e apps/quickstart
./.venv/Scripts/python.exe -m unittest discover -s apps/quickstart/tests
./.venv/Scripts/swisstip-quickstart mvp-zurich                                  # fetch into .local/packs/mvp-zurich, check, test
./.venv/Scripts/swisstip-quickstart mvp-zurich --serve                          # then serve on http://127.0.0.1:8000/mcp, with the calendars
./.venv/Scripts/swisstip-quickstart mvp-zurich --no-fetch                       # the checks and tests again, offline
./.venv/Scripts/swisstip-quickstart mvp-zurich --ref mvp-zurich-2026-09-24-v5   # a tag or commit of the packs repository
./.venv/Scripts/swisstip-quickstart --url http://127.0.0.1:8000/mcp             # the round trip against a running server
```

## What it fetches

The packs repository publishes each pack under `releases/<pack>/`: the
release, its readiness record, its semantic index and its acceptance suite.
The command resolves `--ref` (default `main`) to one commit through the
GitHub API and fetches every file at that commit, so a push in between
cannot mix two releases; when the API does not answer (it allows 60 requests
an hour per address without a `GITHUB_TOKEN`), the files are fetched at the
ref itself and the hash checks still hold. It reads the readiness record
first and takes what the record attests: `release.json` must hash to
`release_sha256`, `semantic-index.json` to the index binding and
`acceptance.yaml` to the suite binding; `regression.yaml` and the pack's
README come along when the pack has them, and so do the pack's dataset
bundles, `datasets/<pack>/<dataset>/dataset.json` at the same commit, listed
through the GitHub API (when it does not answer, the bundles of an earlier
fetch are kept). The files land in
`<packs-dir>/<pack>/` (default `.local/packs/<pack>/`, outside Git) with a
`source.json` naming the repository, the ref, the commit and the release;
a file whose hash already matches is not downloaded again, so a second run
downloads nothing but the two unhashed files.

## What it tests

The tests need no model and no further network. One line per check, `ok`,
`FAIL`, `skip` or `info`, then the number of failures, which is the exit
code (2 when the pack could not be fetched):

- the release validates, with its counts, and its readiness record attests
  exactly this file, the check `--require-ready` makes;
- the attested semantic index, when there is one, is bound to exactly this
  release, the check the slim release image makes at build time;
- the dataset bundles, when the pack has any, validate and bind to
  concepts of the release, with the server's own registration;
- the acceptance suite is the one the record attests, and it is replayed
  against the release with the code of the pipeline's `accept` stage; the
  regression pack, when the pack has one, is replayed lexically after it,
  quarantined cases counted apart;
- a client round trip over MCP stdio against the server started from this
  environment, the round trip of `scripts/test/mcp/check_wheel.py`
  (`swisstip.mcp_server.roundtrip`).

It ends with the commands that serve the pack, print a client configuration
and run hybrid search beside the embedding sidecar, in the form of the
environment it runs in: `uv run ...` in a uv project, `uvx ...` from the
published package, the venv path otherwise.

The package build tests the wheel the same way on the synthetic release of
the server's tests, written as a fetched pack by
`scripts/test/container/synthetic_pack.py` and checked with `--no-fetch`,
so the build depends on no knowledge base.

| Option | Effect |
| --- | --- |
| `pack` | The pack, `releases/<pack>` of the packs repository; default `$SWISSTIP_PACK` |
| `--repository OWNER/NAME` | The GitHub repository of the packs; default `swisstip/swiss-tip-mvp` |
| `--ref REF` | Branch, tag or commit of that repository; default `main`, resolved to one commit |
| `--packs-dir DIR` | Where the pack lands, under `<packs-dir>/<pack>`; default `.local/packs` |
| `--no-fetch` | No request: the checks and tests on the files fetched before |
| `--url URL` | The round trip against a running Streamable HTTP endpoint, with `/health` beside it, and nothing else |
| `--serve` | After the checks, serve the pack with `--require-ready` over Streamable HTTP; `--port`, default 8000. A pack with datasets gets its calendar connector first, on `127.0.0.1:8100`, and the server lists `lookup` |
| `--connector-port` | With `--serve`: the loopback port of the calendar connector; default 8100 |
| `--no-calendar` | With `--serve`: without the calendar connector, four tools |
| `--hybrid` | With `--serve`: hybrid search with the attested index and a local Ollama that holds its model (`--ollama-url`, default `http://127.0.0.1:11434`), such as the embedding sidecar published on that port |
