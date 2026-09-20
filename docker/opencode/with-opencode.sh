#!/bin/sh
# Start the agent on a knowledge base, and replace this process with the
# agent's web server, so that the published port is the whole interface.
#
#   1. the knowledge base. Without SWISSTIP_MCP_URL (the demo image) it is
#      the MCP server of the base image, started here on the container's
#      loopback only and wrapped in with-ollama so that search is hybrid.
#      With SWISSTIP_MCP_URL (the OpenCode image, docker/opencode) it is a
#      Swiss TIP MCP server that runs elsewhere, and nothing is started here.
#      When that server sits behind a name and a password (basic
#      credentials, as deploy/aws can put in front of it),
#      SWISSTIP_MCP_USERNAME and SWISSTIP_MCP_PASSWORD are sent with the
#      wait below and with every call of the agent;
#   2. a wait until it answers /health beside its /mcp, so that the agent
#      never sees a half-started tool source; the answer names the pack and
#      the release, which the welcome panel shows in small print;
#   3. the baked OpenCode configuration, rendered with the model of
#      SWISSTIP_DEMO_MODEL and the MCP address;
#   4. opencode web on the loopback, on OPENCODE_INTERNAL_PORT;
#   5. the shim on OPENCODE_PORT, bound to every interface, which serves the
#      web interface of that server, the routes the interface needs and the
#      server does not answer, and the Swiss TIP welcome panel of
#      SWISSTIP_WELCOME (none when it is empty). A panel written for another
#      pack than the server's keeps its workspace registration and loses its
#      coverage text and sample questions.
#
# Arguments given to docker run are appended to the opencode command line.
set -eu

port="${PORT:-8000}"
web_port="${OPENCODE_PORT:-4096}"
internal_port="${OPENCODE_INTERNAL_PORT:-4099}"
workspace="${SWISSTIP_WORKSPACE:-/home/swisstip/workspace}"
rendered="${HOME}/opencode.json"
mcp_url="${SWISSTIP_MCP_URL:-}"

if [ -z "${OPENCODE_SERVER_PASSWORD:-}" ]; then
    # A hosted demo sets SWISSTIP_REQUIRE_PASSWORD, so that it cannot come up open by an omission.
    if [ -n "${SWISSTIP_REQUIRE_PASSWORD:-}" ]; then
        echo "with-opencode: SWISSTIP_REQUIRE_PASSWORD is set and OPENCODE_SERVER_PASSWORD is not; not starting" >&2
        exit 64
    fi
    echo "with-opencode: OPENCODE_SERVER_PASSWORD is not set; anyone who reaches port ${web_port} can use the agent" >&2
fi

if [ -z "${mcp_url}" ]; then
    mcp_url="http://127.0.0.1:${port}/mcp"
    with-ollama swisstip-mcp \
        --release /srv/swiss-tip/release.json \
        --require-ready \
        --semantic-index /srv/swiss-tip/semantic-index.json \
        --transport streamable-http \
        --host 127.0.0.1 \
        --port "${port}" &
fi

pack=$(python - "${mcp_url}" <<'PY'
import base64, json, os, sys, time, urllib.error, urllib.request
from urllib.parse import urlsplit

mcp_url = sys.argv[1]
username, password = os.environ.get("SWISSTIP_MCP_USERNAME"), os.environ.get("SWISSTIP_MCP_PASSWORD")
if bool(username) != bool(password):
    sys.exit("with-opencode: SWISSTIP_MCP_USERNAME and SWISSTIP_MCP_PASSWORD go together; one of them is empty")
headers = {}
if username:
    headers["Authorization"] = "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
parts = urlsplit(mcp_url)
if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.query or parts.fragment \
        or not parts.path.rstrip("/"):
    sys.exit("with-opencode: SWISSTIP_MCP_URL must be the http(s) address of a Swiss TIP MCP endpoint, "
             "for example http://swiss-tip:8000/mcp, not %r" % mcp_url)
# /health is served beside /mcp, also behind a proxy that puts both under a path.
url = mcp_url.rstrip("/").rsplit("/", 1)[0] + "/health"
deadline = time.monotonic() + 120
while True:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=4) as answer:
            health = json.load(answer)
        break
    except (urllib.error.URLError, OSError, ValueError) as error:
        # Waiting does not change this answer: the server is up and wants credentials, or other ones.
        if getattr(error, "code", None) == 401:
            sys.exit("with-opencode: %s asks for a name and a password%s; set SWISSTIP_MCP_USERNAME and "
                     "SWISSTIP_MCP_PASSWORD" % (url, " and refused the ones given" if headers else ""))
        if time.monotonic() > deadline:
            sys.exit("with-opencode: the MCP server did not answer %s: %s" % (url, error))
        time.sleep(0.5)
