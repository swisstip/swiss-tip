import dataclasses
import unittest
from datetime import date

from swisstip.core import validation
from swisstip.core.basis import LEVEL_WORD, level_of_jurisdiction
from swisstip.core.contracts import TOOL_DESCRIPTIONS
from swisstip.core.hierarchy import (LEGACY_WORDING, NEVER, SWISS, SWISS_PATTERN, Wording, containment_sentence,
                                     either_or, generate_wording, hierarchy_of, level_list, wording_for)
from swisstip.core.places import CITY_AMBIGUOUS, CITY_OUTSIDE_REGION, REGION_AMBIGUOUS, PlaceError, PlaceIndex
from swisstip.core.release import Place, PlaceHierarchy, PlaceLevel, PlaceRegister

POLISH_LEVELS = [
    PlaceLevel(id="national", adjective="National", noun="country", plural="countries", label="national"),
    PlaceLevel(id="voivodeship", adjective="Voivodeship", noun="voivodeship", plural="voivodeships",
               label="województwo {name}", segment="[0-9]{2}"),
    PlaceLevel(id="county", adjective="County", noun="county", plural="counties", label="{name}", segment="[0-9]{2}"),
    PlaceLevel(id="commune", adjective="Commune", noun="commune", plural="communes", label="commune of {name}",
               segment="[0-9]{3}",
               note="the gmina, not a city district (dzielnica or delegatura), a housing estate (osiedle), a village "
                    "or a postcode")]
# The synthetic Polish register of the design (section 10.1); PL-12-06-052 is a made-up rural commune code.
POLISH_PLACES = [
    Place(code="PL", name="Poland", aliases=["Polska"]),
    Place(code="PL-02", name="dolnośląskie", aliases=["Lower Silesia"]),
    Place(code="PL-02-01", name="powiat bolesławiecki"),
    Place(code="PL-02-01-011", name="Bolesławiec (gmina miejska)"),
    Place(code="PL-02-01-022", name="Bolesławiec (gmina wiejska)"),
    Place(code="PL-10", name="łódzkie"),
    Place(code="PL-10-61", name="powiat m. Łódź"),
    Place(code="PL-10-61-011", name="Łódź"),
    Place(code="PL-12", name="małopolskie", aliases=["Lesser Poland", "Małopolska"]),
    Place(code="PL-12-06", name="powiat krakowski"),
    Place(code="PL-12-06-052", name="Gmina Wiejska"),
    Place(code="PL-12-61", name="powiat m. Kraków"),
    Place(code="PL-12-61-011", name="Kraków", aliases=["Cracow"]),
    Place(code="PL-14", name="mazowieckie", aliases=["Masovia"]),
    Place(code="PL-14-65", name="powiat m. st. Warszawa"),
    Place(code="PL-14-65-011", name="Warszawa", aliases=["Warsaw"])]


def register(places: list[Place] | None = None, levels: list[PlaceLevel] | None = None,
             declared: bool = True) -> PlaceRegister:
    hierarchy = PlaceHierarchy(country_adjective="Polish", levels=levels or POLISH_LEVELS) if declared else None
    return PlaceRegister(title="TERC", publisher="GUS", url="https://example.gov/terc", accessed_on=date(2026, 9, 27),
                         raw_sha256="a" * 64, hierarchy=hierarchy, places=places or POLISH_PLACES)


