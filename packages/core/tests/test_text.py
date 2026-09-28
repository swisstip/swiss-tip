import unicodedata
import unittest

from swisstip.core.places import place_key
from swisstip.core.text import LETTERS, fold, letters_for

# Mixed scripts and the letters NFKD treats differently: decomposed accents, the umlauts, ß and its capital, ligatures,
# the Polish letters, œ (in mvp-zurich), the dotless ı (in mvp-wallisellen), the dotted İ, Nordic letters.
SAMPLE = ("Zürich Genève Neuchâtel Graubünden Straße STRASSE ẞ ﬁnance Œuvre œuvre İstanbul ı Ørsted Æble Ångström "
          "Łódź Wrocław Bolesławiec Kraków ŻÓŁĆ źdźbło ąę ñandú Çà ǅ Ⅻ ² ½ ＡＢＣ ‘quoted’ Ärzte Öl Übung")


def todays_fold(text: str) -> str:
    """fold() before letters existed, pasted verbatim."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


class FoldTests(unittest.TestCase):
    def test_without_letters_the_fold_is_todays(self):
        self.assertEqual(fold(SAMPLE), todays_fold(SAMPLE))
        self.assertEqual(fold(SAMPLE, None), todays_fold(SAMPLE))
        self.assertEqual(fold(SAMPLE, {}), todays_fold(SAMPLE))
        for word in SAMPLE.split():
            with self.subTest(word=word):
                self.assertEqual(fold(word), todays_fold(word))
        # NFKD leaves ł whole, so without the Polish letters it stays and splits a name into two words.
        self.assertEqual(fold("Wrocław"), "wrocław")
        self.assertEqual(place_key("Wrocław"), "wrocaw")

    def test_the_polish_letters(self):
        self.assertEqual(LETTERS["pl"], {"ł": "l"})
        self.assertEqual(fold("Wrocław", LETTERS["pl"]), "wroclaw")
        # Casefold turns Ł into ł first, so one entry serves both cases.
        self.assertEqual(fold("ŁÓDŹ", LETTERS["pl"]), "lodz")
        self.assertEqual(fold("Łódź", LETTERS["pl"]), fold("Lodz", LETTERS["pl"]))
        self.assertEqual(place_key("Wrocław", LETTERS["pl"]), "wroclaw")
        # No other letter is folded: œ and ı stay as today.
        self.assertEqual(fold("œuvre ı", LETTERS["pl"]), todays_fold("œuvre ı"))

    def test_letters_for_the_declared_languages(self):
        self.assertIsNone(letters_for([]))
        self.assertIsNone(letters_for(["en", "de", "fr", "it", "rm"]))
        self.assertEqual(letters_for(["en", "pl"]), {"ł": "l"})
        self.assertEqual(letters_for(iter(["pl", "pl"])), {"ł": "l"})
        # A copy: changing it leaves LETTERS alone.
        letters_for(["pl"])["x"] = "y"
        self.assertEqual(LETTERS["pl"], {"ł": "l"})


if __name__ == "__main__":
    unittest.main()
