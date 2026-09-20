import unittest
from datetime import date

from swisstip.core.places import PlaceError, PlaceIndex, place_key
from swisstip.core.release import Place, PlaceRegister

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


if __name__ == "__main__":
    unittest.main()
