import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.readiness import (CaseCounts, Gate, Readiness, dump_readiness, readiness_path, readiness_status,
                                     sha256_file)


def record(release: Path, **overrides) -> Readiness:
    fields = dict(pack="test", release_id="test-v1", release_sha256=sha256_file(release), content_sha256="c" * 64,
                  suite_sha256="s" * 64, attested_at=datetime(2026, 9, 15, 12, tzinfo=UTC), attested_by="A. Person",
                  gates=[Gate(gate="G1", title="validates", status="passed")], cases=CaseCounts(total=1, blocking=1),
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
        self.assertEqual((status["release_id"], status["attested_by"], status["gates"]), ("test-v1", "A. Person", ["G1"]))

    def test_a_record_for_other_bytes_is_a_candidate(self):
        self.write(record(self.release))
        self.release.write_bytes(b'{"manifest": {"limitations": ["edited by hand"]}}')
        status = readiness_status(self.release)
        self.assertEqual(status["status"], "candidate")
        self.assertIn("another release file", status["reason"])

    def test_a_record_with_a_failed_gate_is_a_candidate(self):
        self.write(record(self.release, gates=[Gate(gate="G3", title="cases", status="failed", detail="UAT-1")]))
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


if __name__ == "__main__":
    unittest.main()
