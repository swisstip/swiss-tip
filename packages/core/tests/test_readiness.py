import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.readiness import (CaseCounts, Gate, Readiness, dump_readiness, readiness_path, readiness_status,
                                     SemanticIndexBinding, sha256_file)


def record(release: Path, **overrides) -> Readiness:
    fields = dict(pack="test", release_id="test-v1", release_sha256=sha256_file(release), content_sha256="c" * 64,
                  suite_sha256="s" * 64, attested_at=datetime(2026, 9, 15, 12, tzinfo=UTC), attested_by="A. Person",
                    gates=[Gate(gate=f"G{number}", title=f"gate {number}", status="passed")
                        for number in range(1, 7)], cases=CaseCounts(total=1, blocking=1),
                  review_statuses={"human-reviewed": 1}, snapshot_date=date(2026, 9, 14), stale_from=date(2026, 11, 13),
                  min_runway_days=14)
    return Readiness(**(fields | overrides))


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.release = Path(self.temporary.name) / "release.json"
        self.release.write_bytes(b'{"manifest": {}}')

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, item: Readiness) -> None:
        readiness_path(self.release).write_text(dump_readiness(item), encoding="utf-8")

    def test_no_record_is_a_candidate(self):
        status = readiness_status(self.release)
        self.assertEqual(status["status"], "candidate")
        self.assertIn("no readiness record", status["reason"])

    def test_a_matching_record_is_ready(self):
        self.write(record(self.release))
        status = readiness_status(self.release)
        self.assertEqual(status["status"], "ready")
        self.assertEqual((status["release_id"], status["attested_by"], status["gates"]),
                 ("test-v1", "A. Person", ["G1", "G2", "G3", "G4", "G5", "G6"]))

    def test_a_record_for_other_bytes_is_a_candidate(self):
        self.write(record(self.release))
        self.release.write_bytes(b'{"manifest": {"limitations": ["edited by hand"]}}')
        status = readiness_status(self.release)
        self.assertEqual(status["status"], "candidate")
        self.assertIn("another release file", status["reason"])

    def test_a_record_with_a_failed_gate_is_a_candidate(self):
        gates = [Gate(gate=f"G{number}", title=f"gate {number}", status="passed") for number in range(1, 7)]
        gates[2] = Gate(gate="G3", title="cases", status="failed", detail="UAT-1")
        self.write(record(self.release, gates=gates))
        status = readiness_status(self.release)
        self.assertEqual(status["status"], "candidate")
        self.assertIn("['G3']", status["reason"])

    def test_a_malformed_record_is_a_candidate(self):
        readiness_path(self.release).write_text("not json", encoding="utf-8")
        self.assertEqual(readiness_status(self.release)["status"], "candidate")
        readiness_path(self.release).write_text('{"schema_version": "swiss-tip-readiness/v1"}', encoding="utf-8")
        self.assertIn("does not load", readiness_status(self.release)["reason"])

    def test_the_attesting_person_is_named(self):
        with self.assertRaises(ValidationError):
            record(self.release, attested_by="")

    def test_all_six_unique_gates_are_required(self):
        with self.assertRaises(ValidationError):
            record(self.release, gates=[Gate(gate="G1", title="validates", status="passed")])
        with self.assertRaises(ValidationError):
            record(self.release, gates=[Gate(gate="G1", title="validates", status="passed") for _ in range(6)])

    def test_a_distributed_suite_change_invalidates_readiness(self):
        suite = self.release.with_name("acceptance.yaml")
        suite.write_text("pack: test\n", encoding="utf-8")
        self.write(record(self.release, suite_file_sha256=sha256_file(suite)))
        self.assertEqual(readiness_status(self.release)["status"], "ready")
        suite.write_text("pack: changed\n", encoding="utf-8")
        status = readiness_status(self.release)
        self.assertEqual(status["status"], "candidate")
        self.assertIn("acceptance.yaml changed", status["reason"])

    def test_semantic_serving_requires_the_exact_attested_index_and_settings(self):
        binding = SemanticIndexBinding(
            file_sha256="a" * 64, content_sha256="b" * 64, release_id="test-v1",
            release_content_sha256="c" * 64, model="model", model_digest="d" * 64,
            dimension=2, input_version="input/v1", query_version="query/v1",
            query_prefix_sha256="e" * 64, min_score=0.5, candidate_limit=10)
        self.write(record(self.release, semantic_index=binding))
        self.assertEqual(readiness_status(
            self.release, semantic_index=binding.model_dump(mode="json"))["status"], "ready")
        changed = binding.model_copy(update={"file_sha256": "f" * 64})
        status = readiness_status(self.release, semantic_index=changed.model_dump(mode="json"))
        self.assertEqual(status["status"], "candidate")
        self.assertIn("semantic index or retrieval settings changed", status["reason"])

    def test_legacy_v1_readiness_is_lexical_only(self):
        legacy = record(self.release, schema_version="swiss-tip-readiness/v1",
                        suite_file_sha256=None, semantic_index=None)
        self.write(legacy)
        self.assertEqual(readiness_status(self.release)["status"], "ready")
        status = readiness_status(self.release, semantic_index={"file_sha256": "a" * 64})
        self.assertEqual(status["status"], "candidate")
        self.assertIn("does not attest semantic retrieval", status["reason"])


if __name__ == "__main__":
    unittest.main()
