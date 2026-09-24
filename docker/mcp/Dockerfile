# syntax=docker/dockerfile:1
#
# The basic Swiss TIP MCP server image: swisstip-mcp from PyPI and nothing
# else, no knowledge release and no Ollama. Mount a release directory
# (release.json and readiness.json, optionally semantic-index.json) on
# /srv/swiss-tip; the slim release images (docker/slim) carry one instead.
#
#   docker build -f docker/mcp/Dockerfile -t swiss-tip-mcp:0.2.5 .
#   docker run --rm -p 8000:8000 -v "$PWD/releases/<pack>:/srv/swiss-tip:ro" swiss-tip-mcp:0.2.5
#   docker run --rm -i -v "$PWD/releases/<pack>:/srv/swiss-tip:ro" swiss-tip-mcp:0.2.5 --transport stdio
#
# The build context is the repository root, for LICENSE and NOTICE; the
# Dockerfile.dockerignore next to this file admits those two files only.
#
# Search is lexical. For hybrid search, add --semantic-index
# /srv/swiss-tip/semantic-index.json and provide Ollama with the index's
# model on the container's loopback: the server accepts no other Ollama
# address, so Ollama shares this container's network namespace. The
# embedding sidecar (docker/ollama) joins it with
# --network container:<this container>. See docker/README.md.

ARG PYTHON_IMAGE=python:3.14-slim
FROM ${PYTHON_IMAGE}
ARG SWISSTIP_MCP_VERSION=0.2.5

LABEL org.opencontainers.image.title="Swiss TIP MCP server" \
      org.opencontainers.image.description="Swiss TIP MCP server (swisstip-mcp ${SWISSTIP_MCP_VERSION}) without a knowledge release: mount release.json and readiness.json on /srv/swiss-tip. Streamable HTTP on port 8000 at /mcp, or stdio with --transport stdio; optional semantic search with a loopback Ollama." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${SWISSTIP_MCP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PORT=8000

RUN pip install --no-cache-dir "swisstip-mcp==${SWISSTIP_MCP_VERSION}" \
 && useradd --uid 10001 --create-home --home-dir /home/swisstip --shell /usr/sbin/nologin swisstip \
 && mkdir -p /srv/swiss-tip /usr/share/doc/swiss-tip

# Outside /srv/swiss-tip, which a release mount replaces: every image built on
# this one carries the licence of the server and the notices of its sources.
# The directory is made above, because COPY would create it unreadable (0644).
COPY --chmod=644 LICENSE NOTICE /usr/share/doc/swiss-tip/

USER 10001
WORKDIR /srv/swiss-tip
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"

# Arguments given to docker run are appended; a later --transport or --host wins.
ENTRYPOINT ["swisstip-mcp", "--release", "/srv/swiss-tip/release.json", "--require-ready", "--transport", "streamable-http", "--host", "0.0.0.0"]
