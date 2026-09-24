"""Build a dataset bundle from its curation file: download, pin, import, validate.

Section 3 of docs/architecture/dataset-connectors.md. The curation file
`datasets/<pack>/<dataset>/dataset.yaml` names the sources, the importer
with its column mapping, the published period and the concept binding. The
build is the one step that reaches the network: it downloads every source
whose file is not at hand into the sources directory, hashes it, compares
the hash with the one the curation file pins (or records it on a first
download), turns the file into rows, validates the bundle and refuses to
write one that does not validate. A source whose downloaded bytes differ
from the pinned hash stops the build unless the refresh flag says the new
file is wanted; the curation file is then rewritten with the new pins.
"""

import csv
import hashlib
import io
import re
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from swisstip.core.datasets import (ZONE, Dataset, DatasetManifest, DatasetRow, DatasetSource, DatasetType, Period,
                                    RefreshPolicy, content_hash, row_key, source_filename, validate_dataset)

from . import DATASET_CURATION_SCHEMA_VERSION
from .curation import CurationDumper

USER_AGENT = "Mozilla/5.0 (compatible; SwissTIPDatasetBuild/0.1)"
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")
POSTAL_CODE = re.compile(r"^\d{4}$")
ZONE_PATTERN = re.compile(ZONE)
Fetch = Callable[[str], bytes]


class BuildError(ValueError):
    pass


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CuratedSource(Strict):
    """A source as the curator writes it: the URL, and the pins the build fills in on the first download."""

    url: str
    sha256: str | None = None
    bytes: int | None = None
    downloaded_on: date | None = None

    @property
    def pinned(self) -> bool:
        return self.sha256 is not None


class Columns(Strict):
    """The file's columns: exactly one of postal_code and zone names the key column."""

    postal_code: str | None = Field(default=None, description="Column holding the four-digit postal code.")
    zone: str | None = Field(default=None, description="Column holding the publisher's collection zone.")
    date: str = Field(description="Column holding the date (ISO, dd.mm.yyyy or dd/mm/yyyy).")
    location: str | None = Field(default=None, description="Column holding a place name, when the file has one.")

    @model_validator(mode="after")
    def one_key(self) -> "Columns":
        if (self.postal_code is None) == (self.zone is None):
            raise ValueError("name exactly one of the postal_code and zone columns")
        return self

    @property
    def key(self) -> str:
        return "zone" if self.zone is not None else "postal_code"


class Importer(Strict):
    name: Literal["csv-columns"] = "csv-columns"
    columns: Columns


class DatasetCuration(Strict):
    schema_version: Literal["swiss-tip-dataset-curation/v1"] = DATASET_CURATION_SCHEMA_VERSION
    dataset_id: str
    type: DatasetType
    title: str
    label: str
    pack: str
    concept_id: str
    jurisdiction: str
    publisher: str
    publisher_url: str
    licence: str
    refresh: RefreshPolicy = "bundled"
    sources: list[CuratedSource] = Field(min_length=1)
    importer: Importer
    period: Period
    zone_lookup_url: str | None = Field(default=None, description=(
        "For a dataset keyed by zone: the publisher's page where a resident finds their zone from the address."))
    notes: list[str] = Field(default_factory=list)


def load_dataset_curation(path: Path) -> DatasetCuration:
    return DatasetCuration.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def dump_dataset_curation(curation: DatasetCuration) -> str:
    data = curation.model_dump(mode="json", exclude_none=True)
    if not data.get("notes"):
        data.pop("notes", None)
    return yaml.dump(data, Dumper=CurationDumper, allow_unicode=True, sort_keys=False, width=110)


def save_dataset_curation(path: Path, curation: DatasetCuration) -> None:
    Path(path).write_text(dump_dataset_curation(curation), encoding="utf-8", newline="\n")


