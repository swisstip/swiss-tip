from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from swisstip.core.release import content_hash
from swisstip.runtime.semantic import (OllamaEmbedder, QUERY_PREFIX, SemanticError, SemanticSearch, _NoRedirects,
                                      _index_hash, build_index, concept_texts, load_index, save_index)
from test_service import sample_release


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode("utf-8"))


class FakeEmbedder:
    def __init__(self):
        self.model = "qwen3-embedding:0.6b"
        self.digest = "d" * 64
        self.digest_calls = 0
        self.inputs = []
        self.query_vector = [1, 0]

    def model_digest(self):
        self.digest_calls += 1
        return self.digest

    def embed(self, texts):
        self.inputs.append(texts)
        if len(texts) == 1:
            return [self.query_vector]
        return [[2, 0], [3, 0], [0, 4], [-5, 0], [0, -6]]


class OllamaEmbedderTests(unittest.TestCase):
    def test_requests_are_bounded_use_full_inputs_and_normalize_vectors(self):
        opener = FakeOpener([{"model": "qwen3-embedding:0.6b", "embeddings": [[3, 4], [0, 2]]},
                             {"models": [{"name": "qwen3-embedding:0.6b", "digest": "sha256:" + "d" * 64}]}])
        embedder = OllamaEmbedder(opener=opener, timeout_seconds=2)
        self.assertEqual(embedder.embed(["question", "German source"]), [[0.6, 0.8], [0.0, 1.0]])
        self.assertEqual(embedder.model_digest(), "d" * 64)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/embed")
        self.assertEqual(timeout, 2)
        self.assertEqual(json.loads(request.data), {"model": "qwen3-embedding:0.6b",
                                                   "input": ["question", "German source"], "truncate": False})
        self.assertEqual(opener.calls[1][0].full_url, "http://127.0.0.1:11434/api/tags")
        self.assertIsNone(opener.calls[1][0].data)

    def test_only_loopback_origins_are_accepted_and_localhost_is_pinned(self):
        for url in ("https://example.com", "http://192.168.1.2:11434", "http://localhost.example", "file:///tmp/model",
                    "http://user:secret@127.0.0.1", "http://127.0.0.1/api", "http://127.0.0.1?redirect=x",
                    "http://127.0.0.1/#x", "http://127.0.0.1:bad"):
            with self.subTest(url=url), self.assertRaises(SemanticError):
                OllamaEmbedder(base_url=url, opener=FakeOpener([]))
        self.assertEqual(OllamaEmbedder(base_url="http://localhost:11434/").base_url, "http://127.0.0.1:11434")
        self.assertEqual(OllamaEmbedder(base_url="http://[::1]:11434").base_url, "http://[::1]:11434")
        with patch("urllib.request.getproxies", side_effect=AssertionError("proxy discovery is disabled")):
            embedder = OllamaEmbedder()
        self.assertTrue(any(isinstance(handler, _NoRedirects) for handler in embedder.opener.handlers))
        with self.assertRaisesRegex(SemanticError, "redirects"):
            _NoRedirects().redirect_request(None, None, 302, "moved", {}, "https://example.com")

    def test_invalid_and_oversized_responses_fail_without_partial_vectors(self):
        for response in ({"embeddings": []}, {"embeddings": [[0, 0]]}, {"embeddings": [[float("nan"), 1]]},
                         {"embeddings": [[float("inf"), 1]]}, {"embeddings": [[True, 1]]},
                         {"embeddings": [["1", 2]]}, {"embeddings": [[1, 2], [1, 2]]},
                         {"error": "model unavailable"}, [], b"not json", TimeoutError()):
            if isinstance(response, dict):
                response = {"model": "qwen3-embedding:0.6b", **response}
            with self.subTest(response=response), self.assertRaises(SemanticError):
                OllamaEmbedder(opener=FakeOpener([response])).embed(["question"])
        with self.assertRaisesRegex(SemanticError, "dimensions"):
            OllamaEmbedder(opener=FakeOpener([{"model": "qwen3-embedding:0.6b", "embeddings": [[1, 0], [1, 0, 0]]}])).embed(["a", "b"])
        with patch("swisstip.runtime.semantic.MAX_RESPONSE_BYTES", 10), self.assertRaisesRegex(SemanticError, "byte limit"):
            OllamaEmbedder(opener=FakeOpener([b"x" * 11])).embed(["question"])

    def test_embedding_response_must_name_the_requested_model(self):
        for response in ({"embeddings": [[1, 0]]}, {"model": "other-model", "embeddings": [[1, 0]]}):
            with self.subTest(response=response), self.assertRaisesRegex(SemanticError, "requested model"):
                OllamaEmbedder(opener=FakeOpener([response])).embed(["question"])

    def test_model_digest_requires_exact_local_tag_and_valid_digest(self):
        for response in ({"models": []}, {"models": [{"name": "different", "digest": "d" * 64}]},
                         {"models": [{"name": "qwen3-embedding:0.6b", "digest": "bad"}]}, {"models": None}):
            with self.subTest(response=response), self.assertRaises(SemanticError):
                OllamaEmbedder(opener=FakeOpener([response])).model_digest()

    def test_unload_confirms_model_disappearance_and_has_a_bounded_wait(self):
        opener = FakeOpener([{"embeddings": []}, {"models": [{"name": "qwen3-embedding:0.6b"}]}, {"models": []}])
        with patch("swisstip.runtime.semantic.time.sleep"):
            OllamaEmbedder(opener=opener).unload()
        self.assertEqual(json.loads(opener.calls[0][0].data),
                         {"model": "qwen3-embedding:0.6b", "input": [], "keep_alive": 0})
        self.assertEqual([request.full_url.rsplit("/", 1)[-1] for request, _ in opener.calls], ["embed", "ps", "ps"])
        self.assertLessEqual(opener.calls[1][1], 5)
        with patch("swisstip.runtime.semantic.time.monotonic", side_effect=[0, 6]), self.assertRaisesRegex(SemanticError, "unload"):
            OllamaEmbedder(opener=FakeOpener([{"embeddings": []}])).unload()


