"""Check the OpenCode web interface of the demo image or of the OpenCode image.

    python docker/opencode/check_interface.py --url http://127.0.0.1:4096
    python docker/opencode/check_interface.py --url https://demo.example.org --password ...

The interface is only as good as the routes it reads: one project to open a
session in, the knowledge base connected, a model set, and the welcome panel
served with the workspace it registers as the project. No question is asked:
that would spend a free model's budget and make the check depend on a
provider. With --password the check first asks without credentials and
expects 401, so an interface that came up open fails it. The standard
library is all it needs.
"""

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request

WORKSPACE = "/home/swisstip/workspace"
WELCOME_PREFIX = "window.__SWISSTIP_WELCOME__ = "


class Interface:
    def __init__(self, url: str, username: str, password: str | None):
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii") if password else None
        self.headers = {"Authorization": f"Basic {token}"} if token else {}

    def text(self, path: str, credentials: bool = True) -> str:
        request = urllib.request.Request(self.url + path, headers=self.headers if credentials else {})
        with urllib.request.urlopen(request, timeout=10) as answer:
            return answer.read().decode("utf-8")

    def data(self, path: str):
        return json.loads(self.text(path))["data"]


def route_failures(interface: Interface) -> tuple[list[str], dict | None]:
    try:
        projects = interface.data("/api/project") or []
        servers = interface.data("/api/mcp") or []
        model = interface.data("/api/model/default")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        return [f"the interface did not answer: {error}"], None
    checks = (
        ("no project to open a session in", any(p.get("worktree") == WORKSPACE for p in projects)),
        ("swiss_tip is not connected", any(s.get("name") == "swiss_tip" and s.get("status") == "connected" for s in servers)),
        ("no default model", bool(model and model.get("providerID"))),
    )
    return [message for message, ok in checks if not ok], model


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="the web interface, for example http://127.0.0.1:4096")
    parser.add_argument("--username", default="opencode", help="OPENCODE_SERVER_USERNAME; default opencode")
    parser.add_argument("--password", help="OPENCODE_SERVER_PASSWORD, when the interface has one")
    parser.add_argument("--wait", type=float, default=60, help="seconds to wait for the routes; default 60")
    parser.add_argument("--questions", choices=["some", "none", "any"], default="some",
                        help="sample questions the welcome panel must carry: some (default), none, as on a server "
                             "with another pack than the panel's, or any")
    args = parser.parse_args(argv)
    interface = Interface(args.url, args.username, args.password)

    if args.password:
        try:
            interface.text("/", credentials=False)
            return fail("the interface answers without credentials although a password was given")
        except urllib.error.HTTPError as error:
            if error.code != 401:
                return fail(f"without credentials the interface answered {error.code}, not 401")
        except (urllib.error.URLError, OSError) as error:
            return fail(f"the interface did not answer: {error}")
        print("interface: 401 without credentials")

    deadline = time.monotonic() + args.wait
    while True:
        failures, model = route_failures(interface)
        if not failures:
            break
        if time.monotonic() > deadline:
            return fail("; ".join(failures))
        time.sleep(2)
    print("interface: project, swiss_tip connected, model %s/%s" % (model["providerID"], model["id"]))

    # The welcome panel: the page loads it, and the script carries the sample
    # questions and the workspace it registers as the project.
    page, script = interface.text("/"), interface.text("/swiss-tip/welcome.js")
    if '<script src="/swiss-tip/welcome.js"></script>' not in page or not script.startswith(WELCOME_PREFIX):
        return fail("the welcome panel is not served")
    welcome = json.loads(script[len(WELCOME_PREFIX):script.index(";\n")])
    questions = welcome.get("questions") or []
    if welcome.get("worktree") != WORKSPACE:
        return fail(f"the welcome panel does not register the workspace: {welcome}")
    if (args.questions == "some" and not questions) or (args.questions == "none" and questions):
        return fail(f"the welcome panel has {len(questions)} sample questions, expected {args.questions}")
    if not welcome.get("release"):
        return fail(f"the welcome panel does not name the release: {welcome}")
    print("interface: welcome panel %r of release %s with %d sample questions"
          % (welcome.get("title"), welcome["release"], len(questions)))
    return 0


def fail(message: str) -> int:
    print("FAILED: " + message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
