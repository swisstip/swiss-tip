"""Read the official register of Swiss municipalities into a place file.

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
"""

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

from .crawler import DEFAULT_USER_AGENT

PLACES_SCHEMA_VERSION = "swiss-tip-places/v1"
SNAPSHOT_URL = "https://www.agvchapp.bfs.admin.ch/api/communes/snapshot?date={day:%d-%m-%Y}"
TITLE = "Amtliches Gemeindeverzeichnis der Schweiz (official register of Swiss municipalities), snapshot of {day:%d.%m.%Y}"
PUBLISHER = "Federal Statistical Office FSO"
COUNTRY = "CH"


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


def fetch(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True, help="place file to write, for example config/places/ch-register.json")
    parser.add_argument("--snapshot", type=Path, help="a saved snapshot CSV to read instead of fetching the register")
    parser.add_argument("--accessed-on", type=date.fromisoformat, default=date.today(),
                        help="the day the saved snapshot was fetched and is for; today when fetching")
    args = parser.parse_args(argv)
    day = args.accessed_on if args.snapshot else date.today()
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