class SemanticIndexTests(unittest.TestCase):
    def setUp(self):
        self.release = sample_release()
        self.embedder = FakeEmbedder()
        self.index = build_index(self.release, self.embedder)

    def test_index_round_trip_binds_full_published_text_and_model(self):
        self.assertEqual(self.index.release_id, self.release.manifest.release_id)
        self.assertEqual(self.index.release_content_sha256, self.release.manifest.content_sha256)
        self.assertEqual(self.index.model_digest, "d" * 64)
        self.assertEqual(self.index.dimension, 2)
        self.assertEqual(self.index.concepts[0].vector, (1.0, 0.0))
        self.assertEqual(self.embedder.inputs[0], [text for _, text in concept_texts(self.release)])
        self.assertNotIn(QUERY_PREFIX, self.embedder.inputs[0][0])
        for text in (self.release.concepts[0].label, self.release.concepts[0].description,
                     self.release.concepts[0].aliases[0], self.release.concepts[0].questions[0],
                     self.release.facts[0].statement):
            self.assertIn(text, self.index.concepts[0].text)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.json"
            save_index(self.index, path)
            self.assertEqual(load_index(path, self.release), self.index)

    def test_release_changes_and_rehashed_input_substitution_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.json"
            save_index(self.index, path)
            self.release.manifest.release_id = "other-v1"
            with self.assertRaisesRegex(SemanticError, "different release"):
                load_index(path, self.release)
            self.release.manifest.release_id = self.index.release_id
            self.release.facts[0].statement += " Added fact content."
            self.release.manifest.content_sha256 = content_hash(self.release)
            with self.assertRaisesRegex(SemanticError, "different release"):
                load_index(path, self.release)
            self.release = sample_release()
            entry = replace(self.index.concepts[0], text="Substituted input",
                            text_sha256=hashlib.sha256(b"Substituted input").hexdigest())
            forged = replace(self.index, concepts=(entry, *self.index.concepts[1:]))
            forged = replace(forged, content_sha256=_index_hash(forged))
            save_index(forged, path)
            with self.assertRaisesRegex(SemanticError, "inputs do not match"):
                load_index(path, self.release)

    def test_index_tampering_and_incompatible_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.json"
            save_index(self.index, path)
            original = path.read_text(encoding="utf-8")
            for field, value in (("dimension", 3), ("query_prefix", "Different task: "), ("schema_version", "new/v2"),
                                 ("content_sha256", "0" * 64)):
                data = json.loads(original)
                data[field] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(SemanticError):
                    load_index(path, self.release)
            data = json.loads(original)
            data["concepts"][0]["vector"] = [0.0, 1.0]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(SemanticError, "content hash"):
                load_index(path, self.release)
            path.write_text(original, encoding="utf-8")
            with patch("swisstip.runtime.semantic.MAX_INDEX_BYTES", 10), self.assertRaisesRegex(SemanticError, "byte limit"):
                load_index(path, self.release)

    def test_ranking_checks_digest_per_query_applies_query_instruction_and_limits(self):
        search = SemanticSearch(self.index, self.embedder, min_score=0.5, candidate_limit=1)
        self.assertEqual(self.embedder.digest_calls, 2)  # Before/after build; constructing search calls no service.
        self.assertEqual(search.rank("register"), [(1.0, "deadline")])
        self.assertEqual(self.embedder.inputs[-1], [QUERY_PREFIX + "register"])
        search.rank("another question")
        self.assertEqual(self.embedder.digest_calls, 4)  # Each query checks the digest before embedding.
        self.assertEqual(len(self.embedder.inputs), 3)  # Query embeddings are not cached.
        self.embedder.query_vector = [-1, 0]
        self.assertEqual(search.rank("other"), [(1.0, "contact")])
        self.embedder.digest = "e" * 64
        inputs_before = len(self.embedder.inputs)
        with self.assertRaisesRegex(SemanticError, "digest differs"):
            search.rank("model tag moved")
        self.assertEqual(len(self.embedder.inputs), inputs_before)

    def test_the_candidates_can_be_cut_among_allowed_concepts_from_one_embedding(self):
        search = SemanticSearch(self.index, self.embedder, min_score=0.5, candidate_limit=1)
        scored = search.scores("register")
        self.assertEqual(len(self.embedder.inputs), 2)  # the index build and this one query
        self.assertEqual([concept_id for _, concept_id in scored][0], "deadline")
        self.assertEqual(len(scored), len(self.index.concepts))
        self.assertEqual(search.cut(scored), search.ranked("register"))
        best, candidates = search.cut(scored, {"contact"})
        self.assertEqual(best, round(dict((c, s) for s, c in scored)["contact"], 4))
        self.assertEqual(candidates, [item for item in scored if item[1] == "contact" and item[0] >= 0.5])
        self.assertEqual(search.cut(scored, set()), (None, []))

    def test_index_build_rejects_a_model_digest_change_during_embedding(self):
        with patch.object(self.embedder, "model_digest", side_effect=["d" * 64, "e" * 64]):
            with self.assertRaisesRegex(SemanticError, "changed during indexing"):
                build_index(self.release, self.embedder)

    def test_model_changes_dimension_mismatch_and_provider_failure_are_explicit(self):
        self.embedder.model = "other-model"
        with self.assertRaisesRegex(SemanticError, "same model"):
            SemanticSearch(self.index, self.embedder)
        self.embedder.model = self.index.model
        search = SemanticSearch(self.index, self.embedder)
        self.embedder.digest = "e" * 64
        with self.assertRaisesRegex(SemanticError, "digest differs"):
            search.rank("query")
        self.embedder.digest = self.index.model_digest
        self.embedder.query_vector = [1, 0, 0]
        with self.assertRaisesRegex(SemanticError, "dimensions"):
            search.rank("query")
        with patch.object(self.embedder, "embed", side_effect=TimeoutError()), self.assertRaises(SemanticError):
            search.rank("query")


if __name__ == "__main__":
    unittest.main()
