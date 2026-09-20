"""Source terms: words and phrases taken verbatim from the cited excerpts, served as search aliases.

A concept's `source_terms` in the curation file are the terms the publisher itself uses on the
cited page: "Familiennachzug", "Niederlassungsbewilligung", "Anmeldung bei der Wohngemeinde".
They are not translations of the English label. The build checks that every term occurs in the
concept's resolved excerpts (case and whitespace do not matter) and merges the verified terms
into the released `aliases`, so that `search` matches them; a term that no excerpt of the concept
contains fails the build.

`candidate_terms` proposes terms for the curator to pick from: the capitalised words and the
hyphen or slash compounds of the excerpts, most frequent first, without function words and legal
boilerplate. Print them per concept of a built release with

    python -m swisstip.build.source_terms releases/<pack>/release.json
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from swisstip.core.text import normalise

# Sentence starters, pronouns, legal boilerplate and address words that a capitalised-word scan picks up.
STOP = frozenset("""
Sie Die Der Das Den Dem Des Ihr Ihre Ihrer Ihres Ihnen Bei Für Nach Wird Als Ein Eine Einen Einer Diese Dies Es Ja Wann Was
Wenn Wollen Stammen Damit Hier Alternativ Ausserdem Folgende Innert Innerhalb Ohne Zudem Davon Grundsätzlich Kopie
Art Abs Absatz Absätzen Artikel Buchstabe Buchstaben Bundesgesetz Bundesrat Gesetzes Oktober
Tel Fax Internet Postfach Case Route Avenue Rue Via Strasse Platz
The Anyone Register
""".split())
WORD = re.compile(r"[A-ZÄÖÜÉÈ]\w+(?:[-/]\w+)*")


def missing_source_terms(terms: list[str], excerpts: list[str]) -> list[str]:
    """The terms that occur in none of the excerpts, in the order given."""
    haystack = normalise(" ".join(excerpts))
    return [term for term in terms if normalise(term) not in haystack]


def candidate_terms(excerpts: list[str], limit: int = 20) -> list[str]:
    """Capitalised words and compounds of the excerpts, most frequent first, for the curator to choose from."""
    counts: Counter[str] = Counter()
    for excerpt in excerpts:
        for match in WORD.finditer(excerpt):
            word = match.group(0).rstrip("-/")
            if len(word) > 3 and word not in STOP:
                counts[word] += 1
    return [word for word, _ in counts.most_common(limit)]


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    release = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    facts = {fact["fact_id"]: fact for fact in release["facts"]}
    evidence = {item["evidence_id"]: item for item in release["evidence"]}
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for concept in release["concepts"]:
        excerpts = [evidence[eid]["original_excerpt"] for fid in concept["fact_ids"] for eid in facts[fid]["evidence_ids"]]
        print(f"{concept['concept_id']} ({len(excerpts)} excerpts; aliases: {', '.join(concept.get('aliases') or []) or 'none'})")
        print("  " + ", ".join(candidate_terms(excerpts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
