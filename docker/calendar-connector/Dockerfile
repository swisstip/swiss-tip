# syntax=docker/dockerfile:1
#
# The generic calendar connector image: swisstip-calendar-connector from PyPI
# and nothing else, no dataset. Mount a directory of dataset bundles (one
# subdirectory per dataset, each with its dataset.json) on
# /srv/swiss-tip/datasets; the pack's calendar image in the packs repository
# carries the bundles instead, on this image.
#
#   docker build -f docker/calendar-connector/Dockerfile -t swiss-tip-calendar-connector:0.3.2 .
#   docker run --rm -p 8100:8100 -v "$PWD/datasets/<pack>:/srv/swiss-tip/datasets:ro" swiss-tip-calendar-connector:0.3.2
#
# The build context is the repository root, for LICENSE and NOTICE; the
# Dockerfile.dockerignore next to this file admits those two files only.
# PACKAGE_INDEX names another index than PyPI to take the package from, for a
# rehearsal on a version that is not released yet, as for the slim MCP image.
#
# The connector is meant for the MCP server only: in compose.yaml it joins
# the server's network namespace, so the server reaches it on
# 127.0.0.1:8100 (--connector http://127.0.0.1:8100) and nobody else does.
# See docker/README.md and docs/architecture/dataset-connectors.md.

ARG PYTHON_IMAGE=python:3.14-slim
FROM ${PYTHON_IMAGE}
ARG SWISSTIP_VERSION=0.3.2
# Empty is PyPI. Another index is used with PyPI behind it, for the dependencies.
ARG PACKAGE_INDEX=""

LABEL org.opencontainers.image.title="Swiss TIP calendar connector" \
      org.opencontainers.image.description="Swiss TIP calendar connector (swisstip-calendar-connector ${SWISSTIP_VERSION}) without a dataset: mount dataset bundles on /srv/swiss-tip/datasets. HTTP on port 8100: /manifest, /lookup, /health, for the MCP server." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${SWISSTIP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PORT=8100

RUN pip install --no-cache-dir \
      ${PACKAGE_INDEX:+--index-url "${PACKAGE_INDEX}" --extra-index-url https://pypi.org/simple/} \
      "swisstip-calendar-connector==${SWISSTIP_VERSION}" \
 && useradd --uid 10001 --create-home --home-dir /home/swisstip --shell /usr/sbin/nologin swisstip \
 && mkdir -p /srv/swiss-tip/datasets /usr/share/doc/swiss-tip

COPY --chmod=644 LICENSE NOTICE /usr/share/doc/swiss-tip/

USER 10001
WORKDIR /srv/swiss-tip
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8100'), timeout=4)"

# Arguments given to docker run are appended; a later --host or --datasets-dir wins.
ENTRYPOINT ["swisstip-calendar-connector", "--datasets-dir", "/srv/swiss-tip/datasets", "--host", "0.0.0.0"]
