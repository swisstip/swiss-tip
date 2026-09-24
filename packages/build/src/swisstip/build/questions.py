"""The language of an authored sample question, for the build's check that every concept has one per query language.

A concept's `questions` in the curation file are phrased the way users ask, and search weighs them as an anchor field.
A pack whose callers ask in English and German lists both in the curation's `question_languages`; the build then
refuses a concept that has no sample question in one of them, so a question in that language does not have to reach the
concept through its label or the publisher's terms alone.

A question's language is told from its function words, which a full question always has and which do not depend on the
subject: "Where do I register?" and "Wo melde ich mich an?". Words both languages use ("in", "an", "was", "will") count
for neither. English and German only; the build refuses other codes.
"""

import re

ENGLISH = frozenset("""
the a is are do does did can could may might must should would shall i you he she it we they my your our their me him
her us them what which who whom whose when where why how to of on at for from with without after before if or and not
this that these those there have has had be been get got any
""".split())
GERMAN = frozenset("""
der die das den dem des ein eine einen einer einem eines ich du er sie es wir ihr mein meine meinen meiner meinem mich
mir uns und oder nicht kein keine keinen ist sind bin habe hat haben wird werden kann können darf dürfen muss müssen
soll sollen welche welcher welches welchen wer wie wo wann warum mit für von vom zum zur beim bei nach auf aus über unter
ohne bis als wenn dass auch noch schon im ins gibt bekomme brauche
""".split())
LANGUAGES = frozenset({"en", "de"})
WORD = re.compile(r"[^\W\d_]+")


def question_language(question: str) -> str | None:
    """"en" or "de" by the question's function words; None when neither language has more of them."""
    words = WORD.findall(question.lower())
    english = sum(word in ENGLISH for word in words)
    german = sum(word in GERMAN for word in words)
    if english == german:
        return None
    return "en" if english > german else "de"


def missing_question_languages(questions: list[str], languages: list[str]) -> list[str]:
    """The required languages no question is written in, in the order given."""
    found = {question_language(question) for question in questions}
    return [language for language in languages if language not in found]
