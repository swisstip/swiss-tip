"""Screen 4.8: the tool sandbox and the stored checks.

Four panels, one per tool, each with a request form generated from the
contract models and a response pane that shows both the JSON the caller gets
and a rendered view. Every call is logged like a server call, so the expert
sees response sizes. A resolve or search result can be saved as a stored
check; "run all checks" runs them against the chosen release and writes the
result into `checks.yaml`.
"""

import json
import time
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from swisstip.core.contracts import ToolError
from swisstip.core.release import load_release
from swisstip.runtime.service import ReleaseService

from ..app import get_pack, render, require_editor
from ..calls import append_calls, parse_line
from ..checks import ChecksFile, Check, run_checks, seed_checks
from ..data import PackData
from ..writes import WriteRefused, write_checks

read_router = APIRouter()
write_router = APIRouter()

TOOLS = ("get_coverage", "search", "resolve", "get_evidence")


def service_for(pack: PackData, release_id: str | None) -> ReleaseService | None:
    """The pack's current release, or any release the console archived."""
    path = pack.release_path
    if release_id and release_id != pack.release_id():
        candidate = pack.console_dir / "releases" / f"{release_id}.json"
        if not candidate.is_file():
            return None
        path = candidate
    if not path.is_file():
        return None
    return ReleaseService(load_release(path))


def release_choices(pack: PackData) -> list[str]:
    current = pack.release_id()
    archived = [path.stem for path in pack.archived_releases()]
    return [item for item in dict.fromkeys([*( [current] if current else [] ), *sorted(archived, reverse=True)])]


