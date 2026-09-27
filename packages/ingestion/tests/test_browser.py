from email.message import Message
from pathlib import Path
import sys
import time
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from swisstip.ingestion import CrawlLimits, SafeCrawler, SourceDefinition  # noqa: E402
from swisstip.ingestion.browser import BrowserOpener, challenge_request_allowed, challenged, remaining  # noqa: E402
from test_crawler import FakeResponse, public_resolver  # noqa: E402

UA = "Mozilla/5.0 (compatible; SwissTIPDemoCrawler/0.1)"
ORIGIN = "https://official.example"


def challenge() -> FakeResponse:
    return FakeResponse(202, b"", headers={"x-amzn-waf-action": "challenge"})


class SequenceOpener:
    """The plain HTTP side: scripted responses per URL (the last one repeats), with the headers each request sent."""

    def __init__(self, responses: dict[str, list]) -> None:
        self.responses = {url: list(items) for url, items in responses.items()}
        self.sent: list[tuple[str, dict]] = []

    def open(self, request, timeout=None):
        self.sent.append((request.full_url, dict(request.header_items())))
        items = self.responses.get(request.full_url)
        if not items:
            raise OSError(f"unexpected request: {request.full_url}")
        item = items.pop(0) if len(items) > 1 else items[0]
        return item() if callable(item) else item


class FakeRoute:
    def __init__(self, url: str, navigation: bool) -> None:
        self.request = type("Request", (), {"url": url, "is_navigation_request": lambda _self: navigation})()
        self.outcome = None

    def continue_(self) -> None:
        self.outcome = "continued"

    def abort(self) -> None:
        self.outcome = "aborted"


class FakePage:
    """What the challenge page does: the listed requests pass the route guard, then the token cookie changes after
    ``issue_after`` waits (never, when None)."""

    def __init__(self, context, requests, issue_after) -> None:
        self.context, self.requests, self.issue_after = context, requests, issue_after
        self.guard, self.routes, self.waits, self.closed = None, [], 0, False

    def route(self, pattern, handler) -> None:
        self.guard = handler

    def goto(self, url, wait_until=None, timeout=None) -> None:
        for request_url, navigation in [(url, True), *self.requests]:
            route = FakeRoute(request_url, navigation)
            self.guard(route)
            self.routes.append(route)
        if self.issue_after == 0:
            self.context.token = "new"

    def wait_for_timeout(self, ms) -> None:
        self.waits += 1
        if self.issue_after is not None and self.waits >= self.issue_after:
            self.context.token = "new"

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, token: str | None = None, requests=(), issue_after=2) -> None:
        self.token, self.requests, self.issue_after, self.pages = token, list(requests), issue_after, []

    def cookies(self, url=None):
        return [{"name": "aws-waf-token", "value": self.token}] if self.token else []

    def new_page(self):
        page = FakePage(self, self.requests, self.issue_after)
        self.pages.append(page)
        return page


def browser_with(responses, context: FakeContext) -> tuple[BrowserOpener, SequenceOpener]:
    plain = SequenceOpener(responses)
    browser = BrowserOpener(opener=plain)
    browser._context_for = lambda user_agent: browser._contexts.setdefault(user_agent, context)  # noqa: SLF001
    return browser, plain


def request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": UA})


