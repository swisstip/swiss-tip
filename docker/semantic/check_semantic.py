"""Fail an image build unless search really runs hybrid on the bundled model.

    with-ollama python check_semantic.py RELEASE INDEX QUERY...

Validates the release and the semantic index exactly as the server does, then
runs every query through the release service with semantic ranking. A
lexical fallback (another model digest, an index for another release, a
failed embedding call) is an error, printed with its reason. A query given
as "weak:<question>" is one the release does not cover: it must come back
with a weak or empty match, so an off-topic question is declined in hybrid
mode too. Prints one line per query with the retrieval mode, the match
strength, the latency and the top concepts.
"""

import sys
import time
from pathlib import Path

from swisstip.core.contracts import SearchRequest
from swisstip.runtime.semantic import OllamaEmbedder, SemanticError, SemanticSearch, load_index
from swisstip.runtime.service import ReleaseService


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    release, index_path, queries = Path(argv[0]), Path(argv[1]), argv[2:]
    service = ReleaseService.from_file(release)
    try:
        index = load_index(index_path, service.release)
    except SemanticError as exc:
        print(f"check-semantic: {index_path}: {exc}", file=sys.stderr)
        return 1
    service.semantic_search = SemanticSearch(index, OllamaEmbedder(model=index.model))
    failed = 0
    for query in queries:
        expect_weak = query.startswith("weak:")
        query = query.removeprefix("weak:")
        started = time.monotonic()
        result = service.search(SearchRequest(query=query))
        milliseconds = (time.monotonic() - started) * 1000
        top = ", ".join(hit.concept_id for hit in result.results[:3])
        print(f"{result.retrieval_mode:<16} {result.match_strength:<7} {milliseconds:6.0f} ms  {query!r} -> {top}")
        if result.retrieval_mode != "hybrid":
            print(f"check-semantic: not hybrid: {result.fallback_reason}", file=sys.stderr)
            failed += 1
        elif expect_weak and result.match_strength == "strong":
            print(f"check-semantic: {query!r} is outside the release but reports a strong match "
                  f"({result.match_signals.model_dump(exclude_none=True)})", file=sys.stderr)
            failed += 1
    print(f"check-semantic: {service.release_id}, index {index.content_sha256[:12]}, model {index.model} "
          f"({index.model_digest[:12]}): {len(queries) - failed}/{len(queries)} queries hybrid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