class SwissHierarchyTests(unittest.TestCase):
    """The built-in hierarchy reproduces today's Swiss strings and levels; the expected texts are pasted copies."""

    def test_the_pattern_is_the_validators_regex_object(self):
        self.assertIs(SWISS.pattern, SWISS_PATTERN)
        self.assertIs(SWISS.pattern, validation.JURISDICTION)
        self.assertEqual(SWISS.pattern.pattern, r"^CH(?:-[A-Z]{2}(?:-\d{1,4})?)?$")

    def test_the_swiss_levels(self):
        self.assertEqual(SWISS.ids, ("federal", "cantonal", "municipal"))
        self.assertEqual(SWISS.words, LEVEL_WORD)
        self.assertTrue(all(level.adjective.lower() == level.id for level in SWISS.levels))
        self.assertEqual((SWISS.country, SWISS.country_adjective, SWISS.builtin, SWISS.lowest), ("CH", "Swiss", True, 2))
        self.assertEqual(SWISS.levels[2].note, "the political commune, not a district, a quarter or a postcode")
        # Today's level_of_jurisdiction, including a code below the municipality counting as municipal.
        for code in ("CH", "CH-ZH", "CH-ZH-261", "CH-ZH-261-1"):
            with self.subTest(code=code):
                self.assertEqual(SWISS.level_id(code), level_of_jurisdiction(code))
        self.assertEqual(SWISS.level_id("CH-ZH-261-1"), "municipal")

    def test_a_release_without_a_declared_hierarchy_is_swiss(self):
        self.assertIs(hierarchy_of(None), SWISS)
        self.assertIs(hierarchy_of(register([Place(code="CH", name="Switzerland")], declared=False)), SWISS)

    def test_labels_are_todays(self):
        self.assertEqual(SWISS.label("CH", "Switzerland"), "CH (federal)")
        self.assertEqual(SWISS.label("CH"), "CH (federal)")
        self.assertEqual(SWISS.label("CH-ZH", "Zürich"), "CH-ZH (canton of Zürich)")
        self.assertEqual(SWISS.label("CH-ZH-261", "Zürich"), "CH-ZH-261 (municipality of Zürich)")
        self.assertEqual(SWISS.label("CH-ZH"), "CH-ZH (canton)")
        self.assertEqual(SWISS.label("CH-ZH-261"), "CH-ZH-261 (municipality)")

    def test_the_containment_sentence_and_the_level_list_are_todays(self):
        self.assertEqual(containment_sentence(SWISS), "A federal concept answers for any canton, a cantonal concept "
                                                      "for its municipalities, never upward or sideways.")
        self.assertEqual(level_list(SWISS), "federal, cantonal, municipal")
        # Both occur verbatim in the served resolve description.
        self.assertIn(containment_sentence(SWISS), TOOL_DESCRIPTIONS["resolve"])
        self.assertIn(f"its level ({level_list(SWISS)})", TOOL_DESCRIPTIONS["resolve"])

    def test_region_of(self):
        self.assertIsNone(SWISS.region_of("CH"))
        self.assertEqual(SWISS.region_of("CH-ZH"), "CH-ZH")
        self.assertEqual(SWISS.region_of("CH-ZH-261"), "CH-ZH")


class DeclaredHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.polish = hierarchy_of(register())

    def test_the_declared_hierarchy(self):
        self.assertFalse(self.polish.builtin)
        self.assertEqual((self.polish.country, self.polish.country_adjective, self.polish.lowest), ("PL", "Polish", 3))
        self.assertEqual(self.polish.ids, ("national", "voivodeship", "county", "commune"))
        self.assertEqual(self.polish.words, {"national": "National", "voivodeship": "Voivodeship", "county": "County",
                                             "commune": "Commune"})

    def test_the_code_pattern_follows_the_segments(self):
        self.assertEqual(self.polish.pattern.pattern, r"^PL(?:-[0-9]{2}(?:-[0-9]{2}(?:-[0-9]{3})?)?)?$")
        for code in ("PL", "PL-12", "PL-12-61", "PL-12-61-011"):
            with self.subTest(code=code):
                self.assertTrue(self.polish.pattern.match(code))
        for code in ("PL-12-61-11", "PL-1", "CH-ZH", "pl-12", "PL-12-61-011-1", "PL-12-6a", "PL-12-61-0111", "PL-"):
            with self.subTest(code=code):
                self.assertIsNone(self.polish.pattern.match(code))

    def test_level_ids_by_depth(self):
        self.assertEqual([self.polish.level_id(code) for code in ("PL", "PL-12", "PL-12-61", "PL-12-61-011")],
                         ["national", "voivodeship", "county", "commune"])
        # Declared, a code deeper than the lowest level has no level (built in, it counts as the lowest).
        self.assertIsNone(self.polish.level_id("PL-12-61-011-1"))
        self.assertEqual(self.polish.label("PL-12-61-011-1", "x"), "PL-12-61-011-1")
        self.assertEqual(self.polish.depth("PL-12-61-011"), 3)

    def test_the_region_is_the_depth_one_ancestor(self):
        self.assertEqual(self.polish.region_of("PL-12-61-011"), "PL-12")
        self.assertEqual(self.polish.region_of("PL-12-61"), "PL-12")
        self.assertEqual(self.polish.region_of("PL-12"), "PL-12")
        self.assertIsNone(self.polish.region_of("PL"))

    def test_labels(self):
        self.assertEqual(self.polish.label("PL", "Poland"), "PL (national)")
        self.assertEqual(self.polish.label("PL-12", "małopolskie"), "PL-12 (województwo małopolskie)")
        self.assertEqual(self.polish.label("PL-12-61", "powiat m. Kraków"), "PL-12-61 (powiat m. Kraków)")
        self.assertEqual(self.polish.label("PL-12-61-011", "Kraków"), "PL-12-61-011 (commune of Kraków)")
        # Without a name a template with {name} gives way to the level's noun.
        self.assertEqual(self.polish.label("PL-14"), "PL-14 (voivodeship)")
        self.assertEqual(self.polish.label("PL-14-65"), "PL-14-65 (county)")
        self.assertEqual(self.polish.label("PL-14-65-011"), "PL-14-65-011 (commune)")

    def test_the_containment_sentence_and_the_level_list(self):
        self.assertEqual(containment_sentence(self.polish),
                         "A national concept answers for any voivodeship, a voivodeship concept for its counties and "
                         "communes, a county concept for its communes, never upward or sideways.")
        self.assertEqual(level_list(self.polish), "national, voivodeship, county, commune")
        levels = [PlaceLevel(id="national", adjective="National", noun="country", plural="countries", label="national"),
                  PlaceLevel(id="autonomous_community", adjective="Autonomous community", noun="autonomous community",
                             plural="autonomous communities", label="{name}", segment="[A-Z]{2}"),
                  PlaceLevel(id="provincial", adjective="Provincial", noun="province", plural="provinces",
                             label="province of {name}", segment="[0-9]{2}"),
                  PlaceLevel(id="municipal", adjective="Municipal", noun="municipality", plural="municipalities",
                             label="municipality of {name}", segment="[0-9]{3}")]
        spanish = hierarchy_of(register([Place(code="ES", name="Spain")], levels))
        self.assertEqual(containment_sentence(spanish),
                         "A national concept answers for any autonomous community, an autonomous community concept for "
                         "its provinces and municipalities, a provincial concept for its municipalities, never upward "
                         "or sideways.")
        self.assertEqual(spanish.pattern.pattern, r"^ES(?:-[A-Z]{2}(?:-[0-9]{2}(?:-[0-9]{3})?)?)?$")

    def test_a_malformed_hierarchy_accepts_no_code_and_does_not_crash(self):
        without_country = [place for place in POLISH_PLACES if place.code != "PL"]
        two_countries = [*POLISH_PLACES, Place(code="PX", name="Elsewhere")]
        no_segment = [POLISH_LEVELS[0], POLISH_LEVELS[1].model_copy(update={"segment": None}), *POLISH_LEVELS[2:]]
        inverted_width = [*POLISH_LEVELS[:3], POLISH_LEVELS[3].model_copy(update={"segment": "[0-9]{9,1}"})]
        for name, hierarchy in (("no country place", hierarchy_of(register(without_country))),
                                ("two country places", hierarchy_of(register(two_countries))),
                                ("a level without a segment", hierarchy_of(register(levels=no_segment))),
                                ("an inverted width", hierarchy_of(register(levels=inverted_width)))):
            with self.subTest(name):
                self.assertIs(hierarchy.pattern, NEVER)
                for code in ("PL", "PL-12", "PL-12-61-011", "", "PX"):
                    self.assertIsNone(hierarchy.pattern.match(code))
        self.assertIsNone(hierarchy_of(register(two_countries)).country)


