import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import date, datetime
from io import StringIO
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from swisstip.core.datasets import Dataset, DatasetManifest, DatasetRow, DatasetSource, Period, content_hash, dump_dataset
from swisstip.mcp_server.server import main

FIXTURE = Path(__file__).parent / "fixtures" / "release.json"


def bundle(folder: Path) -> None:
    """A dataset bound to the fixture release: pack test, concept city-arrival, City of Zurich."""
    rows = [DatasetRow(postal_code="8001", date=date(2026, 9, 28)), DatasetRow(postal_code="8001", date=date(2026, 10, 5)),
            DatasetRow(postal_code="8002", date=date(2026, 9, 29))]
    manifest = DatasetManifest(
        dataset_id="test-waste", dataset_version="2026-09-23-v1", type="calendar", title="Organic waste collection days",
        label="Organic waste (Bioabfall)", pack="test", concept_id="city-arrival", jurisdiction="CH-ZH-261",
        publisher="ERZ", publisher_url="https://data.example.ch/dataset/bio", licence="CC0-1.0",
        sources=[DatasetSource(url="https://data.example.ch/bio_2026.csv", sha256="a" * 64, bytes=10, downloaded_on=date(2026, 9, 23))],
        period=Period(start=date(2026, 1, 1), end=date(2026, 12, 31)), postal_codes=["8001", "8002"], row_count=3,
        created_at=datetime(2026, 9, 23, 10, 0), limitations=["Rows are not reviewed."], content_sha256="0" * 64)
    dataset = Dataset(manifest=manifest, rows=rows)
    dataset.manifest.content_sha256 = content_hash(dataset)
    folder.mkdir(parents=True)
    (folder / "dataset.json").write_text(dump_dataset(dataset), encoding="utf-8")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ConnectorRoundTripTests(unittest.TestCase):
    """The server with a running calendar connector: the fifth tool, the offer on resolve, the forwarded lookup."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name) / "test-waste"
        bundle(folder)
        cls.port = free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.process = subprocess.Popen([sys.executable, "-m", "swisstip.calendar_connector.server", "--dataset", str(folder),
                                        "--port", str(cls.port), "--log-level", "WARNING"])
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.url + "/health", timeout=1).read()
                break
            except (urllib.error.URLError, OSError):
                if cls.process.poll() is not None:
                    raise RuntimeError("the connector exited before answering")
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.temporary.cleanup()

    def test_stdio_round_trip_with_a_connector(self):
        async def run():
            params = StdioServerParameters(command=sys.executable,
                                           args=["-m", "swisstip.mcp_server.server", "--release", str(FIXTURE), "--connector", self.url],
                                           env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = (await session.list_tools()).tools
                    resolved = await session.call_tool("resolve", {"concept_ids": ["city-arrival"], "jurisdiction": {"city": "CH-ZH-261"},
                                                                   "as_of": "2026-09-23"})
                    looked_up = await session.call_tool("lookup", {"dataset_id": "test-waste", "postal_code": "8001",
                                                                   "as_of": "2026-09-23", "limit": 1})
                    outside = await session.call_tool("lookup", {"dataset_id": "test-waste", "postal_code": "8304", "as_of": "2026-09-23"})
                    unknown = await session.call_tool("lookup", {"dataset_id": "nowhere", "postal_code": "8001"})
                    return listed, resolved, looked_up, outside, unknown

        listed, resolved, looked_up, outside, unknown = asyncio.run(run())
        self.assertEqual([t.name for t in listed], ["search", "resolve", "get_evidence", "lookup"])
        lookup = next(t for t in listed if t.name == "lookup")
        self.assertTrue(lookup.annotations.readOnlyHint)
        self.assertEqual(lookup.inputSchema["properties"]["limit"]["default"], 3)
        [offer] = resolved.structuredContent["results"][0]["lookups"]
        self.assertEqual((offer["dataset_id"], offer["requires"]), ("test-waste", ["postal_code"]))
        self.assertIn("call lookup", resolved.structuredContent["guidance_for_caller"])
        self.assertEqual(looked_up.structuredContent["status"], "SUPPORTED")
        self.assertEqual([e["date"] for e in looked_up.structuredContent["events"]], ["2026-09-28"])
        self.assertEqual(looked_up.structuredContent["provenance"]["licence"], "CC0-1.0")
        self.assertEqual(json.loads(looked_up.content[0].text), looked_up.structuredContent)
        self.assertEqual(outside.structuredContent["gaps"][0]["dimension"], "postal_code_not_covered")
        self.assertTrue(unknown.isError)
        self.assertEqual(unknown.structuredContent["error"]["code"], "INVALID_ARGUMENT")

    def test_health_names_the_connector_and_survives_an_unreachable_one(self):
        out = StringIO()
        with redirect_stdout(out):
            code = main(["--release", str(FIXTURE), "--health", "--connector", self.url, "--connector", "http://127.0.0.1:1"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        reachable, unreachable = payload["connectors"]
        self.assertEqual((reachable["status"], reachable["connector_id"], reachable["registered"]), ("ok", "calendar-connector", ["test-waste"]))
        self.assertEqual((unreachable["status"], unreachable["registered"]), ("unreachable", []))
        self.assertIn("127.0.0.1:1", unreachable["reason"])


if __name__ == "__main__":
    unittest.main()
