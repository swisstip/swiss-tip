"""Turn the place a caller names into the jurisdiction code the facts carry.

A caller says where the user lives in parts: a country, a canton and a city.
Each part is a name ("Switzerland", "Kanton Zürich", "Wallisellen", in English
or in the local language) or a code ("CH", "ZH", "CH-ZH", "CH-ZH-69", or the
BFS number "69" next to its canton). The names come from the release's place
register, so the server holds no country's names itself: another country's
pack brings its own.

The levels come from the register too (core/hierarchy.py). A release that
declares its hierarchy names its own levels: canton (alias region) is the
depth-1 level, a voivodeship "PL-12", and city the lowest one, a commune
"PL-12-61-011", given by name or by its full code, whose segments are kept as
written. A release without one is Swiss and parses codes as it always has.

Nothing is guessed. A city given alone supplies its canton, because the
register says where it lies. A name the register does not hold is reported as
not recognised and the request runs for the broader place that was
recognised, which still reaches the federal and cantonal facts. A name several
municipalities share ("Buchs" in ZH, SG and AG) and a city that lies in
another canton than the one given are errors that name the candidates. A
release without a register (built before 18 September 2026) accepts codes
only.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field

from .hierarchy import hierarchy_of, wording_for
from .release import Place, PlaceRegister
from .text import collapse_umlauts, fold

COUNTRY_CODE = re.compile(r"[A-Za-z]{2}")
# The Swiss codes, parsed only for a release without a declared hierarchy.
CANTON_CODE = re.compile(r"(?:([A-Za-z]{2})-)?([A-Za-z]{2})")
MUNICIPALITY_CODE = re.compile(r"(?:(?:([A-Za-z]{2})-)?([A-Za-z]{2})-)?0*(\d{1,4})")
QUALIFIER = re.compile(r"\s*\(([^)]*)\)\s*$")
# The place errors, worded by the release's hierarchy (hierarchy.Wording); LEGACY_WORDING renders today's texts.
REGION_AMBIGUOUS = "{value!r} fits several {region_plural}: {candidates}; give one of them."
CITY_OUTSIDE_REGION = ("The {city_noun} does not lie in the given {region_noun}: {candidates}, not in {region}; "
                       "correct one of them, or give only the {city_noun}.")
CITY_AMBIGUOUS = "{value!r} fits several {city_plural}: {candidates}{ambiguity_tail}"


class PlaceError(ValueError):
    """A jurisdiction part the caller has to correct; `path` is the request field."""

    def __init__(self, path: str, message: str):
        super().__init__(message)
        self.path = path
        self.message = message


@dataclass(frozen=True)
class Scope:
    """What a jurisdiction was understood as: the codes a request runs for and the register's names for them."""

    country_code: str | None
    canton_code: str | None = None
    municipality_id: str | None = None
    country: str | None = None
    canton: str | None = None
    city: str | None = None
    not_recognised: dict[str, str] = field(default_factory=dict)
    served: bool = True

    @property
    def code(self) -> str | None:
        """The most specific code; None when the country is not one the release serves."""
        return (self.municipality_id or self.canton_code or self.country_code) if self.served else None


def name_words(text: str, letters: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """The words of a name, each as written (casefolded) and folded: without diacritics and with the umlaut digraphs
    collapsed, so that "Zürich", "Zuerich" and "zurich" meet. Punctuation separates words ("St. Gallen", "St Gallen").
    `letters` are the release's letter folds (text.letters_for): with Polish, "Łódź" and "Lodz" meet."""
    words = []
    for word in re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()):
        folded = "".join(re.findall(r"[a-z0-9]+", collapse_umlauts(fold(word, letters))))
        if folded:
            words.append((word, folded))
    return words


def place_key(text: str, letters: Mapping[str, str] | None = None) -> str:
    """The folded spelling of a name, the key names are found by."""
    return " ".join(folded for _, folded in name_words(text, letters))


def level_of(code: str) -> int:
    """The depth of a code: 0 for the country, 1 for a canton (a voivodeship), and so on down to the lowest level, 2
    for a Swiss municipality and 3 for a Polish commune."""
    return code.count("-")


def declared_code_pattern(segments: list[str | None]) -> re.Pattern | None:
    """A code of a declared hierarchy down to a level, with or without the country: "PL-12-61-011" and "12-61-011"
    for the segments [0-9]{2}, [0-9]{2}, [0-9]{3}; each segment is a group, kept as written. None when a level has
    no segment or a width does not compile (the validator names the cause)."""
    if not segments or any(segment is None for segment in segments):
        return None
    try:
        return re.compile("(?ai)(?:([a-z]{2})-)?" + "-".join(f"({segment})" for segment in segments))
    except re.error:
        return None


