# syntax=docker/dockerfile:1
#
# The Swiss TIP MCP server with local semantic search: the basic image
# (docker/mcp, swisstip-mcp from PyPI) plus a CPU-only Ollama bound to
# loopback inside the container and the qwen3-embedding:0.6b model. It holds
# no knowledge release; the pack images of the packs repository
# (swiss-tip-mvp) add one with its semantic index.
#
#   docker build -f docker/mcp/Dockerfile -t swiss-tip-mcp:0.3.0 .
#   docker build -t swiss-tip-semantic:0.3.0 docker/semantic
#
# The server only talks to a loopback Ollama, so both run in this container:
# the entrypoint with-ollama starts Ollama, checks that the model has the
# digest the semantic indexes were built with, loads it, and starts the
# server. When Ollama fails, the server still serves lexical search.
#
# The same image builds a semantic index for a release, with exactly the
# model it will be served with:
#
#   docker run --rm -v "$PWD/releases/<pack>:/kb" swiss-tip-semantic:0.3.0 \
#     python -m swisstip.runtime.search_cli index --release /kb/release.json --output /kb/semantic-index.json --timeout 600

ARG BASE_IMAGE=swiss-tip-mcp:0.3.0
ARG OLLAMA_IMAGE=ollama/ollama:0.34.0

# Ollama's CPU runtime only: its binary, the ggml and llama libraries with a
# CPU backend per x86 generation, and the licences. The CUDA, MLX and Vulkan
# libraries (4.6 GB) stay out.
FROM ${OLLAMA_IMAGE} AS ollama
RUN mkdir -p /out/bin /out/lib/ollama \
 && cp -a /usr/bin/ollama /out/bin/ \
 && cp -a /usr/lib/ollama/lib* /usr/lib/ollama/llama-server /usr/lib/ollama/*LICENSE* /usr/lib/ollama/*NOTICE* /out/lib/ollama/

FROM ${BASE_IMAGE}
ARG OLLAMA_IMAGE
ARG EMBEDDING_MODEL=qwen3-embedding:0.6b
# The manifest digest recorded in the packs' semantic-index.json files. A
# model pulled with another digest fails the build: the published indexes
# would not be used with it. Build with --build-arg EMBEDDING_MODEL_DIGEST=
# to accept the current model, then rebuild every index with this image.
ARG EMBEDDING_MODEL_DIGEST=ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d

LABEL org.opencontainers.image.title="Swiss TIP MCP server with semantic search" \
      org.opencontainers.image.description="Swiss TIP MCP server with a loopback CPU Ollama and ${EMBEDDING_MODEL} for hybrid concept search. Base image without a knowledge release." \
      swiss-tip.ollama.image="${OLLAMA_IMAGE}" \
      swiss-tip.embedding.model="${EMBEDDING_MODEL}" \
      swiss-tip.embedding.model-digest="${EMBEDDING_MODEL_DIGEST}"

ENV HOME=/home/swisstip \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/opt/ollama/models \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NOPRUNE=1 \
    OLLAMA_NO_CLOUD=1 \
    SWISSTIP_EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    SWISSTIP_EMBEDDING_MODEL_DIGEST=${EMBEDDING_MODEL_DIGEST}

USER root
# tini reaps Ollama's runner processes and forwards signals to the server.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /opt/ollama/models \
 && chown swisstip /opt/ollama/models

COPY --from=ollama /out/bin/ollama /usr/bin/ollama
COPY --from=ollama /out/lib/ollama /usr/lib/ollama
COPY --chmod=755 with-ollama.sh /usr/local/bin/with-ollama
COPY ollama_ready.py check_semantic.py /usr/local/lib/swiss-tip/

USER 10001
# Pull the model as the runtime user, check its digest and load it once.
RUN ollama serve > /tmp/ollama-pull.log 2>&1 & \
    python /usr/local/lib/swiss-tip/ollama_ready.py --pull || { cat /tmp/ollama-pull.log; exit 1; }; \
    rm -f /tmp/ollama-pull.log

# Model loading at start takes a few seconds on CPU, hence the longer start period.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"

ENTRYPOINT ["tini", "--", "with-ollama"]
CMD ["swisstip-mcp", "--help"]
