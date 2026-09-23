"""Optional local embedding retrieval over a release-bound concept index.

Embeddings only rank published concept IDs. Applicability and evidence selection
remain the responsibility of the deterministic release service.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from swisstip.core.release import Release
from swisstip.core.validation import assert_valid

SCHEMA_VERSION = "swiss-tip-semantic-index/v1"
INPUT_VERSION = "concept-text/v1"
QUERY_VERSION = "qwen3-retrieval/v1"
QUERY_PREFIX = "Instruct: Given a query, retrieve relevant Swiss public-information concepts.\nQuery: "
DEFAULT_MIN_SCORE = 0.5
DEFAULT_CANDIDATE_LIMIT = 10
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_INDEX_BYTES = 32 * 1024 * 1024


class SemanticError(ValueError):
    """The optional semantic index or local embedding service is unavailable or invalid."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", value):
        raise SemanticError("Ollama returned an invalid model digest.")
    return value.removeprefix("sha256:").lower()


def _vectors(value, expected_count: int, dimension: int | None = None) -> list[list[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != expected_count or not value:
        raise SemanticError("Embedding response count does not match its inputs.")
    result = []
    for vector in value:
        if not isinstance(vector, (list, tuple)) or not vector:
            raise SemanticError("An embedding must be a non-empty vector.")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in vector):
            raise SemanticError("Embedding coordinates must be finite numbers.")
        try:
            numbers = [float(v) for v in vector]
        except (OverflowError, ValueError) as exc:
            raise SemanticError("Embedding coordinates must be finite numbers.") from exc
        if not all(math.isfinite(v) for v in numbers):
            raise SemanticError("Embedding coordinates must be finite numbers.")
        dimension = dimension or len(numbers)
        if len(numbers) != dimension:
            raise SemanticError("Embedding vector dimensions do not match.")
        norm = math.hypot(*numbers)
        if not norm or not math.isfinite(norm):
            raise SemanticError("An embedding must have a finite, nonzero norm.")
        result.append([v / norm for v in numbers])
    return result


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SemanticError("Ollama redirects are disabled.")


class OllamaEmbedder:
    """Bounded Ollama /api/embed requests; loopback only, without proxies or redirects."""

    def __init__(self, model: str = "qwen3-embedding:0.6b", base_url: str = "http://127.0.0.1:11434",
                 timeout_seconds: float = 10, opener=None):
        if not isinstance(model, str) or not model.strip():
            raise SemanticError("An Ollama model tag is required.")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise SemanticError("Ollama timeout must be finite and positive.")
        try:
            parsed = urlsplit(base_url)
            host = parsed.hostname
            # Pin the conventional hostname to a literal loopback address instead of consulting DNS.
            host = "127.0.0.1" if host == "localhost" else host
            if parsed.scheme not in ("http", "https") or not host or not ipaddress.ip_address(host).is_loopback \
                    or parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/") \
                    or parsed.query or parsed.fragment:
                raise ValueError("not a loopback origin")
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise SemanticError("Ollama base URL must be an HTTP(S) loopback origin without credentials or a path.") from exc
        authority = f"[{host}]" if ":" in host else host
        self.base_url = f"{parsed.scheme}://{authority}" + (f":{port}" if port is not None else "")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.opener = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirects())

    def _request(self, path: str, payload: dict | None = None, *, timeout_seconds: float | None = None) -> dict:
        try:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") if payload is not None else None
            request = Request(self.base_url + path, data=data,
                              headers={"Accept": "application/json", "Content-Type": "application/json"})
            with self.opener.open(request, timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SemanticError("Ollama response exceeds the byte limit.")
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("error"):
                raise SemanticError("Ollama returned an invalid response or reported an error.")
            return result
        except SemanticError:
            raise
        except Exception as exc:
            raise SemanticError(f"Local Ollama request failed ({type(exc).__name__}).") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list) or not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise SemanticError("Embedding inputs must be a non-empty list of non-empty texts.")
        result = self._request("/api/embed", {"model": self.model, "input": texts, "truncate": False})
        if result.get("model") != self.model:
            raise SemanticError("Ollama embedding response does not name the requested model tag.")
        return _vectors(result.get("embeddings"), len(texts))

    def model_digest(self) -> str:
        models = self._request("/api/tags").get("models")
        if not isinstance(models, list):
            raise SemanticError("Ollama model listing is invalid.")
        matches = [item for item in models if isinstance(item, dict)
                   and self.model in (item.get("name"), item.get("model"))]
        if len(matches) != 1:
            raise SemanticError(f"Ollama model {self.model!r} is not uniquely available locally.")
        return _digest(matches[0].get("digest"))

    def unload(self) -> None:
        """Unload this model and confirm it leaves /api/ps before a model-cold benchmark."""
        self._request("/api/embed", {"model": self.model, "input": [], "keep_alive": 0})
        deadline = time.monotonic() + 5
        while (remaining := deadline - time.monotonic()) > 0:
            models = self._request("/api/ps", timeout_seconds=min(self.timeout_seconds, remaining)).get("models")
            if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
                raise SemanticError("Ollama running-model listing is invalid.")
            if not any(self.model in (item.get("name"), item.get("model")) for item in models):
                return
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise SemanticError("Ollama model did not unload within five seconds.")