class PlaceIndex:
    def __init__(self, register: PlaceRegister | None, jurisdictions: list[str],
                 letters: Mapping[str, str] | None = None):
        self.register = register
        self.letters = letters
        # The levels of the register's country, SWISS when it declares none; the words its errors name places with.
        self.hierarchy = hierarchy_of(register)
        self.wording = wording_for(self.hierarchy)
        segments = [level.segment for level in self.hierarchy.levels[1:]]
        self.region_code = None if self.hierarchy.builtin else declared_code_pattern(segments[:1])
        self.city_code = None if self.hierarchy.builtin else declared_code_pattern(segments)
        self.countries = sorted({code.split("-")[0] for code in jurisdictions})
        # A release that serves one country needs no country from the caller.
        self.default_country = self.countries[0] if len(self.countries) == 1 else None
        self.places: dict[str, Place] = {place.code: place for place in register.places} if register else {}
        self.generic = {token for word in (register.generic_words if register else [])
                        for token in place_key(word, letters).split()}
        # Per level, folded key to (code, spelling as written): the exact keys (official name, aliases) and the loose
        # ones (the name without its qualifier "(ZH)", each part of "Biel/Bienne", at the lowest level the name
        # followed by its region's segment, the canton's abbreviation "Buchs ZH").
        self.exact: list[dict[str, list[tuple[str, str]]]] = [{} for _ in self.hierarchy.levels]
        self.loose: list[dict[str, list[tuple[str, str]]]] = [{} for _ in self.hierarchy.levels]
        for place in self.places.values():
            level = level_of(place.code)
            for name in (place.name, *place.aliases):
                self.add(self.exact[level], name, place.code)
                base = QUALIFIER.sub("", name)
                for part in (base, *(base.split("/") if "/" in base else [])):
                    self.add(self.loose[level], part, place.code)
                    if level == self.hierarchy.lowest:
                        self.add(self.loose[level], f"{part} {place.code.split('-')[1]}", place.code)
            # The country's and the regions' own segments ("CH", "ZH"; "PL", "12"), never a county's or a city's.
            if level <= 1:
                self.add(self.exact[level], place.code.split("-")[-1], place.code)

    def add(self, table: dict[str, list[tuple[str, str]]], name: str, code: str) -> None:
        words = name_words(name, self.letters)
        entry = (code, " ".join(written for written, _ in words))
        if words and entry not in table.setdefault(" ".join(folded for _, folded in words), []):
            table[" ".join(folded for _, folded in words)].append(entry)

    # --- naming --------------------------------------------------------------

    def name(self, code: str | None) -> str | None:
        place = self.places.get(code or "")
        return place.name if place else None

    def label(self, code: str) -> str:
        """"CH (federal)", "CH-ZH (canton of Zürich)", "CH-ZH-261 (municipality of Zürich)"; "PL-12 (województwo
        małopolskie)" with a declared hierarchy; without a register, the level alone."""
        return self.hierarchy.label(code, self.name(code))

    def described(self, code: str) -> str:
        """"Zürich (CH-ZH-261)" for a message; the code alone without a register."""
        name = self.name(code)
        return f"{name} ({code})" if name else code

    # --- matching ------------------------------------------------------------

    def lookup(self, level: int, value: str, within: str | None = None, merged: bool = False) -> list[str]:
        """Codes of the places of one level that the name fits, below `within` when given: the exact keys before the
        loose ones, then the same again without the generic words around the name. Folding makes different places
        meet ("Brugg" and "Brügg", "Uezwil" and "Uzwil"); among several, the one written as the caller wrote it wins.

        `merged` takes the exact and the loose keys together: a declared hierarchy qualifies only the names shared
        within a region ("Świdnica (gmina miejska)"), so the same name can be exact in one region and loose in another,
        and an exact key alone would hide the other places of that name."""
        words = name_words(value, self.letters)
        bare = list(words)
        while bare and bare[0][1] in self.generic:
            bare = bare[1:]
        while bare and bare[-1][1] in self.generic:
            bare = bare[:-1]
        for candidate in (words, bare) if bare != words else (words,):
            written, folded = " ".join(w for w, _ in candidate), " ".join(f for _, f in candidate)
            tables = [[*self.exact[level].get(folded, []), *self.loose[level].get(folded, [])]] if merged else \
                [table.get(folded, []) for table in (self.exact[level], self.loose[level])]
            for entries in tables:
                found = [(code, spelling) for code, spelling in entries if within is None or code.startswith(within + "-")]
                as_written = [code for code, spelling in found if spelling == written]
                codes = list(dict.fromkeys(as_written or [code for code, _ in found]))
                if codes:
                    return codes
        return []

    def country_of(self, value: str | None) -> tuple[str | None, bool]:
        """(code, served): the default country when none is given; a country the release does not serve keeps the
        caller's code when it sent one."""
        if value is None or not value.strip():
            if self.default_country is None:
                raise PlaceError("jurisdiction.country", f"This release serves {', '.join(self.countries)}; name the country.")
            return self.default_country, True
        value = value.strip()
        if COUNTRY_CODE.fullmatch(value) and value.upper() in self.countries:
            return value.upper(), True
        codes = [code for code in self.lookup(0, value) if code in self.countries]
        if codes:
            return codes[0], True
        return (value.upper() if COUNTRY_CODE.fullmatch(value) else None), False

    def resolve(self, country: str | None = None, canton: str | None = None, city: str | None = None) -> Scope:
        country_code, served = self.country_of(country)
        if not served:
            return Scope(country_code=country_code, country=None if country_code else country.strip(), served=False)
        not_recognised: dict[str, str] = {}
        canton_code = self.canton_of(country_code, canton, not_recognised)
        municipality = self.city_of(country_code, canton_code, city, not_recognised)
        if municipality and canton_code is None and level_of(municipality) == self.hierarchy.lowest:
            canton_code = self.hierarchy.region_of(municipality)
        return Scope(country_code=country_code, canton_code=canton_code, municipality_id=municipality,
                     country=self.name(country_code), canton=self.name(canton_code), city=self.name(municipality),
                     not_recognised=not_recognised)

    def canton_of(self, country: str, value: str | None, not_recognised: dict[str, str]) -> str | None:
        """The depth-1 place: a canton, a voivodeship. The path and the not_recognised key stay "canton", the wire
        names of the request part."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if self.hierarchy.builtin:
            match = CANTON_CODE.fullmatch(value)
            if match and (match[1] or country).upper() == country:
                code = f"{country}-{match[2].upper()}"
                if not self.places or code in self.places:
                    return code
        else:
            code = self.declared_code(self.region_code, country, value)
            if code in self.places:
                return code
        codes = self.lookup(1, value, country)
        if len(codes) == 1:
            return codes[0]
        if len(codes) > 1:
            raise PlaceError("jurisdiction.canton", REGION_AMBIGUOUS.format(
                value=value, region_plural=self.wording.region_plural,
                candidates=", ".join(self.described(code) for code in codes)))
        not_recognised["canton"] = value
        return None

    def city_of(self, country: str, canton: str | None, value: str | None, not_recognised: dict[str, str]) -> str | None:
        """A place at the lowest level: a municipality, a commune. Its name is never looked up at a level in between
        (a county), and a code that parses but is not listed is not recognised, with no name fallback."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        codes = None
        if self.hierarchy.builtin:
            match = MUNICIPALITY_CODE.fullmatch(value)
            if match and (match[1] or country).upper() == country:
                codes = self.numbered(country, canton, match[2], int(match[3]))
        else:
            code = self.declared_code(self.city_code, country, value)
            if code is not None:
                codes = [code] if code in self.places else []
        if codes is None:
            codes = self.lookup(self.hierarchy.lowest, value, country, merged=not self.hierarchy.builtin)
        if not codes:
            not_recognised["city"] = value
            return None
        wording = self.wording
        inside = [code for code in codes if canton is None or code.startswith(canton + "-")]
        if not inside:
            raise PlaceError("jurisdiction.city", CITY_OUTSIDE_REGION.format(
                city_noun=wording.city_noun, region_noun=wording.region_noun, region=self.described(canton),
                candidates=", ".join(f"{self.described(code)} lies in {self.described(self.hierarchy.region_of(code))}"
                                     for code in codes)))
        if len(inside) > 1:
            # Each candidate with its parent: the canton, or the county, which tells apart communes of one voivodeship.
            raise PlaceError("jurisdiction.city", CITY_AMBIGUOUS.format(
                value=value, city_plural=wording.city_plural, ambiguity_tail=wording.ambiguity_tail,
                candidates=", ".join(f"{self.described(code)} in {self.described(code.rsplit('-', 1)[0])}"
                                     for code in inside)))
        return inside[0]

    @staticmethod
    def declared_code(pattern: re.Pattern | None, country: str, value: str) -> str | None:
        """The code a caller wrote in a declared hierarchy's format ("pl-12-61-011", "12-61-011" for PL-12-61-011),
        with its segments as written, only upper-cased; None when it is not such a code or names another country."""
        match = pattern.fullmatch(value) if pattern else None
        if match is None or (match[1] or country).upper() != country:
            return None
        return "-".join([country, *(segment.upper() for segment in match.groups()[1:])])

    def numbered(self, country: str, canton: str | None, given_canton: str | None, number: int) -> list[str]:
        """A municipality given by its number in a code ("ZH-261", "CH-ZH-261"), or alone ("261") next to a canton.
        A bare number without a canton is not looked up across the country: a postcode would find a municipality
        (4001 is Basel's postcode and Aarau's number)."""
        prefix = f"{country}-{given_canton.upper()}" if given_canton else canton
        if prefix is None:
            return []
        code = f"{prefix}-{number}"
        return [code] if not self.places or code in self.places else []
