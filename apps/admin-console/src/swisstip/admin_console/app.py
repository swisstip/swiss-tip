"""The console application: one FastAPI app over the files of the packs.

    swisstip-admin --packs-dir ../swiss-tip-mvp        edit mode on 127.0.0.1:8765
    swisstip-admin --actor "Anna Meier"  name the person who writes
    swisstip-admin --read-only           evaluator view: no write and no job route
    swisstip-admin --host 0.0.0.0 --auth-file .local/console-users.txt

The packs live in their own repository: `--packs-dir` (or SWISSTIP_PACKS)
names the folder that holds `releases/<pack>` and `.local/<pack>`; this
checkout holds no pack.

Routes are grouped by screen under `screens/`, one module and one template
folder per screen of section 4 of docs/architecture/admin-console.md. In
read-only mode the write and job routers are not registered at all, so a
missing button is not the only thing that stops a write.
"""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Iterable

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import CONSOLE_NAME, CONSOLE_VERSION
from .data import Console, PackData
from .jobs import JobRunner
from .writes import WriteConflict, WriteRefused

PACKS_VARIABLE = "SWISSTIP_PACKS"
PACKAGE = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

templates = Jinja2Templates(directory=str(PACKAGE / "views"))


# --- shared template helpers ------------------------------------------------


def shorten(value, length: int = 12) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= length else text[:length] + "..."


def as_json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def block_number(block_id: str) -> int:
    return int(str(block_id).rsplit(":b", 1)[-1])


templates.env.filters["shorten"] = shorten
templates.env.filters["as_json"] = as_json
templates.env.filters["block_number"] = block_number
templates.env.globals["console_version"] = CONSOLE_VERSION


def render(request: Request, name: str, *, status_code: int = 200, **context) -> HTMLResponse:
    """Every screen renders through here, so the header is the same everywhere."""
    app = request.app
    pack = context.get("pack")
    values = dict(
        console=app.state.console, read_only=app.state.console.read_only, actor=actor_of(request),
        layout="fragment.html" if request.headers.get("hx-request") else "base.html",
        packs=app.state.console.pack_names(), pack_name=pack.pack if isinstance(pack, PackData) else pack,
        notice=request.query_params.get("notice"), error=request.query_params.get("error"), active=None)
    values.update(context)  # a screen may name its own `error` or `notice`
    return templates.TemplateResponse(request, name, values, status_code=status_code)


def actor_of(request: Request) -> str:
    return getattr(request.state, "actor", None) or request.app.state.console.actor


# --- authentication (hosted mode, section 9) --------------------------------


def read_users(path: Path) -> dict[str, tuple[str, str]]:
    """`user:<sha256 of password>:<editor|reader>` per line; the file lives outside Git."""
    users: dict[str, tuple[str, str]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            raise ValueError(f"Bad line in {path}: expected user:sha256:role")
        users[parts[0]] = (parts[1].lower(), parts[2].strip().lower())
    return users


def authenticate(request: Request) -> str:
    """HTTP basic; returns the user name. No secret is ever rendered."""
    users = request.app.state.users
    if not users:
        return request.app.state.console.actor
    header = request.headers.get("authorization", "")
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required",
                                 headers={"WWW-Authenticate": "Basic realm=\"Swiss TIP admin console\""})
    if not header.lower().startswith("basic "):
        raise unauthorized
    try:
        name, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception as exc:
        raise unauthorized from exc
    expected = users.get(name)
    if expected is None or not secrets.compare_digest(expected[0], hashlib.sha256(password.encode("utf-8")).hexdigest()):
        raise unauthorized
    request.state.actor = name
    request.state.role = expected[1]
    return name


def require_editor(request: Request, user: str = Depends(authenticate)) -> str:
    """Write endpoints are for users listed as editors; in local mode there is one actor."""
    if request.app.state.users and getattr(request.state, "role", "reader") != "editor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This user may read but not write")
    origin = request.headers.get("origin")
    if not request.app.state.users and origin is not None:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if not loopback_host(host):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Unauthenticated console writes require a loopback Host")
    if origin is not None:
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin != expected:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Console writes require a same-origin request")
    return user


def loopback_host(value: str) -> bool:
    host = value.strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def get_pack(request: Request, pack: str) -> PackData:
    try:
        return request.app.state.console.pack(pack)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown pack {pack!r}") from exc


# --- application ------------------------------------------------------------


