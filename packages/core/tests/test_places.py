import unittest
from datetime import date

from swisstip.core.places import PlaceError, PlaceIndex, place_key
from swisstip.core.release import Place, PlaceHierarchy, PlaceLevel, PlaceRegister
from swisstip.core.text import letters_for

REGISTER = PlaceRegister(
    title="Test register", publisher="Federal Statistical Office", url="https://example.gov/register",
    accessed_on=date(2026, 9, 18), raw_sha256="0" * 64,
    generic_words=["canton of", "Kanton", "city of", "Stadt", "Gemeinde", "canton de", "ville de"],
    places=[Place(code="CH", name="Switzerland", aliases=["Schweiz", "Suisse", "Svizzera"]),
            Place(code="CH-ZH", name="Zürich", aliases=["Zurigo"]), Place(code="CH-BE", name="Bern / Berne"),
            Place(code="CH-AG", name="Aargau", aliases=["Argovie"]), Place(code="CH-GE", name="Genève", aliases=["Geneva", "Genf"]),
            Place(code="CH-BS", name="Basel-Stadt", aliases=["Basel"]), Place(code="CH-BL", name="Basel-Landschaft", aliases=["Basel"]),
            Place(code="CH-ZH-261", name="Zürich", aliases=["Zurigo"]), Place(code="CH-ZH-69", name="Wallisellen"),
            Place(code="CH-ZH-83", name="Buchs (ZH)"), Place(code="CH-AG-4003", name="Buchs (AG)"),
            Place(code="CH-AG-4095", name="Brugg"), Place(code="CH-BE-733", name="Brügg"),
            Place(code="CH-BE-371", name="Biel/Bienne"), Place(code="CH-BE-356", name="Muri bei Bern"),
            Place(code="CH-AG-4236", name="Muri (AG)"), Place(code="CH-AG-4001", name="Aarau"),
            Place(code="CH-GE-6621", name="Genève", aliases=["Geneva", "Genf"])])
SERVED = ["CH", "CH-ZH", "CH-ZH-261"]


class PlaceIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = PlaceIndex(REGISTER, SERVED)

    def code(self, **parts):
        return self.index.resolve(**parts).code

    def test_one_key_per_spelling(self):
        self.assertEqual({place_key(name) for name in ("Zürich", "Zuerich", "ZURICH", " zürich ")}, {"zurich"})
        self.assertEqual(place_key("St. Gallen"), place_key("St Gallen"))
        self.assertEqual(place_key("Biel/Bienne"), "biel bienne")
        self.assertEqual(place_key("La Chaux-de-Fonds"), "la chaux de fonds")

    def test_names_and_codes_of_every_part(self):
        for parts, code in ((dict(), "CH"), (dict(country="Schweiz"), "CH"), (dict(country="ch"), "CH"),
                            (dict(canton="Zurich"), "CH-ZH"), (dict(canton="Kanton Zürich"), "CH-ZH"), (dict(canton="zh"), "CH-ZH"),
                            (dict(canton="CH-ZH"), "CH-ZH"), (dict(canton="Canton de Genève"), "CH-GE"), (dict(canton="Genf"), "CH-GE"),
                            (dict(canton="Berne"), "CH-BE"), (dict(city="Wallisellen"), "CH-ZH-69"), (dict(city="Stadt Zürich"), "CH-ZH-261"),
                            (dict(city="Ville de Genève"), "CH-GE-6621"), (dict(city="Geneva"), "CH-GE-6621"),
                            (dict(city="Bienne"), "CH-BE-371"), (dict(city="Biel/Bienne"), "CH-BE-371"),
                            (dict(city="CH-ZH-69"), "CH-ZH-69"), (dict(city="zh-0069"), "CH-ZH-69"),
                            (dict(canton="ZH", city="69"), "CH-ZH-69"), (dict(city="Muri bei Bern"), "CH-BE-356"),
                            (dict(canton="AG", city="Muri"), "CH-AG-4236"), (dict(city="Buchs AG"), "CH-AG-4003")):
            with self.subTest(parts=parts):
                self.assertEqual(self.code(**parts), code)

    def test_a_city_supplies_its_canton_and_the_register_names_the_scope(self):
        scope = self.index.resolve(city="zuerich")
        self.assertEqual((scope.country_code, scope.canton_code, scope.municipality_id), ("CH", "CH-ZH", "CH-ZH-261"))
        self.assertEqual((scope.country, scope.canton, scope.city), ("Switzerland", "Zürich", "Zürich"))
        self.assertEqual(scope.not_recognised, {})

    def test_the_spelling_as_written_wins_over_the_folded_one(self):
        # "Brugg" and "Brügg" fold to one key; each is found by its own spelling, and only "Bruegg" fits both.
        self.assertEqual(self.code(city="Brugg"), "CH-AG-4095")
        self.assertEqual(self.code(city="Brügg"), "CH-BE-733")
        with self.assertRaises(PlaceError) as raised:
            self.index.resolve(city="Bruegg")
        self.assertIn("Brugg (CH-AG-4095) in Aargau (CH-AG)", raised.exception.message)
        self.assertEqual(self.code(canton="Bern", city="Bruegg"), "CH-BE-733")

    def test_nothing_is_guessed(self):
        for parts, missing, code in ((dict(city="Oerlikon"), {"city": "Oerlikon"}, "CH"),
                                     (dict(canton="Zurich", city="8050"), {"city": "8050"}, "CH-ZH"),
                                     (dict(city="4001"), {"city": "4001"}, "CH"),  # Basel's postcode is Aarau's number
                                     (dict(canton="Bavaria", city="Wallisellen"), {"canton": "Bavaria"}, "CH-ZH-69"),
                                     (dict(canton="XX"), {"canton": "XX"}, "CH"), (dict(city="CH-ZH-999"), {"city": "CH-ZH-999"}, "CH")):
            with self.subTest(parts=parts):
                scope = self.index.resolve(**parts)
                self.assertEqual((scope.not_recognised, scope.code), (missing, code))
        for parts, path, fragment in ((dict(city="Buchs"), "jurisdiction.city", "fits several municipalities"),
                                      (dict(canton="Basel"), "jurisdiction.canton", "fits several cantons"),
                                      (dict(canton="Bern", city="Zurich"), "jurisdiction.city", "does not lie in the given canton"),
                                      (dict(canton="BE", city="CH-ZH-261"), "jurisdiction.city", "not in Bern / Berne (CH-BE)")):
            with self.subTest(parts=parts):
                with self.assertRaises(PlaceError) as raised:
                    self.index.resolve(**parts)
                self.assertEqual(raised.exception.path, path)
                self.assertIn(fragment, raised.exception.message)

    def test_a_country_the_release_does_not_serve(self):
        for country, code, name in (("Germany", None, "Germany"), ("de", "DE", None), ("Deutschland", None, "Deutschland")):
            with self.subTest(country=country):
                scope = self.index.resolve(country=country, canton="Zurich")
                self.assertEqual((scope.served, scope.code, scope.country_code, scope.country, scope.canton_code),
                                 (False, None, code, name, None))

    def test_labels(self):
        self.assertEqual([self.index.label(code) for code in ("CH", "CH-ZH", "CH-ZH-261", "CH-XX")],
                         ["CH (federal)", "CH-ZH (canton of Zürich)", "CH-ZH-261 (municipality of Zürich)", "CH-XX (canton)"])
        self.assertEqual(self.index.described("CH-BE-371"), "Biel/Bienne (CH-BE-371)")

    def test_a_release_of_several_countries_has_no_default(self):
        index = PlaceIndex(None, ["CH", "LI"])
        with self.assertRaises(PlaceError) as raised:
            index.resolve(canton="ZH")
        self.assertEqual(raised.exception.path, "jurisdiction.country")
        self.assertEqual(index.resolve(country="li").code, "LI")

    def test_without_a_register_codes_only(self):
        index = PlaceIndex(None, SERVED)
        self.assertEqual(index.resolve(canton="zh", city="261").code, "CH-ZH-261")
        self.assertEqual(index.resolve(city="ch-zh-261").canton_code, "CH-ZH")
        self.assertEqual(index.resolve(canton="Zurich").not_recognised, {"canton": "Zurich"})
        self.assertEqual(index.resolve(country="Switzerland").served, False)
        self.assertEqual(index.label("CH-ZH"), "CH-ZH (canton)")
        with self.assertRaises(PlaceError):
            index.resolve(canton="CH-BE", city="CH-ZH-261")


