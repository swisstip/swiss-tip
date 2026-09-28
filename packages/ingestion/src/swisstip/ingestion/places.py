"""Read an official register of places into a place file: the Swiss municipalities or the Polish TERC.

    swisstip-places --output config/places/ch-register.json

The Federal Statistical Office publishes the Amtliches Gemeindeverzeichnis
through the application of the Swiss municipalities; its snapshot endpoint
returns, for a date, every canton, district and municipality valid on that
day as CSV (`HistoricalCode, BfsCode, ValidFrom, ValidTo, Level, Parent,
Name, ShortName, ...`, level 1 a canton, 2 a district, 3 a municipality).
The place file keeps the cantons and the municipalities with the jurisdiction
codes the releases use (`CH-ZH`, `CH-ZH-261`: the canton's abbreviation and
the BFS number), the official names as the register spells them, and where,
when and with which hash the register was read. Districts are dropped: no
fact is published for one.

Municipalities merge, mostly on 1 January, and their numbers retire, so the
file is dated data: fetch it again and rebuild the releases when it changes.
The release build embeds it together with the hand-written aliases
(`swisstip.build.places`).

    swisstip-places --terc TERC_Urzedowy_<day>.csv --url <download page> --accessed-on <day>
                    --output config/places/pl-register.json

Statistics Poland (GUS) publishes TERC, the official register of the
territorial division of Poland, as full files on its eTERYT pages. The
download is a form, so the file is saved by hand and read with --terc, never
fetched; --url names the page it came from and --accessed-on the day. The CSV
(`WOJ;POW;GMI;RODZ;NAZWA;NAZWA_DOD;STAN_NA`) lists voivodeships, counties,
communes and parts of communes. The place file keeps the 16 voivodeships
(`PL-12`, the name lower-cased as in "województwo małopolskie"), the counties
(`PL-12-06` "powiat krakowski", `PL-12-61` "powiat m. Kraków") and the
communes (`PL-12-61-011`: the commune number followed by its type digit, RODZ
1, 2 or 3). It drops the town and rural parts of an urban-rural commune (RODZ
4 and 5), the districts of Warsaw (8) and the delegatury (9): they are parts
of a commune, not places inside it. Communes of one voivodeship that share a
name carry their type when the types differ ("Bolesławiec (gmina miejska)",
"Tarnów (gmina wiejska)"), otherwise their county ("Rogowo (powiat
rypiński)"), since a caller names the voivodeship and the city but never the
county. The register's own date (STAN_NA) goes into the title.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

from .crawler import DEFAULT_USER_AGENT

PLACES_SCHEMA_VERSION = "swiss-tip-places/v1"
SNAPSHOT_URL = "https://www.agvchapp.bfs.admin.ch/api/communes/snapshot?date={day:%d-%m-%Y}"
TITLE = "Amtliches Gemeindeverzeichnis der Schweiz (official register of Swiss municipalities), snapshot of {day:%d.%m.%Y}"
PUBLISHER = "Federal Statistical Office FSO"
COUNTRY = "CH"

TERC_TITLE = ("TERC - Krajowy rejestr urzędowy podziału terytorialnego kraju (official register of the territorial division "
              "of Poland), stan na {day:%Y-%m-%d}")
TERC_PUBLISHER = "Główny Urząd Statystyczny (Statistics Poland)"
TERC_COUNTRY = "PL"
TERC_COLUMNS = ("WOJ", "POW", "GMI", "RODZ", "NAZWA", "NAZWA_DOD", "STAN_NA")
TERC_VOIVODESHIPS = 16
TERC_SEGMENT = re.compile(r"[0-9]{2}")
# A county's name by its NAZWA_DOD: a land county, a city with county rights, the capital.
TERC_COUNTY_NAMES = {"powiat": "powiat {}", "miasto na prawach powiatu": "powiat m. {}",
                     "miasto stołeczne, na prawach powiatu": "powiat m. st. {}"}
# RODZ of a commune row: an urban (1), rural (2) or urban-rural (3) commune is kept; the town (4) and the rural area (5)
# of an urban-rural commune, a district of Warsaw (8) and a delegatura (9) are parts of a commune and are dropped.
TERC_KEPT = frozenset("123")
TERC_DROPPED = frozenset("4589")


class RegisterError(ValueError):
    pass


def parse_snapshot(text: str) -> list[dict]:
    """The cantons and municipalities of a snapshot as `{code, name}`, cantons first, each group by code.

    A municipality hangs on its district and the district on its canton; a canton without districts carries its
    municipalities directly. The historical codes of the three levels overlap, so a parent is looked up within the
    level it must have."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows or not {"BfsCode", "Level", "Parent", "Name", "ShortName", "HistoricalCode"} <= set(rows[0]):
        raise RegisterError("not a snapshot of the municipality register: the expected columns are missing")
    cantons = {row["HistoricalCode"]: row for row in rows if row["Level"] == "1"}
    districts = {row["HistoricalCode"]: row for row in rows if row["Level"] == "2"}
    places = []
    for row in cantons.values():
        abbreviation = row["ShortName"].strip().upper()
        if len(abbreviation) != 2 or not abbreviation.isalpha():
            raise RegisterError(f"canton {row['Name']!r} has no two-letter abbreviation: {row['ShortName']!r}")
        places.append(dict(code=f"{COUNTRY}-{abbreviation}", name=row["Name"].strip()))
    municipalities = []
    for row in rows:
        if row["Level"] != "3":
            continue
        parent = districts.get(row["Parent"])
        canton = cantons.get(parent["Parent"]) if parent else cantons.get(row["Parent"])
        if canton is None:
            raise RegisterError(f"municipality {row['Name']!r} ({row['BfsCode']}) hangs on no canton")
        municipalities.append((canton["ShortName"].strip().upper(), int(row["BfsCode"]), row["Name"].strip()))
    places.sort(key=lambda place: place["code"])
    places += [dict(code=f"{COUNTRY}-{canton}-{number}", name=name) for canton, number, name in sorted(municipalities)]
    codes = [place["code"] for place in places]
    if len(set(codes)) != len(codes):
        raise RegisterError("the snapshot lists a code twice")
    if len(cantons) != 26:
        raise RegisterError(f"the snapshot lists {len(cantons)} cantons, not 26")
    return places


