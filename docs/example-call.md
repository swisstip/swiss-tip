# Example calls

**Last update:** 26 September 2026

Two calls that show the server works: a `search` finds the concept for a
question, and a `resolve` returns its reviewed fact with the citation. This is
the path an assistant takes for a covered question.

The endpoint is stateless Streamable HTTP, so each call is one request with no
session and no authentication. The commands use a local container at
`http://127.0.0.1:8000/mcp`, started as the [README](../README.md#quick-start)
describes.

## 1. Search

**Tool:** `search`

**Arguments:**

```json
{"query": "register arrival City of Zurich", "limit": 3}
```

**What it returns:** the concepts that match the question, best first, with
the `concept_id` to resolve and the context each one needs. The first hit is
`city-zurich-arrival`, "City of Zurich: registering arrival from abroad",
which needs `arrival_origin` (`abroad` or `within_switzerland`). The result
also names the `match_strength` (`strong` here), the `retrieval_mode`
(`hybrid` in the `mvp-zurich` image) and a `guidance_for_caller` that says to
resolve the relevant concept IDs next.

```shell
curl -s http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search","arguments":{"query":"register arrival City of Zurich","limit":3}}}'
```

The result, as `structuredContent`, shortened to its main fields:

```json
{
  "release_id": "mvp-zurich-2026-09-25-v3",
  "query": "register arrival City of Zurich",
  "results": [
    {
      "concept_id": "city-zurich-arrival",
      "topic_id": "residence",
      "label": "City of Zurich: registering arrival from abroad",
      "jurisdictions": ["CH-ZH-261"],
      "required_context": ["arrival_origin"],
      "context_schema": {"arrival_origin": {"type": "string", "enum": ["abroad", "within_switzerland"]}}
    },
    {"concept_id": "city-zurich-arrival-documents", "label": "City of Zurich: documents for arrival from abroad", "...": "..."},
    {"concept_id": "city-zurich-first-steps", "label": "First steps after moving to the City of Zurich", "...": "..."}
  ],
  "match_strength": "strong",
  "retrieval_mode": "hybrid",
  "guidance_for_caller": "Ranked candidates from this release. Resolve the relevant concept IDs with the known jurisdiction and context to select applicable facts; ..."
}
```

## 2. Resolve

**Tool:** `resolve`

**Arguments:** the first hit of the search, the place and the context it
asked for:

```json
{
  "concept_ids": ["city-zurich-arrival"],
  "jurisdiction": {"city": "Zurich"},
  "context": {"arrival_origin": "abroad"}
}
```

**What it returns:** the status `SUPPORTED` and the facts of the concept for
the City of Zurich (`CH-ZH-261`): the statement that an arrival from abroad
needs an appointment and personal registration at the Personenmeldeamt, its
review status (`human-reviewed`), and one citation to the City of Zurich's
page with its publisher, URL and access date. Next to them come the
`release_id`, the snapshot date and the date the release goes stale, and a
`guidance_for_caller` that tells the assistant to answer from the statements
only and to cite only the returned URL.

```shell
curl -s http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"resolve","arguments":{"concept_ids":["city-zurich-arrival"],"jurisdiction":{"city":"Zurich"},"context":{"arrival_origin":"abroad"}}}}'
```

The result, shortened to its main fields:

```json
{
  "release_id": "mvp-zurich-2026-09-25-v3",
  "status": "SUPPORTED",
  "executed_scope": {"canton": "Zürich", "city": "Zürich", "municipality_id": "CH-ZH-261"},
  "freshness": {"snapshot_date": "2026-09-25", "max_age_days": 60, "stale_from": "2026-11-24"},
  "results": [{
    "concept_id": "city-zurich-arrival",
    "status": "SUPPORTED",
    "review_status": "human-reviewed",
    "facts": [{
      "fact_id": "city-zurich-arrival-1",
      "statement": "For arrival from abroad, the City of Zurich requires an appointment and personal registration at Personenmeldeamt Zurich Sud. Without an appointment registration is not possible; for family registration every family member must attend in person.",
      "condition": {"arrival_origin": "abroad"},
      "evidence_ids": ["e-city-zurich-arrival-1-1"]
    }],
    "citations": [{
      "source_title": "Zuzug in die Stadt Zürich | Stadt Zürich",
      "publisher": "City of Zurich, Population Office",
      "level": "municipal",
      "url": "https://www.stadt-zuerich.ch/de/lebenslagen/einwohner-services/umziehen-melden/zuzug.html",
      "accessed_on": "2026-09-11"
    }],
    "missing_context": [],
    "gaps": []
  }],
  "guidance_for_caller": "These facts and citations are everything this release holds for the requested concepts. Answer now from the statements only, cite only the returned URLs, ..."
}
```

The release ID, the dates and the ranking follow the release the server
loads; the statement and the citation stay the same until the page they cite
changes.

## Variations worth one more call

Each changes the `resolve` arguments above.

- **Without the context:** leave out `context` and the status becomes
  `NEEDS_CONTEXT`, naming the field `arrival_origin` and its allowed values
  `abroad` and `within_switzerland`.
- **Another city:** `"jurisdiction": {"city": "Bern"}` returns
  `OUT_OF_COVERAGE` with a named gap: the City of Zurich's procedure is not
  served for Bern.

The full request and result fields of all five tools are in the
[tool contracts](architecture/tool-contracts.md).
