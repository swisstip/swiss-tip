"""The place hierarchy of a release's country: its levels, their words, the code format and the wording of places.

A release that declares its country (swiss-tip-release/v3) carries the hierarchy in its place register: the levels
from the country down, a code segment per level below the country ("PL-12-61-011" is a commune at depth 3), and the
nouns and label templates the server words places with. A release without a hierarchy is Swiss by definition and
gets the built-in SWISS hierarchy, never serialised, whose code pattern is the one the validator has always used.

The depth of a code is its number of dashes and the index of its level. The request parts map to depths
structurally: country is depth 0, canton (alias region) depth 1 and city the lowest depth. The served messages name
places through a Wording: LEGACY_WORDING, today's Swiss words, for the built-in hierarchy, and words generated from
the levels for a declared one. Design: docs/architecture/country-profiles.md, sections 2.1, 3.2, 3.5 and 4.2.
"""

import re
from dataclasses import dataclass

from .release import PlaceLevel, PlaceRegister

# CH, a canton (CH-ZH) or a municipality by its BFS number (CH-ZH-261); validation.JURISDICTION is this object.
SWISS_PATTERN = re.compile(r"^CH(?:-[A-Z]{2}(?:-\d{1,4})?)?$")
# The pattern of a declared hierarchy that cannot say what a code looks like (no single country place, a level
# without a segment): it accepts no code, and the validator names the cause.
NEVER = re.compile(r"(?!)")


@dataclass(frozen=True)
class Hierarchy:
    """The levels of a country, the country first, with its code pattern; built in for Swiss releases."""

    levels: tuple[PlaceLevel, ...]
    country_adjective: str
    country: str | None
    pattern: re.Pattern
    builtin: bool

    @property
    def lowest(self) -> int:
        """The depth of the lowest level, the one the request part city names."""
        return len(self.levels) - 1

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(level.id for level in self.levels)

    @property
    def words(self) -> dict[str, str]:
        """Level id to the adjective a basis label starts with: {"federal": "Federal", ...}."""
        return {level.id: level.adjective for level in self.levels}

    @staticmethod
    def depth(code: str) -> int:
        return code.count("-")

    def level(self, code: str) -> PlaceLevel | None:
        """The level of a code by its depth. Built in, a code below the municipality counts as municipal, as the Swiss
        levels always have; declared, a code deeper than the lowest level has none."""
        depth = self.depth(code)
        if self.builtin:
            return self.levels[min(depth, self.lowest)]
        return self.levels[depth] if depth <= self.lowest else None

    def level_id(self, code: str) -> str | None:
        level = self.level(code)
        return level.id if level else None

    def region_of(self, code: str) -> str | None:
        """The depth-1 place a code lies in (or is): CH-ZH for CH-ZH-261, PL-12 for PL-12-61-011; None for the country."""
        return "-".join(code.split("-")[:2]) if self.depth(code) >= 1 else None

    def label(self, code: str, name: str | None = None) -> str:
        """"CH (federal)", "CH-ZH (canton of Zürich)", "PL-12-61-011 (commune of Kraków)"; without a name, the level's
        noun: "CH-ZH (canton)". A code without a level is returned alone."""
        level = self.level(code)
        if level is None:
            return code
        if "{name}" in level.label:
            text = level.label.format(name=name) if name else level.noun
        else:
            text = level.label
        return f"{code} ({text})"


SWISS = Hierarchy(
    levels=(PlaceLevel(id="federal", adjective="Federal", noun="country", plural="countries", label="federal"),
            PlaceLevel(id="cantonal", adjective="Cantonal", noun="canton", plural="cantons", label="canton of {name}",
                       segment="[A-Z]{2}"),
            # The segments of SWISS are informational: the pattern above is what the validator applies.
            PlaceLevel(id="municipal", adjective="Municipal", noun="municipality", plural="municipalities",
                       label="municipality of {name}", segment="[0-9]{1,4}",
                       note="the political commune, not a district, a quarter or a postcode")),
    country_adjective="Swiss", country="CH", pattern=SWISS_PATTERN, builtin=True)


def declared_pattern(country: str | None, levels: tuple[PlaceLevel, ...]) -> re.Pattern:
    """^PL(?:-[0-9]{2}(?:-[0-9]{2}(?:-[0-9]{3})?)?)?$: the country, then each level's segment, each optional below
    the one before."""
    segments = [level.segment for level in levels[1:]]
    if country is None or any(segment is None for segment in segments):
        return NEVER
    body = "".join(f"(?:-{segment}" for segment in segments) + ")?" * len(segments)
    try:
        return re.compile(f"^{re.escape(country)}{body}$")
    except re.error:  # a width such as {9,1}: the segment grammar admits it, the regular expression does not
        return NEVER