@dataclass(frozen=True)
class ConceptEmbedding:
    concept_id: str
    text: str
    text_sha256: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class EmbeddingIndex:
    schema_version: str
    release_id: str
    release_content_sha256: str
    model: str
    model_digest: str
    dimension: int
    input_version: str
    query_version: str
    query_prefix: str
    concepts: tuple[ConceptEmbedding, ...]
    content_sha256: str


def _index_hash(index: EmbeddingIndex) -> str:
    body = asdict(index)
    body.pop("content_sha256")
    return _sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def concept_texts(release: Release) -> list[tuple[str, str]]:
    """Stable embedding inputs made only from published catalogue and fact content."""
    facts = {fact.fact_id: fact for fact in release.facts}
    return [(concept.concept_id, "\n".join([
        f"Label: {concept.label}", f"Description: {concept.description}",
        "Aliases: " + "; ".join(concept.aliases), "Questions: " + "; ".join(concept.questions),
        "Published facts:", *(facts[fact_id].statement for fact_id in concept.fact_ids)]))
        for concept in release.concepts]


def _valid_release(release: Release) -> None:
    try:
        assert_valid(release)
    except ValueError as exc:
        raise SemanticError("Cannot use an invalid knowledge release for semantic search.") from exc


def build_index(release: Release, embedder: OllamaEmbedder) -> EmbeddingIndex:
    _valid_release(release)
    inputs = concept_texts(release)
    if not inputs:
        raise SemanticError("Cannot index a release without concepts.")
    digest = _digest(embedder.model_digest())
    vectors = _vectors(embedder.embed([text for _, text in inputs]), len(inputs))
    if _digest(embedder.model_digest()) != digest:
        raise SemanticError("Ollama model digest changed during indexing; rebuild with a stable model.")
    index = EmbeddingIndex(
        schema_version=SCHEMA_VERSION, release_id=release.manifest.release_id,
        release_content_sha256=release.manifest.content_sha256, model=embedder.model, model_digest=digest,
        dimension=len(vectors[0]), input_version=INPUT_VERSION, query_version=QUERY_VERSION, query_prefix=QUERY_PREFIX,
        concepts=tuple(ConceptEmbedding(concept_id, text, _sha256(text), tuple(vector))
                       for (concept_id, text), vector in zip(inputs, vectors)), content_sha256="")
    index = replace(index, content_sha256=_index_hash(index))
    _validate_index(index)
    return index


def _validate_index(index: EmbeddingIndex) -> None:
    if index.schema_version != SCHEMA_VERSION or index.input_version != INPUT_VERSION \
            or index.query_version != QUERY_VERSION or index.query_prefix != QUERY_PREFIX:
        raise SemanticError("Semantic index format or embedding instructions are incompatible.")
    if not isinstance(index.dimension, int) or isinstance(index.dimension, bool) or index.dimension < 1:
        raise SemanticError("Semantic index dimension is invalid.")
    if not isinstance(index.model, str) or not index.model.strip():
        raise SemanticError("Semantic index model tag is invalid.")
    _digest(index.model_digest)
    if not index.concepts or len({entry.concept_id for entry in index.concepts}) != len(index.concepts):
        raise SemanticError("Semantic index concepts are empty or duplicated.")
    _vectors([entry.vector for entry in index.concepts], len(index.concepts), index.dimension)
    for entry in index.concepts:
        if entry.text_sha256 != _sha256(entry.text):
            raise SemanticError("Semantic index embedding input hash does not match.")
        if not math.isclose(math.hypot(*entry.vector), 1, rel_tol=1e-6, abs_tol=1e-6):
            raise SemanticError("Semantic index vectors must be normalized.")
    if _index_hash(index) != index.content_sha256:
        raise SemanticError("Semantic index content hash does not match.")


