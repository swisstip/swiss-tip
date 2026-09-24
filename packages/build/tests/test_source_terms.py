import unittest

from swisstip.build.source_terms import candidate_terms, missing_source_terms, normalise

EXCERPT = ("Art. 47 Frist für den Familiennachzug\n\n1 Der Anspruch auf Familiennachzug muss innerhalb von fünf Jahren "
           "geltend gemacht werden. Kinder über zwölf Jahre müssen innerhalb von zwölf Monaten nachgezogen werden.")
SEM = ("Nicht-EU/EFTA-Angehörige\n\nStammen Sie aus einem Nicht-EU/EFTA-Staat und möchten in der Schweiz erwerbstätig "
       "werden, so können Sie nur zugelassen werden, wenn Sie gut qualifiziert sind. Als gut qualifiziert gelten "
       "Führungskräfte, Spezialistinnen und Spezialisten sowie andere qualifizierte Arbeitskräfte.")


class SourceTermTests(unittest.TestCase):
    def test_normalise_ignores_case_soft_hyphens_and_line_breaks(self):
        self.assertEqual(normalise("Frist für den\n\nFamilien­nachzug"), "frist für den familiennachzug")

    def test_missing_source_terms_matches_verbatim_terms_across_case_and_whitespace(self):
        terms = ["familiennachzug", "Frist für  den Familiennachzug", "innerhalb von fünf Jahren", "Quote", "Trennung"]
        self.assertEqual(missing_source_terms(terms, [EXCERPT]), ["Quote", "Trennung"])
        self.assertEqual(missing_source_terms([], [EXCERPT]), [])

    def test_candidate_terms_rank_nouns_and_compounds_and_drop_boilerplate(self):
        candidates = candidate_terms([EXCERPT, SEM])
        self.assertEqual(candidates[0], "Familiennachzug")
        self.assertIn("Nicht-EU/EFTA-Angehörige", candidates)
        self.assertIn("Führungskräfte", candidates)
        for boilerplate in ("Art", "Der", "Sie", "Stammen"):
            self.assertNotIn(boilerplate, candidates)
        self.assertEqual(candidate_terms([EXCERPT], limit=1), ["Familiennachzug"])


if __name__ == "__main__":
    unittest.main()
