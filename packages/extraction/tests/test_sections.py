import tempfile
import unittest
from pathlib import Path

from support import CATALOGUE_URL, FEDLEX_URL, IN_SCOPE_URL, make_run
from swisstip.extraction.dataset import read_json
from swisstip.extraction.extract_cli import run_extraction
from swisstip.extraction.sections import (apply_candidates, build_sections, candidate_status, content_sections,
                                          is_content, repeated_sections, section_fields, section_text)


def block(number: int, text: str, heading: list[str], kind: str = "paragraph", **locator) -> dict:
    base = dict(region="main", furniture=[], explicit_hidden=False, is_footnote=False)
    base.update(locator)
    return dict(block_id=f"doc-x:b{number:05d}", kind=kind, text=text, heading_path=heading, links=[], source_locator=base)


RECORD = dict(document_id="doc-x", blocks=[
    block(1, "Home", ["Navigation"], region="nav", furniture=["navigation"]),
    block(2, "Anmeldung", ["Anmeldung"], kind="heading"),
    block(3, "Sie melden sich innert 14 Tagen an.", ["Anmeldung"]),
    block(4, "Fristen", ["Anmeldung", "Fristen"], kind="heading"),
    block(5, "Vor Arbeitsbeginn.", ["Anmeldung", "Fristen"]),
    block(6, "Links", ["Links"], kind="heading"),
    dict(block_id="doc-x:b00007", kind="list_item", text="SEM", heading_path=["Links"],
         links=[dict(text="SEM", href="https://sem")], source_locator=dict(region="main", furniture=[])),
    block(8, "Kanton", ["Kontakt"], region="footer"),
])


class SectionTests(unittest.TestCase):
    def test_content_sections_leave_out_furniture_and_link_lists(self):
        sections = build_sections(RECORD)
        self.assertEqual([s["heading_path"] for s in sections],
                         [["Navigation"], ["Anmeldung"], ["Anmeldung", "Fristen"], ["Links"], ["Kontakt"]])
        self.assertEqual([is_content(s) for s in sections], [False, True, True, False, False])
        self.assertEqual(sections[3]["reason"], "link_only")
        self.assertEqual(sections[4]["reason"], "page_furniture_heading")  # the heading rule outranks the footer region
        content = content_sections(RECORD)
        self.assertEqual([(s["first_block"], s["last_block"]) for s in content], [(2, 3), (4, 5)])
        self.assertEqual(section_fields(RECORD), dict(sections=5, content_sections=2,
                                                      content_characters=len(RECORD["blocks"][2]["text"]) + len(RECORD["blocks"][4]["text"])))

    def test_candidate_status_names_the_first_excluding_rule(self):
        base = dict(eligible_for_processing=True, superseded=False, preferred_representation=True, attribution_kind="catalogue")
        self.assertEqual(candidate_status(base), (True, None))
        self.assertEqual(candidate_status(base | dict(eligible_for_processing=False)), (False, "not_extractable"))
        self.assertEqual(candidate_status(base | dict(superseded=True)), (False, "superseded"))
        self.assertEqual(candidate_status(base | dict(preferred_representation=False)), (False, "secondary_representation"))
        self.assertEqual(candidate_status(base | dict(attribution_kind="language-variant")), (False, "language_variant"))
        self.assertEqual(candidate_status(base | dict(attribution_kind="out-of-scope")), (False, "out_of_scope_page"))
        # A bare record carries the kind under attribution.
        self.assertEqual(candidate_status(dict(base, attribution_kind=None, attribution=dict(kind="in-scope"))), (True, None))

    def test_apply_candidates_marks_entries_and_counts(self):
        entries = [dict(eligible_for_processing=True, superseded=False, preferred_representation=True, attribution_kind="catalogue"),
                   dict(eligible_for_processing=True, superseded=False, preferred_representation=False, attribution_kind="catalogue"),
                   dict(eligible_for_processing=False, superseded=False, preferred_representation=False, attribution_kind="catalogue")]
        counts = apply_candidates(entries)
        self.assertEqual(counts, dict(curation_candidates=1, candidate_exclusions={"secondary_representation": 1, "not_extractable": 1}))
        self.assertEqual([e["curation_candidate"] for e in entries], [True, False, False])
        self.assertEqual(entries[1]["candidate_exclusion"], "secondary_representation")


