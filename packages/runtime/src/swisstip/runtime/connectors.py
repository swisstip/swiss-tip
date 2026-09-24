"""The dataset connectors a server registers, and what it forwards to them.

Section 5 of docs/architecture/dataset-connectors.md. At startup, and again
on every health probe while a connector is unreachable, the registry reads
each connector's manifest and binds every dataset that stands behind a
concept of the served release: the pack must be the release's, the concept
must exist, and the dataset's jurisdiction must lie inside one the concept
publishes for. A dataset that fails one of these, or that the connector
itself lists as invalid, is rejected with its reason and reported in the
health payload. A connector that cannot be reached leaves the server as it
is: the release's tools never depend on one.

The HTTP client is the standard library's; a test passes its own.
"""

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from swisstip.core import CONNECTOR_SCHEMA_VERSION
from swisstip.core.connector import ConnectorError, ConnectorLookupResponse, ConnectorManifest, DatasetSummary
from swisstip.core.release import Release
from swisstip.core.validation import contains

log = logging.getLogger("swiss-tip.connectors")
KNOWN_TYPES = ("calendar",)
Client = Callable[[str, str, dict | None], tuple[int, dict]]


class ConnectorUnavailable(Exception):
    """The connector did not answer, or answered something other than the contract."""


def http_client(timeout: float = 10) -> Client:
    """GET or POST one JSON document; the status and the decoded body, or ConnectorUnavailable."""

    def call(url: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url.rstrip("/") + path, data=data,
                                         headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            except ValueError as exc:
                raise ConnectorUnavailable(f"{url}: HTTP {error.code} without a JSON body") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ConnectorUnavailable(f"{url}: {exc}") from exc

    return call


@dataclass
class Registered:
    url: str
    summary: DatasetSummary


@dataclass
class ConnectorEntry:
    url: str
    status: str = "unreachable"
    reason: str | None = None
    connector_id: str | None = None
    registered: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def health(self) -> dict:
        return dict(url=self.url, status=self.status, **(dict(reason=self.reason) if self.reason else {}),
                    connector_id=self.connector_id, registered=list(self.registered), rejected=list(self.rejected))


class ConnectorRegistry:
    def __init__(self, urls: list[str], release: Release, client: Client | None = None):
        self.release = release
        self.client = client or http_client()
        self.entries = [ConnectorEntry(url=url) for url in urls]
        self.datasets: dict[str, Registered] = {}
        self.refresh()

    # --- registration ---------------------------------------------------------------------------------------------

    def refresh(self, only_unreachable: bool = False) -> None:
        for entry in self.entries:
            if only_unreachable and entry.status == "ok":
                continue
            try:
                status, payload = self.client(entry.url, "/manifest", None)
                if status != 200:
                    raise ConnectorUnavailable(f"{entry.url}: HTTP {status} on /manifest")
                manifest = ConnectorManifest.model_validate(payload)
            except ConnectorUnavailable as exc:
                self.mark(entry, "unreachable", str(exc))
                continue
            except ValidationError as exc:
                self.mark(entry, "manifest_invalid", f"{entry.url}: {exc.errors()[0]['msg']}")
                continue
            self.register(entry, manifest)

    def mark(self, entry: ConnectorEntry, status: str, reason: str) -> None:
        if entry.status != status or entry.reason != reason:
            log.warning("connector %s: %s", status, reason)
        entry.status, entry.reason = status, reason
        for dataset_id in entry.registered:
            self.datasets.pop(dataset_id, None)
        entry.registered, entry.rejected = [], []

    def register(self, entry: ConnectorEntry, manifest: ConnectorManifest) -> None:
        for dataset_id in entry.registered:
            self.datasets.pop(dataset_id, None)
        entry.status, entry.reason, entry.connector_id = "ok", None, manifest.connector_id
        entry.registered, entry.rejected = [], []
        concepts = {concept.concept_id: concept for concept in self.release.concepts}
        for summary in manifest.datasets:
            reason = self.binding_issue(summary, concepts)
            if reason:
                entry.rejected.append(dict(dataset_id=summary.dataset_id, reason=reason))
                log.warning("connector %s: dataset %s rejected: %s", entry.url, summary.dataset_id, reason)
                continue
            self.datasets[summary.dataset_id] = Registered(url=entry.url, summary=summary)
            entry.registered.append(summary.dataset_id)
        log.info("connector %s (%s): %d dataset(s) registered, %d rejected", entry.url, manifest.connector_id,
                 len(entry.registered), len(entry.rejected))

    def binding_issue(self, summary: DatasetSummary, concepts: dict) -> str | None:
        if summary.type not in KNOWN_TYPES:
            return f"type {summary.type!r} is not one this server serves ({', '.join(KNOWN_TYPES)})"
        if summary.status != "ok":
            return f"the connector lists it as invalid: {summary.issue}"
        if summary.dataset_id in self.datasets:
            return f"dataset {summary.dataset_id} is already registered from {self.datasets[summary.dataset_id].url}"
        pack = self.release.manifest.pack
        if summary.pack != pack:
            return f"bound to pack {summary.pack!r}, this server serves {pack!r}"
        concept = concepts.get(summary.concept_id)
        if concept is None:
            return f"concept {summary.concept_id!r} is not published by this release"
        if not any(contains(published, summary.jurisdiction) for published in concept.jurisdictions):
            return (f"jurisdiction {summary.jurisdiction} lies outside those the concept publishes for "
                    f"({', '.join(concept.jurisdictions)})")
        return None

    # --- serving --------------------------------------------------------------------------------------------------

    def offers(self, concept_id: str, requested: str) -> list[DatasetSummary]:
        """The datasets behind a concept whose jurisdiction contains the resolved place, in registration order."""
        return [item.summary for item in self.datasets.values()
                if item.summary.concept_id == concept_id and contains(item.summary.jurisdiction, requested)]

    def lookup(self, dataset_id: str, body: dict) -> ConnectorLookupResponse | ConnectorError:
        """Forward one lookup; ConnectorUnavailable when the connector does not answer in the contract's shape."""
        item = self.datasets[dataset_id]
        status, payload = self.client(item.url, "/lookup", body)
        try:
            if status == 400:
                return ConnectorError.model_validate(payload)
            if status != 200:
                raise ConnectorUnavailable(f"{item.url}: HTTP {status} on /lookup")
            return ConnectorLookupResponse.model_validate(payload)
        except ValidationError as exc:
            raise ConnectorUnavailable(f"{item.url}: the answer is not a {CONNECTOR_SCHEMA_VERSION} shape: "
                                       f"{exc.errors()[0]['msg']}") from exc

    def health(self) -> list[dict]:
        self.refresh(only_unreachable=True)
        return [entry.health() for entry in self.entries]
