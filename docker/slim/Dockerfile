# syntax=docker/dockerfile:1
#
# A slim release image of the Swiss TIP MCP server: the slim MCP image
# (docker/mcp-slim, swisstip-mcp from PyPI) plus one pack's release, its readiness
# record and its semantic index, and no Ollama and no model. One Dockerfile
# serves every pack; the build context is the pack directory, a checkout's
# releases/<pack> in the packs repository (swiss-tip-mvp) or the same files
# downloaded from a GitHub release. Published as
# ghcr.io/<owner>/swiss-tip:<release_id>-slim by the workflow of the packs
# repository.
#
#   docker build -f docker/mcp-slim/Dockerfile -t swiss-tip-mcp:0.3.0-slim .
#   docker build -f docker/slim/Dockerfile --build-arg PACK=<pack> -t swiss-tip:<pack>-slim <packs>/releases/<pack>
#   docker run --rm -p 8000:8000 swiss-tip:<pack>-slim          MCP on http://127.0.0.1:8000/mcp
#   docker run --rm -i swiss-tip:<pack>-slim --transport stdio
#
# On its own the image serves lexical search. Hybrid search comes from the
# embedding sidecar (docker/ollama) started in this container's network
# namespace, with the index the image already carries:
#
#   docker run -d --name swiss-tip -p 8000:8000 swiss-tip:<pack>-slim --semantic-index /srv/swiss-tip/semantic-index.json
#   docker run -d --name swiss-tip-embeddings --network container:swiss-tip swiss-tip-ollama:qwen3-embedding-0.6b
#
# The repository's compose.yaml is the same in one command. The build fails
# when the release does not validate or is not ready, and when the semantic
# index was not built for exactly this release.

ARG BASE_IMAGE=swiss-tip-mcp:0.3.0-slim
FROM ${BASE_IMAGE}
ARG PACK=""
ARG RELEASE_ID=""
ARG RELEASE_CONTENT_SHA256=""
ARG REVISION=""
LABEL org.opencontainers.image.title="Swiss TIP MCP server with the ${PACK} knowledge release (slim)" \
      org.opencontainers.image.description="Swiss TIP MCP server with the ${PACK} knowledge release and its semantic index, without an embedding model: lexical concept search on its own, hybrid search with the swiss-tip-ollama sidecar in the same network namespace and --semantic-index /srv/swiss-tip/semantic-index.json. Streamable HTTP on port 8000 at /mcp, or stdio with --transport stdio. The licence and notices are in /usr/share/doc/swiss-tip/, and the labels swiss-tip.release.id and swiss-tip.release.content-sha256 name the bundled release." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${RELEASE_ID}" \
      org.opencontainers.image.revision="${REVISION}" \
      swiss-tip.pack="${PACK}" \
      swiss-tip.release.id="${RELEASE_ID}" \
      swiss-tip.release.content-sha256="${RELEASE_CONTENT_SHA256}"

# README.md is the pack's image README, which only some packs publish; the
# pattern lets the copy pass without it.
COPY release.json readiness.json semantic-index.json README.m[d] /srv/swiss-tip/

RUN swisstip-mcp --release /srv/swiss-tip/release.json --require-ready --health > /dev/null \
 && python -c "import json, sys; m = json.load(open(sys.argv[1], encoding='utf-8'))['manifest']; got = (m['release_id'], m['content_sha256']); sys.exit(0 if all(w in ('', g) for w, g in zip(sys.argv[2:], got)) else 'release.json is %s %s, the build arguments name %s %s' % (*got, *sys.argv[2:]))" \
      /srv/swiss-tip/release.json "${RELEASE_ID}" "${RELEASE_CONTENT_SHA256}" \
 && python -c "import sys; from pathlib import Path; from swisstip.runtime.semantic import load_index; from swisstip.runtime.service import ReleaseService; index = load_index(Path(sys.argv[2]), ReleaseService.from_file(Path(sys.argv[1])).release); print('semantic index %s for %s, model %s (%s), %d concepts' % (index.content_sha256[:12], index.release_id, index.model, index.model_digest[:12], len(index.concepts)))" \
      /srv/swiss-tip/release.json /srv/swiss-tip/semantic-index.json

# The slim MCP image's check, reported as soon as the server answers (Docker
# Engine 25 or newer), so that "docker compose up --wait" returns in seconds.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"
