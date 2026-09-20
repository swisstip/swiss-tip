"""Docker health check of the images that serve the OpenCode web interface.

Healthy means the web interface answers on OPENCODE_PORT and, in the demo
image, the knowledge base of the same container answers /health on PORT. The
OpenCode image (docker/opencode) has no knowledge base of its own: there
SWISSTIP_MCP_URL names a server elsewhere, whose health is that server's to
report, not this container's.

With OPENCODE_SERVER_PASSWORD the interface answers 401 to a request without
credentials. That is an answer: the check sends none and takes 401 as alive.
"""

import os
import sys
import urllib.error
import urllib.request


def answers(url: str, unauthorised_is_alive: bool = False) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=4):
            return True
    except urllib.error.HTTPError as error:
        return unauthorised_is_alive and error.code == 401
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    checks = [("http://127.0.0.1:%s/" % os.environ.get("OPENCODE_PORT", "4096"), True)]
    if not os.environ.get("SWISSTIP_MCP_URL"):
        checks.append(("http://127.0.0.1:%s/health" % os.environ.get("PORT", "8000"), False))
    failed = [url for url, unauthorised_is_alive in checks if not answers(url, unauthorised_is_alive)]
    if failed:
        print("no answer from " + ", ".join(failed), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