class ChallengeRulesTests(unittest.TestCase):
    def test_the_challenge_page_may_reach_only_its_robots_txt_and_the_token_service(self) -> None:
        allowed = [
            (f"{ORIGIN}/robots.txt", True),
            (f"{ORIGIN}/robots.txt", False),
            (f"{ORIGIN}/assets/app.js", False),
            ("https://b41b.98b1.eu-west-1.token.awswaf.com/b41b/challenge.js", False),
        ]
        refused = [
            (f"{ORIGIN}/", True),  # a redirect of the robots.txt navigation
            ("https://elsewhere.example/robots.txt", True),
            ("https://tracker.example/pixel.gif", False),
            ("http://official.example/robots.txt", True),
            ("https://token.awswaf.com.evil.example/x.js", False),
            ("https://b41b.token.awswaf.com/page", True),
        ]
        for url, navigation in allowed:
            self.assertTrue(challenge_request_allowed(url, ORIGIN, navigation), url)
        for url, navigation in refused:
            self.assertFalse(challenge_request_allowed(url, ORIGIN, navigation), url)

    def test_a_challenge_needs_the_status_and_the_header_and_time_runs_out_as_an_os_error(self) -> None:
        self.assertTrue(challenged(challenge()))
        self.assertFalse(challenged(FakeResponse(202, b"")))
        self.assertFalse(challenged(FakeResponse(200, b"", headers={"x-amzn-waf-action": "challenge"})))
        with self.assertRaises(OSError):
            remaining(time.monotonic() - 1)


class BrowserOpenerTests(unittest.TestCase):
    def test_a_host_without_a_challenge_never_starts_the_browser(self) -> None:
        plain = SequenceOpener({f"{ORIGIN}/a": [FakeResponse(200, b"<html>a</html>")]})
        browser = BrowserOpener(opener=plain)
        browser._context_for = lambda user_agent: self.fail("no browser is needed")  # noqa: SLF001
        self.assertEqual(browser.open(request(f"{ORIGIN}/a")).read(), b"<html>a</html>")
        self.assertNotIn("Cookie", plain.sent[0][1])
        self.assertEqual(browser.take_log(), [])

    def test_a_challenge_is_passed_in_the_browser_and_the_request_repeated_with_its_cookie(self) -> None:
        context = FakeContext(token="old", requests=[("https://x.eu-west-1.token.awswaf.com/challenge.js", False),
                                                     ("https://tracker.example/pixel.gif", False),
                                                     (f"{ORIGIN}/", True)])
        browser, plain = browser_with({f"{ORIGIN}/a": [challenge(), FakeResponse(200, b"<html>a</html>")]}, context)
        browser._contexts[UA] = context  # noqa: SLF001 - a session whose token has expired
        response = browser.open(request(f"{ORIGIN}/a"), timeout=5)
        self.assertEqual((response.getcode(), response.read()), (200, b"<html>a</html>"))
        self.assertEqual([headers.get("Cookie") for _, headers in plain.sent],
                         ["aws-waf-token=old", "aws-waf-token=new"], "the repeat carries the new token")
        self.assertTrue(all(headers["User-agent"] == UA for _, headers in plain.sent))
        page = context.pages[0]
        self.assertTrue(page.closed)
        self.assertGreaterEqual(page.waits, 2, "an old token does not end the wait; only a new one does")
        [entry] = browser.take_log()
        self.assertEqual(entry["origin"], ORIGIN)
        self.assertEqual(entry["requested"], [f"{ORIGIN}/robots.txt", "https://x.eu-west-1.token.awswaf.com/challenge.js"])
        self.assertEqual(entry["refused"], ["https://tracker.example/pixel.gif", f"{ORIGIN}/"])
        self.assertEqual([route.outcome for route in page.routes], ["continued", "continued", "aborted", "aborted"])

    def test_no_new_token_or_a_second_challenge_fails_closed(self) -> None:
        stuck = FakeContext(token="old", issue_after=None)
        browser, _ = browser_with({f"{ORIGIN}/a": [challenge()]}, stuck)
        with self.assertRaisesRegex(OSError, "set no new token"):
            browser.open(request(f"{ORIGIN}/a"), timeout=30)
        self.assertIn("set no new token", browser.take_log()[0]["error"])
        stubborn, _ = browser_with({f"{ORIGIN}/a": [challenge()]}, FakeContext())
        with self.assertRaisesRegex(OSError, "still answers with a bot challenge"):
            stubborn.open(request(f"{ORIGIN}/a"), timeout=5)

    def test_a_browser_failure_reaches_the_crawler_as_a_network_failure(self) -> None:
        browser, _ = browser_with({f"{ORIGIN}/a": [challenge()]}, FakeContext())

        def broken(user_agent):
            raise RuntimeError("Executable doesn't exist")

        browser._context_for = broken  # noqa: SLF001
        with self.assertRaisesRegex(OSError, "browser session failed .* Executable doesn't exist"):
            browser.open(request(f"{ORIGIN}/a"), timeout=5)

    def test_one_browser_context_per_user_agent(self) -> None:
        contexts: dict = {}
        browser = BrowserOpener(opener=SequenceOpener({}))
        browser._context_for = lambda user_agent: browser._contexts.setdefault(user_agent, contexts.setdefault(user_agent, FakeContext()))  # noqa: SLF001,E501
        browser._pass_challenge(f"{ORIGIN}/a", "agent-one", time.monotonic() + 5)  # noqa: SLF001
        browser._pass_challenge(f"{ORIGIN}/a", "agent-two", time.monotonic() + 5)  # noqa: SLF001
        self.assertEqual(set(browser._contexts), {"agent-one", "agent-two"})  # noqa: SLF001


