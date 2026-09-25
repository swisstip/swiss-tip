import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from swisstip.core.readiness import Gate, Readiness, dump_readiness, readiness_path, sha256_file
from swisstip.core.release import load_release
from swisstip.mcp_server.server import client_config, create_http_app, health, main, remote_client_config
from swisstip.runtime.semantic import build_index, save_index, semantic_index_binding
from swisstip.runtime.service import ReleaseService

ROOT = Path(__file__).resolve().parents[3]
# A synthetic release (five concepts on example.gov pages, written from the sample release of
# packages/runtime/tests/test_service.py), so that the server's tests depend on no knowledge base. The server has no
# default release; that the published packs are ready is checked next to the packs.
FIXTURE = Path(__file__).parent / "fixtures" / "release.json"
RESOLVE = {"concept_ids": ["deadline"], "jurisdiction": {"canton": "Zurich"}, "as_of": "2026-09-12",
           "context": {"population": "eu_efta"}}


def attest(release_path: Path) -> None:
    """Write a readiness record with every gate passed next to a release file."""
    manifest = load_release(release_path).manifest
    record = Readiness(pack=manifest.pack, release_id=manifest.release_id, release_sha256=sha256_file(release_path),
                       content_sha256=manifest.content_sha256, suite_sha256="0" * 64, attested_at="2026-09-12T12:00:00Z",
                      attested_by="Test",
                      gates=[Gate(gate=f"G{number}", title=f"test {number}", status="passed")
                          for number in range(1, 7)],
                       cases=dict(total=0, blocking=0), review_statuses=manifest.review_statuses,
                       snapshot_date=manifest.freshness.snapshot_date, stale_from=manifest.freshness.stale_from,
                       min_runway_days=0)
    readiness_path(release_path).write_text(dump_readiness(record), encoding="utf-8")


