"""Turn the place a caller names into the jurisdiction code the facts carry.

A caller says where the user lives in parts: a country, a canton and a city.
Each part is a name ("Switzerland", "Kanton Zürich", "Wallisellen", in English
or in the local language) or a code ("CH", "ZH", "CH-ZH", "CH-ZH-69", or the
BFS number "69" next to its canton). The names come from the release's place
register, so the server holds no country's names itself: another country's
pack brings its own.

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
from dataclasses import dataclass, field

from .release import Place, PlaceRegister
from .text import collapse_umlauts, fold

COUNTRY_CODE = re.compile(r"[A-Za-z]{2}")
CANTON_CODE = re.compile(r"(?:([A-Za-z]{2})-)?([A-Za-z]{2})")
MUNICIPALITY_CODE = re.compile(r"(?:(?:([A-Za-z]{2})-)?([A-Za-z]{2})-)?0*(\d{1,4})")
QUALIFIER = re.compile(r"\s*\(([^)]*)\)\s*$")


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


def name_words(text: str) -> list[tuple[str, str]]:
    """The words of a name, each as written (casefolded) and folded: without diacritics and with the umlaut digraphs
    collapsed, so that "Zürich", "Zuerich" and "zurich" meet. Punctuation separates words ("St. Gallen", "St Gallen")."""
    words = []
    for word in re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()):
        folded = "".join(re.findall(r"[a-z0-9]+", collapse_umlauts(fold(word))))
        if folded:
            words.append((word, folded))
    return words


def place_key(text: str) -> str:
    """The folded spelling of a name, the key names are found by."""
    return " ".join(folded for _, folded in name_words(text))


def level_of(code: str) -> int:
    """0 for the country, 1 for a canton, 2 for a municipality."""
    return code.count("-")


class PlaceIndex:
    def __init__(self, register: PlaceRegister | None, jurisdictions: list[str]):
        self.register = register
        self.countries = sorted({code.split("-")[0] for code in jurisdictions})
        # A release that serves one country needs no country from the caller.
        self.default_country = self.countries[0] if len(self.countries) == 1 else None
        self.places: dict[str, Place] = {place.code: place for place in register.places} if register else {}
        self.generic = {token for word in (register.generic_words if register else []) for token in place_key(word).split()}
        # Per level, folded key to (code, spelling as written): the exact keys (official name, aliases) and the loose
        # ones (the name without its qualifier "(ZH)", each part of "Biel/Bienne", the name followed by the canton's
        # abbreviation).
        self.exact: list[dict[str, list[tuple[str, str]]]] = [{}, {}, {}]
        self.loose: list[dict[str, list[tuple[str, str]]]] = [{}, {}, {}]
        for place in self.places.values():
            level = level_of(place.code)
            for name in (place.name, *place.aliases):
                self.add(self.exact[level], name, place.code)
                base = QUALIFIER.sub("", name)
                for part in (base, *(base.split("/") if "/" in base else [])):
                    self.add(self.loose[level], part, place.code)
                    if level == 2:
                        self.add(self.loose[level], f"{part} {place.code.split('-')[1]}", place.code)
            if level < 2:
                self.add(self.exact[level], place.code.split("-")[-1], place.code)

    @staticmethod
    def add(table: dict[str, list[tuple[str, str]]], name: str, code: str) -> None:
        words = name_words(name)
        entry = (code, " ".join(written for written, _ in words))
        if words and entry not in table.setdefault(" ".join(folded for _, folded in words), []):
            table[" ".join(folded for _, folded in words)].append(entry)

    # --- naming --------------------------------------------------------------

    def name(self, code: str | None) -> str | None:
        place = self.places.get(code or "")
        return place.name if place else None

    def label(self, code: str) -> str:
        """"CH (federal)", "CH-ZH (canton of Zürich)", "CH-ZH-261 (municipality of Zürich)"; without a register, the
        level alone."""
        level = level_of(code)
        if level == 0:
            return f"{code} (federal)"
        word = "canton" if level == 1 else "municipality"
        name = self.name(code)
        return f"{code} ({word} of {name})" if name else f"{code} ({word})"

    def described(self, code: str) -> str:
        """"Zürich (CH-ZH-261)" for a message; the code alone without a register."""
        name = self.name(code)
        return f"{name} ({code})" if name else code

    # --- matching ------------------------------------------------------------

    def lookup(self, level: int, value: str, within: str | None = None) -> list[str]:
        """Codes of the places of one level that the name fits, below `within` when given: the exact keys before the
        loose ones, then the same again without the generic words around the name. Folding makes different places
        meet ("Brugg" and "Brügg", "Uezwil" and "Uzwil"); among several, the one written as the caller wrote it wins."""
        words = name_words(value)
        bare = list(words)
        while bare and bare[0][1] in self.generic:
            bare = bare[1:]
        while bare and bare[-1][1] in self.generic:
            bare = bare[:-1]
        for candidate in (words, bare) if bare != words else (words,):
            written, folded = " ".join(w for w, _ in candidate), " ".join(f for _, f in candidate)
            for table in (self.exact[level], self.loose[level]):
                found = [(code, spelling) for code, spelling in table.get(folded, []) if within is None or code.startswith(within + "-")]
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
        if municipality and canton_code is None and level_of(municipality) == 2:
            canton_code = municipality.rsplit("-", 1)[0]
        return Scope(country_code=country_code, canton_code=canton_code, municipality_id=municipality,
                     country=self.name(country_code), canton=self.name(canton_code), city=self.name(municipality),
                     not_recognised=not_recognised)

    def canton_of(self, country: str, value: str | None, not_recognised: dict[str, str]) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        match = CANTON_CODE.fullmatch(value)
        if match and (match[1] or country).upper() == country:
            code = f"{country}-{match[2].upper()}"
            if not self.places or code in self.places:
                return code
        codes = self.lookup(1, value, country)
        if len(codes) == 1:
            return codes[0]
        if len(codes) > 1:
            raise PlaceError("jurisdiction.canton", f"{value!r} fits several cantons: "
                             + ", ".join(self.described(code) for code in codes) + "; give one of them.")
        not_recognised["canton"] = value
        return None

    def city_of(self, country: str, canton: str | None, value: str | None, not_recognised: dict[str, str]) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        match = MUNICIPALITY_CODE.fullmatch(value)
        if match and (match[1] or country).upper() == country:
            codes = self.numbered(country, canton, match[2], int(match[3]))
        else:
            codes = self.lookup(2, value, country)
        if not codes:
            not_recognised["city"] = value
            return None
        inside = [code for code in codes if canton is None or code.startswith(canton + "-")]
        if not inside:
            raise PlaceError("jurisdiction.city", "The municipality does not lie in the given canton: "
                             + ", ".join(f"{self.described(code)} lies in {self.described(code.rsplit('-', 1)[0])}" for code in codes)
                             + f", not in {self.described(canton)}; correct one of them, or give only the municipality.")
        if len(inside) > 1:
            raise PlaceError("jurisdiction.city", f"{value!r} fits several municipalities: "
                             + ", ".join(f"{self.described(code)} in {self.described(code.rsplit('-', 1)[0])}" for code in inside)
                             + "; add the canton or give the full name.")
        return inside[0]

    def numbered(self, country: str, canton: str | None, given_canton: str | None, number: int) -> list[str]:
        """A municipality given by its number in a code ("ZH-261", "CH-ZH-261"), or alone ("261") next to a canton.
        A bare number without a canton is not looked up across the country: a postcode would find a municipality
        (4001 is Basel's postcode and Aarau's number)."""
        prefix = f"{country}-{given_canton.upper()}" if given_canton else canton
        if prefix is None:
            return []
        code = f"{prefix}-{number}"
        return [code] if not self.places or code in self.places else []
