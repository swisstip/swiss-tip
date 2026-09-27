"""Pass a JavaScript bot challenge in a headless browser, for official hosts that answer non-browsers with one.

Some hosts (the City of Warsaw's ``um.warszawa.pl`` and its document and district servers) answer every request
that does not come from a browser, robots.txt included, with an AWS WAF challenge: HTTP 202, an empty body and
``x-amzn-waf-action: challenge``. ``BrowserOpener`` is an opener for SafeCrawler that makes every request through
the crawler's own non-redirecting urllib opener, with the cookies of a browser session. Only when a response is a
challenge does it open the origin's robots.txt in a headless browser, whose challenge script sets a new token
cookie, and then repeat the request once. So every response the crawler receives, robots.txt included, is a plain
HTTP response it reads itself: its size limits, byte and request budgets, content-encoding rule, robots, scope and
redirect checks all apply as for any other transport, and the saved bytes are the server's.

The challenge page may load nothing but its origin's robots.txt and AWS WAF's token service; any other request,
and any navigation away from robots.txt, is aborted. What the page requested is recorded for the attempt's
manifest (``take_log``). The session keeps the request's User-Agent (one browser context per agent); nothing is
disguised.

Playwright is an optional dependency (``pip install -e packages/ingestion[browser]``). The browser is an installed
Chromium-family browser named by ``channel`` (``msedge``, ``chrome``), or Playwright's own Chromium after
``python -m playwright install chromium``.
"""

from datetime import UTC, datetime
import re
import time
import urllib.request
from urllib.parse import urlsplit

from .crawler import _NoRedirectHandler

CHALLENGE_HEADER = "x-amzn-waf-action"
TOKEN_COOKIE = "aws-waf-token"
CHALLENGE_WAIT_MS = 10_000
# Besides its own origin, a challenge page may only reach AWS WAF's token service.
CHALLENGE_HOST = re.compile(r"(?:^|\.)token\.awswaf\.com$")


def challenged(response) -> bool:
    return response.getcode() == 202 and (response.headers.get(CHALLENGE_HEADER) or "").strip().lower() == "challenge"


def challenge_request_allowed(url: str, origin: str, navigation: bool) -> bool:
    """What the challenge page may load: HTTPS only; its origin, where the only document is robots.txt; the scripts
    and calls of AWS WAF's token service."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    if f"{parts.scheme}://{parts.netloc}" == origin:
        return not navigation or parts.path == "/robots.txt"
    return not navigation and CHALLENGE_HOST.search(parts.hostname) is not None


def remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("the request's time is used up")
    return left


class BrowserOpener:
    """An opener for SafeCrawler: urllib requests with a browser session's cookies, the browser only for challenges."""

    def __init__(self, channel: str | None = None, headless: bool = True, opener=None) -> None:
        self.channel = channel
        self.headless = headless
        self._opener = opener or urllib.request.build_opener(_NoRedirectHandler())
        self._playwright = self._browser = None
        self._contexts: dict[str | None, object] = {}
        self._log: list[dict] = []

    def open(self, request, timeout=None):
        # One deadline for the whole call: the request, a challenge and the repeated request share the crawler's
        # request timeout, and running out is a TimeoutError, an OSError the crawler fails closed on.
        deadline = time.monotonic() + (timeout or 20)
        user_agent = request.get_header("User-agent")
        response = self._request(request, user_agent, deadline)
        if not challenged(response):
            return response
        response.close()
        self._pass_challenge(request.full_url, user_agent, deadline)
        response = self._request(request, user_agent, deadline)
        if challenged(response):
            response.close()
            raise OSError(f"{urlsplit(request.full_url).netloc} still answers with a bot challenge after the browser passed it")
        return response

    def take_log(self) -> list[dict]:
        """The challenges passed since the last call, with what the page requested and what was refused."""
        log, self._log = self._log, []
        return log

    def close(self) -> None:
        for context in self._contexts.values():
            try:
                context.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        self._contexts.clear()
        for resource, method in ((self._browser, "close"), (self._playwright, "stop")):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:  # noqa: BLE001
                    pass
        self._playwright = self._browser = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _request(self, request, user_agent: str | None, deadline: float):
        headers = dict(request.header_items())
        context = self._contexts.get(user_agent)
        if context is not None:
            cookies = [f"{cookie['name']}={cookie['value']}" for cookie in context.cookies(request.full_url)]
            if cookies:
                headers["Cookie"] = "; ".join(cookies)
        plain = urllib.request.Request(request.full_url, headers=headers, method=request.get_method())
        return self._opener.open(plain, timeout=remaining(deadline))

    def _context_for(self, user_agent: str | None):
        """The browser session of one User-Agent; the browser starts on the first challenge."""
        if user_agent in self._contexts:
            return self._contexts[user_agent]
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise OSError("The browser session needs Playwright: pip install -e packages/ingestion[browser]") from exc
            try:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(channel=self.channel, headless=self.headless)
            except Exception as exc:
                # Leave nothing half-started, so the next target starts clean and reports the same cause.
                self.close()
                raise OSError(f"the browser did not start: {type(exc).__name__}: {exc}") from exc
        context = self._browser.new_context(user_agent=user_agent) if user_agent else self._browser.new_context()
        self._contexts[user_agent] = context
        return context

    def _pass_challenge(self, url: str, user_agent: str | None, deadline: float) -> None:
        """Open the origin's robots.txt and wait until the challenge script has set a new token cookie."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        entry = {"origin": origin, "at": datetime.now(UTC).isoformat(), "requested": [], "refused": []}
        try:
            context = self._context_for(user_agent)
            tokens = lambda: {cookie["value"] for cookie in context.cookies(origin)  # noqa: E731
                              if cookie["name"].startswith(TOKEN_COOKIE)}
            before = tokens()
            page = context.new_page()

            def guard(route) -> None:
                request = route.request
                address = request.url.split("?")[0]
                if challenge_request_allowed(request.url, origin, request.is_navigation_request()):
                    entry["requested"].append(address)
                    route.continue_()
                else:
                    entry["refused"].append(address)
                    route.abort()

            try:
                page.route("**/*", guard)
                try:
                    page.goto(origin + "/robots.txt", wait_until="load", timeout=remaining(deadline) * 1000)
                except Exception as exc:  # noqa: BLE001 - a refused redirect after the token is set is not a failure
                    entry["navigation_error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                waited = 0
                while not (tokens() - before):
                    if waited >= CHALLENGE_WAIT_MS:
                        raise TimeoutError(f"{origin}: the bot challenge set no new token")
                    step = min(250, round(remaining(deadline) * 1000))
                    page.wait_for_timeout(step)
                    waited += step
            finally:
                page.close()
        except OSError as exc:
            entry["error"] = str(exc)
            self._log.append(entry)
            raise
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            self._log.append(entry)
            raise OSError(f"the browser session failed for {origin}: {type(exc).__name__}: {exc}") from exc
        self._log.append(entry)