# A synthetic Polish register that declares its hierarchy (country-profiles.md, sections 2.1 and 10.1): four levels,
# the commune code ending in its TERYT type digit. PL-12-06-052 is a made-up code.
POLISH_REGISTER = PlaceRegister(
    title="TERC (synthetic)", publisher="Statistics Poland", url="https://example.gov/terc",
    accessed_on=date(2026, 9, 27), raw_sha256="1" * 64,
    hierarchy=PlaceHierarchy(country_adjective="Polish", levels=[
        PlaceLevel(id="national", adjective="National", noun="country", plural="countries", label="national"),
        PlaceLevel(id="voivodeship", adjective="Voivodeship", noun="voivodeship", plural="voivodeships",
                   label="województwo {name}", segment="[0-9]{2}"),
        PlaceLevel(id="county", adjective="County", noun="county", plural="counties", label="{name}",
                   segment="[0-9]{2}"),
        PlaceLevel(id="commune", adjective="Commune", noun="commune", plural="communes", label="commune of {name}",
                   segment="[0-9]{3}", note="the gmina, not a city district (dzielnica or delegatura), a housing "
                                              "estate (osiedle), a village or a postcode")]),
    generic_words=["województwo", "woj", "voivodeship", "province", "powiat", "county", "gmina", "commune",
                   "municipality", "miasto", "m", "st", "m. st.", "miasto stołeczne", "city", "city of", "town", "the"],
    places=[Place(code="PL", name="Poland", aliases=["Polska"]),
            Place(code="PL-02", name="dolnośląskie", aliases=["Lower Silesia"]),
            Place(code="PL-02-01", name="powiat bolesławiecki"),
            Place(code="PL-02-01-011", name="Bolesławiec (gmina miejska)"),
            Place(code="PL-02-01-022", name="Bolesławiec (gmina wiejska)"),
            Place(code="PL-10", name="łódzkie"), Place(code="PL-10-61", name="powiat m. Łódź"),
            Place(code="PL-10-61-011", name="Łódź"),
            Place(code="PL-12", name="małopolskie", aliases=["Lesser Poland", "Małopolska"]),
            Place(code="PL-12-06", name="powiat krakowski"), Place(code="PL-12-06-052", name="Liszki"),
            Place(code="PL-12-61", name="powiat m. Kraków"),
            Place(code="PL-12-61-011", name="Kraków", aliases=["Cracow"]),
            Place(code="PL-14", name="mazowieckie", aliases=["Masovia"]),
            Place(code="PL-14-65", name="powiat m. st. Warszawa"),
            Place(code="PL-14-65-011", name="Warszawa", aliases=["Warsaw"])])
POLISH_SERVED = ["PL", "PL-12-61-011", "PL-14-65-011"]


