"""The contract between the server and a dataset connector (schema swiss-tip-connector/v1).

Section 5 of docs/architecture/dataset-connectors.md. A connector is a
separate process that serves dataset bundles over three HTTP routes; the
server reads its manifest at registration, binds every dataset to the
concept it stands behind, and forwards a caller's lookup. Both directions
are strict JSON objects. The connector composes nothing: no guidance, no
release ID, no limitations of the release; the server adds those on the way
out.

The reference semantics of the one connector type, the calendar, are here
too (calendar_lookup), so the connector app, the server's tests and a
connector written elsewhere answer alike.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import CONNECTOR_SCHEMA_VERSION
from .datasets import POSTAL_CODE, ZONE, Dataset, DatasetKey, DatasetSource, DatasetType, Period, absent, zone_key

CALENDAR_ACCEPTS = ["start", "end", "limit"]
LookupStatus = Literal["SUPPORTED", "OUT_OF_COVERAGE"]
ConnectorGapDimension = Literal["postal_code_not_covered", "zone_not_covered", "period_not_published", "no_dates_in_range"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetSummary(Strict):
    """One dataset as the manifest lists it: the binding, the provenance, what a lookup takes, and whether it loaded."""

    dataset_id: str
    dataset_version: str
    type: DatasetType
    title: str
    label: str
    pack: str
    concept_id: str
    jurisdiction: str
    publisher: str
    publisher_url: str
    licence: str
    sources: list[DatasetSource]
    period: Period
    postal_codes: list[str] = Field(description="So the server answers a code outside them without a round trip.")
    key: DatasetKey | None = Field(default=None, exclude_if=absent, description="postal_code when absent, else zone.")
    zones: list[str] | None = Field(default=None, exclude_if=absent, description="The zones as published, for a dataset keyed by zone.")
    zone_lookup_url: str | None = Field(default=None, exclude_if=absent, description=(
        "The publisher's page where a resident finds their zone, for a dataset keyed by zone."))
    row_count: int
    requires: list[str] = Field(description="The input fields a lookup must carry.")
    accepts: list[str] = Field(description="The optional input fields.")
    status: Literal["ok", "invalid"]
    issue: str | None = Field(default=None, exclude_if=absent, description="The validator's first message when invalid.")
    limitations: list[str]


class ConnectorManifest(Strict):
    schema_version: Literal["swiss-tip-connector/v1"] = CONNECTOR_SCHEMA_VERSION
    connector_id: str
    types: list[DatasetType]
    datasets: list[DatasetSummary]


class ConnectorLookupRequest(Strict):
    dataset_id: str
    postal_code: str | None = Field(default=None, pattern=POSTAL_CODE)
    zone: str | None = Field(default=None, pattern=ZONE, description="For a dataset keyed by zone, as the user gave it.")
    start: date | None = Field(default=None, description="First date to return; the server sends its as_of.")
    end: date | None = Field(default=None, description="Last date to return, inclusive; null means the end of the period.")
    limit: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def ordered(self) -> "ConnectorLookupRequest":
        if (self.postal_code is None) == (self.zone is None):
            raise ValueError("give exactly one of postal_code and zone")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end lies before start")
        return self


class ConnectorEvent(Strict):
    date: date
    label: str
    location: str | None = Field(default=None, exclude_if=absent)


class ConnectorProvenance(Strict):
    publisher: str
    publisher_url: str
    licence: str
    sources: list[DatasetSource]
    period: Period
    dataset_version: str


class ConnectorGap(Strict):
    dimension: ConnectorGapDimension
    message: str
    published_values: list[str]


class ConnectorLookupResponse(Strict):
    dataset_id: str
    dataset_version: str
    status: LookupStatus
    events: list[ConnectorEvent]
    truncated: bool = Field(description="More rows than limit fell into the range.")
    provenance: ConnectorProvenance
    gaps: list[ConnectorGap]


class ConnectorError(Strict):
    """The body of an HTTP 400: a malformed request. Anything else a connector answers is unavailability."""

    code: Literal["INVALID_ARGUMENT"] = "INVALID_ARGUMENT"
    message: str
    path: str | None = None


def summary_of(dataset: Dataset, issue: str | None = None) -> DatasetSummary:
    """The manifest line of a bundle; with an issue, the line of one the validator refused."""
    manifest = dataset.manifest
    return DatasetSummary(
        dataset_id=manifest.dataset_id, dataset_version=manifest.dataset_version, type=manifest.type,
        title=manifest.title, label=manifest.label, pack=manifest.pack, concept_id=manifest.concept_id,
        jurisdiction=manifest.jurisdiction, publisher=manifest.publisher, publisher_url=manifest.publisher_url,
        licence=manifest.licence, sources=manifest.sources, period=manifest.period, postal_codes=manifest.postal_codes,
        key=manifest.key, zones=manifest.zones, zone_lookup_url=manifest.zone_lookup_url,
        row_count=manifest.row_count, requires=[manifest.lookup_key], accepts=list(CALENDAR_ACCEPTS),
        status="invalid" if issue else "ok", issue=issue, limitations=manifest.limitations)


def provenance_of(dataset: Dataset) -> ConnectorProvenance:
    manifest = dataset.manifest
    return ConnectorProvenance(publisher=manifest.publisher, publisher_url=manifest.publisher_url, licence=manifest.licence,
                               sources=manifest.sources, period=manifest.period, dataset_version=manifest.dataset_version)


class WrongKey(ValueError):
    """A lookup that names the other key than the one the dataset is keyed by: a malformed request."""


def calendar_lookup(dataset: Dataset, request: ConnectorLookupRequest) -> ConnectorLookupResponse:
    """The dates of one postal code or zone inside a range, oldest first, at most `limit` of them.

    The range is the request's, clipped to the published period; a range wholly outside it is the
    period_not_published gap, a postal code or zone the rows do not hold the postal_code_not_covered or
    zone_not_covered gap, and a covered one with no date left in the range the no_dates_in_range gap. A zone is
    matched by zone_key, so "zone l-west" finds "L West". Dates are compared as calendar dates: no time of day, no
    time zone. A request with the other key raises WrongKey.
    """
    manifest = dataset.manifest
    period = manifest.period

    def answer(events: list[ConnectorEvent], truncated: bool = False, gap: ConnectorGap | None = None):
        return ConnectorLookupResponse(dataset_id=manifest.dataset_id, dataset_version=manifest.dataset_version,
                                       status="SUPPORTED" if events else "OUT_OF_COVERAGE", events=events,
                                       truncated=truncated, provenance=provenance_of(dataset), gaps=[gap] if gap else [])

    published = [period.start.isoformat(), period.end.isoformat()]
    key = manifest.lookup_key
    given = request.zone if request.zone is not None else request.postal_code
    if (request.zone is not None) != (key == "zone"):
        other = "zone" if request.zone is not None else "postal_code"
        raise WrongKey(f"dataset {manifest.dataset_id} is keyed by {key}; give {key}, not {other}")
    if key == "zone":
        held = {zone_key(zone): zone for zone in manifest.zones or []}
        wanted = held.get(zone_key(given))
        if wanted is None:
            zones = manifest.zones or []
            return answer([], gap=ConnectorGap(
                dimension="zone_not_covered", published_values=zones,
                message=f"{manifest.title}: no dates are published for zone {given!r}; the dataset holds the zones "
                        f"{', '.join(zones)} of {manifest.jurisdiction}. The resident finds their zone at "
                        f"{manifest.zone_lookup_url}."))
        label = f"zone {wanted}"
    elif given not in manifest.postal_codes:
        return answer([], gap=ConnectorGap(
            dimension="postal_code_not_covered", published_values=manifest.postal_codes,
            message=f"{manifest.title}: no dates are published for postal code {given}; the dataset "
                    f"holds {len(manifest.postal_codes)} postal codes of {manifest.jurisdiction}."))
    else:
        wanted, label = given, f"postal code {given}"
    start = request.start or period.start
    end = request.end or period.end
    if start > period.end or end < period.start:
        return answer([], gap=ConnectorGap(
            dimension="period_not_published", published_values=published,
            message=f"{manifest.title}: dates are published from {period.start.isoformat()} to {period.end.isoformat()} "
                    f"only; {start.isoformat()} to {end.isoformat()} lies outside that period."))
    rows = [row for row in dataset.rows if row.key == wanted and start <= row.date <= end]
    if not rows:
        return answer([], gap=ConnectorGap(
            dimension="no_dates_in_range", published_values=published,
            message=f"{manifest.title}: no date for {label} between {start.isoformat()} and "
                    f"{end.isoformat()}; the published period ends on {period.end.isoformat()}."))
    events = [ConnectorEvent(date=row.date, label=manifest.label, location=row.location) for row in rows[:request.limit]]
    return answer(events, truncated=len(rows) > request.limit)
