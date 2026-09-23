"""The place register a release embeds: the official place file plus the hand-written aliases.

A curation file names two files, relative to itself:

    place_register: ../../config/places/ch-register.json
    place_aliases: ../../config/places/ch-aliases.json

The place file is written by `swisstip-places` from the official register of
municipalities (cantons and municipalities with their codes and official
names, the URL, the access date and the hash of the response). The alias file
is written by hand: the country's own entry, which the register does not
list, the other-language names of cantons and cities ("Geneva", "Genf"), and
the generic words a caller may put around a name ("Canton of", "Stadt").
Every pack of a country embeds the whole register, not its own places only:
a user elsewhere still has to be placed to be told what applies to them.

An alias for a code the register does not list fails the build: after a
merger of municipalities the number is gone and the alias has to follow.
"""

import json
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.places import place_key
from swisstip.core.release import Place, PlaceRegister

from .curation import Curation

PLACES_SCHEMA_VERSION = "swiss-tip-places/v1"
ALIASES_SCHEMA_VERSION = "swiss-tip-place-aliases/v1"


class PlaceFileError(ValueError):
    pass


def read_json(path: Path, schema_version: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlaceFileError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != schema_version:
        raise PlaceFileError(f"{path} is not a {schema_version} file")
    return data


def load_place_register(register_path: Path, aliases_path: Path | None = None) -> PlaceRegister:
    register = read_json(register_path, PLACES_SCHEMA_VERSION)
    aliases = read_json(aliases_path, ALIASES_SCHEMA_VERSION) if aliases_path else {}
    country = aliases.get("country") or dict(code=register["country"], name=register["country"])
    if country["code"] != register["country"]:
        raise PlaceFileError(f"{aliases_path} is for {country['code']}, the register for {register['country']}")
    by_code = dict(aliases.get("aliases") or {})
    places = [dict(country), *register["places"]]
    unknown = sorted(set(by_code) - {place["code"] for place in places})
    if unknown:
        raise PlaceFileError(f"{aliases_path} names places the register does not list: {', '.join(unknown)}")
    merged = []
    for place in places:
        # An alias that folds to the official name or to an earlier alias adds nothing; the rest keep their order.
        seen, extra = {place_key(place["name"])}, []
        for alias in [*place.get("aliases", []), *by_code.get(place["code"], [])]:
            if place_key(alias) not in seen:
                seen.add(place_key(alias))
                extra.append(alias)
        merged.append(Place(code=place["code"], name=place["name"], aliases=extra))
    try:
        return PlaceRegister(title=register["title"], publisher=register["publisher"], url=register["url"],
                             accessed_on=register["accessed_on"], raw_sha256=register["raw_sha256"],
                             generic_words=list(dict.fromkeys(aliases.get("generic_words") or [])), places=merged)
    except (KeyError, ValidationError) as exc:
        raise PlaceFileError(f"{register_path} does not make a place register: {exc}") from exc


def place_files(curation: Curation, curation_path: Path) -> list[Path]:
    """The files the curation names, resolved against the folder of the curation file; empty when it names none."""
    curation_path = Path(curation_path).resolve()
    base = curation_path.parent
    candidates = [(base / name).resolve() for name in (curation.place_register, curation.place_aliases) if name]
    # Canonical layout: <workspace>/releases/<pack>/curation.yaml. Standalone builds are contained by their
    # curation tree as well; place inputs never become arbitrary host-file reads.
    workspace = curation_path.parents[2] if base.parent.name == "releases" else base.parent
    for path in candidates:
        if not path.is_relative_to(workspace):
            raise PlaceFileError(f"place file leaves the packs workspace: {path}")
    return candidates


def place_register_for(curation: Curation, curation_path: Path) -> PlaceRegister | None:
    if not curation.place_register:
        if curation.place_aliases:
            raise PlaceFileError("the curation names place_aliases without a place_register")
        return None
    files = place_files(curation, curation_path)
    return load_place_register(files[0], files[1] if len(files) > 1 else None)