class ServerTests(unittest.TestCase):
    def test_health_and_client_config(self):
        service = ReleaseService.from_file(FIXTURE)
        report = health(service)
        self.assertEqual(report["status"], "ok")
        self.assertEqual((report["concepts"], report["facts"]), (5, 7))
        self.assertIn("CH-ZH-261", report["jurisdictions"])
        config = client_config(FIXTURE, "opencode")
        self.assertEqual(config["mcp"]["swiss_tip"]["command"][1:3], ["-m", "swisstip.mcp_server.server"])
        self.assertIn("mcpServers", client_config(FIXTURE, "generic"))
        self.assertEqual(report["search"]["configured_mode"], "lexical")

    def test_optional_semantic_configuration_is_preserved_for_clients(self):
        index = ROOT / ".local" / "search index.json"
        config = client_config(FIXTURE, "opencode", semantic_index=index,
                               semantic_timeout=3, semantic_min_score=0.6, semantic_candidates=8)
        command = config["mcp"]["swiss_tip"]["command"]
        self.assertEqual(command[command.index("--semantic-index") + 1], str(index.resolve()))
        self.assertEqual(command[command.index("--semantic-timeout") + 1], "3")
        self.assertEqual(command[command.index("--semantic-min-score") + 1], "0.6")
        self.assertNotIn("--semantic-index", client_config(FIXTURE, "generic")["mcpServers"]["swiss-tip"]["args"])

    def test_require_ready_refuses_a_release_without_a_matching_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "release.json"
            shutil.copyfile(FIXTURE, copy)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--release", str(copy), "--health", "--require-ready"]), 2)
            self.assertIn("not ready", json.loads(output.getvalue())["error"])
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--release", str(copy), "--health"]), 0)
            self.assertEqual(json.loads(output.getvalue())["readiness"]["status"], "candidate")
            attest(copy)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--release", str(copy), "--health", "--require-ready"]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual((report["readiness"]["status"], report["readiness"]["attested_by"]), ("ready", "Test"))
            self.assertEqual(report["readiness"]["release_id"], report["release_id"])
            copy.write_bytes(copy.read_bytes() + b"\n")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--release", str(copy), "--health", "--require-ready"]), 2)
            self.assertIn("another release file", json.loads(output.getvalue())["error"])

    def test_require_ready_binds_the_exact_semantic_index_and_settings(self):
        class Embedder:
            model = "test-model"

            def model_digest(self):
                return "d" * 64

            def embed(self, texts):
                if len(texts) == 1:
                    return [[1.0, 1.0]]
                return [[1.0, float(number + 1)] for number, _ in enumerate(texts)]

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            release_path = folder / "release.json"
            shutil.copyfile(FIXTURE, release_path)
            release = load_release(release_path)
            index_path = folder / "semantic-index.json"
            index = build_index(release, Embedder())
            save_index(index, index_path)
            manifest = release.manifest
            binding = semantic_index_binding(index_path, index)
            record = Readiness(
                pack=manifest.pack, release_id=manifest.release_id, release_sha256=sha256_file(release_path),
                content_sha256=manifest.content_sha256, suite_sha256="0" * 64,
                attested_at="2026-09-12T12:00:00Z", attested_by="Test",
                gates=[Gate(gate=f"G{number}", title=f"test {number}", status="passed")
                       for number in range(1, 7)], cases=dict(total=0, blocking=0),
                review_statuses=manifest.review_statuses, snapshot_date=manifest.freshness.snapshot_date,
                stale_from=manifest.freshness.stale_from, min_runway_days=0, semantic_index=binding)
            readiness_path(release_path).write_text(dump_readiness(record), encoding="utf-8")
            runtime_embedder = Embedder()
            output = StringIO()
            with patch("swisstip.mcp_server.server.OllamaEmbedder", return_value=runtime_embedder), \
                    redirect_stdout(output):
                self.assertEqual(main(["--release", str(release_path), "--semantic-index", str(index_path),
                                       "--health", "--require-ready"]), 0)
            output = StringIO()
            with patch("swisstip.mcp_server.server.OllamaEmbedder", return_value=runtime_embedder), \
                    redirect_stdout(output):
                self.assertEqual(main(["--release", str(release_path), "--semantic-index", str(index_path),
                                       "--semantic-min-score", "0.6", "--health", "--require-ready"]), 2)
            self.assertIn("semantic index or retrieval settings changed", json.loads(output.getvalue())["error"])

            runtime_embedder.digest = "e" * 64
            runtime_embedder.model_digest = lambda: runtime_embedder.digest
            output = StringIO()
            with patch("swisstip.mcp_server.server.OllamaEmbedder", return_value=runtime_embedder), \
                    redirect_stdout(output):
                self.assertEqual(main(["--release", str(release_path), "--semantic-index", str(index_path),
                                       "--health", "--require-ready"]), 2)
            self.assertIn("model digest differs", json.loads(output.getvalue())["error"])

            runtime_embedder.digest = "d" * 64
            runtime_embedder.model_digest = lambda: runtime_embedder.digest
            runtime_embedder.embed = lambda texts: [[1.0, 1.0, 1.0]]
            output = StringIO()
            with patch("swisstip.mcp_server.server.OllamaEmbedder", return_value=runtime_embedder), \
                    redirect_stdout(output):
                self.assertEqual(main(["--release", str(release_path), "--semantic-index", str(index_path),
                                       "--health", "--require-ready"]), 2)
            self.assertIn("dimensions", json.loads(output.getvalue())["error"])

    def test_release_comes_from_the_option_or_the_variable(self):
        with patch.dict(os.environ, {"SWISSTIP_RELEASE": ""}):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--health"]), 2)
            self.assertIn("SWISSTIP_RELEASE", json.loads(output.getvalue())["error"])
        with patch.dict(os.environ, {"SWISSTIP_RELEASE": str(FIXTURE)}):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--health"]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "ok")
            self.assertEqual(main(["--print-client-config", "opencode", "--url", "https://host/mcp"]), 0)

    def test_missing_optional_index_reports_fallback_without_network(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--release", str(FIXTURE), "--semantic-index",
                         str(ROOT / ".local" / "missing-test-semantic-index.json"), "--health"])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["search"]["configured_mode"], "lexical-fallback")
        self.assertTrue(report["search"]["fallback_reason"])

    def test_stdio_round_trip(self):
        async def run():
            params = StdioServerParameters(command=sys.executable,
                                           args=["-m", "swisstip.mcp_server.server", "--release", str(FIXTURE)],
                                           env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    listed = (await session.list_tools()).tools
                    coverage = await session.call_tool("get_coverage", {})
                    resolved = await session.call_tool("resolve", RESOLVE)
                    error = await session.call_tool("get_evidence", {"evidence_ids": []})
                    return init, listed, coverage, resolved, error

        init, listed, coverage, resolved, error = asyncio.run(run())
        self.assertEqual(init.serverInfo.name, "swiss-tip")
        self.assertEqual([t.name for t in listed], ["search", "resolve", "get_evidence"])
        self.assertTrue(coverage.isError)
        self.assertIn("Unknown tool", coverage.content[0].text)
        # The release's query languages reach the caller before its first search: in the instructions and in the
        # search description and query field.
        note = "Write the search query in English"
        self.assertIn(note, init.instructions)
        search = next(t for t in listed if t.name == "search")
        self.assertIn(note, search.description)
        self.assertIn(note, search.inputSchema["properties"]["query"]["description"])
        self.assertEqual(resolved.structuredContent["status"], "SUPPORTED")
        self.assertEqual(json.loads(resolved.content[0].text), resolved.structuredContent)
        self.assertEqual(resolved.structuredContent["executed_scope"]["canton_code"], "CH-ZH")
        self.assertEqual(resolved.structuredContent["results"][0]["citations"][0]["url"], "https://example.gov/doc-a")
        self.assertTrue(error.isError)
        self.assertEqual(error.structuredContent["error"]["code"], "INVALID_ARGUMENT")

    def test_remote_client_configuration(self):
        url = "https://swiss-tip.example/mcp"
        opencode = remote_client_config(url, "opencode")["mcp"]["swiss_tip"]
        self.assertEqual((opencode["type"], opencode["url"]), ("remote", url))
        generic = remote_client_config(url, "generic")["mcpServers"]["swiss-tip"]
        self.assertEqual(generic, {"type": "http", "url": url})
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--print-client-config", "opencode", "--url", url]), 0)
        self.assertEqual(json.loads(output.getvalue()), remote_client_config(url, "opencode"))

    def test_streamable_http_round_trip_in_process(self):
        service = ReleaseService.from_file(FIXTURE)

        async def run():
            app = create_http_app(service, host="0.0.0.0")
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://swiss-tip.test") as client:
                    index = (await client.get("/")).json()
                    probe = await client.get("/health")
                    async with streamable_http_client("http://swiss-tip.test/mcp", http_client=client) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            init = await session.initialize()
                            tools = [t.name for t in (await session.list_tools()).tools]
                            resolved = await session.call_tool("resolve", RESOLVE)
                    return index, probe, init.serverInfo.name, tools, resolved

        index, probe, name, tools, resolved = asyncio.run(run())
        self.assertEqual(index["mcp_endpoint"], "/mcp")
        self.assertEqual(probe.status_code, 200)
        self.assertEqual(probe.json(), health(service))
        self.assertEqual(name, "swiss-tip")
        self.assertEqual(tools, ["search", "resolve", "get_evidence"])
        self.assertEqual(resolved.structuredContent["status"], "SUPPORTED")
        self.assertEqual(json.loads(resolved.content[0].text), resolved.structuredContent)

    def test_loopback_binding_rejects_a_foreign_host_header(self):
        service = ReleaseService.from_file(FIXTURE)

        async def run():
            app = create_http_app(service, host="127.0.0.1")
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://attacker.test") as client:
                    return (await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                              headers={"Accept": "application/json, text/event-stream"})).status_code

        self.assertEqual(asyncio.run(run()), 421)


if __name__ == "__main__":
    unittest.main()
