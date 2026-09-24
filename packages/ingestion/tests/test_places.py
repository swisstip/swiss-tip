import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from swisstip.ingestion.places import PLACES_SCHEMA_VERSION, RegisterError, main, parse_snapshot, place_file

HEADER = "HistoricalCode,BfsCode,ValidFrom,ValidTo,Level,Parent,Name,ShortName,Inscription,Radiation,Rec_Type_fr,Rec_Type_de"
ABBREVIATIONS = ["ZH", "BE", "LU", "UR", "SZ", "OW", "NW", "GL", "ZG", "FR", "SO", "BS", "BL", "SH", "AR", "AI", "SG", "GR", "AG",
                 "TG", "TI", "VD", "VS", "NE", "GE", "JU"]


def snapshot(extra: list[str] = ()) -> str:
    """The 26 cantons, two districts and a few municipalities. The historical codes of the levels overlap on purpose:
    district 10078 (Horgen) and municipality 10078 (Vionnaz) share one, as in the register."""
    cantons = [f"{number},{number},12.09.1848,,1,,{'Zürich' if code == 'ZH' else 'Canton ' + code},{code},,,," for number, code in
               enumerate(ABBREVIATIONS, 1)]
    rows = ["10078,106,12.09.1848,,2,1,Bezirk Horgen,Horgen,100,,,", "10013,2304,12.09.1848,,2,23,District de Monthey,Monthey,,,,",
            "12701,135,12.09.1848,,3,10078,Kilchberg (ZH),Kilchberg (ZH),,,,", "11742,261,12.09.1848,,3,10078,Zürich,Zürich,,,,",
            "10078,6158,12.09.1848,,3,10013,Vionnaz,Vionnaz,,,,",
            # Basel-Stadt has no districts: its municipalities hang on the canton.
            "13000,2701,12.09.1848,,3,12,Basel,Basel,,,,"]
    return "﻿" + "\n".join([HEADER, *cantons, *rows, *extra]) + "\n"


class PlaceRegisterTests(unittest.TestCase):
    def test_cantons_and_municipalities_get_their_codes(self):
        places = parse_snapshot(snapshot())
        self.assertEqual(len(places), 30)
        self.assertEqual(places[0], {"code": "CH-AG", "name": "Canton AG"})
        self.assertIn({"code": "CH-ZH", "name": "Zürich"}, places)
        self.assertEqual(places[26:], [{"code": "CH-BS-2701", "name": "Basel"}, {"code": "CH-VS-6158", "name": "Vionnaz"},
                                       {"code": "CH-ZH-135", "name": "Kilchberg (ZH)"}, {"code": "CH-ZH-261", "name": "Zürich"}])
        self.assertNotIn("Bezirk Horgen", [place["name"] for place in places])

    def test_a_response_that_is_not_the_register_is_refused(self):
        for text, fragment in (("<html>maintenance</html>", "expected columns"), (HEADER + "\n", "expected columns"),
                               (snapshot().replace(",ZH,", ",Zuerich,"), "two-letter abbreviation"),
                               (snapshot(["99999,9999,12.09.1848,,3,55555,Nowhere,Nowhere,,,,"]), "hangs on no canton"),
                               (snapshot(["12702,135,12.09.1848,,3,10078,Twin,Twin,,,,"]), "lists a code twice"),
                               ("\n".join(snapshot().splitlines()[:-10]), "cantons, not 26")):
            with self.subTest(fragment=fragment):
                with self.assertRaisesRegex(RegisterError, fragment):
                    parse_snapshot(text)

    def test_the_place_file_says_where_and_when_the_register_was_read(self):
        raw = snapshot().encode("utf-8")
        result = place_file(raw, "https://example.gov/snapshot", date(2026, 9, 18))
        self.assertEqual((result["schema_version"], result["country"], result["accessed_on"]), (PLACES_SCHEMA_VERSION, "CH", "2026-09-18"))
        self.assertIn("snapshot of 18.09.2026", result["title"])
        self.assertRegex(result["raw_sha256"], r"^[0-9a-f]{64}$")

    def test_the_command_reads_a_saved_snapshot_without_the_network(self):
        with tempfile.TemporaryDirectory() as folder:
            saved, output = Path(folder) / "snapshot.csv", Path(folder) / "places" / "ch-register.json"
            saved.write_bytes(snapshot().encode("utf-8"))
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["--output", str(output), "--snapshot", str(saved), "--accessed-on", "2026-09-01"]), 0)
            self.assertEqual(json.loads(printed.getvalue())["municipalities"], 4)
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual((written["accessed_on"], len(written["places"])), ("2026-09-01", 30))
            self.assertIn("date=01-09-2026", written["url"])
            saved.write_text("not a register", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as complaint:
                self.assertEqual(main(["--output", str(output), "--snapshot", str(saved)]), 2)
            self.assertIn("expected columns are missing", complaint.getvalue())


if __name__ == "__main__":
    unittest.main()