class BrowserSessionInTheCrawlerTests(unittest.TestCase):
    """SafeCrawler keeps every rule when the browser session is its opener."""

    def crawl(self, responses, context=None, max_response_bytes=10_000):
        browser, plain = browser_with(responses, context or FakeContext())
        source = SourceDefinition(source_id="test", start_url=f"{ORIGIN}/allowed/start",
                                  allowed_hosts=(), allowed_path_prefixes=("/allowed/",))
        limits = CrawlLimits(max_depth=0, max_pages=1, max_requests=10, max_total_bytes=100_000,
                             max_response_bytes=max_response_bytes, max_duration_seconds=10,
                             request_timeout_seconds=5, delay_seconds=0, max_redirects=2, max_links_per_page=10,
                             max_queued_urls=5, max_failures=3)
        crawler = SafeCrawler(source, limits, opener=browser, resolver=public_resolver)
        return crawler.crawl(), plain

    def test_a_challenged_robots_txt_is_read_after_the_challenge_and_obeyed(self) -> None:
        report, plain = self.crawl({
            f"{ORIGIN}/robots.txt": [challenge(), FakeResponse(200, b"User-agent: *\nDisallow: /allowed/\n",
                                                               content_type="text/plain")],
            f"{ORIGIN}/allowed/start": [FakeResponse(200, b"<html>start</html>")],
        })
        self.assertEqual([url for url, _ in plain.sent], [f"{ORIGIN}/robots.txt", f"{ORIGIN}/robots.txt"])
        self.assertEqual(report.skipped[0].reason, "robots-disallowed")

    def test_pages_are_saved_and_redirects_and_size_limits_stay_the_crawlers(self) -> None:
        robots = lambda: FakeResponse(200, b"User-agent: *\nDisallow: /szukaj\n", content_type="text/plain")  # noqa: E731
        report, _ = self.crawl({f"{ORIGIN}/robots.txt": [robots], f"{ORIGIN}/allowed/start": [FakeResponse(200, b"<html>start</html>")]})
        self.assertEqual([page.final_url for page in report.pages], [f"{ORIGIN}/allowed/start"])
        report, plain = self.crawl({
            f"{ORIGIN}/robots.txt": [robots],
            f"{ORIGIN}/allowed/start": [FakeResponse(302, headers={"Location": "https://elsewhere.example/x"})],
        })
        self.assertEqual(report.pages, [])
        self.assertNotIn("https://elsewhere.example/x", [url for url, _ in plain.sent])
        report, _ = self.crawl({f"{ORIGIN}/robots.txt": [robots],
                                f"{ORIGIN}/allowed/start": [FakeResponse(200, b"x" * 5_000)]}, max_response_bytes=1_000)
        self.assertEqual([(page.outcome, page.bytes_downloaded) for page in report.pages], [("response-too-large", 0)],
                         "the crawler's response limit applies to what the session fetches")


if __name__ == "__main__":
    unittest.main()