def place_file(raw: bytes, url: str, day: date) -> dict:
    return dict(schema_version=PLACES_SCHEMA_VERSION, country=COUNTRY, title=TITLE.format(day=day), publisher=PUBLISHER,
                url=url, accessed_on=day.isoformat(), raw_sha256=hashlib.sha256(raw).hexdigest(),
                places=parse_snapshot(raw.decode("utf-8")))


def parse_terc(raw: bytes) -> tuple[list[dict], date, Counter]:
    """The voivodeships, counties and communes of a TERC file as `{code, name}` sorted by code, the day the register
    states (STAN_NA) and the dropped commune rows per RODZ.

    Every segment is kept as the register writes it, zeros included: the code is the country, WOJ, POW and GMI
    followed by RODZ. A file that is not the whole register, or not one register, is refused."""
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), delimiter=";")
        missing = [column for column in TERC_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise RegisterError(f"not a TERC file: missing columns {', '.join(missing)}")
        rows = [{column: (row[column] or "").strip() for column in TERC_COLUMNS} for row in reader]
    except csv.Error as exc:
        raise RegisterError(f"not a TERC file: {exc}") from exc
    voivodeships, counties, communes, codes, parents, dropped = {}, {}, [], set(), [], Counter()
    for row in rows:
        woj, county, commune, kind, name = row["WOJ"], row["POW"], row["GMI"], row["RODZ"], row["NAZWA"]
        malformed = not TERC_SEGMENT.fullmatch(woj) or (commune and not county)
        if malformed or any(part and not TERC_SEGMENT.fullmatch(part) for part in (county, commune)):
            raise RegisterError(f"{name!r} has a malformed code: WOJ {woj!r}, POW {county!r}, GMI {commune!r}")
        if kind not in (TERC_KEPT | TERC_DROPPED if commune else {""}):
            raise RegisterError(f"{name!r} ({TERC_COUNTRY}-{'-'.join(filter(None, (woj, county, commune)))}) has an "
                                f"unknown RODZ {kind!r}")
        code = "-".join(filter(None, (TERC_COUNTRY, woj, county, commune + kind)))
        if code in codes:
            raise RegisterError(f"the file lists the code {code} twice")
        codes.add(code)
        if not county:
            voivodeships[code] = dict(code=code, name=name.lower())
        elif not commune:
            template = TERC_COUNTY_NAMES.get(row["NAZWA_DOD"])
            if template is None:
                raise RegisterError(f"county {code} ({name}) has an unexpected NAZWA_DOD {row['NAZWA_DOD']!r}")
            counties[code] = dict(code=code, name=template.format(name))
            parents.append(("county", code, f"{TERC_COUNTRY}-{woj}", voivodeships, "voivodeship"))
        else:
            parents.append(("commune", code, f"{TERC_COUNTRY}-{woj}-{county}", counties, "county"))
            if kind in TERC_DROPPED:
                dropped[int(kind)] += 1
            else:
                communes.append((code, f"{TERC_COUNTRY}-{woj}-{county}", name, row["NAZWA_DOD"]))
    days = sorted({row["STAN_NA"] for row in rows})
    if len(days) > 1:
        raise RegisterError(f"the file states more than one STAN_NA: {', '.join(days)}")
    for level, code, parent, listed, parent_level in parents:
        if parent not in listed:
            raise RegisterError(f"{level} {code} has no {parent_level}: {parent} is not listed")
    if len(voivodeships) != TERC_VOIVODESHIPS:
        raise RegisterError(f"the file lists {len(voivodeships)} voivodeships, not {TERC_VOIVODESHIPS}")
    # Communes of one voivodeship that share a name are told apart, because a caller names the voivodeship and the
    # city, never the county: by their type when the types differ (a city with county rights and the rural commune
    # around it: "Tarnów (gmina miejska)", "Tarnów (gmina wiejska)"), otherwise by their county ("Rogowo (powiat
    # rypiński)"). A name shared only with communes of other voivodeships stays as it is.
    groups: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    for commune in communes:
        groups.setdefault((commune[0].split("-")[1], commune[2]), []).append(commune)
    names = {}
    for members in groups.values():
        by_type = len({addition for _, _, _, addition in members}) == len(members)
        for code, county, name, addition in members:
            names[code] = name if len(members) == 1 else \
                f"{name} ({addition if by_type else counties[county]['name']})"
    places = [dict(code=code, name=names[code]) for code, _, _, _ in communes]
    repeated = Counter((place["code"].split("-")[1], place["name"]) for place in places)
    for (woj, name), count in sorted(repeated.items()):
        if count > 1:
            raise RegisterError(f"the name {name!r} is not unique in voivodeship {TERC_COUNTRY}-{woj}, even qualified")
    try:
        day = date.fromisoformat(days[0])
    except ValueError as exc:
        raise RegisterError(f"STAN_NA {days[0]!r} is not a date") from exc
    places = sorted([*voivodeships.values(), *counties.values(), *places], key=lambda place: place["code"])
    return places, day, dropped


def terc_place_file(raw: bytes, url: str, day: date) -> dict:
    """The place file of a saved TERC file: `raw_sha256` hashes the bytes read, the register's STAN_NA is in the title."""
    places, valid_on, _ = parse_terc(raw)
    return dict(schema_version=PLACES_SCHEMA_VERSION, country=TERC_COUNTRY, title=TERC_TITLE.format(day=valid_on),
                publisher=TERC_PUBLISHER, url=url, accessed_on=day.isoformat(), raw_sha256=hashlib.sha256(raw).hexdigest(),
                places=places)


def fetch(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def import_terc(args) -> int:
    try:
        raw = args.terc.read_bytes()
        result, dropped = terc_place_file(raw, args.url, args.accessed_on), parse_terc(raw)[2]
    except (OSError, RegisterError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    levels = [place["code"].count("-") for place in result["places"]]
    print(json.dumps(dict(output=str(args.output), voivodeships=levels.count(1), counties=levels.count(2),
                          communes=levels.count(3), dropped={str(kind): count for kind, count in sorted(dropped.items())},
                          raw_sha256=result["raw_sha256"], accessed_on=result["accessed_on"]), indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True, help="place file to write, for example config/places/ch-register.json")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--snapshot", type=Path, help="a saved snapshot CSV to read instead of fetching the register")
    source.add_argument("--terc", type=Path, help="a saved TERC CSV of Statistics Poland to read (never fetched); needs --url "
                                                  "and --accessed-on")
    parser.add_argument("--url", help="with --terc: the page the TERC file was downloaded from")
    parser.add_argument("--accessed-on", type=date.fromisoformat,
                        help="the day the saved snapshot was fetched and is for, or the day the TERC file was downloaded; "
                             "today when fetching")
    args = parser.parse_args(argv)
    if args.terc:
        if not args.url or not args.accessed_on:
            parser.error("--terc needs --url (the page the file came from) and --accessed-on (the day it was downloaded)")
        return import_terc(args)
    if args.url:
        parser.error("--url goes with --terc; the snapshot URL of the municipality register is built from the day")
    day = (args.accessed_on or date.today()) if args.snapshot else date.today()
    url = SNAPSHOT_URL.format(day=day)
    try:
        raw = args.snapshot.read_bytes() if args.snapshot else fetch(url)
        result = place_file(raw, url, day)
    except (OSError, RegisterError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    levels = [place["code"].count("-") for place in result["places"]]
    print(json.dumps(dict(output=str(args.output), cantons=levels.count(1), municipalities=levels.count(2),
                          raw_sha256=result["raw_sha256"], accessed_on=result["accessed_on"]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
