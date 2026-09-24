"""Serve dataset bundles to the MCP server over HTTP.

    swisstip-calendar-connector --dataset datasets/<pack>/<dataset>     one bundle directory per --dataset;
                                                                       without the option, SWISSTIP_DATASETS
                                                                       lists them, separated by the path separator
    swisstip-calendar-connector --datasets-dir datasets/<pack>         every subdirectory holding a dataset.json
    swisstip-calendar-connector --dataset ... --host 0.0.0.0 --port 8100
    swisstip-calendar-connector --dataset ... --health                 load, validate, print the datasets, exit

Every directory holds a `dataset.json` (swiss-tip-dataset/v1). Each bundle
is validated at startup; one that does not validate is listed in the
manifest as invalid with the validator's first message and is not served,
while the others are. The routes are those of the connector contract
(docs/architecture/dataset-connectors.md, section 5): GET /manifest, POST
/lookup and GET /health, plus a short index on /. The connector composes
nothing: no guidance, no release ID, no limitations of the release. It has
no MCP endpoint and is meant to be reached by the server only, on the
loopback address of a shared network namespace.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from swisstip.core.connector import (ConnectorError, ConnectorLookupRequest, ConnectorLookupResponse, ConnectorManifest,
                                     DatasetSummary, WrongKey, calendar_lookup, summary_of)
from swisstip.core.datasets import Dataset, load_dataset, validate_dataset

from . import CONNECTOR_ID, CONNECTOR_VERSION

DATASETS_VARIABLE = "SWISSTIP_DATASETS"
BUNDLE_NAME = "dataset.json"


class ConnectorService:
    """The loaded bundles: the valid ones are served, the others are listed with their issue."""

    def __init__(self, directories: list[Path]):
        self.datasets: dict[str, Dataset] = {}
        self.issues: dict[str, str] = {}
        self.summaries: list[DatasetSummary] = []
        for directory in directories:
            path = Path(directory) / BUNDLE_NAME
            dataset = load_dataset(path)
            dataset_id = dataset.manifest.dataset_id
            if dataset_id in self.datasets or dataset_id in self.issues:
                raise ValueError(f"dataset {dataset_id} is given twice, the second time as {path}")
            issues = validate_dataset(dataset)
            if issues:
                self.issues[dataset_id] = issues[0]
                self.summaries.append(summary_of(dataset, issue=issues[0]))
            else:
                self.datasets[dataset_id] = dataset
                self.summaries.append(summary_of(dataset))

    def manifest(self) -> ConnectorManifest:
        return ConnectorManifest(connector_id=CONNECTOR_ID, types=["calendar"], datasets=self.summaries)

    def lookup(self, arguments: dict) -> ConnectorLookupResponse | ConnectorError:
        try:
            request = ConnectorLookupRequest.model_validate(arguments)
        except ValidationError as exc:
            first = exc.errors()[0]
            return ConnectorError(message=first["msg"], path=".".join(str(part) for part in first["loc"]) or None)
        dataset = self.datasets.get(request.dataset_id)
        if dataset is None:
            issue = self.issues.get(request.dataset_id)
            served = sorted(self.datasets)
            message = (f"dataset {request.dataset_id} failed validation and is not served: {issue}" if issue else
                       f"unknown dataset {request.dataset_id!r}; served: {served}")
            return ConnectorError(message=message, path="dataset_id")
        try:
            return calendar_lookup(dataset, request)
        except WrongKey as exc:
            return ConnectorError(message=str(exc), path="zone" if request.zone is not None else "postal_code")

    def health(self, today: date | None = None) -> dict:
        today = today or date.today()
        return dict(status="ok", connector_id=CONNECTOR_ID, version=CONNECTOR_VERSION, schema=self.manifest().schema_version,
                    datasets=[dict(dataset_id=s.dataset_id, dataset_version=s.dataset_version, status=s.status,
                                   **(dict(issue=s.issue) if s.issue else {}),
                                   period=dict(start=s.period.start.isoformat(), end=s.period.end.isoformat()),
                                   row_count=s.row_count, expires_on=s.period.end.isoformat(), expired=s.period.end < today)
                              for s in self.summaries])


def create_http_app(service: ConnectorService) -> Starlette:
    log = logging.getLogger(CONNECTOR_ID)

    async def manifest_route(request: Request):
        return JSONResponse(service.manifest().model_dump(mode="json"))

    async def lookup_route(request: Request):
        started = time.perf_counter()
        try:
            arguments = await request.json()
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            result = ConnectorError(message="the request body must be a JSON object", path=None)
        else:
            result = service.lookup(arguments)
        is_error = isinstance(result, ConnectorError)
        payload = result.model_dump(mode="json", exclude_none=True)
        log.info("lookup dataset=%s status=%s ms=%.1f", (arguments or {}).get("dataset_id") if isinstance(arguments, dict) else None,
                 result.code if is_error else result.status, (time.perf_counter() - started) * 1000)
        return JSONResponse(payload, status_code=400 if is_error else 200)

    async def health_route(request: Request):
        return JSONResponse(service.health())

    async def index_route(request: Request):
        return JSONResponse(dict(name=CONNECTOR_ID, version=CONNECTOR_VERSION, manifest="/manifest", lookup="/lookup", health="/health"))

    return Starlette(routes=[Route("/manifest", endpoint=manifest_route, methods=["GET"]),
                             Route("/lookup", endpoint=lookup_route, methods=["POST"]),
                             Route("/health", endpoint=health_route, methods=["GET"]),
                             Route("/", endpoint=index_route, methods=["GET"])])


def dataset_directories(option: list[Path] | None, parents: list[Path] | None = None) -> list[Path]:
    """The bundle directories named one by one, plus every subdirectory of a parent that holds a bundle."""
    directories = [Path(item) for item in option or []]
    for parent in parents or []:
        directories.extend(sorted(child for child in Path(parent).iterdir() if (child / BUNDLE_NAME).is_file()))
    if directories:
        return directories
    listed = os.environ.get(DATASETS_VARIABLE, "")
    return [Path(item) for item in listed.split(os.pathsep) if item.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, action="append", help=f"a bundle directory; repeatable; default ${DATASETS_VARIABLE}")
    parser.add_argument("--datasets-dir", type=Path, action="append", help="a directory whose subdirectories hold bundles; repeatable")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8100), help="port to bind; default $PORT, else 8100")
    parser.add_argument("--health", action="store_true", help="load and validate the bundles, print the health payload, exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    directories = dataset_directories(args.dataset, args.datasets_dir)
    if not directories:
        print(json.dumps(dict(status="error", error=f"no dataset to serve: pass --dataset DIR or set {DATASETS_VARIABLE}")),
              file=sys.stdout if args.health else sys.stderr)
        return 2
    try:
        service = ConnectorService(directories)
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status="error", error=str(exc))), file=sys.stdout if args.health else sys.stderr)
        return 2
    if args.health:
        print(json.dumps(service.health(), indent=2))
        return 0 if service.datasets else 2
    log = logging.getLogger(CONNECTOR_ID)
    log.info("serving datasets=%s invalid=%s endpoint=http://%s:%d/", sorted(service.datasets), sorted(service.issues), args.host, args.port)
    uvicorn.run(create_http_app(service), host=args.host, port=args.port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
