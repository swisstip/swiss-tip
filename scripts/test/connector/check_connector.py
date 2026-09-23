"""Conformance test of a dataset connector: does it speak swiss-tip-connector/v1?

    ./.venv/Scripts/python.exe scripts/test/connector/check_connector.py
    ./.venv/Scripts/python.exe scripts/test/connector/check_connector.py --url http://127.0.0.1:8100

Without --url the calendar connector of this repository is started on a free port with the synthetic bundle of its
tests, so the check depends on no dataset; with --url it runs against a running connector, a container, whatever
language it is written in. It reads the manifest and validates it against the contract models, then, for every
dataset the manifest lists as ok, asks for the first postal code, for a code the dataset does not hold, for a range
outside the published period, and sends one malformed request, and checks the four answers against section 5 of
docs/architecture/dataset-connectors.md. A connector that passes can be registered with the server.
"""

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.connector import ConnectorError, ConnectorLookupResponse, ConnectorManifest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "apps" / "calendar-connector" / "tests" / "fixtures" / "test-waste-bioabfall"
failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


def call(url: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def lookup(url: str, body: dict) -> tuple[int, ConnectorLookupResponse | ConnectorError | None]:
    status, payload = call(url, "/lookup", body)
    try:
        return status, (ConnectorError if status == 400 else ConnectorLookupResponse).model_validate(payload)
    except ValidationError as exc:
        check(f"the answer to {body} is a contract shape: {exc.errors()[0]['msg']}", False)
        return status, None


def run(url: str) -> None:
    status, payload = call(url, "/manifest")
    check(f"GET /manifest answers 200", status == 200)
    try:
        manifest = ConnectorManifest.model_validate(payload)
    except ValidationError as exc:
        check(f"the manifest validates against swiss-tip-connector/v1: {exc.errors()[0]['msg']}", False)
        return
    check(f"connector {manifest.connector_id} serves types {manifest.types}", bool(manifest.types))
    served = [d for d in manifest.datasets if d.status == "ok"]
    check(f"the manifest lists {len(manifest.datasets)} dataset(s), {len(served)} served", bool(served))
    health_status, health = call(url, "/health")
    check("GET /health answers 200 with one line per dataset",
          health_status == 200 and {d["dataset_id"] for d in health.get("datasets", [])} == {d.dataset_id for d in manifest.datasets})
    for dataset in served:
        first = dataset.postal_codes[0]
        status, answer = lookup(url, dict(dataset_id=dataset.dataset_id, postal_code=first, start=dataset.period.start.isoformat(), limit=2))
        if answer is not None:
            dates = [event.date for event in answer.events]
            check(f"{dataset.dataset_id}: the first postal code {first} answers SUPPORTED, oldest first, at most 2",
                  status == 200 and answer.status == "SUPPORTED" and 0 < len(dates) <= 2 and dates == sorted(dates)
                  and all(dataset.period.start <= day <= dataset.period.end for day in dates)
                  and answer.provenance.dataset_version == dataset.dataset_version)
        outside = next(code for code in ("0000", "9999", "1234") if code not in dataset.postal_codes)
        status, answer = lookup(url, dict(dataset_id=dataset.dataset_id, postal_code=outside, limit=1))
        if answer is not None:
            check(f"{dataset.dataset_id}: postal code {outside} answers the postal_code_not_covered gap",
                  status == 200 and answer.status == "OUT_OF_COVERAGE" and [g.dimension for g in answer.gaps] == ["postal_code_not_covered"]
                  and answer.gaps[0].published_values == dataset.postal_codes)
        after = (dataset.period.end + timedelta(days=1)).isoformat()
        status, answer = lookup(url, dict(dataset_id=dataset.dataset_id, postal_code=first, start=after, limit=1))
        if answer is not None:
            check(f"{dataset.dataset_id}: a range from {after} answers the period_not_published gap",
                  status == 200 and answer.status == "OUT_OF_COVERAGE" and [g.dimension for g in answer.gaps] == ["period_not_published"])
        status, answer = lookup(url, dict(dataset_id=dataset.dataset_id, postal_code="12", limit=1))
        check(f"{dataset.dataset_id}: a malformed postal code is an HTTP 400 INVALID_ARGUMENT",
              status == 400 and isinstance(answer, ConnectorError))
    status, answer = lookup(url, dict(dataset_id="no-such-dataset", postal_code="8001", limit=1))
    check("an unknown dataset_id is an HTTP 400 INVALID_ARGUMENT", status == 400 and isinstance(answer, ConnectorError))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="a running connector; without it the repository's connector is started on the synthetic bundle")
    parser.add_argument("--dataset", type=Path, default=FIXTURE, help="bundle directory for the started connector")
    args = parser.parse_args(argv)
    process = None
    url = args.url
    if url is None:
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([sys.executable, "-m", "swisstip.calendar_connector.server", "--dataset", str(args.dataset),
                                    "--port", str(port), "--log-level", "WARNING"])
        for _ in range(100):
            try:
                call(url, "/health")
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                if process.poll() is not None:
                    print("FAIL the connector exited before answering")
                    return 1
                time.sleep(0.1)
    try:
        run(url)
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
    print(f"{len(failures)} failure(s)" if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