def log_call(pack: PackData, tool: str, answer, milliseconds: float, release_id: str, query: str = "") -> dict:
    """One line in the sandbox log, the same shape the server writes."""
    payload = answer.model_dump(mode="json", exclude_none=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    status = payload["error"]["code"] if isinstance(answer, ToolError) else payload.get("status", "OK")
    parts = [f"tool={tool}", f"status={status}", f"bytes={len(text.encode('utf-8'))}",
             f"ms={milliseconds:.1f}", f"release={release_id}"]
    if tool == "search":
        hits = len(payload.get("results", []))
        parts.append(f"hits={hits}")
        if hits == 0 and query:
            parts.append(f"query=\"{query[:80]}\"")
    if tool == "resolve":
        gaps = [gap["dimension"] for item in payload.get("results", []) for gap in item.get("gaps", [])]
        missing = [item["field"] for entry in payload.get("results", []) for item in entry.get("missing_context", [])]
        if gaps or missing:
            parts.append(f"gap={(missing or gaps)[0]}")
    line = f"{date.today().isoformat()}T00:00:00 swiss-tip-sandbox INFO " + " ".join(parts)
    entry = parse_line(line)
    append_calls(pack.console_dir / "sandbox-calls.jsonl", [entry] if entry else [])
    return dict(entry or {}, text=text, payload=payload, bytes=len(text.encode("utf-8")))


@read_router.get("/packs/{pack}/sandbox")
def index(request: Request, pack: PackData = Depends(get_pack), release_id: str = ""):
    service = service_for(pack, release_id or None)
    checks = pack.checks.current()
    concepts = sorted(service.concepts) if service else []
    return render(request, "sandbox/index.html", pack=pack, active="sandbox", service=service,
                  release_id=(release_id or pack.release_id() or ""), releases=release_choices(pack),
                  checks=checks, checks_error=pack.checks.error, concepts=concepts,
                  topics=sorted(service.topics) if service else [],
                  context_fields=service.context_fields if service else {}, results=None,
                  cantons=sorted({fact.jurisdiction for fact in service.release.facts}) if service else [])


@read_router.post("/packs/{pack}/sandbox/{tool}")
def call_tool(request: Request, tool: str, pack: PackData = Depends(get_pack), release_id: str = Form(""),
              arguments: str = Form("{}")):
    """The request form posts the arguments as JSON, so what is sent is what the contract validates."""
    if tool not in TOOLS:
        return RedirectResponse(f"/packs/{pack.pack}/sandbox?error=unknown+tool", status_code=303)
    service = service_for(pack, release_id or None)
    if service is None:
        return RedirectResponse(f"/packs/{pack.pack}/sandbox?error=no+release+to+call", status_code=303)
    try:
        payload = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return render(request, "sandbox/result.html", pack=pack, tool=tool, error=f"the arguments are not JSON: {exc}",
                      call=None, answer=None, release_id=release_id)
    started = time.perf_counter()
    answer = service.dispatch(tool, payload)
    call = log_call(pack, tool, answer, (time.perf_counter() - started) * 1000, service.release_id,
                    str(payload.get("query", "")))
    return render(request, "sandbox/result.html", pack=pack, tool=tool, call=call, answer=answer, error=None,
                  arguments=json.dumps(payload, ensure_ascii=False), release_id=release_id or service.release_id,
                  is_error=isinstance(answer, ToolError))


@write_router.post("/packs/{pack}/sandbox/checks/seed")
def seed(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
         release_id: str = Form("")):
    if pack.checks.current() is not None:
        raise WriteRefused(f"{pack.checks_path.name} already exists")
    write_checks(pack, seed_checks(pack.pack, service_for(pack, release_id or None)), actor,
                 "seed the standing cases as stored checks", ["checks"])
    return RedirectResponse(f"/packs/{pack.pack}/sandbox?notice=checks+seeded", status_code=303)


@write_router.post("/packs/{pack}/sandbox/checks/run")
def run(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
        release_id: str = Form("")):
    checks = pack.checks.current()
    if checks is None:
        raise WriteRefused(f"no {pack.checks_path.name} yet; seed the standing cases first")
    service = service_for(pack, release_id or None)
    if service is None:
        raise WriteRefused("no release to run the checks against")
    updated, results = run_checks(checks, service)
    write_checks(pack, updated, actor, f"run {len(results)} check(s) against {service.release_id}",
                 [row["check_id"] for row in results])
    return render(request, "sandbox/checks.html", pack=pack, active="sandbox", results=results, checks=updated,
                  release_id=service.release_id)


@write_router.post("/packs/{pack}/sandbox/checks/save")
def save_as_check(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
                  check_id: str = Form(...), title: str = Form(""), tool: str = Form(...),
                  arguments: str = Form(...), expect_status: str = Form(""), expect_fact_ids: str = Form(""),
                  expect_concepts: str = Form("")):
    """Save as check: any resolve or search result becomes an expectation."""
    checks = pack.checks.current() or ChecksFile(pack=pack.pack)
    if checks.check(check_id.strip()):
        raise WriteRefused(f"check {check_id} already exists")
    statuses = {}
    for line in (expect_status or "").replace(",", "\n").splitlines():
        name, _, value = line.partition("=")
        if value.strip():
            statuses[name.strip()] = value.strip()
    try:
        check = Check(check_id=check_id.strip(), title=title.strip(), tool=tool,
                      request=json.loads(arguments or "{}"),
                      expect=dict(status=statuses,
                                  fact_ids=[item.strip() for item in expect_fact_ids.replace(",", "\n").splitlines()
                                            if item.strip()],
                                  concept_ids_in_top=[item.strip() for item
                                                      in expect_concepts.replace(",", "\n").splitlines()
                                                      if item.strip()]))
    except (ValueError, TypeError) as exc:
        raise WriteRefused(f"the check does not match the contract: {exc}") from exc
    updated = checks.model_copy(deep=True)
    updated.checks.append(check)
    write_checks(pack, updated, actor, f"save check {check.check_id} from a sandbox result", [check.check_id])
    return RedirectResponse(f"/packs/{pack.pack}/sandbox?notice=check+{check.check_id}+saved", status_code=303)
