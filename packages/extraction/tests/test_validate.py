import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from support import IN_SCOPE_URL, make_run, sha256
from swisstip.extraction.dataset import read_json
from swisstip.extraction.extract_cli import run_extraction
from swisstip.extraction.validate import main, validate_dataset


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.temporary.name))
        self.text = self.run / "text"
        run_extraction(self.run, log=lambda *a, **k: None)

    def tearDown(self):
        self.temporary.cleanup()

    def checks(self, report) -> dict:
        return {check["name"]: check for check in report["checks"]}

    def test_fresh_dataset_passes_and_writes_validation_json(self):
        report = validate_dataset(self.text)
        self.assertTrue(report["passed"])
        self.assertEqual(report["records"], 5)
        self.assertEqual(report["run_path"], str(self.run.resolve()))
        self.assertTrue((self.text / "validation.json").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--text", str(self.text)]), 0)

    def test_tampered_record_is_reported(self):
        path = next((self.text / "documents").glob("doc-*.json"))
        record = read_json(path)
        record["blocks"][0]["text"] = record["blocks"][0]["text"] + "x"
        path.write_text(json.dumps(record), encoding="utf-8")
        report = validate_dataset(self.text)
        self.assertFalse(report["passed"])
        self.assertIn("offsets of", self.checks(report)["content hash, block offsets and block hashes"]["detail"][0]["problems"][0])

    def test_tampered_snapshot_is_reported(self):
        target = self.run / "pages" / sha256(IN_SCOPE_URL.encode()) / "attempt-001" / "response.html"
        target.write_bytes(b"<html>changed</html>")
        report = validate_dataset(self.text)
        self.assertFalse(report["passed"])
        detail = self.checks(report)["saved responses present with the recorded hash"]["detail"]
        self.assertEqual(detail[0]["problem"], "saved response hash differs")

    def test_a_self_consistent_record_without_a_plan_manifest_is_refused(self):
        path = next((self.text / "documents").glob("doc-*.json"))
        record = read_json(path)
        injected = self.run / "pages" / "injected" / "attempt-001" / "response.html"
        injected.parent.mkdir(parents=True)
        raw = b"<html><p>Injected but self-hashed</p></html>"
        injected.write_bytes(raw)
        record["acquisition"]["path"] = injected.relative_to(self.run).as_posix()
        record["acquisition"]["raw_sha256"] = sha256(raw)
        record["acquisition"]["manifest_path"] = "pages/injected/latest.json"
        record["acquisition"]["manifest_sha256"] = "0" * 64
        path.write_text(json.dumps(record), encoding="utf-8")

        report = validate_dataset(self.text)

        self.assertFalse(report["passed"])
        detail = self.checks(report)["saved responses present with the recorded hash"]["detail"]
        self.assertIn("approved plan-derived manifest", detail[0]["problem"])

    def test_index_mismatch_and_missing_view_are_reported(self):
        view = next((self.text / "reading").glob("*.md"))
        view.unlink()
        report = validate_dataset(self.text)
        self.assertFalse(report["passed"])
        self.assertFalse(self.checks(report)["eligible records have a reading view"]["passed"])
        (self.text / "documents" / (view.stem + ".json")).unlink()
        report = validate_dataset(self.text)
        self.assertEqual(self.checks(report)["index matches the records on disk"]["detail"]["in_index_not_on_disk"], [view.stem])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--text", str(self.text)]), 1)


if __name__ == "__main__":
    unittest.main()
