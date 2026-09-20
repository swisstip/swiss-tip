# syntax=docker/dockerfile:1
#
# Test image, not a release image: the OpenCode agent with its web interface
# and nothing else - no MCP server, no knowledge release, no Ollama. It is
# the browser interface of a pack's demo image (packs repository) as a
# container of its own, for a Swiss TIP MCP server that runs elsewhere: the
# slim release image beside the embedding sidecar, as the repository's
# compose.yaml starts them, or a hosted endpoint.
#
#   docker compose --profile demo up -d --wait       with compose.yaml: GUI on http://127.0.0.1:4096
#   docker run --rm -p 127.0.0.1:4096:4096 -e SWISSTIP_MCP_URL=https://<host>/mcp swiss-tip-opencode
#
# SWISSTIP_MCP_URL names the server; its default is the address of the
# server in compose.yaml. The start script waits for the /health beside it
# and ends with an error when it does not answer. The agent's configuration,
# the plugin that refuses every built-in tool, the start script, the shim and
# the welcome panel are in this directory; a pack's demo image shares them
# and brings its own welcome.json with the pack's coverage and questions.
#
# The image carries no credentials. Without an API key OpenCode uses the
# public OpenCode Zen key, which leaves only the free models enabled; the
# default model is one of them. It needs the network at run time: the model
# is called over the internet.
#
#   docker build -f docker/opencode/Dockerfile -t swiss-tip-opencode .
#
# The build context is the repository root, for LICENSE and NOTICE. The
# OpenCode binary is the platform package of the npm release, downloaded by
# digest; no Node.js is installed. A pack's demo image names the same
# version, package and digest: change them there too.

ARG PYTHON_IMAGE=python:3.14-slim
# Declared before the first FROM, so that every stage that names them gets these values: an ARG with its default
# inside a stage is empty in the next one, which left the version label, and the image tag taken from it, empty.
ARG OPENCODE_VERSION=1.18.31
ARG OPENCODE_PACKAGE=opencode-linux-x64
ARG OPENCODE_SHA256=6d89da252a8b030d923e728396dc34465cf6095101b78222b0ee337b68140dea

# The download stage keeps the tarball out of the final image.
FROM ${PYTHON_IMAGE} AS opencode
ARG OPENCODE_VERSION
ARG OPENCODE_PACKAGE
ARG OPENCODE_SHA256
ADD https://registry.npmjs.org/${OPENCODE_PACKAGE}/-/${OPENCODE_PACKAGE}-${OPENCODE_VERSION}.tgz /tmp/opencode.tgz
RUN echo "${OPENCODE_SHA256}  /tmp/opencode.tgz" | sha256sum -c - \
 && mkdir -p /out \
 && tar -xzf /tmp/opencode.tgz -C /out --strip-components=2 package/bin/opencode \
 && chmod 755 /out/opencode

# The Python base of the basic image and of the embedding sidecar, so a host
# that runs them shares these layers.
FROM ${PYTHON_IMAGE}
ARG OPENCODE_VERSION
ARG OPENCODE_PACKAGE
ARG DEMO_MODEL=opencode/ling-3.0-flash-fin-free
LABEL org.opencontainers.image.title="Swiss TIP demo: OpenCode for a Swiss TIP MCP server" \
      org.opencontainers.image.description="Test image. The OpenCode agent and its web interface on port 4096, for a Swiss TIP MCP server that runs elsewhere and is named by SWISSTIP_MCP_URL (default http://swiss-tip:8000/mcp, the server of the repository's compose.yaml). No MCP server, no knowledge release and no embedding model inside. OpenCode is configured with that server as its only tool source, with a plugin that refuses every built-in tool; the browser is the whole interface. It carries no credentials and defaults to a free OpenCode Zen model, and it needs the network to reach that model." \
      org.opencontainers.image.licenses="Apache-2.0 AND MIT" \
      org.opencontainers.image.version="${OPENCODE_VERSION}" \
      swiss-tip.demo.opencode.version="${OPENCODE_VERSION}" \
      swiss-tip.demo.opencode.package="${OPENCODE_PACKAGE}" \
      swiss-tip.demo.model="${DEMO_MODEL}"

