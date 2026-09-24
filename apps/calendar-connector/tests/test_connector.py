import asyncio
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path

import httpx

from swisstip.calendar_connector.server import ConnectorService, create_http_app, main
from swisstip.core.connector import ConnectorLookupResponse, ConnectorManifest

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLE = FIXTURES / "test-waste-bioabfall"


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def tampered(self) -> Path:
        folder = self.root / "tampered"
        shutil.copytree(BUNDLE, folder)
        bundle = json.loads((folder / "dataset.json").read_text(encoding="utf-8"))
        bundle["manifest"]["dataset_id"] = "test-waste-tampered"
        bundle["rows"][0]["date"] = "2026-01-06"
        (folder / "dataset.json").write_text(json.dumps(bundle), encoding="utf-8")
        return folder

    def call(self, app, method: str, path: str, body: dict | str | None = None):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://calendar.test") as client:
                if method == "GET":
                    return await client.get(path)
                if isinstance(body, str):
                    return await client.post(path, content=body, headers={"content-type": "application/json"})
                return await client.post(path, json=body)
        return asyncio.run(run())

    def test_the_manifest_and_health_list_every_bundle(self):
        service = ConnectorService([BUNDLE, self.tampered()])
        manifest = ConnectorManifest.model_validate(self.call(create_http_app(service), "GET", "/manifest").json())
        self.assertEqual([(d.dataset_id, d.status) for d in manifest.datasets], [("test-waste-bioabfall", "ok"), ("test-waste-tampered", "invalid")])
        self.assertEqual(manifest.datasets[1].issue, "manifest content_sha256 does not match the rows")
        self.assertEqual(manifest.datasets[0].concept_id, "test-organic-waste")
        health = service.health(today=date(2027, 1, 1))
        self.assertEqual([(d["status"], d["expired"]) for d in health["datasets"]], [("ok", True), ("invalid", True)])
        self.assertFalse(service.health(today=date(2026, 12, 31))["datasets"][0]["expired"])
        self.assertEqual(self.call(create_http_app(service), "GET", "/").json()["lookup"], "/lookup")

    def test_a_dataset_given_twice_stops_the_connector(self):
        with self.assertRaises(ValueError):
            ConnectorService([BUNDLE, BUNDLE])

    def test_lookups_and_their_gaps_over_http(self):
        app = create_http_app(ConnectorService([BUNDLE, self.tampered()]))
        supported = self.call(app, "POST", "/lookup", dict(dataset_id="test-waste-bioabfall", postal_code="8001", start="2026-09-23", limit=2))
        self.assertEqual(supported.status_code, 200)
        response = ConnectorLookupResponse.model_validate(supported.json())
        self.assertEqual(([e.date.isoformat() for e in response.events], response.truncated), (["2026-09-28", "2026-10-05"], True))
        self.assertEqual(response.provenance.licence, "CC0-1.0")

        gap = self.call(app, "POST", "/lookup", dict(dataset_id="test-waste-bioabfall", postal_code="8304", limit=1)).json()
        self.assertEqual((gap["status"], gap["gaps"][0]["dimension"]), ("OUT_OF_COVERAGE", "postal_code_not_covered"))

        for body, path in ((dict(dataset_id="test-waste-bioabfall", postal_code="801", limit=1), "postal_code"),
                           (dict(dataset_id="test-waste-bioabfall", postal_code="8001", limit=99), "limit"),
                           (dict(dataset_id="test-waste-bioabfall", postal_code="8001"), "limit"),
                           (dict(dataset_id="nowhere", postal_code="8001", limit=1), "dataset_id"),
                           (dict(dataset_id="test-waste-tampered", postal_code="8001", limit=1), "dataset_id"),
                           (dict(dataset_id="test-waste-bioabfall", postal_code="8001", limit=1, street="x"), "street")):
            rejected = self.call(app, "POST", "/lookup", body)
            self.assertEqual(rejected.status_code, 400, body)
            self.assertEqual((rejected.json()["code"], rejected.json()["path"]), ("INVALID_ARGUMENT", path), body)
        not_json = self.call(app, "POST", "/lookup", "[1, 2")
        self.assertEqual(not_json.status_code, 400)
        self.assertNotIn("path", not_json.json())

    def test_a_request_with_the_other_key_is_a_400(self):
        app = create_http_app(ConnectorService([BUNDLE]))
        response = self.call(app, "POST", "/lookup", dict(dataset_id="test-waste-bioabfall", zone="A", limit=1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual((response.json()["code"], response.json()["path"]), ("INVALID_ARGUMENT", "zone"))
        self.assertIn("keyed by postal_code", response.json()["message"])
        both = self.call(app, "POST", "/lookup", dict(dataset_id="test-waste-bioabfall", zone="A", postal_code="8001", limit=1))
        self.assertEqual(both.status_code, 400)

    def test_the_command_reports_health_and_refuses_nothing_to_serve(self):
        out = StringIO()
        with redirect_stdout(out):
            code = main(["--dataset", str(BUNDLE), "--health"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual((payload["status"], payload["datasets"][0]["dataset_id"]), ("ok", "test-waste-bioabfall"))
        tampered = self.tampered()
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--dataset", str(tampered), "--health"]), 2)
        self.assertEqual(json.loads(out.getvalue())["datasets"][0]["status"], "invalid")
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--health"]), 2)
        self.assertIn("no dataset to serve", out.getvalue())
        # A parent directory: every subdirectory with a bundle, and nothing else.
        parent = self.root / "datasets"
        shutil.copytree(BUNDLE, parent / "test-waste-bioabfall")
        shutil.copytree(tampered, parent / "tampered")
        (parent / "notes").mkdir()
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--datasets-dir", str(parent), "--health"]), 0)
        # Subdirectories in name order: "tampered" sorts before "test-waste-bioabfall".
        self.assertEqual([d["dataset_id"] for d in json.loads(out.getvalue())["datasets"]], ["test-waste-tampered", "test-waste-bioabfall"])


if __name__ == "__main__":
    unittest.main()
