"""Round trip against any release, with no knowledge of its content: what the package build runs on the installed
wheel, and the image build on the slim MCP image, with a synthetic release, so that neither depends on a knowledge base.

    ./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py
    ./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py --release releases/<pack>/release.json
    ./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py --url http://127.0.0.1:8000/mcp

Without --url the server is started over stdio with the interpreter that runs this script, so it tests the packages
installed there; with --url the round trip runs against a running Streamable HTTP endpoint, a container. It takes
the first concept of the first topic from get_coverage, finds it again with search by its label, resolves it for its
own jurisdiction with the first allowed value of every required context field, reads the evidence of a served fact
and provokes a typed error. The checks of the published packs live with the packs, in swiss-tip-mvp.
"""

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "apps" / "mcp-server" / "tests" / "fixtures" / "release.json"
failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


@asynccontextmanager
async def connect(release: Path, url: str | None):
    if url:
        async with streamable_http_client(url) as (read, write, _):
            yield read, write
        return
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "swisstip.mcp_server.server", "--release", str(release)],
                                   env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    async with stdio_client(params) as (read, write):
        yield read, write


async def run(release: Path, url: str | None) -> None:
    async with connect(release, url) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = [tool.name for tool in (await session.list_tools()).tools]
            check(f"server {init.serverInfo.name} {init.serverInfo.version} lists the four tools",
                  tools == ["get_coverage", "search", "resolve", "get_evidence"])
            root = (await session.call_tool("get_coverage", {})).structuredContent
            check(f"coverage root of {root.get('release_id')} names topics and jurisdictions",
                  bool(root.get("topics")) and bool(root.get("jurisdictions")) and bool(root.get("scope_statement")))
            topic = (await session.call_tool("get_coverage", {"parent_id": root["topics"][0]["topic_id"]})).structuredContent
            concept = topic["concepts"][0]
            found = (await session.call_tool("search", {"query": concept["label"], "limit": 10})).structuredContent
            hit = next((item for item in found["results"] if item["concept_id"] == concept["concept_id"]), None)
            check(f"search by its label finds {concept['concept_id']}", hit is not None)
            schema = (hit or {}).get("context_schema", {})
            context = {field: (schema.get(field, {}).get("enum") or [""])[0] for field in concept["required_context"]}
            code = concept["jurisdictions"][0]
            parts = code.split("-")
            jurisdiction = {"country": parts[0], **({"canton": "-".join(parts[:2])} if len(parts) > 1 else {}),
                            **({"city": code} if len(parts) > 2 else {})}
            resolved = await session.call_tool("resolve", {"concept_ids": [concept["concept_id"]], "jurisdiction": jurisdiction,
                                                           "as_of": root["freshness"]["snapshot_date"], "context": context})
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", type=Path, default=FIXTURE, help="release.json to serve; default: the synthetic test release")
    parser.add_argument("--url", help="Streamable HTTP endpoint of a running server, instead of starting one over stdio")
    args = parser.parse_args()
    asyncio.run(run(args.release, args.url))
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
