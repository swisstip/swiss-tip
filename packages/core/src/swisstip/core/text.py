"""Text normalisation shared by the build's source-term check, the acceptance check, search and place names."""

import unicodedata

SOFT_HYPHEN = "­"


def fold(text: str) -> str:
    """Casefold and drop diacritics, so that "Zürich" and "zurich", or "Genève" and "geneve", are one spelling."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


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
