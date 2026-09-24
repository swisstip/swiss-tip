# swisstip-calendar-connector

**Last update:** 23 September 2026

The first dataset connector: a separate process that serves dataset bundles
of dated events by postal code (`swiss-tip-dataset/v1`, the waste-collection
calendars of a city) to the MCP server over HTTP, under the connector
contract `swiss-tip-connector/v1` of
[docs/architecture/dataset-connectors.md](../../docs/architecture/dataset-connectors.md).
The server registers each bundle behind the concept it names and offers a
`lookup` on `resolve`; this process answers the lookups from the rows it
carries. It reaches no network, composes no guidance and has no MCP
endpoint; it is meant to be reached by the server only, on the loopback
address of a shared network namespace. It imports `swisstip-core` and
nothing else of the product.

```shell
./.venv/Scripts/python.exe -m pip install -e packages/core -e apps/calendar-connector
./.venv/Scripts/python.exe -m unittest discover -s apps/calendar-connector/tests
./.venv/Scripts/python.exe -m swisstip.calendar_connector.server --dataset datasets/<pack>/<dataset> --health
./.venv/Scripts/python.exe -m swisstip.calendar_connector.server --datasets-dir datasets/<pack>
./.venv/Scripts/python.exe scripts/test/connector/check_connector.py --url http://127.0.0.1:8100
```

| Option | Effect |
| --- | --- |
| `--dataset DIR` | A bundle directory holding `dataset.json`; repeatable. Without it and `--datasets-dir`, `SWISSTIP_DATASETS` lists the directories, separated by the path separator |
| `--datasets-dir DIR` | A directory whose subdirectories hold bundles, each with its `dataset.json`; repeatable. The container image serves `/srv/swiss-tip/datasets` this way |
| `--host HOST`, `--port PORT` | Interface and port; default `127.0.0.1` and `$PORT`, else 8100 |
| `--health` | Load and validate the bundles, print the health payload, exit 0 when at least one is served, else 2 |
| `--log-level LEVEL` | One line per lookup on stderr with dataset, status and latency |

| Route | Answer |
| --- | --- |
| `GET /manifest` | The connector manifest: `connector_id`, the types served (`calendar`), and one summary per bundle with its binding (`pack`, `concept_id`, `jurisdiction`), provenance, period, postal codes, the fields a lookup requires and accepts, and `status` `ok` or `invalid` with the validator's first message |
| `POST /lookup` | `dataset_id`, `postal_code`, optional `start` and `end`, `limit` (1 to 60): the dates of that postal code in the range, oldest first, with the dataset's provenance, or `OUT_OF_COVERAGE` with the gap `postal_code_not_covered`, `period_not_published` or `no_dates_in_range`. A malformed request, an unknown dataset or one that failed validation is an HTTP 400 with `code` `INVALID_ARGUMENT` and the `path` of the field |
| `GET /health` | One line per bundle with its version, status, period, row count, `expires_on` and `expired` |

Every bundle is validated at startup. One that does not validate is listed
as invalid and not served; the others are. The same `dataset_id` in two
directories stops the connector. The reference semantics of a lookup are in
`swisstip.core.connector.calendar_lookup`, so a connector written elsewhere
answers alike; `scripts/test/connector/check_connector.py` proves it against
a running one, or starts this one on the synthetic bundle of its tests.
