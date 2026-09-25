"""A client round trip over MCP against any release, with no knowledge of its content.

The round trip of the package build (scripts/test/mcp/check_wheel.py, on the installed wheel and the synthetic
release), of the image build (the slim MCP image with that release mounted) and of the quickstart (on a fetched
pack). It checks that get_coverage is hidden (or, on a server started with --with-coverage, that its root names the
scope), takes the first concept a search with the scope statement of the server instructions finds, finds it again
with search by its label, resolves it for its own jurisdiction with the first allowed value of every required context
field, reads the evidence of a served fact and provokes a typed error. Over stdio the server is started with the interpreter given,
so the packages installed there are what is tested; with a URL the round trip runs against a running Streamable
HTTP endpoint, a container. The checks that know a pack's content live with the packs, in swiss-tip-mvp.
"""

import asyncio
import json
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

Report = Callable[[str, bool], None]


def print_check(label: str, ok: bool) -> None:
    print(("ok   " if ok else "FAIL ") + label)


@asynccontextmanager
async def connect(release: Path | None, url: str | None, python: str):
    if url:
        async with streamable_http_client(url) as (read, write, _):
            yield read, write
        return
    params = StdioServerParameters(command=python,
                                   args=["-m", "swisstip.mcp_server.server", "--release", str(release)],
                                   env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    async with stdio_client(params) as (read, write):
        yield read, write


async def run(release: Path | None, url: str | None, report: Report = print_check,
              python: str = sys.executable) -> list[str]:
    """Run the round trip and return the labels of the checks that failed."""
    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        report(label, ok)
        if not ok:
            failures.append(label)

    async with connect(release, url, python) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = [tool.name for tool in (await session.list_tools()).tools]
            # get_coverage is listed only by a server started with --with-coverage; lookup while a dataset connector
            # is registered, as in a pack image that carries its datasets.
            three = ["search", "resolve", "get_evidence"]
            core = [name for name in tools if name != "lookup"]
            check(f"server {init.serverInfo.name} {init.serverInfo.version} lists " + ", ".join(tools),
                  core in (three, ["get_coverage", *three]) and tools[len(core):] in ([], ["lookup"]))
            coverage = await session.call_tool("get_coverage", {})
            if "get_coverage" in tools:
                root = coverage.structuredContent
                check(f"coverage root of {root.get('release_id')} names topics and jurisdictions",
                      bool(root.get("topics")) and bool(root.get("jurisdictions")) and bool(root.get("scope_statement")))
            else:
                check("the hidden get_coverage is refused with a typed error",
                      coverage.isError and coverage.structuredContent["error"]["code"] == "INVALID_ARGUMENT")
            # A concept without knowing the content: the instructions end with the release's scope statement.
            scope = (init.instructions or "").partition("Scope: ")[2].split("\n")[0]
            first = (await session.call_tool("search", {"query": scope or "permit", "limit": 1})).structuredContent
            check("a search with the scope statement of the instructions finds a concept", bool(first.get("results")))
            if not first.get("results"):
                return failures
            concept = first["results"][0]
            found = (await session.call_tool("search", {"query": concept["label"], "limit": 10})).structuredContent
            hit = next((item for item in found["results"] if item["concept_id"] == concept["concept_id"]), None)
            check(f"search by its label finds {concept['concept_id']}", hit is not None)
            schema = (hit or {}).get("context_schema", {})
            context = {field: (schema.get(field, {}).get("enum") or [""])[0] for field in concept["required_context"]}
            code = concept["jurisdictions"][0]
            parts = code.split("-")
            jurisdiction = {"country": parts[0], **({"canton": "-".join(parts[:2])} if len(parts) > 1 else {}),
                            **({"city": code} if len(parts) > 2 else {})}
            # Resolved for today: an ageing release answers STALE with the facts it published, which is still a round
            # trip; the freshness window is the readiness check's business, not the transport's.
            resolved = await session.call_tool("resolve", {"concept_ids": [concept["concept_id"]], "jurisdiction": jurisdiction,
                                                           "context": context})
            body = resolved.structuredContent
            facts = body["results"][0].get("facts", []) if not resolved.isError else []
            check(f"resolve for {code} with {context or 'no context'} serves facts with citations: {body.get('status')}",
                  not resolved.isError and body["status"] in ("SUPPORTED", "STALE") and bool(facts)
                  and bool(body["results"][0]["citations"]) and json.loads(resolved.content[0].text) == body)
            if facts:
                evidence = (await session.call_tool("get_evidence", {"evidence_ids": facts[0]["evidence_ids"][:1]})).structuredContent
                check("get_evidence returns the excerpt of a served fact", bool(evidence["evidence"][0]["original_excerpt"]))
            error = await session.call_tool("get_evidence", {"evidence_ids": []})
            check("an empty evidence request is a typed error",
                  error.isError and error.structuredContent["error"]["code"] == "INVALID_ARGUMENT")
    return failures


def roundtrip(release: Path | None = None, url: str | None = None, *, report: Report = print_check,
              python: str = sys.executable) -> list[str]:
    """The round trip against a release served over stdio from this environment, or against a URL; the failed labels."""
    if release is None and url is None:
        raise ValueError("a release to serve over stdio or the URL of a running server is needed")
    return asyncio.run(run(None if url else release, url, report, python))
