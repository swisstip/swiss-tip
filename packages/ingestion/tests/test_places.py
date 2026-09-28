import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

from swisstip.ingestion.places import PLACES_SCHEMA_VERSION, RegisterError, main, parse_snapshot, place_file
from swisstip.ingestion.places import TERC_PUBLISHER, parse_terc, terc_place_file

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


TERC_HEADER = "WOJ;POW;GMI;RODZ;NAZWA;NAZWA_DOD;STAN_NA"
VOIVODESHIPS = {"02": "DOLNOŚLĄSKIE", "04": "KUJAWSKO-POMORSKIE", "06": "LUBELSKIE", "08": "LUBUSKIE", "10": "ŁÓDZKIE",
                "12": "MAŁOPOLSKIE", "14": "MAZOWIECKIE", "16": "OPOLSKIE", "18": "PODKARPACKIE", "20": "PODLASKIE",
                "22": "POMORSKIE", "24": "ŚLĄSKIE", "26": "ŚWIĘTOKRZYSKIE", "28": "WARMIŃSKO-MAZURSKIE", "30": "WIELKOPOLSKIE",
                "32": "ZACHODNIOPOMORSKIE"}
TERC_ROWS = [
    # A land county with an urban/rural pair of one name: both are qualified by their type.
    "02;01;;;bolesławiecki;powiat", "02;01;01;1;Bolesławiec;gmina miejska", "02;01;02;2;Bolesławiec;gmina wiejska",
    # An urban-rural commune (RODZ 3) with its town (4) and rural area (5); the name is shared with the pair above in
    # another county, so it stays unqualified.
    "10;18;;;wieruszowski;powiat", "10;18;01;3;Bolesławiec;gmina miejsko-wiejska", "10;18;01;4;Bolesławiec;miasto",
    "10;18;01;5;Bolesławiec;obszar wiejski",
    "12;06;;;krakowski;powiat", "12;06;01;2;Czernichów;gmina wiejska",
    # A city with county rights: county 61 with its commune 011, and a delegatura (RODZ 9).
    "12;61;;;Kraków;miasto na prawach powiatu", "12;61;01;1;Kraków;gmina miejska", "12;61;05;9;Kraków-Śródmieście;delegatura",
    # The capital and one of its districts (RODZ 8).
    "14;65;;;Warszawa;miasto stołeczne, na prawach powiatu", "14;65;01;1;Warszawa;gmina miejska, miasto stołeczne",
    "14;65;02;8;Bemowo;dzielnica",
    "24;17;;;żywiecki;powiat", "24;17;02;2;Czernichów;gmina wiejska"]


def terc(extra: list[str] = (), without: str = "", header: str = TERC_HEADER) -> bytes:
    """A synthetic TERC file as the eTERYT download writes it: UTF-8 with BOM, CRLF, `;`, the voivodeships in capitals.
    A row without its STAN_NA gets 2026-01-01; `without` leaves out the rows that contain it."""
    rows = [f"{code};;;;{name};województwo" for code, name in VOIVODESHIPS.items()] + TERC_ROWS + list(extra)
    rows = [row if row.count(";") == 6 else row + ";2026-01-01" for row in rows if not without or without not in row]
    return ("﻿" + "\r\n".join([header, *rows]) + "\r\n").encode("utf-8")