def page(document_id: str, url: str, own: str, card: str = "Telefon 044 000 00 00, Montag bis Freitag.") -> tuple[dict, dict]:
    """An index entry and a record: one section of the page's own text, one contact card shared with other pages."""
    record = dict(document_id=document_id, source_url=url, blocks=[
        block(1, "Thema", ["Thema"], kind="heading"),
        block(2, own, ["Thema"]),
        block(3, "Telefon", ["Telefon"], kind="heading"),
        block(4, card, ["Telefon"]),
    ])
    for b in record["blocks"]:
        b["block_id"] = f"{document_id}:b{int(b['block_id'].rsplit(':b', 1)[1]):05d}"
    return dict(document_id=document_id, source_url=url, curation_candidate=True), record


class RepetitionTests(unittest.TestCase):
    def test_section_text_ignores_whitespace_and_case(self):
        a = build_sections(page("doc-a", "https://www.zh.example/a", "x", "Telefon  044 000 00 00,\nMontag")[1])
        b = build_sections(page("doc-b", "https://www.zh.example/b", "y", "telefon 044 000 00 00, montag")[1])
        self.assertEqual(section_text(a[1]), section_text(b[1]))
        self.assertNotEqual(section_text(a[0]), section_text(b[0]))

    def test_repeated_sections_count_pages_per_host_and_leave_out_the_page_s_own_text(self):
        candidates = [page("doc-a", "https://www.zh.example/a.html", "Nur hier steht Regel A."),
                      page("doc-b", "https://www.zh.example/b.html", "Nur hier steht Regel B."),
                      page("doc-c", "https://www.zh.example/c.html", "Nur hier steht Regel C."),
                      page("doc-d", "https://www.other.example/d.html", "Nur hier steht Regel D."),
                      page("doc-e", "https://www.other.example/e.html", "Nur hier steht Regel E.", card="Andere Karte.")]
        repeated = repeated_sections(candidates)
        self.assertEqual(set(repeated), {"doc-a", "doc-b", "doc-c"})  # the card on other.example stands on one page there
        self.assertEqual(repeated["doc-a"], [dict(section_id="section-0002", heading_path=["Telefon"], first_block=3,
                                                 last_block=4, pages=3)])
        self.assertEqual(repeated_sections(candidates[3:]), {})

    def test_a_page_without_content_sections_repeats_nothing(self):
        entry, record = page("doc-a", "https://www.zh.example/a.html", "x")
        record["blocks"] = []
        self.assertEqual(repeated_sections([(entry, record)]), {})


class DatasetCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.temporary.name))
        self.text = self.run / "text"

    def tearDown(self):
        self.temporary.cleanup()

    def test_index_and_summary_carry_sections_and_candidates(self):
        code, summary = run_extraction(self.run, log=lambda *a, **k: None)
        self.assertEqual(code, 0)
        entries = read_json(self.text / "index.json")
        # Two records share the ELI page as source URL and representation (the shell and the resolved act), so the
        # dict keeps one of them; the summary count is checked against the list.
        index = {e["source_url"] + "|" + e["representation"]: e for e in entries}
        page = index[CATALOGUE_URL + "|html"]
        self.assertTrue(page["curation_candidate"])
        self.assertIsNone(page["candidate_exclusion"])
        self.assertGreaterEqual(page["content_sections"], 1)
        self.assertLessEqual(page["content_sections"], page["sections"])
        self.assertTrue(index[IN_SCOPE_URL + "|html"]["curation_candidate"])
        pdf = index[FEDLEX_URL + "|pdf"]  # a plugin document keeps the ELI page as its source URL
        self.assertFalse(pdf["curation_candidate"])
        self.assertEqual(pdf["candidate_exclusion"], "secondary_representation")
        self.assertEqual(summary["curation_candidates"], sum(e["curation_candidate"] for e in entries))
        self.assertIn("secondary_representation", summary["candidate_exclusions"])
        self.assertIn("not_extractable", summary["candidate_exclusions"])
        # No two pages of this run share a section: the field is there and empty, and the summary says so.
        self.assertTrue(all(e["repeated_sections"] == [] for e in entries))
        self.assertEqual(summary["records_with_repeated_sections"], 0)
        self.assertEqual(summary["repeated_sections"], 0)
