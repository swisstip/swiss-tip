# swisstip-calendar-connector

**Last update:** 23 September 2026

The first dataset connector of Swiss TIP: a separate process that serves
dataset bundles of dated events by postal code, such as a city's
waste-collection calendars published as open data, to the Swiss TIP MCP
server over HTTP. The server registers each bundle behind the concept of
the knowledge release it stands behind, offers it on `resolve`, and
forwards the caller's `lookup`; this process answers from the rows it
carries, with the publisher, licence, source file hash and download date of
every dataset. It reaches no network at answer time and composes no
guidance.

```bash
swisstip-calendar-connector --datasets-dir datasets/<pack> --health
swisstip-calendar-connector --datasets-dir datasets/<pack> --host 127.0.0.1 --port 8100
swisstip-mcp --release releases/<pack>/release.json --connector http://127.0.0.1:8100
```

A bundle (`swiss-tip-dataset/v1`) is built from a curation file with
`swisstip-build-dataset` of the `swisstip-builder` distribution; the
datasets of the published packs live in the packs repository,
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp). The contract
between the server and a connector (`swiss-tip-connector/v1`) and the
design are in the repository's
[docs/architecture/dataset-connectors.md](https://github.com/swisstip/swiss-tip/blob/main/docs/architecture/dataset-connectors.md).

Requires `swisstip-core` at the same version. Licence: Apache-2.0.
