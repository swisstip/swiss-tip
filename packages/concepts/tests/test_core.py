import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from swisstip.extraction.records import attach_offsets
from swisstip.concepts.chunks import pack_chunks
from swisstip.concepts.prompts import load_prompts
from swisstip.concepts.schemas import extraction_schema
from swisstip.concepts.sections import build_sections
from swisstip.concepts.validation import ValidationError, parse_proposals, parse_verdicts, validate_proposal


def make_record(blocks):
    record = dict(document_id="doc-example", title="Aufenthalt", language_declared="de",
                  source_url="https://example.invalid/page", document_url="https://example.invalid/page",
                  acquisition={"raw_sha256": "a" * 64}, extractor_version="test",
                  blocks=[dict(kind="paragraph", text=block, heading_path=["Aufenthalt"], source_locator={})
                          if isinstance(block, str) else block for block in blocks])
    attach_offsets(record)
    return record


def proposal(span, **overrides):
    return dict(dict(preferred_label="Anmeldung", alternative_labels=[], concept_type="PROCESS",
                     granularity="ANSWERABLE", description="Personen melden sich bei der Gemeinde an.",
                     scope="Zuziehende Personen", user_questions=["Wo melde ich mich an?"], confidence=0.7,
                     evidence=[{"evidence_id": span["evidence_id"]}], relations=[],
                     primary_section_id=span["section_id"]), **overrides)


class SectionsTests(unittest.TestCase):
    def test_runs_headings_and_contact_only(self):
        record = make_record([dict(kind="heading", text="Kontakt", heading_path=["Kontakt"], source_locator={}),
                              dict(kind="paragraph", text="Rufen Sie uns an.", heading_path=["Kontakt"], source_locator={})])
        sections = build_sections(record)
        self.assertEqual(len(sections), 1)
        self.assertTrue(sections[0]["kept"])
        self.assertEqual([b["block_number"] for b in sections[0]["span_blocks"]], [2])
        record["blocks"].append(dict(record["blocks"][1], heading_path=["Anderes"]))
        record["blocks"].append(dict(record["blocks"][1]))
        sections = build_sections(record)
        self.assertEqual([s["section_id"] for s in sections], ["section-0001", "section-0002", "section-0003"])
        self.assertFalse(sections[0]["kept"])

    def test_every_exclusion_and_no_main_dependency(self):
        cases = [
            ("hidden", {"explicit_hidden": True}, "paragraph", ["Topic"]),
            ("region", {"region": "nav"}, "paragraph", ["Topic"]),
            ("furniture", {"furniture": ["breadcrumb"]}, "paragraph", ["Topic"]),
            ("control", {}, "control", ["Topic"]),
            ("page-furniture", {"furniture": ["page-header"]}, "pdf_paragraph", ["Topic"]),
            ("footnote", {"is_footnote": True}, "paragraph", ["Topic"]),
            ("page_furniture_heading", {}, "paragraph", ["Topic", "Zusta\u0308ndigkeit"]),
            ("embedded_news", {}, "paragraph", ["Topic", "News"]),
        ]
        for expected, locator, kind, path in cases:
            with self.subTest(expected):
                section = build_sections(make_record([dict(text="Text", kind=kind, source_locator=locator, heading_path=path)]))[0]
                self.assertFalse(section["kept"])
                self.assertEqual(section["reason"], expected)
                self.assertEqual(bool(section["context_blocks"]), expected == "footnote")
        section = build_sections(make_record([dict(text="Kontakt und News stehen im Text.", kind="paragraph",
                                                   source_locator={"in_main": False}, heading_path=["News"])]))[0]
        self.assertTrue(section["kept"])

    def test_mixed_section_keeps_content_but_records_every_excluded_block(self):
        record = make_record([dict(kind="heading", text="Aufenthalt", heading_path=["Aufenthalt"], source_locator={}),
                              "Substantive Regel.",
                              dict(kind="paragraph", text="Hidden", heading_path=["Aufenthalt"], source_locator={"explicit_hidden": True}),
                              dict(kind="pdf_paragraph", text="Footer", heading_path=["Aufenthalt"], source_locator={"furniture": ["page-footer"]})])
        original = json.dumps(record)
        section = build_sections(record)[0]
        self.assertEqual([b["block_number"] for b in section["span_blocks"]], [2])
        self.assertEqual(section["excluded_blocks"], [dict(block_number=3, reason="hidden"), dict(block_number=4, reason="page-furniture")])
        self.assertEqual(json.dumps(record), original)

    def test_link_only_labels_and_fallback(self):
        for labels in (["link-only"], []):
            with self.subTest(labels=labels):
                block = dict(text="Formular", kind="list_item", source_locator={"furniture": labels},
                             heading_path=["Links"], links=[dict(text="Formular", href="/form")])
                record = make_record([block])
                self.assertTrue(build_sections(record)[0]["link_only"])
                self.assertEqual(pack_chunks(record)[0]["status"], "skipped_link_only")
        block["text"] = "Nutzen Sie das Formular."
        self.assertFalse(build_sections(make_record([block]))[0]["link_only"])


