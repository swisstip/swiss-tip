# syntax=docker/dockerfile:1
#
# The slim Swiss TIP MCP server image: swisstip-mcp from PyPI and nothing
# else, no knowledge release and no Ollama. Mount a release directory
# (release.json and readiness.json, optionally semantic-index.json) on
# /srv/swiss-tip; the slim release images (docker/slim) carry one instead.
#
#   docker build -f docker/mcp-slim/Dockerfile -t swiss-tip-mcp:0.3.1-slim .
#   docker run --rm -p 8000:8000 -v "$PWD/releases/<pack>:/srv/swiss-tip:ro" swiss-tip-mcp:0.3.1-slim
#   docker run --rm -i -v "$PWD/releases/<pack>:/srv/swiss-tip:ro" swiss-tip-mcp:0.3.1-slim --transport stdio
#
# The build context is the repository root, for LICENSE and NOTICE; the
# Dockerfile.dockerignore next to this file admits those two files only.
#
# PACKAGE_INDEX names another index than PyPI to take swisstip-mcp from, for
# a rehearsal on a version that is not released yet:
#
#   docker build -f docker/mcp-slim/Dockerfile --build-arg SWISSTIP_MCP_VERSION=0.3.0rc1 \
#     --build-arg PACKAGE_INDEX=https://test.pypi.org/simple/ -t swiss-tip-mcp:0.3.0rc1-slim .
#
# PyPI stays the second index, because the dependencies are published there
# and not on TestPyPI. An image built this way is a rehearsal, not a release:
# the workflow gives it no latest tag.
#
# Search is lexical. For hybrid search, add --semantic-index
# /srv/swiss-tip/semantic-index.json and provide Ollama with the index's
# model on the container's loopback: the server accepts no other Ollama
# address, so Ollama shares this container's network namespace. The
# embedding sidecar (docker/ollama) joins it with
# --network container:<this container>. See docker/README.md.

ARG PYTHON_IMAGE=python:3.14-slim
FROM ${PYTHON_IMAGE}
ARG SWISSTIP_MCP_VERSION=0.3.1
# Empty is PyPI. Another index is used with PyPI behind it, for the dependencies.
ARG PACKAGE_INDEX=""

LABEL org.opencontainers.image.title="Swiss TIP MCP server" \
      org.opencontainers.image.description="Swiss TIP MCP server (swisstip-mcp ${SWISSTIP_MCP_VERSION}) without a knowledge release: mount release.json and readiness.json on /srv/swiss-tip. Streamable HTTP on port 8000 at /mcp, or stdio with --transport stdio; optional semantic search with a loopback Ollama." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${SWISSTIP_MCP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PORT=8000

RUN pip install --no-cache-dir \
      ${PACKAGE_INDEX:+--index-url "${PACKAGE_INDEX}" --extra-index-url https://pypi.org/simple/} \
      "swisstip-mcp==${SWISSTIP_MCP_VERSION}" \
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
