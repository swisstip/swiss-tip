"""A client round trip over MCP against any release, with no knowledge of its content.

The round trip of the package build (scripts/test/mcp/check_wheel.py, on the installed wheel and the synthetic
release), of the image build (the slim MCP image with that release mounted) and of the quickstart (on a fetched
pack). It checks the advertised tools, verifies that the disabled get_coverage call is rejected, resolves a known
search result for its own jurisdiction with the first allowed value of every required context field, reads the
evidence of a served fact and provokes a typed error. Over stdio the server is started with the interpreter given,
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
            check(f"server {init.serverInfo.name} {init.serverInfo.version} lists the three tools",
                tools == ["search", "resolve", "get_evidence"])
            coverage = await session.call_tool("get_coverage", {})
            check("get_coverage is disabled", coverage.isError and coverage.structuredContent["error"]["code"] == "INVALID_ARGUMENT")
            found = (await session.call_tool("search", {"query": "registration", "limit": 10})).structuredContent
            concept = found["results"][0] if found["results"] else None
            if concept is None:
                check("search returns a concept for the round trip", False)
                return failures
            hit = next((item for item in found["results"] if item["concept_id"] == concept["concept_id"]), None)
            check(f"search by its label finds {concept['concept_id']}", hit is not None)
            schema = (hit or {}).get("context_schema", {})
            context = {field: (schema.get(field, {}).get("enum") or [""])[0] for field in concept["required_context"]}
            code = concept["jurisdictions"][0]
            parts = code.split("-")
            jurisdiction = {"country": parts[0], **({"canton": "-".join(parts[:2])} if len(parts) > 1 else {}),
                            **({"city": code} if len(parts) > 2 else {})}
            resolved = await session.call_tool("resolve", {"concept_ids": [concept["concept_id"]], "jurisdiction": jurisdiction,
                                                           "context": context})
            body = resolved.structuredContent
            facts = body["results"][0].get("facts", []) if not resolved.isError else []
            check(f"resolve for {code} with {context or 'no context'} serves facts with citations: {body.get('status')}",
                  not resolved.isError and body["status"] == "SUPPORTED" and bool(facts) and bool(body["results"][0]["citations"])
                  and json.loads(resolved.content[0].text) == body)
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