class TercRegisterTests(unittest.TestCase):
    def test_voivodeships_counties_and_communes_get_their_codes_and_names(self):
        places, day, dropped = parse_terc(terc())
        self.assertEqual(places[:5], [{"code": "PL-02", "name": "dolnośląskie"},
                                      {"code": "PL-02-01", "name": "powiat bolesławiecki"},
                                      {"code": "PL-02-01-011", "name": "Bolesławiec (gmina miejska)"},
                                      {"code": "PL-02-01-022", "name": "Bolesławiec (gmina wiejska)"},
                                      {"code": "PL-04", "name": "kujawsko-pomorskie"}])
        for place in ({"code": "PL-10", "name": "łódzkie"}, {"code": "PL-10-18-013", "name": "Bolesławiec"},
                      {"code": "PL-12-06", "name": "powiat krakowski"}, {"code": "PL-12-06-012", "name": "Czernichów"},
                      {"code": "PL-12-61", "name": "powiat m. Kraków"}, {"code": "PL-12-61-011", "name": "Kraków"},
                      {"code": "PL-14-65", "name": "powiat m. st. Warszawa"}, {"code": "PL-14-65-011", "name": "Warszawa"},
                      {"code": "PL-24-17-022", "name": "Czernichów"}):
            with self.subTest(code=place["code"]):
                self.assertIn(place, places)
        codes = [place["code"] for place in places]
        self.assertEqual(codes, sorted(codes))
        depths = Counter(code.count("-") for code in codes)
        self.assertEqual((depths[1], depths[2], depths[3]), (16, 6, 7))
        # Parts of communes are no places: the town and rural area of Bolesławiec, Bemowo, the delegatura.
        self.assertFalse({"PL-10-18-014", "PL-10-18-015", "PL-14-65-028", "PL-12-61-059"} & set(codes))
        self.assertNotIn("Bemowo", [place["name"] for place in places])
        self.assertEqual((day, dropped), (date(2026, 1, 1), Counter({4: 1, 5: 1, 8: 1, 9: 1})))

    def test_names_shared_within_a_voivodeship_are_qualified_even_across_counties(self):
        # A caller names the voivodeship and the city, never the county: a city with county rights and the rural
        # commune of its name lie in different counties of one voivodeship (Tarnów), and so can two communes of one
        # type (Rogowo); each pair must be told apart by name.
        places, _, _ = parse_terc(terc([
            "12;16;;;tarnowski;powiat", "12;16;09;2;Tarnów;gmina wiejska",
            "12;63;;;Tarnów;miasto na prawach powiatu", "12;63;01;1;Tarnów;gmina miejska",
            "04;11;;;rypiński;powiat", "04;11;04;2;Rogowo;gmina wiejska",
            "04;19;;;żniński;powiat", "04;19;05;2;Rogowo;gmina wiejska"]))
        names = {place["code"]: place["name"] for place in places}
        self.assertEqual((names["PL-12-16-092"], names["PL-12-63-011"]), ("Tarnów (gmina wiejska)", "Tarnów (gmina miejska)"))
        self.assertEqual((names["PL-04-11-042"], names["PL-04-19-052"]),
                         ("Rogowo (powiat rypiński)", "Rogowo (powiat żniński)"))
        self.assertEqual((names["PL-12-06-012"], names["PL-24-17-022"]), ("Czernichów", "Czernichów"),
                         "a name shared only across voivodeships stays as it is")

    def test_a_file_that_is_not_the_whole_register_is_refused(self):
        for raw, fragment in ((b"<html>maintenance</html>", "missing columns WOJ, POW"),
                              (terc(header=TERC_HEADER.replace("NAZWA_DOD", "DOD")), "missing columns NAZWA_DOD$"),
                              (terc(without="ZACHODNIOPOMORSKIE"), "15 voivodeships, not 16"),
                              (terc(["34;01;;;nowy;powiat"]), "county PL-34-01 has no voivodeship"),
                              (terc(["12;07;01;2;Zielonki;gmina wiejska"]), "commune PL-12-07-012 has no county"),
                              (terc(["12;61;01;1;Kraków;gmina miejska"]), "code PL-12-61-011 twice"),
                              (terc(["12;06;02;7;Liszki;gmina wiejska"]), "unknown RODZ '7'"),
                              (terc(["12;07;;1;wielicki;powiat"]), "unknown RODZ '1'"),
                              (terc(["12;06;02;;Liszki;gmina wiejska"]), "unknown RODZ ''"),
                              (terc(["12;19;;;Wieliczka;powiat grodzki"]), "unexpected NAZWA_DOD 'powiat grodzki'"),
                              (terc(["12;06;02;2;Liszki;gmina wiejska;2025-01-01"]), "more than one STAN_NA"),
                              (terc().replace(b"2026-01-01", b"01.01.2026"), "STAN_NA '01.01.2026' is not a date"),
                              (terc(["02;01;03;2;Bolesławiec;gmina wiejska"]),
                               "'Bolesławiec \\(powiat bolesławiecki\\)' is not unique in voivodeship PL-02"),
                              (terc(["2;01;;;x;powiat"]), "malformed code"),
                              (terc(["12;;01;2;Liszki;gmina wiejska"]), "malformed code")):
            with self.subTest(fragment=fragment):
                with self.assertRaisesRegex(RegisterError, fragment):
                    parse_terc(raw)

    def test_the_place_file_names_the_register_its_date_and_hash(self):
        raw = terc()
        result = terc_place_file(raw, "https://example.gov/teryt", date(2026, 9, 27))
        self.assertEqual((result["schema_version"], result["country"], result["publisher"], result["url"], result["accessed_on"]),
                         (PLACES_SCHEMA_VERSION, "PL", TERC_PUBLISHER, "https://example.gov/teryt", "2026-09-27"))
        self.assertTrue(result["title"].startswith("TERC - Krajowy rejestr urzędowy podziału terytorialnego kraju"))
        self.assertTrue(result["title"].endswith(", stan na 2026-01-01"))
        self.assertEqual(result["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["places"], parse_terc(raw)[0])

    def test_the_command_reads_a_saved_terc_file_and_prints_the_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            saved, output = Path(folder) / "TERC.csv", Path(folder) / "places" / "pl-register.json"
            saved.write_bytes(terc())
            arguments = ["--terc", str(saved), "--url", "https://example.gov/teryt", "--accessed-on", "2026-09-27", "--output",
                         str(output)]
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(arguments), 0)
            summary = json.loads(printed.getvalue())
            self.assertEqual((summary["voivodeships"], summary["counties"], summary["communes"], summary["dropped"]),
                             (16, 6, 7, {"4": 1, "5": 1, "8": 1, "9": 1}))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")),
                             terc_place_file(terc(), "https://example.gov/teryt", date(2026, 9, 27)))
            output.unlink()
            saved.write_bytes(terc(without="ZACHODNIOPOMORSKIE"))
            with contextlib.redirect_stderr(io.StringIO()) as complaint:
                self.assertEqual(main(arguments), 2)
            self.assertIn("15 voivodeships, not 16", complaint.getvalue())
            self.assertFalse(output.exists())
            # The download is a form: the importer never fetches, so the page and the day are named by the operator.
            for incomplete in (["--terc", str(saved), "--output", str(output), "--accessed-on", "2026-09-27"],
                               ["--terc", str(saved), "--output", str(output), "--url", "https://example.gov/teryt"],
                               ["--output", str(output), "--url", "https://example.gov/teryt"],
                               ["--output", str(output), "--terc", str(saved), "--snapshot", str(saved)]):
                with self.subTest(arguments=incomplete):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                        main(incomplete)
                    self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
