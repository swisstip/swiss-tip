"""Health check of the embedding sidecar: Ollama answers and the embedding model is loaded.

    python embeddings_health.py       exit 0 when SWISSTIP_EMBEDDING_MODEL is listed by /api/ps

The model is loaded once at start and stays loaded (OLLAMA_KEEP_ALIVE=-1), so
a model missing from the list means a query would pay the load time again, or
that the start check has not finished yet.
"""

import json
import os
import sys
import urllib.request

OLLAMA = "http://" + os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
MODEL = os.environ.get("SWISSTIP_EMBEDDING_MODEL", "qwen3-embedding:0.6b")


def main() -> int:
    try:
        # Never through a proxy: the endpoint is loopback, whatever HTTP_PROXY says.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(OLLAMA + "/api/ps", timeout=4) as response:
            running = json.loads(response.read()).get("models", [])
    except Exception as exc:
        print(f"embeddings-health: Ollama does not answer on {OLLAMA}: {exc}", file=sys.stderr)
        return 1
    if not any(MODEL in (item.get("name"), item.get("model")) for item in running):
        print(f"embeddings-health: {MODEL} is not loaded", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
