# syntax=docker/dockerfile:1
#
# The Swiss TIP MCP server built from this checkout's source, with one pack's
# release, served over Streamable HTTP on port 8000: MCP on /mcp, the health
# payload on /health. Only packages/core, packages/runtime and apps/mcp-server
# are installed; no saved pages, text dataset, builder, console or Ollama.
#
# This repository holds no pack. The pack comes in as a named build context,
# a directory with the pack's release.json and readiness.json (and, where the
# pack has them, semantic-index.json and its image README.md): a checkout's
# releases/<pack> in the packs repository, or the same files downloaded from
# a GitHub release. A new release means a new image, and only a ready release
# is built: the server runs with --require-ready, so the release's
# readiness.json (written by the knowledge builder's ready stage) must name
# exactly the copied release.json.
#
#   python scripts/container/build_image.py --pack-dir ../swiss-tip-mvp/releases/<pack>
#                                                      build, label and tag from release.json
#   docker build --build-context pack=../swiss-tip-mvp/releases/<pack> -t swiss-tip:<pack> .
#   docker run --rm -p 8000:8000 swiss-tip:<pack>       serve on http://127.0.0.1:8000/mcp
#   docker run --rm -i swiss-tip:<pack> --transport stdio
#
# The build script adds the release ID and content digest as build arguments,
# labels and tags. The images that install the published packages instead of
# this source are under docker/.

ARG PYTHON_IMAGE=python:3.14-slim

FROM ${PYTHON_IMAGE} AS wheels
WORKDIR /src
COPY packages/core packages/core
COPY packages/runtime packages/runtime
COPY apps/mcp-server apps/mcp-server
RUN pip wheel --no-cache-dir --wheel-dir /wheels packages/core packages/runtime apps/mcp-server

FROM ${PYTHON_IMAGE}
ARG RELEASE_ID=""
ARG RELEASE_CONTENT_SHA256=""
ARG REVISION=""
LABEL org.opencontainers.image.title="Swiss TIP MCP server" \
      org.opencontainers.image.description="Swiss TIP MCP server with one knowledge release: cited facts on Swiss public information, every fact tied to an excerpt of an official page. Serves MCP over Streamable HTTP on port 8000 at /mcp, or stdio with --transport stdio; the labels swiss-tip.release.id and swiss-tip.release.content-sha256 name the bundled release, and the pack's README, where it has one, is /srv/swiss-tip/README.md." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${RELEASE_ID}" \
      org.opencontainers.image.revision="${REVISION}" \
      swiss-tip.release.id="${RELEASE_ID}" \
      swiss-tip.release.content-sha256="${RELEASE_CONTENT_SHA256}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PORT=8000

RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links /wheels swisstip-mcp-server

COPY LICENSE NOTICE /srv/swiss-tip/
# From the pack's build context. The patterns let the copy pass without the
# optional files, which only some packs publish.
COPY --from=pack release.json readiness.json semantic-index.jso[n] README.m[d] /srv/swiss-tip/

# The build fails when the release does not validate, has no matching
# readiness record, or is not the release the build arguments (and so the
# labels and tags) name.
RUN swisstip-server --release /srv/swiss-tip/release.json --require-ready --health \
 && python -c "import json, sys; m = json.load(open(sys.argv[1], encoding='utf-8'))['manifest']; got = (m['release_id'], m['content_sha256']); sys.exit(0 if all(w in ('', g) for w, g in zip(sys.argv[2:], got)) else 'release.json is %s %s, the build arguments name %s %s' % (*got, *sys.argv[2:]))" \
      /srv/swiss-tip/release.json "${RELEASE_ID}" "${RELEASE_CONTENT_SHA256}"

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin swisstip
USER 10001
WORKDIR /srv/swiss-tip
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"

ENTRYPOINT ["swisstip-server", "--release", "/srv/swiss-tip/release.json", "--require-ready", "--transport", "streamable-http", "--host", "0.0.0.0"]
