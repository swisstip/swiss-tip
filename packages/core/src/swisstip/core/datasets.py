"""A dataset release: one versioned, hashed JSON file per table a connector serves.

Section 3 of docs/architecture/dataset-connectors.md. A dataset is a table a
municipality publishes as open data (the first ones: the waste-collection
calendars of the City of Zurich), served behind a published concept of a
knowledge release. The bundle is self-contained like a release: the manifest
names the publisher, the licence, the source files with their hashes and
download dates, the published period and the concept binding; the rows are
the normalised table, one event per row. The downloaded files are working
material of the build, not part of the bundle.

Rows are not reviewed by a person; the hashes and the validator are the
whole gate, and every manifest says so in its limitations.
"""

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import DATASET_SCHEMA_VERSION

DatasetType = Literal["calendar"]
RefreshPolicy = Literal["bundled"]
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
JURISDICTION = re.compile(r"^CH(?:-[A-Z]{2}(?:-\d{1,4})?)?$")
POSTAL_CODE = r"^\d{4}$"
SHA256 = r"^[0-9a-f]{64}$"


def absent(value) -> bool:
    return value is None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Period(Strict):
    """The calendar dates a dataset is published for, both inclusive."""

    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> "Period":
        if self.end < self.start:
            raise ValueError("period end lies before its start")
        return self

    def holds(self, day: date) -> bool:
        return self.start <= day <= self.end


class DatasetSource(Strict):
    """One downloaded file, pinned by its hash: a later build that finds another hash fails."""

    url: str = Field(description="The publisher's download URL; redirects are followed before hashing.")
    sha256: str = Field(pattern=SHA256)
    bytes: int = Field(ge=0)
    downloaded_on: date


class DatasetRow(Strict):
    """One event: a date for a postal code, with the location the publisher names when it names one."""

    postal_code: str = Field(pattern=POSTAL_CODE)
    date: date
    location: str | None = Field(default=None, exclude_if=absent, description=(
        "Free text as published, for a dataset whose events happen at a named place (a collection point)."))


class DatasetManifest(Strict):
    schema_version: Literal["swiss-tip-dataset/v1"] = DATASET_SCHEMA_VERSION
    dataset_id: str
    dataset_version: str = Field(description="<downloaded_on>-v<n>, formed the way a release ID is.")
    type: DatasetType
    title: str = Field(description="English: 'Organic waste collection days, City of Zurich'.")
    label: str = Field(description="The label every row is served with: 'Organic waste (Bioabfall)'.")
    pack: str = Field(description="The pack whose concept the dataset stands behind.")
    concept_id: str = Field(description="The concept the dataset is served behind; checked by the server at registration.")
    jurisdiction: str = Field(description="The municipality the rows are published for: CH-ZH-261.")
    publisher: str
    publisher_url: str = Field(description="The portal page of the dataset.")
    licence: str = Field(description="The licence the portal states, as an SPDX identifier where one exists: CC0-1.0.")
    refresh: RefreshPolicy = Field(default="bundled", description="bundled: the rows are what the build downloaded.")
    sources: list[DatasetSource] = Field(min_length=1)
    period: Period
    postal_codes: list[str] = Field(description="Sorted, as found in the rows.")
    row_count: int = Field(ge=0)
    created_at: datetime
    limitations: list[str]
    content_sha256: str = Field(pattern=SHA256, description="SHA-256 over the canonical JSON of the rows.")


class Dataset(Strict):
    manifest: DatasetManifest
    rows: list[DatasetRow]


class DatasetInvalid(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__(f"{len(issues)} dataset issue(s): " + "; ".join(issues[:5]))
        self.issues = issues


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(dataset: Dataset) -> str:
    rows = [row.model_dump(mode="json") for row in dataset.rows]
    return sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def row_key(row: DatasetRow) -> tuple[str, date, str]:
    return row.postal_code, row.date, row.location or ""


def source_filename(url: str) -> str:
    """The name a downloaded source is kept under: the last path segment of its URL."""
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    if not name:
        raise ValueError(f"source URL {url!r} names no file")
    return name


def validate_dataset(dataset: Dataset, sources_dir: Path | None = None) -> list[str]:
    """Every issue of a bundle; with sources_dir, also whether the downloaded files still match the manifest."""
    issues: list[str] = []
    manifest = dataset.manifest
    for name, value in (("dataset_id", manifest.dataset_id), ("dataset_version", manifest.dataset_version),
                        ("pack", manifest.pack), ("concept_id", manifest.concept_id)):
        if not IDENTIFIER.match(value):
            issues.append(f"malformed {name}: {value!r}")
    if not JURISDICTION.match(manifest.jurisdiction):
        issues.append(f"malformed jurisdiction: {manifest.jurisdiction!r}")
    for name in ("title", "label", "publisher", "publisher_url", "licence"):
        if not getattr(manifest, name).strip():
            issues.append(f"manifest {name} is empty")
    if content_hash(dataset) != manifest.content_sha256:
        issues.append("manifest content_sha256 does not match the rows")
    if not dataset.rows:
        issues.append("the dataset has no rows")
    keys = [row_key(row) for row in dataset.rows]
    if keys != sorted(keys):
        issues.append("rows are not sorted by postal code and date")
    if len(set(keys)) != len(keys):
        issues.append("rows are repeated")
    outside = sorted({row.date for row in dataset.rows if not manifest.period.holds(row.date)})
    if outside:
        issues.append(f"{len(outside)} date(s) lie outside the published period, first {outside[0].isoformat()}")
    codes = sorted({row.postal_code for row in dataset.rows})
    if codes != manifest.postal_codes:
        issues.append("manifest postal_codes differ from the rows")
    if manifest.row_count != len(dataset.rows):
        issues.append(f"manifest row_count is {manifest.row_count}, the bundle holds {len(dataset.rows)} rows")
    if len({source.url for source in manifest.sources}) != len(manifest.sources):
        issues.append("a source URL is listed twice")
    if sources_dir is not None:
        for source in manifest.sources:
            path = sources_dir / source_filename(source.url)
            if not path.is_file():
                issues.append(f"downloaded file {path.name} is missing from {sources_dir}")
                continue
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != source.sha256 or len(data) != source.bytes:
                issues.append(f"downloaded file {path.name} differs from the manifest; a changed upstream file "
                              "is a deliberate refresh, not a rebuild")
    return issues


def assert_valid_dataset(dataset: Dataset, sources_dir: Path | None = None) -> None:
    issues = validate_dataset(dataset, sources_dir)
    if issues:
        raise DatasetInvalid(issues)


def dump_dataset(dataset: Dataset) -> str:
    return json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def load_dataset(path: Path) -> Dataset:
    return Dataset.model_validate_json(Path(path).read_text(encoding="utf-8"))


def dataset_schema() -> dict:
    return Dataset.model_json_schema()