# OPENCODE_PORT is the published one, served by the shim; the agent itself
# listens on OPENCODE_INTERNAL_PORT, on the loopback. SWISSTIP_MCP_URL is
# what makes the start script use a server elsewhere instead of starting one.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    HOME=/home/swisstip \
    SWISSTIP_MCP_URL=http://swiss-tip:8000/mcp \
    OPENCODE_PORT=4096 \
    OPENCODE_INTERNAL_PORT=4099 \
    SWISSTIP_DEMO_MODEL=${DEMO_MODEL} \
    SWISSTIP_WORKSPACE=/home/swisstip/workspace \
    SWISSTIP_WELCOME=/etc/swiss-tip/welcome.json \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    OPENCODE_DISABLE_LSP_DOWNLOAD=1 \
    OPENCODE_DISABLE_PROJECT_CONFIG=1 \
    OPENCODE_DISABLE_SHARE=1 \
    OPENCODE_CONFIG=/etc/swiss-tip/opencode.json

# The same unprivileged user as the server images. tini reaps the agent's
# child processes and passes signals on. There is no browser in the
# container, and opencode web starts one; the link makes that a silent
# success instead of a logged stack trace. Git is for the workspace below,
# not for the agent, which has no shell.
COPY docker/opencode/requirements.txt /tmp/requirements.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends git tini \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt \
 && useradd --uid 10001 --create-home --home-dir /home/swisstip --shell /usr/sbin/nologin swisstip \
 && mkdir -p /etc/swiss-tip /usr/local/lib/swiss-tip /usr/share/doc/swiss-tip /home/swisstip/workspace \
 && chown -R swisstip /home/swisstip \
 && ln -s /bin/true /usr/local/bin/xdg-open

COPY --from=opencode /out/opencode /usr/local/bin/opencode
COPY --chmod=644 LICENSE NOTICE /usr/share/doc/swiss-tip/
COPY --chmod=644 docker/opencode/README.md /usr/share/doc/swiss-tip/opencode.md
COPY --chmod=644 docker/opencode/opencode.json /etc/swiss-tip/opencode.json
COPY --chmod=755 docker/opencode/with-opencode.sh /usr/local/bin/with-opencode
COPY --chmod=644 docker/opencode/opencode_web_shim.py docker/opencode/opencode_health.py \
     docker/opencode/welcome.js docker/opencode/swiss-tip-tools-only.js /usr/local/lib/swiss-tip/
COPY --chmod=644 docker/opencode/welcome.json /etc/swiss-tip/welcome.json

USER 10001
WORKDIR /home/swisstip
# The web interface is project-based, and a project is a version-controlled
# directory; one empty commit in the workspace makes it that project.
RUN git -c init.defaultBranch=main init -q /home/swisstip/workspace \
 && git -C /home/swisstip/workspace \
      -c user.name="Swiss TIP demo" -c user.email="demo@swiss-tip.invalid" \
      commit -q --allow-empty -m "Swiss TIP demo workspace"

# The agent talks to one Swiss TIP MCP server and to nothing else: the build
# fails unless the configuration names swiss_tip as its only MCP server and
# loads the plugin that refuses every built-in tool, and unless the binary
# runs. The server's address is not checked here: the start script writes
# SWISSTIP_MCP_URL into the configuration and refuses what is not an http(s)
# address. No server is started in this image, so none may be installed.
RUN opencode --version \
 && python -c "import ast, pathlib; [ast.parse(pathlib.Path('/usr/local/lib/swiss-tip/' + name).read_text(encoding='utf-8')) for name in ('opencode_web_shim.py', 'opencode_health.py')]" \
 && test -s /usr/local/lib/swiss-tip/welcome.js \
 && python -c "import json, sys; w = json.load(open('/etc/swiss-tip/welcome.json', encoding='utf-8')); sys.exit(None if w.get('title') and all(isinstance(q, str) and q for q in w.get('questions', [])) else 'welcome.json needs a title and non-empty question strings')" \
 && python -c "import httpx, starlette, uvicorn" \
 && python -c "import json, sys; c = json.load(open('/etc/swiss-tip/opencode.json', encoding='utf-8')); sys.exit(None if list(c['mcp']) == ['swiss_tip'] and c['mcp']['swiss_tip']['type'] == 'remote' and c['plugin'] == ['file:///usr/local/lib/swiss-tip/swiss-tip-tools-only.js'] else 'the baked configuration does not restrict the agent to the swiss_tip MCP server')" \
 && grep -q 'startsWith("swiss_tip_")' /usr/local/lib/swiss-tip/swiss-tip-tools-only.js \
 && ! command -v swisstip-mcp \
 && ! command -v ollama

EXPOSE 4096
# The web interface on OPENCODE_PORT. With OPENCODE_SERVER_PASSWORD it
# answers 401, which is an answer. The MCP server reports its own health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=2s --retries=3 \
  CMD python /usr/local/lib/swiss-tip/opencode_health.py

ENTRYPOINT ["tini", "--", "with-opencode"]
CMD []