def save_index(index: EmbeddingIndex, path: Path) -> None:
    _validate_index(index)
    Path(path).write_text(json.dumps(asdict(index), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_index(path: Path, release: Release) -> EmbeddingIndex:
    _valid_release(release)
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_INDEX_BYTES + 1)
        if len(raw) > MAX_INDEX_BYTES:
            raise SemanticError("Semantic index exceeds the byte limit.")
        data = json.loads(raw)
        data["concepts"] = tuple(ConceptEmbedding(**{**entry, "vector": tuple(entry["vector"])}) for entry in data["concepts"])
        index = EmbeddingIndex(**data)
        _validate_index(index)
        if index.release_id != release.manifest.release_id or index.release_content_sha256 != release.manifest.content_sha256:
            raise SemanticError("Semantic index was built for a different release.")
        expected = concept_texts(release)
        if [(entry.concept_id, entry.text) for entry in index.concepts] != expected:
            raise SemanticError("Semantic index embedding inputs do not match the published concepts.")
        return index
    except SemanticError:
        raise
    except (OSError, TypeError, ValueError, KeyError, AttributeError, OverflowError) as exc:
        raise SemanticError(f"Cannot load semantic index ({type(exc).__name__}).") from exc


def semantic_index_binding(path: Path, index: EmbeddingIndex, *,
                           min_score: float = DEFAULT_MIN_SCORE,
                           candidate_limit: int = DEFAULT_CANDIDATE_LIMIT) -> dict:
    """Identity of the exact index bytes and retrieval settings used by a hybrid replay."""
    _validate_index(index)
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) \
            or not math.isfinite(min_score) or not -1 <= min_score <= 1:
        raise SemanticError("Semantic minimum score must be between -1 and 1.")
    if not isinstance(candidate_limit, int) or isinstance(candidate_limit, bool) or candidate_limit < 1:
        raise SemanticError("Semantic candidate limit must be a positive integer.")
    raw = Path(path).read_bytes()
    return {
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "content_sha256": index.content_sha256,
        "release_id": index.release_id,
        "release_content_sha256": index.release_content_sha256,
        "model": index.model,
        "model_digest": index.model_digest,
        "dimension": index.dimension,
        "input_version": index.input_version,
        "query_version": index.query_version,
        "query_prefix_sha256": _sha256(index.query_prefix),
        "min_score": float(min_score),
        "candidate_limit": candidate_limit,
    }


class SemanticSearch:
    def __init__(self, index: EmbeddingIndex, embedder: OllamaEmbedder,
                 min_score: float = DEFAULT_MIN_SCORE, candidate_limit: int = DEFAULT_CANDIDATE_LIMIT):
        _validate_index(index)
        if embedder.model != index.model:
            raise SemanticError("Semantic index and query embedder must use the same model tag.")
        if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) \
                or not math.isfinite(min_score) or not -1 <= min_score <= 1:
            raise SemanticError("Semantic minimum score must be between -1 and 1.")
        if not isinstance(candidate_limit, int) or isinstance(candidate_limit, bool) or candidate_limit < 1:
            raise SemanticError("Semantic candidate limit must be a positive integer.")
        self.index, self.embedder = index, embedder
        self.min_score, self.candidate_limit = min_score, candidate_limit

    def rank(self, query: str) -> list[tuple[float, str]]:
        """The candidates at or above the minimum score, best first, at most the candidate limit."""
        return self.ranked(query)[1]

    def ranked(self, query: str) -> tuple[float | None, list[tuple[float, str]]]:
        """The best raw cosine over every indexed concept, and the candidates as `rank` returns them.

        The best score is reported even when it lies below the minimum, so the service can tell a question that
        is far from every concept from one that merely missed the candidate cut."""
        return self.cut(self.scores(query))

    def scores(self, query: str) -> list[tuple[float, str]]:
        """The raw cosine of every indexed concept, best first, before any cut: one embedding request."""
        if not isinstance(query, str) or not query.strip():
            raise SemanticError("A semantic query must be non-empty text.")
        try:
            if self.embedder.model != self.index.model or _digest(self.embedder.model_digest()) != self.index.model_digest:
                raise SemanticError("Local Ollama model digest differs from the semantic index; rebuild the index.")
            vector = _vectors(self.embedder.embed([self.index.query_prefix + query]), 1, self.index.dimension)[0]
            return sorted(((max(-1.0, min(1.0, sum(a * b for a, b in zip(vector, entry.vector)))), entry.concept_id)
                           for entry in self.index.concepts), key=lambda item: (-item[0], item[1]))
        except SemanticError:
            raise
        except Exception as exc:
            raise SemanticError(f"Local semantic ranking failed ({type(exc).__name__}).") from exc

    def cut(self, scored: list[tuple[float, str]], allowed: set[str] | None = None) -> tuple[float | None, list[tuple[float, str]]]:
        """The best score and the candidates of `scores`, among the `allowed` concepts when given."""
        scored = [item for item in scored if allowed is None or item[1] in allowed]
        best = round(scored[0][0], 4) if scored else None
        return best, [item for item in scored if item[0] >= self.min_score][:self.candidate_limit]
