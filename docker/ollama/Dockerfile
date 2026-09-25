# syntax=docker/dockerfile:1
#
# The embedding sidecar of the Swiss TIP MCP server: a CPU-only Ollama with
# the qwen3-embedding:0.6b model inside, and nothing else - no server and no
# knowledge release. It serves the model on the loopback address only, so it
# is used by a server container whose network namespace it joins: a slim
# release image (docker/slim) or the slim MCP image (docker/mcp-slim) gets hybrid
# search from it. The repository's compose.yaml starts both:
#
#   docker compose up -d --wait
#
# and without Compose:
#
#   docker run -d --name swiss-tip -p 8000:8000 swiss-tip:<pack>-slim --semantic-index /srv/swiss-tip/semantic-index.json
#   docker run -d --name swiss-tip-embeddings --network container:swiss-tip swiss-tip-ollama:qwen3-embedding-0.6b
#
# The Ollama runtime, the model and the start check are copied out of the
# MCP image (docker/mcp), so the model is byte for byte the one the
# pack images serve and the packs' semantic indexes were built with, and
# nothing is pulled from the Ollama registry here. The build starts Ollama
# once and fails unless the copied model has the expected manifest digest.
#
#   docker build -t swiss-tip-mcp:0.3.4 docker/mcp
#   docker build -t swiss-tip-ollama:qwen3-embedding-0.6b docker/ollama

ARG MCP_IMAGE=swiss-tip-mcp:0.3.4
ARG PYTHON_IMAGE=python:3.14-slim

FROM ${MCP_IMAGE} AS mcp

# The Python base of the slim MCP image: the start check and the health check
# are Python, and a client that pulls both images shares these layers.
FROM ${PYTHON_IMAGE}
ARG EMBEDDING_MODEL=qwen3-embedding:0.6b
# The manifest digest recorded in the committed semantic indexes, as in
# docker/mcp/Dockerfile. The model comes from the MCP image, so a
# mismatch means that image was built for another digest.
ARG EMBEDDING_MODEL_DIGEST=ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d
ARG OLLAMA_VERSION=0.34.0

LABEL org.opencontainers.image.title="Swiss TIP embedding sidecar" \
      org.opencontainers.image.description="CPU-only Ollama ${OLLAMA_VERSION} with ${EMBEDDING_MODEL} for the hybrid concept search of the Swiss TIP MCP server. It listens on the loopback address only: start it in the network namespace of the server's container (docker run --network container:<server>, or network_mode: service:<server> in Compose). No server, no knowledge release, no network access at run time." \
      org.opencontainers.image.licenses="Apache-2.0 AND MIT" \
      swiss-tip.ollama.version="${OLLAMA_VERSION}" \
      swiss-tip.embedding.model="${EMBEDDING_MODEL}" \
      swiss-tip.embedding.model-digest="${EMBEDDING_MODEL_DIGEST}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/swisstip \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/opt/ollama/models \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NOPRUNE=1 \
    OLLAMA_NO_CLOUD=1 \
    SWISSTIP_EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    SWISSTIP_EMBEDDING_MODEL_DIGEST=${EMBEDDING_MODEL_DIGEST}

# The same unprivileged user as the server images; Ollama keeps its key pair
# in the home directory.
RUN useradd --uid 10001 --create-home --home-dir /home/swisstip --shell /usr/sbin/nologin swisstip \
 && mkdir -p /usr/local/lib/swiss-tip /usr/share/doc/swiss-tip

# tini reaps Ollama's runner processes and passes signals to the whole group.
COPY --from=mcp /usr/bin/tini /usr/bin/ollama /usr/bin/
COPY --from=mcp /usr/lib/ollama /usr/lib/ollama
COPY --from=mcp --chown=10001:10001 /opt/ollama/models /opt/ollama/models
COPY --from=mcp /usr/local/lib/swiss-tip/ollama_ready.py /usr/local/lib/swiss-tip/
COPY --from=mcp /usr/share/doc/swiss-tip/ /usr/share/doc/swiss-tip/
COPY --chmod=755 serve-embeddings.sh /usr/local/bin/serve-embeddings
COPY embeddings_health.py /usr/local/lib/swiss-tip/

USER 10001
WORKDIR /home/swisstip
# Start Ollama once as the runtime user: the copied runtime must be the
# named version, the copied model must have the expected digest, and it
# must load.
RUN ollama serve > /tmp/ollama-check.log 2>&1 & \
    { python /usr/local/lib/swiss-tip/ollama_ready.py && ollama --version | grep -F "${OLLAMA_VERSION}"; } \
      || { cat /tmp/ollama-check.log; exit 1; }; \
    rm -f /tmp/ollama-check.log

# Healthy means the model is loaded, which takes a few seconds on CPU; the
# short start interval reports it as soon as it is (Docker Engine 25 or newer).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --start-interval=2s --retries=3 \
  CMD python /usr/local/lib/swiss-tip/embeddings_health.py

ENTRYPOINT ["tini", "-g", "--", "serve-embeddings"]