def fetch_url(url: str, timeout: float = 60) -> bytes:
    """Download one file, following redirects, as the build does when no other fetch is given."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_date(text: str) -> date:
    text = text.strip()
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise BuildError(f"unreadable date {text!r}")


def import_csv_columns(data: bytes, columns: Columns, source: str) -> tuple[list[DatasetRow], int]:
    """The rows of one CSV file, sorted and without repeats; the count of repeated rows dropped."""
    text = data.decode("utf-8-sig")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fields = reader.fieldnames or []
    key_column = columns.zone if columns.zone is not None else columns.postal_code
    wanted = [key_column, columns.date] + ([columns.location] if columns.location else [])
    missing = [name for name in wanted if name not in fields]
    if missing:
        raise BuildError(f"{source}: column(s) {missing} not found; the file has {fields}")
    rows: dict[tuple, DatasetRow] = {}
    repeated = 0
    for number, record in enumerate(reader, 2):
        value = " ".join((record[key_column] or "").split())
        if columns.zone is not None:
            if not ZONE_PATTERN.match(value):
                raise BuildError(f"{source}, line {number}: zone {value!r} is empty or longer than 40 characters")
            keyed = dict(zone=value)
        else:
            if not POSTAL_CODE.match(value):
                raise BuildError(f"{source}, line {number}: postal code {value!r} is not four digits")
            keyed = dict(postal_code=value)
        day = parse_date(record[columns.date] or "")
        location = (record[columns.location] or "").strip() or None if columns.location else None
        row = DatasetRow(**keyed, date=day, location=location)
        key = row_key(row)
        if key in rows:
            repeated += 1
        rows[key] = row
    return [rows[key] for key in sorted(rows)], repeated


IMPORTERS = {"csv-columns": import_csv_columns}


def standard_limitations(curation: DatasetCuration) -> list[str]:
    keyed = (f"Zones are the publisher's, and the user states their own zone; nothing checks that it is the zone of "
             f"their address, which the publisher's page {curation.zone_lookup_url} finds."
             if curation.importer.columns.key == "zone" else
             f"Postal codes are the publisher's; no register ties them to {curation.jurisdiction}.")
    return [
        "Calendar rows are taken from the publisher's file unchanged and are not human-reviewed; the hashes of the "
        "downloaded files and the build's checks are the whole gate.",
        keyed,
        f"Licence of the rows: {curation.licence}, published by {curation.publisher} at {curation.publisher_url}.",
    ]


def zone_fields(curation: DatasetCuration, rows: list[DatasetRow]) -> dict:
    """The manifest fields of a dataset keyed by zone; none for one keyed by postal code, whose bundle stays as it was."""
    if curation.importer.columns.key != "zone":
        if curation.zone_lookup_url is not None:
            raise BuildError("zone_lookup_url is set, but the importer names a postal_code column, not a zone column")
        return {}
    if not (curation.zone_lookup_url or "").startswith("https://"):
        raise BuildError("a dataset keyed by zone needs zone_lookup_url, the publisher's https page for finding a zone")
    return dict(key="zone", zones=sorted({row.zone for row in rows if row.zone is not None}),
                zone_lookup_url=curation.zone_lookup_url)


def build_dataset(curation: DatasetCuration, sources_dir: Path, *, fetch: Fetch | None = fetch_url, refresh: bool = False,
                  version: int = 1, today: date | None = None, created_at: datetime | None = None) -> tuple[Dataset, dict]:
    """The bundle and its report; the curation's sources carry the pins afterwards (the caller saves them).

    A source file at hand whose hash matches the pin is used without the network; one that is missing is
    downloaded with `fetch` (None: the build fails offline); one whose bytes differ from the pin stops the build
    unless `refresh` accepts the new file and re-pins it.
    """
    today = today or date.today()
    sources_dir = Path(sources_dir)
    sources_dir.mkdir(parents=True, exist_ok=True)
    importer = IMPORTERS[curation.importer.name]
    rows: dict[tuple, DatasetRow] = {}
    repeated = 0
    report_sources = []
    for source in curation.sources:
        path = sources_dir / source_filename(source.url)
        fetched = False
        if path.is_file() and source.pinned and hashlib.sha256(path.read_bytes()).hexdigest() == source.sha256:
            data = path.read_bytes()
        else:
            if fetch is None:
                raise BuildError(f"{path.name} is not at hand in {sources_dir} and the build has no network fetch")
            data = fetch(source.url)
            fetched = True
        digest = hashlib.sha256(data).hexdigest()
        if source.pinned and digest != source.sha256:
            if not refresh:
                raise BuildError(f"{path.name} differs from the hash pinned in the curation file ({digest[:12]} instead of "
                                 f"{source.sha256[:12]}); pass --refresh to accept the publisher's new file")
            source.downloaded_on = today
        elif not source.pinned:
            source.downloaded_on = today
        source.sha256, source.bytes = digest, len(data)
        if fetched:
            path.write_bytes(data)
        imported, dropped = importer(data, curation.importer.columns, path.name)
        repeated += dropped
        for row in imported:
            key = row_key(row)
            if key in rows:
                repeated += 1
            rows[key] = row
        report_sources.append(dict(url=source.url, file=path.name, sha256=digest, bytes=len(data),
                                   downloaded_on=source.downloaded_on.isoformat(), fetched=fetched, rows=len(imported)))
    ordered = [rows[key] for key in sorted(rows)]
    latest = max(source.downloaded_on for source in curation.sources)
    manifest = DatasetManifest(
        dataset_id=curation.dataset_id, dataset_version=f"{latest.isoformat()}-v{version}", type=curation.type,
        title=curation.title, label=curation.label, pack=curation.pack, concept_id=curation.concept_id,
        jurisdiction=curation.jurisdiction, publisher=curation.publisher, publisher_url=curation.publisher_url,
        licence=curation.licence, refresh=curation.refresh,
        sources=[DatasetSource(url=s.url, sha256=s.sha256, bytes=s.bytes, downloaded_on=s.downloaded_on) for s in curation.sources],
        period=curation.period, postal_codes=sorted({row.postal_code for row in ordered if row.postal_code is not None}),
        **zone_fields(curation, ordered), row_count=len(ordered),
        created_at=created_at or datetime.now(timezone.utc).replace(microsecond=0),
        limitations=standard_limitations(curation), content_sha256="0" * 64)
    dataset = Dataset(manifest=manifest, rows=ordered)
    dataset.manifest.content_sha256 = content_hash(dataset)
    issues = validate_dataset(dataset, sources_dir)
    if issues:
        raise BuildError(f"{len(issues)} issue(s): " + "; ".join(issues))
    report = dict(dataset_id=manifest.dataset_id, dataset_version=manifest.dataset_version, rows=len(ordered),
                  repeated_rows_dropped=repeated, postal_codes=len(manifest.postal_codes), zones=len(manifest.zones or []),
                  period=dict(start=curation.period.start.isoformat(), end=curation.period.end.isoformat()),
                  sources=report_sources)
    return dataset, report
