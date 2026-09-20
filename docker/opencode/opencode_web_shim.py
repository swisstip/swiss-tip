"""Serve OpenCode's web interface with the `/api` routes it misses.

The web interface is the desktop client's, and it asks its server for the
project list, the connected MCP servers, the default model and the state of
the working tree over paths under `/api`. The standalone server of
`opencode web` 1.18.31 answers only some of them: the rest fall through to
the single-page application, and the interface, reading the application
shell where it expects JSON, leaves its project list empty, its "New
session" control disabled, its model unset, its MCP list empty and raises
"Failed to reload <project>: Unexpected token".

The same information is served on the paths without the `/api` prefix. This
proxy answers those requests from them, in the `{"data": ...}` envelope the
`/api` routes use, and passes everything else through unchanged, streaming,
including the event stream.

    python opencode_web_shim.py --listen 0.0.0.0:4096 --upstream 127.0.0.1:4099

The rule is general: any `/api/<path>` the server answers with the
application shell is answered from `/<path>`, when the server has it. Three
paths need more than a prefix: `/api/mcp`, which the interface reads as a
list and the server serves as an object; `/api/model/default`, which the
server does not serve at all and which is the configured model's entry in
`/api/model`; and `/api/project`, whose `global` project, the one a
directory outside a version-controlled tree falls into, has the root of the
file system as its worktree and is dropped here.

A path with no answer at all is logged once and answered `{"data": null}`,
so that a feature of the interface stays inert instead of breaking the page,
and the container's log names the route. If a later OpenCode version serves
these paths itself, this proxy can go.

With `--welcome welcome.json`, the proxy also adds the Swiss TIP welcome
panel to the interface: every HTML page it passes through loads
`/swiss-tip/welcome.js`, which is the `welcome.js` beside this file, preceded
by the content of that file and the agent's worktree as
`window.__SWISSTIP_WELCOME__`.

With `OPENCODE_SERVER_PASSWORD` the agent asks every request for basic
credentials. A browser's request is passed on with the credentials the
browser sent, or none, so the agent itself refuses a visitor without the
password. Only the proxy's two calls at start, for the route list and the
worktree, carry the password themselves.
"""

import argparse
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger("opencode-shim")

# Hop-by-hop headers belong to one connection and are not forwarded; the
# body is re-framed by the server below, so its length and encoding go too.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
    "content-encoding", "host",
})

WELCOME_SCRIPT = Path(__file__).with_name("welcome.js")
WELCOME_PATH = "/swiss-tip/welcome.js"
WELCOME_TAG = f'<script src="{WELCOME_PATH}"></script>'.encode("utf-8")

_reported = set()


def forwarded(headers):
    return [(key, value) for key, value in headers.items() if key.lower() not in HOP_BY_HOP]


def report(path, message, *args):
    """Log once per path, so a reloading interface does not repeat it."""
    if path not in _reported:
        _reported.add(path)
        logger.warning(message, *args)