class DeclaredPlaceIndexTests(unittest.TestCase):
    """A register that declares its hierarchy: its own levels, codes in full with their segments kept, its own words."""

    def setUp(self):
        # The letters of a release whose query languages are English and Polish.
        self.index = PlaceIndex(POLISH_REGISTER, POLISH_SERVED, letters=letters_for(["en", "pl"]))

    def code(self, **parts):
        return self.index.resolve(**parts).code

    def test_a_commune_by_name_or_code_supplies_its_voivodeship(self):
        for city in ("Kraków", "Krakow", "KRAKÓW", "Cracow", "PL-12-61-011", "pl-12-61-011", "12-61-011", " Kraków "):
            with self.subTest(city=city):
                scope = self.index.resolve(city=city)
                self.assertEqual((scope.country_code, scope.canton_code, scope.municipality_id),
                                 ("PL", "PL-12", "PL-12-61-011"))
                self.assertEqual((scope.country, scope.canton, scope.city), ("Poland", "małopolskie", "Kraków"))
                self.assertEqual(scope.not_recognised, {})
        self.assertEqual(self.code(canton="PL-12", city="Kraków"), "PL-12-61-011")
        self.assertEqual(self.code(country="Polska", canton="Lesser Poland", city="12-61-011"), "PL-12-61-011")

    def test_a_voivodeship_by_name_or_code(self):
        for canton in ("PL-12", "pl-12", "12", "Małopolska", "Malopolska", "małopolskie", "województwo małopolskie",
                       "Lesser Poland", "woj. małopolskie"):
            with self.subTest(canton=canton):
                scope = self.index.resolve(canton=canton)
                self.assertEqual((scope.code, scope.canton, scope.not_recognised), ("PL-12", "małopolskie", {}))

    def test_generic_words_around_a_name(self):
        for city in ("m. st. Warszawa", "Warsaw", "Warszawa", "miasto stołeczne Warszawa", "city of Warsaw"):
            with self.subTest(city=city):
                scope = self.index.resolve(city=city)
                self.assertEqual((scope.canton_code, scope.municipality_id), ("PL-14", "PL-14-65-011"))

    def test_the_polish_letters_fold_only_when_given(self):
        for city in ("Łódź", "Lodz", "LODZ", "łódź"):
            with self.subTest(city=city):
                self.assertEqual(self.code(city=city), "PL-10-61-011")
        without = PlaceIndex(POLISH_REGISTER, POLISH_SERVED)
        self.assertEqual(without.resolve(city="Łódź").code, "PL-10-61-011")
        self.assertEqual(without.resolve(city="Lodz").not_recognised, {"city": "Lodz"})
        self.assertEqual(place_key("Wrocław"), "wrocaw")
        self.assertEqual(place_key("Wrocław", letters_for(["pl"])), "wroclaw")

    def test_names_several_communes_share(self):
        with self.assertRaises(PlaceError) as raised:
            self.index.resolve(city="Bolesławiec")
        self.assertEqual(raised.exception.path, "jurisdiction.city")
        self.assertEqual(raised.exception.message,
                         "'Bolesławiec' fits several communes: Bolesławiec (gmina miejska) (PL-02-01-011) in powiat "
                         "bolesławiecki (PL-02-01), Bolesławiec (gmina wiejska) (PL-02-01-022) in powiat bolesławiecki "
                         "(PL-02-01); add the voivodeship, give the full name as listed or give the commune code.")
        self.assertEqual(self.code(city="Bolesławiec (gmina wiejska)"), "PL-02-01-022")
        self.assertEqual(self.code(city="Boleslawiec (gmina miejska)"), "PL-02-01-011")
        self.assertEqual(self.code(city="PL-02-01-022"), "PL-02-01-022")

    def test_a_plain_name_does_not_hide_the_qualified_communes_of_that_name(self):
        # The register qualifies a name only where a voivodeship holds it twice: Świdnica is a plain name in lubuskie
        # and a qualified pair in dolnośląskie. The plain exact name must not win over the pair.
        places = [*POLISH_REGISTER.places,
                  Place(code="PL-02-19", name="powiat świdnicki"),
                  Place(code="PL-02-19-011", name="Świdnica (gmina miejska)"),
                  Place(code="PL-02-19-072", name="Świdnica (gmina wiejska)"),
                  Place(code="PL-08", name="lubuskie"), Place(code="PL-08-09", name="powiat zielonogórski"),
                  Place(code="PL-08-09-092", name="Świdnica")]
        index = PlaceIndex(POLISH_REGISTER.model_copy(update={"places": places}), POLISH_SERVED,
                           letters=letters_for(["en", "pl"]))
        with self.assertRaises(PlaceError) as raised:
            index.resolve(city="Świdnica")
        self.assertIn("'Świdnica' fits several communes", raised.exception.message)
        for code in ("PL-02-19-011", "PL-02-19-072", "PL-08-09-092"):
            self.assertIn(code, raised.exception.message)
        with self.assertRaises(PlaceError) as raised:
            index.resolve(canton="dolnośląskie", city="Świdnica")
        self.assertNotIn("PL-08-09-092", raised.exception.message, "the voivodeship narrows the candidates")
        self.assertIn("PL-02-19-011", raised.exception.message)
        self.assertEqual(index.resolve(canton="lubuskie", city="Świdnica").code, "PL-08-09-092")
        self.assertEqual(index.resolve(city="Świdnica (gmina wiejska)").code, "PL-02-19-072")

    def test_names_several_voivodeships_share(self):
        places = [place.model_copy(update={"aliases": [*place.aliases, "Central Poland"]})
                  if place.code in ("PL-10", "PL-14") else place for place in POLISH_REGISTER.places]
        index = PlaceIndex(POLISH_REGISTER.model_copy(update={"places": places}), POLISH_SERVED)
        with self.assertRaises(PlaceError) as raised:
            index.resolve(canton="Central Poland")
        self.assertEqual((raised.exception.path, raised.exception.message),
                         ("jurisdiction.canton", "'Central Poland' fits several voivodeships: łódzkie (PL-10), "
                                                 "mazowieckie (PL-14); give one of them."))

    def test_a_commune_outside_the_given_voivodeship(self):
        message = ("The commune does not lie in the given voivodeship: Kraków (PL-12-61-011) lies in małopolskie "
                   "(PL-12), not in mazowieckie (PL-14); correct one of them, or give only the commune.")
        for parts in (dict(canton="PL-14", city="Kraków"), dict(canton="Masovia", city="PL-12-61-011")):
            with self.subTest(parts=parts):
                with self.assertRaises(PlaceError) as raised:
                    self.index.resolve(**parts)
                self.assertEqual((raised.exception.path, raised.exception.message), ("jurisdiction.city", message))

    def test_nothing_else_is_a_commune(self):
        # A county, by name or code, a bare TERYT number, a district, a code that parses but is not listed, and a
        # code without its leading zeros: not recognised, and the request runs for the broader place.
        for city in ("powiat krakowski", "PL-12-61", "1261011", "Bemowo", "PL-12-61-099", "PL-12-61-11", "12-61-11",
                     "PL-12-61-0011", "CH-12-61-011"):
            with self.subTest(city=city):
                scope = self.index.resolve(city=city)
                self.assertEqual((scope.not_recognised, scope.code), ({"city": city}, "PL"))
        # A bare commune number is not read next to the voivodeship, as a BFS number is next to its canton.
        scope = self.index.resolve(canton="PL-12", city="011")
        self.assertEqual((scope.not_recognised, scope.code), ({"city": "011"}, "PL-12"))
        for canton in ("PL-99", "99", "PL-12-61", "CH-ZH", "Mazowsze"):
            with self.subTest(canton=canton):
                scope = self.index.resolve(canton=canton)
                self.assertEqual((scope.not_recognised, scope.code), ({"canton": canton}, "PL"))

    def test_segments_keep_their_zeros(self):
        self.assertEqual(self.index.resolve(city="PL-12-61-011").municipality_id, "PL-12-61-011")
        self.assertEqual(self.index.resolve(city="PL-12-06-052").municipality_id, "PL-12-06-052")
        self.assertEqual(self.index.resolve(canton="02").canton_code, "PL-02")

    def test_labels(self):
        self.assertEqual([self.index.label(code) for code in ("PL", "PL-12", "PL-12-61", "PL-12-61-011")],
                         ["PL (national)", "PL-12 (województwo małopolskie)", "PL-12-61 (powiat m. Kraków)",
                          "PL-12-61-011 (commune of Kraków)"])
        self.assertEqual(self.index.label("PL-16"), "PL-16 (voivodeship)")
        self.assertEqual(self.index.described("PL-12-61-011"), "Kraków (PL-12-61-011)")

    def test_a_country_the_release_does_not_serve(self):
        scope = self.index.resolve(country="Switzerland", city="Kraków")
        self.assertEqual((scope.served, scope.code, scope.country), (False, None, "Switzerland"))
        self.assertEqual(self.index.resolve(country="pl").code, "PL")


if __name__ == "__main__":
    unittest.main()
