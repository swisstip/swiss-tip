"""Text normalisation shared by the build's source-term check, the acceptance check, search and place names."""

import unicodedata
from collections.abc import Iterable, Mapping

SOFT_HYPHEN = "­"
# Letters that NFKD leaves whole, per language code: casefold turns "Ł" into "ł" first, and without the fold "Wrocław"
# splits into "wroc" and "aw". A release applies the letters of the query languages it declares (letters_for), so the
# fold of a release that declares none, every Swiss one, is unchanged: "œ" occurs in mvp-zurich and "ı" in
# mvp-wallisellen, and neither is folded.
LETTERS: dict[str, dict[str, str]] = {"pl": {"ł": "l"}}


def fold(text: str, letters: Mapping[str, str] | None = None) -> str:
    """Casefold and drop diacritics, so that "Zürich" and "zurich", or "Genève" and "geneve", are one spelling. With
    `letters` (from letters_for), each such letter is replaced after the casefold: "Wrocław" becomes "wroclaw"."""
    text = text.casefold()
    if letters:
        text = text.translate(str.maketrans(dict(letters)))
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def letters_for(codes: Iterable[str]) -> dict[str, str] | None:
    """The letters fold() replaces for these language codes, united; None when none of them has any (["en", "de"])."""
    letters: dict[str, str] = {}
    for code in sorted(set(codes)):
        letters.update(LETTERS.get(code, {}))
    return letters or None


def collapse_umlauts(token: str) -> str:
    """The ASCII spelling of an umlaut folded to what fold() makes of the umlaut itself, so that "Zuerich" and
    "Zürich" (already "zurich"), or "Fuehrerausweis" and "Führerausweis", are one word: users type both, and the
    release's own aliases use both. The digraphs are collapsed wherever they occur, also in "Steuer" or "neue";
    index and query are folded alike, so a word still matches itself."""
    for digraph, vowel in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        token = token.replace(digraph, vowel)
    return token


def normalise(text: str) -> str:
    """Casefold, drop soft hyphens and collapse whitespace so that a phrase matches across line breaks and hyphenation."""
    return " ".join(unicodedata.normalize("NFKC", text).replace(SOFT_HYPHEN, "").casefold().split())


def contains_phrase(haystack: str, phrase: str) -> bool:
    return normalise(phrase) in normalise(haystack)