class ChunksTests(unittest.TestCase):
    def test_block_packing_and_exact_unicode_offsets(self):
        record = make_record(["Grüezi! " * 40, "Änderung " * 30, "Dritte Regel."])
        chunks = pack_chunks(record, chunk_content_characters=500, chunk_overlap_characters=100)
        self.assertEqual(len(chunks), 2)
        self.assertEqual({s["block_number"] for s in chunks[0]["evidence_spans"]}, {1})
        for chunk in chunks:
            self.assertLessEqual(chunk["characters"], 500)
            for span in chunk["evidence_spans"]:
                self.assertEqual(span["quote"], record["content_text"][span["start"]:span["end"]])

    def test_oversized_block_overlap_and_span_cap(self):
        record = make_record(["Die Bewilligung gilt für diese Person. " * 100])
        chunks = pack_chunks(record, chunk_content_characters=1000, chunk_overlap_characters=150)
        self.assertGreater(len(chunks), 2)
        for previous, current in zip(chunks, chunks[1:]):
            self.assertLess(current["evidence_spans"][0]["start"], previous["evidence_spans"][-1]["end"])
            self.assertGreater(current["evidence_spans"][0]["start"], previous["evidence_spans"][0]["start"])
        for chunk in chunks:
            self.assertLessEqual(chunk["characters"], 1000)
            for span in chunk["evidence_spans"]:
                self.assertLessEqual(len(span["quote"]), 500)
                self.assertEqual(span["quote"], record["content_text"][span["start"]:span["end"]])
                _, left, right = span["evidence_id"].split(":")
                self.assertEqual(record["blocks"][0]["text"][int(left):int(right)], span["quote"])

    def test_cut_at_last_space_and_schema_catalogue(self):
        record = make_record(["A" * 260 + " " + "B" * 200 + " " + "C" * 150])
        chunk = pack_chunks(record)[0]
        spans = chunk["evidence_spans"]
        self.assertEqual(len(spans[0]["text"]), 461)
        schema = extraction_schema(6, [s["evidence_id"] for s in spans], chunk["section_ids"])
        props = schema["properties"]["concepts"]["items"]["properties"]
        self.assertEqual(props["evidence"]["items"]["properties"]["evidence_id"]["enum"], [s["evidence_id"] for s in spans])
        self.assertEqual(props["primary_section_id"]["enum"], ["section-0001"])


