#!/bin/sh
# Start the bundled Ollama on loopback, wait until the embedding model is
# loaded, then run the given command (normally swisstip-mcp) in its place.
#
#   with-ollama swisstip-mcp --release ... --semantic-index ...
#
# Standard output belongs to the command, because MCP over stdio uses it;
# Ollama and this script write to standard error only. When Ollama cannot
# start, the command still runs and the server serves lexical search, unless
# SWISSTIP_REQUIRE_SEMANTIC=1 (the image builds set it), which stops here.
set -eu

# Ollama's access log and llama-server's per-request lines would drown the
# server's own one line per tool call. Only Ollama's structured log lines
# and anything that looks like an error pass; SWISSTIP_OLLAMA_LOG=all passes
# everything.
if [ "${SWISSTIP_OLLAMA_LOG:-}" = all ]; then
    ollama serve 1>&2 &
else
    ollama serve 2>&1 | grep --line-buffered -E '^time=|[Ee]rror|[Ff]ail|panic|fatal' 1>&2 &
fi

if ! python /usr/local/lib/swiss-tip/ollama_ready.py; then
    if [ "${SWISSTIP_REQUIRE_SEMANTIC:-0}" = 1 ]; then
        echo "with-ollama: semantic search is required and Ollama is not ready" >&2
        exit 1
    fi
    echo "with-ollama: Ollama is not ready; the server falls back to lexical search" >&2
fi

exec "$@"
