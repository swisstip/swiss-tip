import unittest

from swisstip.build.questions import missing_question_languages, question_language


class QuestionLanguageTests(unittest.TestCase):
    def test_function_words_tell_english_from_german(self):
        self.assertEqual(question_language("Where do I register after arriving from abroad?"), "en")
        self.assertEqual(question_language("I'm a retired American. Can I live in Zurich?"), "en")
        self.assertEqual(question_language("Wo melde ich mich nach dem Zuzug aus dem Ausland an?"), "de")
        self.assertEqual(question_language("Wer bezahlt den Rettungswagen?"), "de")
        self.assertEqual(question_language("Was kostet es, Sperrgut beim Recyclinghof abzugeben?"), "de")

    def test_words_both_languages_use_count_for_neither(self):
        # "in", "an", "was" and "will" occur in both languages; place names and nouns count for neither.
        self.assertIsNone(question_language("Personenmeldeamt Zürich Öffnungszeiten"))
        self.assertIsNone(question_language("DMV Zurich hours"))
        self.assertIsNone(question_language("was in an"))
        self.assertIsNone(question_language(""))

    def test_missing_languages_are_listed_in_the_order_required(self):
        questions = ["When can I get a C permit?", "Wie lange gilt eine B-Bewilligung?"]
        self.assertEqual(missing_question_languages(questions, ["en", "de"]), [])
        self.assertEqual(missing_question_languages(questions[:1], ["en", "de"]), ["de"])
        self.assertEqual(missing_question_languages([], ["de", "en"]), ["de", "en"])
        self.assertEqual(missing_question_languages([], []), [])


if __name__ == "__main__":
    unittest.main()
