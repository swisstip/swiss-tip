"""Serve a knowledge release over MCP stdio or Streamable HTTP.

    swisstip-server --release releases/<pack>/release.json
                                                      serve a release over stdio; without --release
                                                      the release is read from SWISSTIP_RELEASE
    swisstip-server --transport streamable-http --host 0.0.0.0 --port 8000
                                                      MCP on /mcp, the health payload on /health
    swisstip-server --health                          load, validate, print counts, exit
    swisstip-server --require-ready                   refuse a release without a matching readiness.json
    swisstip-server --print-client-config [opencode]  print a client configuration
    swisstip-server --print-client-config opencode --url https://host/mcp
                                                      print a configuration for a remote endpoint

The release is validated once at startup; any issue stops the server. A
release is `ready` when a `readiness.json` next to it names exactly this
file with every gate of the acceptance gate passed, else a `candidate`: the
health payload says which, a candidate is served with a warning, and
`--require-ready` (the container's setting) refuses it. Every call writes
one log line to stderr with tool, status, bytes and latency. The server
composes no answers: it returns facts, excerpts, citations and typed
statuses, as defined in docs/architecture/tool-contracts.md. Its MCP
instructions and the search description name the languages to search in,
measured on the release.

Over HTTP the MCP endpoint is stateless, answers with JSON and requires no
authentication: it serves a read-only release of public information.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import sys
import time
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from swisstip.core.contracts import ToolError, tool_output_schema
from swisstip.core.readiness import readiness_status
from swisstip.core.validation import ReleaseInvalid
from swisstip.runtime.connectors import ConnectorRegistry
from swisstip.runtime.semantic import (OllamaEmbedder, SemanticError, SemanticSearch, load_index,
                                       semantic_index_binding)
from swisstip.runtime.service import ALL_TOOL_CONTRACTS, ReleaseService, argument_error

from . import SERVER_NAME, SERVER_VERSION

RELEASE_VARIABLE = "SWISSTIP_RELEASE"
CONNECTORS_VARIABLE = "SWISSTIP_CONNECTORS"
MCP_PATH = "/mcp"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MCP_DISABLED_TOOLS = frozenset({"get_coverage"})


def create_server(service: ReleaseService) -> Server:
    # The instructions and the search description name the release's query languages, so a caller translates a
    # question in another language before searching instead of reading a weak result.
    server = Server(SERVER_NAME, version=SERVER_VERSION, instructions=service.instructions)
    log = logging.getLogger(SERVER_NAME)

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=name, description=service.tool_description(name), inputSchema=service.tool_input_schema(name),
                           outputSchema=tool_output_schema(ALL_TOOL_CONTRACTS[name][1]),
                           annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
                for name in service.tools() if name not in MCP_DISABLED_TOOLS]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        started = time.perf_counter()
        if name in MCP_DISABLED_TOOLS:
            result = argument_error("name", f"Unknown tool {name!r}.")
        elif name == "search" and service.semantic_search is not None:
            result = await asyncio.to_thread(service.dispatch, name, arguments)
        else:
            result = service.dispatch(name, arguments)
        payload = result.model_dump(mode="json", exclude_none=True)
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        is_error = isinstance(result, ToolError)
        status = result.error.code.value if is_error else payload.get("status", "OK")
        log.info("tool=%s status=%s bytes=%d ms=%.1f release=%s", name, status, len(text.encode("utf-8")),
                 (time.perf_counter() - started) * 1000, service.release_id)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                    structuredContent=payload, isError=is_error)

    return server


async def serve(service: ReleaseService) -> None:
    server = create_server(service)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


class McpEndpoint:
    """The session manager as a raw ASGI app; Starlette would wrap a function or method as a request handler."""

    def __init__(self, manager: StreamableHTTPSessionManager):
        self.manager = manager

    async def __call__(self, scope, receive, send):
        await self.manager.handle_request(scope, receive, send)


def create_http_app(service: ReleaseService, *, host: str = "127.0.0.1", readiness: dict | None = None) -> Starlette:
    """MCP on /mcp (stateless, JSON responses), the --health payload on /health and a short index on /."""
    # Bound to loopback, the endpoint accepts only loopback Host and Origin headers (DNS rebinding protection), as the
    # MCP SDK does for its own servers. Bound to a public interface, it sits behind the container host's own name.
    security = (TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                          allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
                                          allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"])
                if host in LOOPBACK_HOSTS else None)
    manager = StreamableHTTPSessionManager(app=create_server(service), json_response=True, stateless=True,
                                           security_settings=security)

    async def health_route(request):
        return JSONResponse(health(service, readiness))

    async def index_route(request):
        return JSONResponse(dict(name=SERVER_NAME, version=SERVER_VERSION, release_id=service.release_id,
                                 transport="streamable-http", mcp_endpoint=MCP_PATH, health="/health"))

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    return Starlette(routes=[Route(MCP_PATH, endpoint=McpEndpoint(manager)),
                             Route("/health", endpoint=health_route, methods=["GET"]),
                             Route("/", endpoint=index_route, methods=["GET"])],
                     lifespan=lifespan)


def health(service: ReleaseService, readiness: dict | None = None) -> dict:
    manifest = service.release.manifest
    return dict(status="ok", release_id=manifest.release_id, pack=manifest.pack, created_at=manifest.created_at.isoformat(),
                content_sha256=manifest.content_sha256,
                topics=len(service.topics), concepts=len(service.concepts), facts=len(service.facts),
                evidence=len(service.evidence), documents=len(service.release.documents),
                jurisdictions=manifest.jurisdictions, languages=manifest.languages,
                freshness=dict(snapshot_date=manifest.freshness.snapshot_date.isoformat(),
                               stale_from=manifest.freshness.stale_from.isoformat()),
                review_statuses=manifest.review_statuses,
                institution_levels=manifest.institution_levels, basis_kinds=manifest.basis_kinds,
                readiness=readiness or dict(status="candidate", reason="readiness not checked"),
                connectors=service.connectors.health() if service.connectors is not None else [],
                search=dict(configured_mode="hybrid" if service.semantic_search else
                            "lexical-fallback" if service.semantic_error else "lexical",
                            model=service.semantic_search.index.model if service.semantic_search else None,
                            model_readiness="not-probed" if service.semantic_search else None,
                            fallback_reason=service.semantic_error))


def remote_client_config(url: str, flavour: str) -> dict:
    """A client configuration for a Streamable HTTP endpoint."""
    if flavour == "opencode":
        return {"mcp": {"swiss_tip": {"type": "remote", "url": url, "enabled": True, "timeout": 60000}}}
    return {"mcpServers": {"swiss-tip": {"type": "http", "url": url}}}


def client_config(release: Path, flavour: str, *, semantic_index: Path | None = None,
                  ollama_url: str = "http://127.0.0.1:11434", semantic_timeout: float = 10,
                  semantic_min_score: float = 0.5, semantic_candidates: int = 10) -> dict:
    python = sys.executable
    args = ["-m", "swisstip.mcp_server.server", "--release", str(release.resolve())]
    if semantic_index is not None:
        args.extend(["--semantic-index", str(semantic_index.resolve()), "--ollama-url", ollama_url,
                     "--semantic-timeout", str(semantic_timeout), "--semantic-min-score", str(semantic_min_score),
                     "--semantic-candidates", str(semantic_candidates)])
    if flavour == "opencode":
        return {"mcp": {"swiss_tip": {"type": "local", "command": [python, *args], "enabled": True, "timeout": 60000,
                                      "environment": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}}}}
    return {"mcpServers": {"swiss-tip": {"command": python, "args": args,
                                         "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}}}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", type=Path, default=os.environ.get(RELEASE_VARIABLE) or None,
                        help=f"release.json to serve; default ${RELEASE_VARIABLE}. The server holds no knowledge of its own")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http: interface to bind")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000),
                        help="streamable-http: port to bind; default $PORT, else 8000")
    parser.add_argument("--health", action="store_true", help="load and validate the release, print counts, exit")
    parser.add_argument("--require-ready", action="store_true",
                        help="refuse to serve (or to report healthy) a release without a matching readiness.json next to it")
    parser.add_argument("--print-client-config", nargs="?", const="generic", choices=["generic", "opencode"],
                        help="print a client configuration for this server and exit")
    parser.add_argument("--url", help="with --print-client-config: the remote Streamable HTTP endpoint to connect to")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--semantic-index", type=Path, help="optional release-bound embedding index; lexical search is the default")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="local Ollama endpoint for query embeddings")
    parser.add_argument("--semantic-timeout", type=float, default=10, help="maximum seconds per local embedding request")
    parser.add_argument("--semantic-min-score", type=float, default=0.5, help="minimum cosine similarity for semantic candidates")
    parser.add_argument("--semantic-candidates", type=int, default=10, help="maximum semantic candidates before rank fusion")
    parser.add_argument("--connector", action="append", metavar="URL",
                        help=f"a dataset connector to register (repeatable); default ${CONNECTORS_VARIABLE}, comma-separated")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.print_client_config and args.url:
        print(json.dumps(remote_client_config(args.url, args.print_client_config), indent=2))
        return 0
    if args.release is None:
        print(json.dumps(dict(status="error", release=None,
                              error=f"no release to serve: pass --release FILE or set {RELEASE_VARIABLE}")),
              file=sys.stdout if args.health else sys.stderr)
        return 2
    if args.print_client_config:
        print(json.dumps(client_config(args.release, args.print_client_config, semantic_index=args.semantic_index,
                                       ollama_url=args.ollama_url, semantic_timeout=args.semantic_timeout,
                                       semantic_min_score=args.semantic_min_score,
                                       semantic_candidates=args.semantic_candidates), indent=2))
        return 0
    try:
        service = ReleaseService.from_file(args.release)
    except (OSError, ValueError, ReleaseInvalid) as exc:
        print(json.dumps(dict(status="error", release=str(args.release), error=str(exc))), file=sys.stderr if not args.health else sys.stdout)
        return 2
    index = None
    embedder = None
    semantic_binding = None
    if args.semantic_index is not None:
        try:
            index = load_index(args.semantic_index, service.release)
            semantic_binding = semantic_index_binding(
                args.semantic_index, index, min_score=args.semantic_min_score,
                candidate_limit=args.semantic_candidates)
            embedder = OllamaEmbedder(model=index.model, base_url=args.ollama_url,
                                      timeout_seconds=args.semantic_timeout)
            if args.require_ready and embedder.model_digest() != index.model_digest:
                raise SemanticError("Local Ollama model digest differs from the attested semantic index.")
            if args.require_ready:
                probe = SemanticSearch(index, embedder, min_score=args.semantic_min_score,
                                       candidate_limit=args.semantic_candidates)
                probe.scores("Swiss TIP semantic readiness probe")
        except (OSError, ValueError, SemanticError) as exc:
            if args.require_ready:
                print(json.dumps(dict(status="error", release=str(args.release),
                                      error=f"the release is not ready: semantic search is unavailable: {exc}")),
                      file=sys.stderr if not args.health else sys.stdout)
                return 2
            service.semantic_error = str(exc)
            logging.getLogger(SERVER_NAME).warning("semantic search unavailable; lexical fallback: %s", exc)
    readiness = readiness_status(args.release, semantic_index=semantic_binding)
    if args.require_ready and readiness["status"] != "ready":
        print(json.dumps(dict(status="error", release=str(args.release), error=f"the release is not ready: {readiness['reason']}")),
              file=sys.stderr if not args.health else sys.stdout)
        return 2
    if readiness["status"] != "ready" and not args.health:
        logging.getLogger(SERVER_NAME).warning("serving a candidate release, not a ready one: %s", readiness["reason"])
    if args.semantic_index is not None and index is not None and embedder is not None:
        try:
            service.semantic_search = SemanticSearch(index, embedder, min_score=args.semantic_min_score,
                                                     candidate_limit=args.semantic_candidates)
        except (OSError, ValueError, SemanticError) as exc:
            service.semantic_error = str(exc)
            logging.getLogger(SERVER_NAME).warning("semantic search unavailable; lexical fallback: %s", exc)
    connectors = args.connector or [item.strip() for item in os.environ.get(CONNECTORS_VARIABLE, "").split(",") if item.strip()]
    if connectors:
        # Registration reads each manifest once; an unreachable connector is probed again on every health request,
        # and the release's own tools never wait for one.
        service.connectors = ConnectorRegistry(connectors, service.release)
    if args.health:
        print(json.dumps(health(service, readiness), indent=2))
        return 0
    log = logging.getLogger(SERVER_NAME)
    if args.transport == "streamable-http":
        log.info("serving release=%s facts=%d concepts=%d transport=streamable-http endpoint=http://%s:%d%s",
                 service.release_id, len(service.facts), len(service.concepts), args.host, args.port, MCP_PATH)
        # uvicorn logs through the root handler configured above, without access lines, and the stateless transport's
        # "Terminating session: None" after every request is silenced: the per-call line stays the request log.
        logging.getLogger("mcp.server.streamable_http").setLevel(max(logging.WARNING, log.getEffectiveLevel()))
        uvicorn.run(create_http_app(service, host=args.host, readiness=readiness), host=args.host, port=args.port,
                    log_config=None, access_log=False, proxy_headers=True, forwarded_allow_ips="*")
        return 0
    log.info("serving release=%s facts=%d concepts=%d", service.release_id, len(service.facts), len(service.concepts))
    asyncio.run(serve(service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
