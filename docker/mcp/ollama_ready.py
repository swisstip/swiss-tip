"""Wait for the bundled Ollama, check the embedding model, and load it into memory.

    python ollama_ready.py            wait, check the model digest, load the model (exit 1 on failure)
    python ollama_ready.py --pull     also pull the model first (the image build only)

The model and its expected manifest digest come from SWISSTIP_EMBEDDING_MODEL
and SWISSTIP_EMBEDDING_MODEL_DIGEST. A semantic index records the digest it
was built with, and the server refuses to rank with any other, so a model
with a different digest fails here instead of silently turning every search
lexical. Loading uses OLLAMA_KEEP_ALIVE (-1 in the image), so the model stays
in memory and the first query does not pay the load time. Everything goes to
standard error: standard output belongs to the MCP server started afterwards.
"""

import json
import os
import sys
import time
import urllib.request

OLLAMA = "http://" + os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
MODEL = os.environ.get("SWISSTIP_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
DIGEST = os.environ.get("SWISSTIP_EMBEDDING_MODEL_DIGEST", "")
START_SECONDS = 60
# Never through a proxy: the endpoint is loopback, and HTTP_PROXY in a container environment must not reroute it.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(message: str) -> None:
    print(f"ollama-ready: {message}", file=sys.stderr, flush=True)


def call(path: str, body: dict | None = None, timeout: float = 10) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(OLLAMA + path, data=data, headers={"Content-Type": "application/json"})
    with OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read())


def wait_for_server() -> None:
    deadline = time.monotonic() + START_SECONDS
    while True:
        try:
            call("/api/version", timeout=2)
            return
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"Ollama did not answer on {OLLAMA} within {START_SECONDS} s") from None
            time.sleep(0.25)


def pull() -> None:
    log(f"pulling {MODEL}")
    request = urllib.request.Request(OLLAMA + "/api/pull", data=json.dumps({"model": MODEL}).encode(),
                                     headers={"Content-Type": "application/json"})
    with OPENER.open(request, timeout=1800) as response:
        for line in response:
            status = json.loads(line)
            if status.get("error"):
                raise RuntimeError(f"pull failed: {status['error']}")
    log(f"pulled {MODEL}")


def installed_digest() -> str:
    matches = [model for model in call("/api/tags").get("models", []) if MODEL in (model.get("name"), model.get("model"))]
    if len(matches) != 1:
        raise RuntimeError(f"model {MODEL} is not installed")
    return matches[0]["digest"].removeprefix("sha256:")


def main(argv: list[str]) -> int:
    started = time.monotonic()
    try:
        wait_for_server()
        if "--pull" in argv:
            pull()
        digest = installed_digest()
        if DIGEST and digest != DIGEST:
            raise RuntimeError(f"{MODEL} has digest {digest}, expected {DIGEST}; a semantic index built with "
                               "the expected model would not be used")
        call("/api/embed", {"model": MODEL, "input": ["warm-up"]}, timeout=120)
    except Exception as exc:
        log(f"not ready: {exc}")
        return 1
    log(f"{MODEL} ({digest[:12]}) loaded in {time.monotonic() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