# A small Swiss register without a hierarchy: the built-in path the attested releases take.
SWISS_REGISTER = PlaceRegister(
    title="Test register", publisher="Federal Statistical Office", url="https://example.gov/register",
    accessed_on=date(2026, 9, 18), raw_sha256="0" * 64, generic_words=["Kanton"],
    places=[Place(code="CH", name="Switzerland"), Place(code="CH-ZH", name="Zürich"),
            Place(code="CH-BE", name="Bern / Berne"), Place(code="CH-AG", name="Aargau"),
            Place(code="CH-BS", name="Basel-Stadt", aliases=["Basel"]),
            Place(code="CH-BL", name="Basel-Landschaft", aliases=["Basel"]), Place(code="CH-ZH-261", name="Zürich"),
            Place(code="CH-ZH-83", name="Buchs (ZH)"), Place(code="CH-AG-4003", name="Buchs (AG)")])


class LegacyWordingTests(unittest.TestCase):
    """Literal-copy tests: the templates rendered with today's words equal pasted copies of today's texts."""

    def error(self, index: PlaceIndex, **parts) -> tuple[str, str]:
        with self.assertRaises(PlaceError) as raised:
            index.resolve(**parts)
        return raised.exception.path, raised.exception.message

    def test_the_place_templates_are_todays_expressions(self):
        # Each right-hand side is today's expression in places.py, pasted with its values filled in.
        w = LEGACY_WORDING
        self.assertEqual(REGION_AMBIGUOUS.format(value="Basel", region_plural=w.region_plural, candidates="A, B"),
                         f"{'Basel'!r} fits several cantons: " + "A, B" + "; give one of them.")
        self.assertEqual(CITY_OUTSIDE_REGION.format(city_noun=w.city_noun, region_noun=w.region_noun, candidates="A",
                                                    region="B"),
                         "The municipality does not lie in the given canton: " + "A"
                         + f", not in {'B'}; correct one of them, or give only the municipality.")
        self.assertEqual(CITY_AMBIGUOUS.format(value="Buchs", city_plural=w.city_plural, candidates="A, B",
                                               ambiguity_tail=w.ambiguity_tail),
                         f"{'Buchs'!r} fits several municipalities: " + "A, B"
                         + "; add the canton or give the full name.")

    def test_the_place_errors_are_todays(self):
        index = PlaceIndex(SWISS_REGISTER, ["CH", "CH-ZH", "CH-ZH-261"])
        self.assertIs(index.hierarchy, SWISS)
        self.assertIs(index.wording, LEGACY_WORDING)
        self.assertEqual(self.error(index, canton="Basel"),
                         ("jurisdiction.canton", "'Basel' fits several cantons: Basel-Stadt (CH-BS), Basel-Landschaft "
                                                 "(CH-BL); give one of them."))
        self.assertEqual(self.error(index, city="Buchs"),
                         ("jurisdiction.city", "'Buchs' fits several municipalities: Buchs (ZH) (CH-ZH-83) in Zürich "
                                               "(CH-ZH), Buchs (AG) (CH-AG-4003) in Aargau (CH-AG); add the canton or "
                                               "give the full name."))
        for parts in (dict(canton="Bern", city="Zurich"), dict(canton="BE", city="CH-ZH-261")):
            with self.subTest(parts=parts):
                self.assertEqual(self.error(index, **parts),
                                 ("jurisdiction.city", "The municipality does not lie in the given canton: Zürich "
                                                       "(CH-ZH-261) lies in Zürich (CH-ZH), not in Bern / Berne "
                                                       "(CH-BE); correct one of them, or give only the municipality."))
        # Without a register the codes stand alone.
        self.assertEqual(self.error(PlaceIndex(None, ["CH"]), canton="BE", city="ZH-261"),
                         ("jurisdiction.city", "The municipality does not lie in the given canton: CH-ZH-261 lies in "
                                               "CH-ZH, not in CH-BE; correct one of them, or give only the "
                                               "municipality."))

    def test_the_place_labels_are_todays(self):
        index = PlaceIndex(SWISS_REGISTER, ["CH"])
        codes = ("CH", "CH-ZH", "CH-ZH-261", "CH-XX", "CH-XX-1", "CH-ZH-261-1")
        self.assertEqual([index.label(code) for code in codes],
                         ["CH (federal)", "CH-ZH (canton of Zürich)", "CH-ZH-261 (municipality of Zürich)",
                          "CH-XX (canton)", "CH-XX-1 (municipality)", "CH-ZH-261-1 (municipality)"])
        without = PlaceIndex(None, ["CH", "LI"])
        self.assertEqual([without.label(code) for code in ("CH", "LI", "CH-ZH", "CH-ZH-261")],
                         ["CH (federal)", "LI (federal)", "CH-ZH (canton)", "CH-ZH-261 (municipality)"])

    def test_the_legacy_words_are_todays_substrings(self):
        # Each right-hand side is pasted from today's served text (runtime/service.py), the slot values in context.
        w = LEGACY_WORDING
        for rendered, today in (
                (f"official {w.adjective} sources", "official Swiss sources"),
                (f"the user's {w.region_noun} or {w.city_noun} as jurisdiction",
                 "the user's canton or municipality as jurisdiction"),
                (f"the official name of the {w.city_noun}{w.city_note} if",
                 "the official name of the municipality (the political commune, not a district, a quarter or a "
                 "postcode) if"),
                (f"{w.city_note} or with the {w.region_noun};",
                 " (the political commune, not a district, a quarter or a postcode) or with the canton;"),
                (f"has a {w.below_adjectives} part", "has a cantonal or municipal part"),
                (f"for another {w.below_nouns}.", "for another canton or municipality."),
                (f"add the {w.region_noun} or the city", "add the canton or the city"),
                (f"{w.national} concepts still apply.", "Federal concepts still apply."),
                (f"already said{w.context_example} and", "already said (for example a Czech citizen is population "
                                                         "eu_efta) and")):
            with self.subTest(today=today):
                self.assertEqual(rendered, today)
        # Today's f"the {part} {value!r}" for each key of not_recognised.
        for part in ("canton", "city"):
            self.assertEqual(f"the {w.part_word(part)} {'Oerlikon'!r}", f"the {part} {'Oerlikon'!r}")

    def test_the_generated_swiss_words_differ_only_where_designed(self):
        generated = generate_wording(SWISS)
        self.assertEqual(dataclasses.replace(generated, context_example=LEGACY_WORDING.context_example,
                                             ambiguity_tail=LEGACY_WORDING.ambiguity_tail), LEGACY_WORDING)
        self.assertEqual(generated.context_example, "")
        self.assertEqual(generated.ambiguity_tail,
                         "; add the canton, give the full name as listed or give the municipality code.")
        self.assertIs(wording_for(SWISS), LEGACY_WORDING)