def hierarchy_of(register: PlaceRegister | None) -> Hierarchy:
    """The declared hierarchy of a register that carries one; SWISS otherwise, also for a release without a register."""
    if register is None or register.hierarchy is None:
        return SWISS
    levels = tuple(register.hierarchy.levels)
    countries = [place.code for place in register.places if "-" not in place.code]
    country = countries[0] if len(countries) == 1 else None
    return Hierarchy(levels=levels, country_adjective=register.hierarchy.country_adjective, country=country,
                     pattern=declared_pattern(country, levels), builtin=False)


def joined(items, conjunction: str) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def either_or(items) -> str:
    """"a", "a or b", "a, b or c": the join rule of the runtime's language_list."""
    return joined(items, "or")


def article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def containment_sentence(hierarchy: Hierarchy) -> str:
    """What a concept answers for: its own place and every place below it. For SWISS: "A federal concept answers for
    any canton, a cantonal concept for its municipalities, never upward or sideways." """
    levels = hierarchy.levels
    top = levels[0].adjective.lower()
    clauses = [f"{article(top).capitalize()} {top} concept answers for any {levels[1].noun}"]
    for depth in range(1, hierarchy.lowest):
        word = levels[depth].adjective.lower()
        below = joined([level.plural for level in levels[depth + 1:]], "and")
        clauses.append(f"{article(word)} {word} concept for its {below}")
    return ", ".join(clauses) + ", never upward or sideways."


def level_list(hierarchy: Hierarchy) -> str:
    """"federal, cantonal, municipal": the levels' adjectives, lower-cased, the country first."""
    return ", ".join(level.adjective.lower() for level in hierarchy.levels)


@dataclass(frozen=True)
class Wording:
    """The words the served messages name places with: the slots of the templates in places.py and in the runtime's
    guidance and gap texts. The request part canton (alias region) names the depth-1 level, city the lowest one."""

    adjective: str  # "official Swiss sources"
    region_noun: str  # "the given canton"
    region_plural: str  # "fits several cantons"
    city_noun: str  # "the official name of the municipality"
    city_plural: str  # "fits several municipalities"
    city_note: str  # what a name at the lowest level is not, in parentheses with a leading space; empty without a note
    below_adjectives: str  # "a cantonal or municipal part"
    below_nouns: str  # "another canton or municipality"
    national: str  # "Federal concepts still apply."
    context_example: str  # the example after "what the user already said"; empty on the declared path
    ambiguity_tail: str  # what a caller does about a city name several places share

    def part_word(self, key: str) -> str:
        """The word for a key of Scope.not_recognised: "canton" is the region's noun, "city" stays "city"."""
        return self.region_noun if key == "canton" else key


# Today's Swiss words, pasted verbatim from the served strings: every Swiss message renders byte-identical from them.
LEGACY_WORDING = Wording(adjective="Swiss", region_noun="canton", region_plural="cantons", city_noun="municipality",
                         city_plural="municipalities",
                         city_note=" (the political commune, not a district, a quarter or a postcode)",
                         below_adjectives="cantonal or municipal", below_nouns="canton or municipality",
                         national="Federal", context_example=" (for example a Czech citizen is population eu_efta)",
                         ambiguity_tail="; add the canton or give the full name.")


def generate_wording(hierarchy: Hierarchy) -> Wording:
    """The words of a declared hierarchy. On the SWISS levels it gives LEGACY_WORDING except context_example (no
    example: the Swiss one names a Swiss context value) and ambiguity_tail (a declared code is written in full, so the
    code is offered as a way out)."""
    levels = hierarchy.levels
    region, city = levels[1], levels[hierarchy.lowest]
    return Wording(adjective=hierarchy.country_adjective, region_noun=region.noun, region_plural=region.plural,
                   city_noun=city.noun, city_plural=city.plural, city_note=f" ({city.note})" if city.note else "",
                   below_adjectives=either_or(level.adjective.lower() for level in levels[1:]),
                   below_nouns=either_or(level.noun for level in levels[1:]), national=levels[0].adjective,
                   context_example="",
                   ambiguity_tail=(f"; add the {region.noun}, give the full name as listed or give the {city.noun} "
                                   "code."))


def wording_for(hierarchy: Hierarchy) -> Wording:
    """LEGACY_WORDING for the built-in hierarchy, the generated words for a declared one."""
    return LEGACY_WORDING if hierarchy.builtin else generate_wording(hierarchy)
