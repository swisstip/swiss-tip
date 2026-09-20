#!/bin/sh
# Serve the embedding model of the sidecar image: start Ollama on the
# loopback address, check that the model has the digest the semantic indexes
# were built with, load it, and stay in the foreground while Ollama runs.
#
# The Swiss TIP server reaches it on 127.0.0.1:11434, so this container joins
# the network namespace of the server's container. The server does not wait
# for it: until the model is loaded, and whenever this container is gone,
# every search reports retrieval_mode lexical-fallback with the reason.
#
# A model that does not pass the check ends the container with an error
# instead of leaving a server beside it that silently never runs hybrid.
set -eu

# Ollama's access log and llama-server's per-request lines would bury its
# own messages. Only its structured log lines and anything that looks like
# an error pass; SWISSTIP_OLLAMA_LOG=all passes everything.
if [ "${SWISSTIP_OLLAMA_LOG:-}" = all ]; then
    ollama serve 1>&2 &
else
    ollama serve 2>&1 | grep --line-buffered -E '^time=|[Ee]rror|[Ff]ail|panic|fatal' 1>&2 &
fi

if ! python /usr/local/lib/swiss-tip/ollama_ready.py; then
    echo "serve-embeddings: the embedding model is not ready; stopping" >&2
    exit 1
fi

wait
echo "serve-embeddings: Ollama ended" >&2
exit 1
