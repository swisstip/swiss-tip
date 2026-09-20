import copy
import tempfile
import unittest
from pathlib import Path

from swisstip.concepts.checkpoints import read_json
from swisstip.concepts.extract import extract_record
from swisstip.concepts.results import consolidate, prune, rebuild_dataset, reusable, select_entries, write_report
from test_core import make_record
from test_flow import FakeProvider


class ResultsTests(unittest.TestCase):
    def test_selection_filters_and_language_fallback(self):
        template = dict(eligible_for_processing=True, preferred_representation=True, superseded=False,
                        source_url="https://example.invalid", attribution_kind="catalogue", source_ids=["canton"], language_hint="de-CH")
        entries = [dict(template, document_id="good")]
        for identity, override in (("ineligible", dict(eligible_for_processing=False)),
                                   ("not_preferred", dict(preferred_representation=False)),
                                   ("old", dict(superseded=True)), ("outside", dict(attribution_kind="out-of-scope")),
                                   ("french", dict(language_declared="fr"))):
            entries.append(dict(template, document_id=identity, **override))
        self.assertEqual([e["document_id"] for e in select_entries(entries, languages=["de"])], ["good"])
        self.assertEqual({e["document_id"] for e in select_entries(entries, scope="all")}, {"good", "outside", "french"})
        self.assertEqual(select_entries(entries, sources=["other"]), [])
        self.assertEqual(select_entries(entries, kinds=["plugin"]), [])
        self.assertEqual(len(select_entries(entries, max_pages=1)), 1)

    def test_reuse_requires_every_identity_field_and_no_failures(self):
        record = make_record(["Regel."])
        report = extract_record(record, FakeProvider(), profile="fake", model="test")
        args = (record, report["prompts"], "fake", "test", report["settings"])
        self.assertTrue(reusable(report, *args))
        for field, value in (("schema_version", "other"), ("content_sha256", "changed"), ("profile", "other"),
                             ("model", "other"), ("settings", {}), ("failures", [{}]),
                             ("prompts", {"extraction": {"sha256": "different"}}),
                             ("prompts", dict(report["prompts"], basis={"sha256": "different"})),
                             ("chunks", [{"status": "awaiting_response"}])):
            with self.subTest(field=field):
                self.assertFalse(reusable(dict(report, **{field: value}), *args))
        # A run without the basis call ignores the basis prompt, so reports of the two-call pipeline stay reusable.
        without = dict(report["settings"], classify_basis=False)
        self.assertTrue(reusable(dict(report, settings=without, prompts=dict(report["prompts"], basis={"sha256": "x"})),
                                 record, report["prompts"], "fake", "test", without))

    def test_indexes_aggregate_disk_and_changed_record_history_pruning(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            report = extract_record(make_record(["Regel."]), FakeProvider())
            write_report(output, report)
            changed = dict(report, content_sha256="b" * 64)
            write_report(output, changed)
            self.assertEqual(len(list((output / "history").glob("*/*.json"))), 1)
            second = dict(report, document_id="second", output_tokens=None)
            write_report(output, second)
            summary = rebuild_dataset(output)
            self.assertEqual(summary["reports"], 2)
            self.assertEqual(summary["candidates"], 2)
            self.assertIsNone(summary["output_tokens"])
            self.assertEqual(len(read_json(output / "index.json")), 2)
            self.assertIn("doc-example.json", (output / "index.md").read_text(encoding="utf-8"))
            removed = prune(output, {"second"})
            self.assertEqual(len(removed), 2)
            self.assertEqual(rebuild_dataset(output)["reports"], 1)

    def test_consolidation_keeps_language_and_conflicts_and_alias_pairs(self):
        report = extract_record(make_record(["Regel."]), FakeProvider())
        identical = dict(report, document_id="second")
        conflict = copy.deepcopy(report)
        conflict["document_id"] = "conflict"
        conflict["candidates"][0].update(description="Eine andere Aussage.")
        translation = dict(report, document_id="french", language="fr")
        summary = consolidate([report, identical, conflict, translation])
        self.assertEqual(len(summary["consolidated_concepts"]), 3)
        self.assertEqual(len(summary["consolidated_concepts"][0]["members"]), 2)
        self.assertEqual(len(summary["duplicate_review_pairs"]), 1)
        self.assertEqual(summary["duplicate_review_pairs"][0]["different_fields"], ["description"])

    def test_duplicate_pairs_capped_without_merging(self):
        report = extract_record(make_record(["Regel."]), FakeProvider())
        candidates = []
        for index in range(22):
            candidates.append(dict(report["candidates"][0], preferred_label=f"Begriff {index}", alternative_labels=["Gemeinsamer Alias"]))
        report["candidates"] = candidates
        summary = consolidate([report])
        self.assertEqual(len(summary["consolidated_concepts"]), 22)
        self.assertEqual(len(summary["duplicate_review_pairs"]), 200)
        self.assertEqual(summary["duplicate_review_pair_count"], 231)
        self.assertTrue(summary["duplicate_review_pairs_truncated"])