print("with-opencode: the knowledge base %s answers on %s" % (health.get("release_id"), url), file=sys.stderr)
print(health.get("pack") or "")
print(health.get("release_id") or "")
PY
)
release=$(printf '%s\n' "${pack}" | sed -n 2p)
pack=$(printf '%s\n' "${pack}" | sed -n 1p)

python - "${OPENCODE_CONFIG:-/etc/swiss-tip/opencode.json}" "${rendered}" "${mcp_url}" <<'PY'
import base64, json, os, sys

source, target, mcp_url = sys.argv[1:4]
with open(source, encoding="utf-8") as handle:
    config = json.load(handle)
model = os.environ.get("SWISSTIP_DEMO_MODEL")
if model:
    config["model"] = model
config["mcp"]["swiss_tip"]["url"] = mcp_url
username, password = os.environ.get("SWISSTIP_MCP_USERNAME"), os.environ.get("SWISSTIP_MCP_PASSWORD")
if username and password:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    config["mcp"]["swiss_tip"]["headers"] = {"Authorization": "Basic " + token}
# The file can hold the credentials, so it is the runtime user's alone.
with open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, indent=2)
print("with-opencode: the agent uses %s and the tools of swiss_tip on %s" % (config["model"], mcp_url), file=sys.stderr)
PY

OPENCODE_CONFIG="${rendered}"
export OPENCODE_CONFIG
cd "${workspace}"
opencode web --hostname 127.0.0.1 --port "${internal_port}" "$@" &

python - "${internal_port}" "${web_port}" <<'PY'
import sys, time, urllib.error, urllib.request

url = "http://127.0.0.1:%s/global/health" % sys.argv[1]
deadline = time.monotonic() + 120
while True:
    try:
        with urllib.request.urlopen(url, timeout=4):
            break
    except (urllib.error.URLError, OSError) as error:
        # With OPENCODE_SERVER_PASSWORD the agent answers every route with 401, this one too: it is up.
        if getattr(error, "code", None) == 401:
            break
        if time.monotonic() > deadline:
            sys.exit("with-opencode: the agent did not answer %s: %s" % (url, error))
        time.sleep(0.5)
# OpenCode's banner above names this internal port; the browser never uses it.
print("with-opencode: the agent answers on %s, inside the container only; the web interface is on container port %s"
      % (url, sys.argv[2]), file=sys.stderr)
PY

welcome="${SWISSTIP_WELCOME-/etc/swiss-tip/welcome.json}"
if [ -n "${welcome}" ]; then
    welcome=$(python - "${welcome}" "${pack}" "${release}" "${HOME}/welcome.json" <<'PY'
import json, sys

source, pack, release, target = sys.argv[1:5]
with open(source, encoding="utf-8") as handle:
    welcome = json.load(handle)
if welcome.get("pack") not in (None, pack):
    # The coverage text and the sample questions describe another pack. The panel is kept, because it
    # is also what registers the workspace as the project in a browser that has none yet.
    print("with-opencode: %s is written for %s and the server serves %s; its coverage text and questions are left out"
          % (source, welcome["pack"], pack or "an unnamed pack"), file=sys.stderr)
    welcome = {"pack": pack, "title": "Swiss TIP knowledge base: %s" % (pack or "unknown pack"),
               "note": welcome.get("note")}
# The release is the server's, so the panel names the one that answers.
welcome["release"] = release or None
with open(target, "w", encoding="utf-8") as handle:
    json.dump(welcome, handle, ensure_ascii=False)
print(target)
PY
)
fi

exec python /usr/local/lib/swiss-tip/opencode_web_shim.py \
    --listen "0.0.0.0:${web_port}" --upstream "127.0.0.1:${internal_port}" \
    ${welcome:+--welcome "${welcome}"}