def create_app(root: Path, *, read_only: bool = False, actor: str = "operator",
               run_dirs: dict[str, Path] | None = None, server_log: Path | None = None,
               auth_file: Path | None = None) -> FastAPI:
    from .graph_data import GraphConsole
    from .screens import documents, graph, operations, packs, release, review, runs, sandbox, sources, workbench, workflow

    console = Console(root, read_only=read_only, actor=actor, run_dirs=run_dirs, server_log=server_log)
    users = read_users(auth_file) if auth_file else {}
    # Hosted: every page needs a user, so the actor on a write is the authenticated one.
    app = FastAPI(title="Swiss TIP admin console", version=CONSOLE_VERSION, docs_url=None, redoc_url=None,
                  dependencies=[Depends(authenticate)] if users else [])
    app.state.console = console
    app.state.jobs = JobRunner()
    app.state.users = users
    app.state.graphs = GraphConsole(root)
    if not users:
        app.add_middleware(TrustedHostMiddleware,
                           allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")
    for name in console.pack_names():
        app.state.jobs.mark_interrupted(console.pack(name))
    for name in app.state.graphs.names():
        app.state.jobs.mark_interrupted(app.state.graphs.graph(name))

    @app.exception_handler(WriteRefused)
    async def refused(request: Request, exc: WriteRefused):
        if wants_json(request):
            return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                content=dict(refused=exc.message, issues=exc.issues, fields=exc.fields))
        return render(request, "refused.html", status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                      message=exc.message, issues=exc.issues, fields=exc.fields,
                      back=request.headers.get("referer", "/"))

    @app.exception_handler(WriteConflict)
    async def conflict(request: Request, exc: WriteConflict):
        return render(request, "conflict.html", status_code=status.HTTP_409_CONFLICT, message=exc.message,
                      last_actor=exc.last_actor, current_sha256=exc.current_sha256,
                      back=request.headers.get("referer", "/"))

    @app.get("/healthz")
    def healthz():
        return dict(status="ok", name=CONSOLE_NAME, version=CONSOLE_VERSION, root=str(console.root),
                    mode="read-only" if read_only else "edit", packs=console.pack_names())

    for module in (packs, workflow, sources, runs, documents, workbench, review, release, sandbox, operations, graph):
        app.include_router(module.read_router)
        if not read_only and hasattr(module, "write_router"):
            app.include_router(module.write_router)
    return app


def wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--packs-dir", type=Path, default=os.environ.get(PACKS_VARIABLE) or None,
                        help=f"the packs repository, holding releases/<pack> and .local/<pack>; default ${PACKS_VARIABLE}")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address; default {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--actor", default="operator", help="name recorded on every write")
    parser.add_argument("--read-only", action="store_true", help="register no write and no job route")
    parser.add_argument("--run-dir", action="append", default=[], metavar="PACK=DIR",
                        help="run directory of a pack; default releases/<pack> when it holds a run, else .local/<pack>")
    parser.add_argument("--server-log", type=Path, help="log file of a served release, for the operations screen")
    parser.add_argument("--auth-file", type=Path, help="HTTP basic users: user:sha256:editor|reader per line")
    parser.add_argument("--hash-password", metavar="PASSWORD", help="print the SHA-256 for an --auth-file line and exit")
    args = parser.parse_args(argv)
    if args.hash_password:
        print(hashlib.sha256(args.hash_password.encode("utf-8")).hexdigest())
        return 0
    if args.packs_dir is None:
        print(f"error: no packs directory: pass --packs-dir DIR or set {PACKS_VARIABLE}", file=sys.stderr)
        return 2
    run_dirs = {}
    for item in args.run_dir:
        name, _, path = item.partition("=")
        if not path:
            parser.error("--run-dir takes PACK=DIR")
        run_dirs[name] = Path(path)
    if not loopback_host(args.host) and not args.auth_file:
        print("error: binding beyond loopback requires --auth-file", file=sys.stderr)
        return 2
    app = create_app(args.packs_dir, read_only=args.read_only, actor=args.actor, run_dirs=run_dirs,
                     server_log=args.server_log, auth_file=args.auth_file)
    import uvicorn
    print(f"{CONSOLE_NAME} {CONSOLE_VERSION} on http://{args.host}:{args.port} "
          f"({'read-only' if args.read_only else 'edit'} mode, actor {args.actor!r}, packs {args.packs_dir})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