def own_credentials():
    """Basic credentials for the proxy's own calls at start, or None without a password.

    Never set on the client: every request it sends would carry them, a
    visitor's too, and the password would protect nothing.
    """
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    return (os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode", password) if password else None


async def plain_routes(client, auth=None):
    """The paths the server documents for GET, without path parameters."""
    try:
        document = (await client.get("/doc", auth=auth)).json()
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("the server did not describe itself at /doc: %s", error)
        return frozenset()
    return frozenset(path for path, methods in document.get("paths", {}).items()
                     if "get" in methods and "{" not in path)


async def upstream_get(request: Request, path: str):
    """The server's answer to `path`, as (payload, status).

    The interface passes a directory as `location`; the server takes it as
    `directory`. Any other query parameter is passed on unchanged.
    """
    client: httpx.AsyncClient = request.app.state.client
    params = dict(request.query_params)
    location = params.pop("location", None)
    if location and "directory" not in params:
        params["directory"] = location
    response = await client.get(path, params=params, headers=forwarded(request.headers))
    if response.status_code != 200:
        report(path, "%s answered %s", path, response.status_code)
        return None, response.status_code
    return response.json(), 200


async def project_list(request: Request):
    """`GET /api/project`, from `/project`, without the `global` project."""
    payload, status = await upstream_get(request, "/project")
    if isinstance(payload, list) and len(payload) > 1:
        named = [project for project in payload if project.get("id") != "global"]
        payload = named or payload
    return JSONResponse({"data": payload}, status_code=status)


async def mcp_list(request: Request):
    """`GET /api/mcp`, from `/mcp`.

    The server serves an object of server name to state; the interface reads
    a list of objects with a name and a status.
    """
    payload, status = await upstream_get(request, "/mcp")
    if isinstance(payload, dict):
        payload = [{"name": name, **(state if isinstance(state, dict) else {"status": state})}
                   for name, state in payload.items()]
    return JSONResponse({"data": payload}, status_code=status)


async def model_default(request: Request):
    """`GET /api/model/default`: the configured model's entry in `/api/model`.

    The server has no route of its own for this. The interface reads the
    provider and the model out of the answer, so it is the entry of the
    model list, not the identifier alone.
    """
    client: httpx.AsyncClient = request.app.state.client
    headers = forwarded(request.headers)
    configured = None
    try:
        configured = (await client.get("/config", headers=headers)).json().get("model")
        models = (await client.get("/api/model", headers=headers)).json().get("data") or []
    except (httpx.HTTPError, ValueError) as error:
        report("/api/model/default", "the default model could not be read: %s", error)
        return JSONResponse({"data": None})
    chosen = None
    if isinstance(configured, str) and "/" in configured:
        provider, _, identifier = configured.partition("/")
        chosen = next((model for model in models
                       if model.get("providerID") == provider and model.get("id") == identifier), None)
        if chosen is None:
            report("/api/model/default", "the configured model %s is not in the model list", configured)
    return JSONResponse({"data": chosen or (models[0] if models else None)})


async def welcome_bundle(client, source: Path, auth=None) -> bytes:
    """`welcome.js`, preceded by its configuration.

    The interface's content security policy allows scripts from its own
    origin only, not inline ones, so the configuration travels in the served
    script. The worktree comes from the server's `/path`, so the panel
    registers the project the agent actually runs in.
    """
    welcome = json.loads(source.read_text(encoding="utf-8"))
    try:
        welcome["worktree"] = (await client.get("/path", auth=auth)).json().get("worktree")
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("the worktree could not be read from /path: %s", error)
    data = json.dumps(welcome, ensure_ascii=False)
    return f"window.__SWISSTIP_WELCOME__ = {data};\n".encode("utf-8") + WELCOME_SCRIPT.read_bytes()


async def welcome_script(request: Request):
    bundle = request.app.state.welcome
    if bundle is None:
        return Response(status_code=404)
    return Response(bundle, media_type="text/javascript", headers={"cache-control": "no-cache"})


async def proxy(request: Request):
    """Everything else, streamed to and from the OpenCode server.

    A GET under `/api` that comes back as the application shell is the
    server saying it has no such route; it is answered from the path without
    the prefix when the server has that one.
    """
    client: httpx.AsyncClient = request.app.state.client
    headers = forwarded(request.headers)
    if request.app.state.welcome and "text/html" in request.headers.get("accept", ""):
        # A page cached without the panel would otherwise be confirmed as fresh.
        headers = [(key, value) for key, value in headers
                   if key.lower() not in ("if-none-match", "if-modified-since")]
    upstream = client.build_request(
        request.method,
        request.url.path,
        params=dict(request.query_params),
        headers=headers,
        content=request.stream(),
    )
    response = await client.send(upstream, stream=True)
    path = request.url.path
    if (request.method == "GET" and path.startswith("/api/")
            and "text/html" in response.headers.get("content-type", "")):
        await response.aclose()
        plain = path[len("/api"):]
        if plain in request.app.state.plain_routes:
            report(path, "answering %s from %s: the server has no %s", path, plain, path)
            payload, status = await upstream_get(request, plain)
            return JSONResponse({"data": payload}, status_code=status)
        report(path, "the interface asked for %s and neither it nor %s is served; "
                     "answering with no data", path, plain)
        return JSONResponse({"data": None})

    if (request.app.state.welcome and request.method == "GET"
            and "text/html" in response.headers.get("content-type", "")):
        try:
            page = await response.aread()
        finally:
            await response.aclose()
        page = page.replace(b"</head>", WELCOME_TAG + b"</head>", 1)
        # The server's validators describe the page without the panel.
        headers = {key: value for key, value in forwarded(response.headers)
                   if key.lower() not in ("etag", "last-modified", "cache-control")}
        headers["cache-control"] = "no-cache"
        return Response(page, status_code=response.status_code, headers=headers)

    async def body():
        # Decoded, not raw: the answer's content-encoding is not forwarded,
        # because the server below re-frames the body. Raw bytes would reach
        # a browser that asked for gzip as gzip without saying so, and every
        # answer would read as "Unexpected token" where JSON was expected.
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()

    return StreamingResponse(body(), status_code=response.status_code,
                             headers=dict(forwarded(response.headers)),
                             media_type=response.headers.get("content-type"))


def build(upstream: str, welcome: Path | None = None, listen: str = "0.0.0.0:4096") -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        # No timeout: the event stream and a model's answer both stay open.
        app.state.client = httpx.AsyncClient(base_url=f"http://{upstream}", timeout=None)
        auth = own_credentials()
        app.state.plain_routes = await plain_routes(app.state.client, auth)
        app.state.welcome = await welcome_bundle(app.state.client, welcome, auth) if welcome else None
        if welcome:
            logger.info("adding the welcome panel of %s", welcome)
        logger.info("serving the OpenCode web interface of %s on %s, with %d routes to fall back on",
                    upstream, listen, len(app.state.plain_routes))
        logger.info("the web interface is on container port %s, the one docker run -p publishes",
                    listen.rpartition(":")[2])
        try:
            yield
        finally:
            await app.state.client.aclose()

    routes = [
        Route("/api/project", project_list, methods=["GET"]),
        Route("/api/mcp", mcp_list, methods=["GET"]),
        Route("/api/model/default", model_default, methods=["GET"]),
        Route(WELCOME_PATH, welcome_script, methods=["GET"]),
        Route("/{path:path}", proxy,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="0.0.0.0:4096", help="host:port to serve on")
    parser.add_argument("--upstream", default="127.0.0.1:4099", help="host:port of opencode web")
    parser.add_argument("--welcome", type=Path,
                        help="welcome.json of the welcome panel; without it the interface is unchanged")
    arguments = parser.parse_args()
    host, _, port = arguments.listen.rpartition(":")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # One line per proxied request would drown the server's own log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(build(arguments.upstream, arguments.welcome, arguments.listen),
                host=host or "0.0.0.0", port=int(port), log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