class PromptsTests(unittest.TestCase):
    def test_predecessor_hashes(self):
        prompts = load_prompts()
        self.assertEqual(prompts.extraction.sha256, "ccd968b7b59b7ef144255483d42cda016872c0c82b49d00deb384578b234ee83")
        self.assertEqual(prompts.review.sha256, "893394d896520067767f1c69eaeefd8efad780658710b99fde64112d8390c78e")
        self.assertEqual(prompts.basis.sources, ("package:swisstip.concepts/prompts/basis_classification_v1.md",))
        self.assertEqual(set(prompts.to_dict()), {"extraction", "review", "basis"})
        self.assertTrue(prompts.basis.text.startswith("Classify the basis of proposed concepts"))

    def test_override_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt.md"
            path.write_bytes(b"\xef\xbb\xbfReplacement\r\n")
            prompt = load_prompts(extraction_prompt_file=path).extraction
            self.assertEqual(prompt.text, "Replacement\n")
            self.assertEqual(prompt.to_dict(), dict(sources=[str(path.resolve())],
                                                   sha256=hashlib.sha256(b"Replacement\n").hexdigest()))


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.chunk = pack_chunks(make_record(["Text", dict(kind="paragraph", text="Other", heading_path=["Other"], source_locator={})]))[0]
        self.spans = self.chunk["evidence_spans"]
        self.valid = proposal(self.spans[0])

    def validate(self, value):
        return validate_proposal(value, self.spans, self.chunk["section_ids"])

    def test_valid_collapses_prose_and_deduplicates_evidence(self):
        self.valid["preferred_label"] = "  Label  with\nspace "
        self.valid["evidence"] *= 2
        draft = self.validate(self.valid)
        self.assertEqual(draft["preferred_label"], "Label with space")
        self.assertEqual(len(draft["evidence"]), 1)
        self.assertEqual(draft["evidence"][0]["quote"], "Text")

    def test_each_structural_rule(self):
        invalid = [
            {"extra": True}, {"preferred_label": "a" * 201}, {"description": "a" * 1201},
            {"scope": ""}, {"scope": "a" * 501}, {"scope": "page"}, {"scope": "section-0001"}, {"scope": "a > b"},
            {"alternative_labels": ["a"] * 11}, {"alternative_labels": ["a" * 201]},
            {"user_questions": []}, {"user_questions": ["q"] * 6}, {"user_questions": ["q" * 301]},
            {"confidence": True}, {"confidence": -0.1}, {"confidence": 1.1}, {"confidence": float("nan")},
            {"concept_type": "UNKNOWN"}, {"granularity": "UNKNOWN"}, {"evidence": []}, {"evidence": [{}] * 6},
            {"evidence": [{"evidence_id": "unknown"}]}, {"evidence": [{"quote": "Text"}]},
            {"primary_section_id": "unknown"}, {"evidence": [{"evidence_id": self.spans[1]["evidence_id"]}]},
            {"relations": [{}] * 11}, {"relations": [{}]},
            {"relations": [dict(relation_type="BAD", target_label="Target", confidence=0.5)]},
            {"relations": [dict(relation_type="RELATED", target_label="", confidence=0.5)]},
            {"relations": [dict(relation_type="RELATED", target_label="Target", confidence=True)]},
        ]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ValidationError):
                self.validate(dict(self.valid, **override))

    def test_chunk_contract_and_strict_json(self):
        for content in ("not json", "[]", '{"concepts":[],"extra":1}', '{"concepts":1}',
                        '{"concepts":[],"concepts":[]}', '{"concepts":[NaN]}', '{"concepts":[1e1000]}', json.dumps({"concepts": [{}] * 7})):
            with self.subTest(content=content), self.assertRaises(ValidationError):
                parse_proposals(content)
        with self.assertRaises(ValidationError):
            parse_proposals('{"concepts":[' + "9" * 5000 + ']}')

    def test_review_contract(self):
        verdict = dict(review_id=1, decision="supported", issue="none", reason="Belegt.")
        self.assertEqual(parse_verdicts(json.dumps({"verdicts": [verdict]}), 1), [verdict])
        changes = [dict(review_id=True), dict(review_id=2), dict(decision="bad"), dict(issue="bad"),
                   dict(decision="unsupported"), dict(issue="wrong_scope"), dict(reason=""), dict(reason="a" * 501), dict(extra=1)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                parse_verdicts(json.dumps({"verdicts": [dict(verdict, **change)]}), 1)
        with self.assertRaises(ValidationError):
            parse_verdicts(json.dumps({"verdicts": [verdict, verdict]}), 2)
        with self.assertRaises(ValidationError):
            parse_verdicts('{"verdicts":[]}', 1)