class DeclaredWordingTests(unittest.TestCase):
    def test_the_polish_words(self):
        polish = hierarchy_of(register())
        self.assertEqual(wording_for(polish), Wording(
            adjective="Polish", region_noun="voivodeship", region_plural="voivodeships", city_noun="commune",
            city_plural="communes",
            city_note=" (the gmina, not a city district (dzielnica or delegatura), a housing estate (osiedle), a "
                      "village or a postcode)",
            below_adjectives="voivodeship, county or commune", below_nouns="voivodeship, county or commune",
            national="National", context_example="",
            ambiguity_tail="; add the voivodeship, give the full name as listed or give the commune code."))
        self.assertEqual(wording_for(polish).part_word("canton"), "voivodeship")
        self.assertEqual(wording_for(polish).part_word("city"), "city")

    def test_a_lowest_level_without_a_note(self):
        levels = [*POLISH_LEVELS[:3], POLISH_LEVELS[3].model_copy(update={"note": None})]
        self.assertEqual(generate_wording(hierarchy_of(register(levels=levels))).city_note, "")


class JoinTests(unittest.TestCase):
    def test_either_or_joins_like_the_language_list(self):
        self.assertEqual(either_or([]), "")
        self.assertEqual(either_or(["English"]), "English")
        self.assertEqual(either_or(["English", "Polish"]), "English or Polish")
        self.assertEqual(either_or(iter(["German", "French", "Italian"])), "German, French or Italian")


if __name__ == "__main__":
    unittest.main()
