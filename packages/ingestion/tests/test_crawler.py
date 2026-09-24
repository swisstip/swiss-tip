import sys
import hashlib
import unittest
from dataclasses import replace
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from swisstip.ingestion import CrawlLimits, SafeCrawler, SourceDefinition  # noqa: E402
from swisstip.ingestion.crawler import robots_agent  # noqa: E402


PUBLIC_DNS_RESULT = [(2, 1, 6, "", ("93.184.216.34", 443))]


def public_resolver(*_args, **_kwargs):
    return PUBLIC_DNS_RESULT


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        *,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
        publish_length: bool = True,
    ) -> None:
        self._status = status
        self._body = body
        self._offset = 0
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if publish_length:
            self.headers["Content-Length"] = str(len(body))
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def getcode(self) -> int:
        return self._status

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._offset
        start = self._offset
        self._offset = min(len(self._body), self._offset + size)
        return self._body[start : self._offset]

    def close(self) -> None:
        pass


class FakeOpener:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.requested: list[str] = []
        self.timeouts: list[float | None] = []
        self.user_agents: list[str | None] = []

    def open(self, request, timeout=None):  # noqa: ANN001, ARG002
        self.requested.append(request.full_url)
        self.user_agents.append(request.get_header("User-agent"))
        self.timeouts.append(timeout)
        response = self.responses.get(request.full_url)
        if response is None:
            raise OSError(f"unexpected request: {request.full_url}")
        return response


def crawler_for(
    responses: dict[str, FakeResponse],
    *,
    max_depth: int = 1,
    max_pages: int = 10,
    max_response_bytes: int = 10_000,
    allowed_hosts: tuple[str, ...] = (),
    respect_robots: bool = True,
) -> tuple[SafeCrawler, FakeOpener]:
    opener = FakeOpener(responses)
    source = SourceDefinition(
        source_id="test-source",
        start_url="https://official.example/allowed/start",
        allowed_hosts=allowed_hosts,
        allowed_path_prefixes=("/allowed/",),
    )
    limits = CrawlLimits(
        max_depth=max_depth,
        max_pages=max_pages,
        max_requests=20,
        max_total_bytes=100_000,
        max_response_bytes=max_response_bytes,
        max_duration_seconds=10,
        request_timeout_seconds=1,
        delay_seconds=0,
        max_redirects=2,
        max_links_per_page=20,
        max_queued_urls=20,
        max_failures=3,
    )
    crawler = SafeCrawler(
        source,
        limits,
        respect_robots=respect_robots,
        opener=opener,
        resolver=public_resolver,
    )
    return crawler, opener


class SafeCrawlerTests(unittest.TestCase):
    def test_depth_scope_queries_and_robots_are_enforced(self) -> None:
        robots = b"User-agent: *\nDisallow: /allowed/blocked\n"
        start = b"""
            <html><title>Start</title><body>
            <a href="/allowed/level-1">one</a>
            <a href="/allowed/blocked">blocked</a>
            <a href="/outside">outside</a>
            <a href="/allowed/search?q=trap">query</a>
            <a href="https://other.example/page">external</a>
            </body></html>
        """
        level_1 = b'<html><a href="/allowed/level-2">too deep</a></html>'
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, robots, content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(200, start),
                "https://official.example/allowed/level-1": FakeResponse(200, level_1),
            }
        )

        report = crawler.crawl()

        self.assertEqual(report.stop_reason, "frontier-exhausted")
        self.assertEqual(report.robots_status, "loaded")
        self.assertEqual(len(report.pages), 2)
        self.assertEqual(report.requests_sent, 3)
        self.assertEqual(report.pages[0].title, "Start")
        self.assertNotIn("https://official.example/allowed/blocked", opener.requested)
        self.assertNotIn("https://official.example/allowed/level-2", opener.requested)
        reasons = {item.reason for item in report.skipped}
        self.assertIn("out-of-scope", reasons)
        self.assertIn("query-string-disabled", reasons)
        self.assertIn("robots-disallowed", reasons)

    def test_override_fetches_disallowed_url_and_records_overridden_status(self) -> None:
        # With respect_robots off, the Disallow rule is not enforced: the operator has permission for the host.
        # robots.txt is not fetched at all, and the report records the override rather than dropping the signal.
        robots = b"User-agent: *\nDisallow: /allowed/blocked\n"
        start = b'<a href="/allowed/blocked">blocked</a>'
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, robots, content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(200, start),
                "https://official.example/allowed/blocked": FakeResponse(200, b"<html/>"),
            },
            respect_robots=False,
        )

        report = crawler.crawl()

        self.assertEqual(report.robots_status, "overridden")
        self.assertIn("https://official.example/allowed/blocked", opener.requested)
        self.assertNotIn("https://official.example/robots.txt", opener.requested)
        self.assertNotIn("robots-disallowed", {item.reason for item in report.skipped})

    def test_browser_compatible_agent_still_obeys_its_own_robots_group(self) -> None:
        robots = (
            b"User-agent: SwissTIPDemoCrawler\nDisallow: /allowed/blocked\n\n"
            b"User-agent: *\nDisallow:\n"
        )
        start = b'<a href="/allowed/open">open</a><a href="/allowed/blocked">blocked</a>'
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, robots, content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(200, start),
                "https://official.example/allowed/open": FakeResponse(200, b"<html/>"),
            }
        )

        report = crawler.crawl()

        self.assertEqual(
            set(opener.user_agents), {"Mozilla/5.0 (compatible; SwissTIPDemoCrawler/0.1)"}
        )
        self.assertEqual(robots_agent(crawler.user_agent), "SwissTIPDemoCrawler")
        self.assertEqual(robots_agent("OtherCrawler/2.0"), "OtherCrawler/2.0")
        self.assertIn("https://official.example/allowed/open", opener.requested)
        self.assertNotIn("https://official.example/allowed/blocked", opener.requested)
        self.assertIn("robots-disallowed", {item.reason for item in report.skipped})

    def test_page_budget_stops_before_an_extra_request(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, b"User-agent: *\nAllow: /\n", content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(
                    200, b'<a href="/allowed/next">next</a>'
                ),
            },
            max_pages=1,
        )

        report = crawler.crawl()

        self.assertEqual(report.stop_reason, "page-limit")
        self.assertEqual(report.requests_sent, 2)
        self.assertEqual(len(opener.requested), 2)

    def test_cross_host_redirect_is_not_followed(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, b"User-agent: *\nAllow: /\n", content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(
                    302,
                    headers={"Location": "https://other.example/escape"},
                ),
            }
        )

        report = crawler.crawl()

        self.assertEqual(len(report.pages), 0)
        self.assertEqual(report.requests_sent, 2)
        self.assertEqual(len(opener.requested), 2)
        self.assertTrue(
            any(item.reason.startswith("redirect-out-of-scope") for item in report.skipped)
        )

    def test_stream_without_content_length_stops_at_response_limit(self) -> None:
        crawler, _ = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, b"User-agent: *\nAllow: /\n", content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(
                    200,
                    b"x" * 100,
                    publish_length=False,
                ),
            },
            max_response_bytes=25,
        )

        report = crawler.crawl()

        self.assertEqual(report.pages[0].outcome, "response-limit-reached")
        self.assertEqual(report.pages[0].bytes_downloaded, 25)

    def test_non_html_body_is_not_downloaded(self) -> None:
        crawler, _ = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, b"User-agent: *\nAllow: /\n", content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(
                    200, b"%PDF large payload", content_type="application/pdf"
                ),
            }
        )

        report = crawler.crawl()

        self.assertEqual(report.pages[0].outcome, "content-type-skipped")
        self.assertEqual(report.pages[0].bytes_downloaded, 0)

    def test_private_dns_target_is_rejected_before_any_request(self) -> None:
        crawler, opener = crawler_for({})
        crawler._resolver = lambda *_args, **_kwargs: [  # noqa: SLF001
            (2, 1, 6, "", ("127.0.0.1", 443))
        ]

        report = crawler.crawl()

        self.assertEqual(report.stop_reason, "unsafe-start-url")
        self.assertEqual(report.requests_sent, 0)
        self.assertEqual(opener.requested, [])

    def test_opt_in_pdf_snapshot_preserves_bytes_and_hash(self) -> None:
        payload = b"%PDF-1.7\ncurated official document"
        crawler, _ = crawler_for({
            "https://official.example/robots.txt": FakeResponse(404),
            "https://official.example/allowed/start": FakeResponse(200, payload, content_type="application/pdf"),
        })
        saved = []
        crawler = SafeCrawler(crawler.source, crawler.limits, opener=crawler._opener,
                              resolver=public_resolver, document_content_types=("application/pdf",),
                              on_document=lambda page, body: saved.append((page, body)))
        crawler.crawl()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][1], payload)
        self.assertEqual(saved[0][0].sha256, hashlib.sha256(payload).hexdigest())

    def test_opt_in_pdf_does_not_save_oversized_response(self) -> None:
        crawler, _ = crawler_for({
            "https://official.example/robots.txt": FakeResponse(404),
            "https://official.example/allowed/start": FakeResponse(200, b"%PDF-" * 100, content_type="application/pdf"),
        }, max_response_bytes=30)
        saved = []
        crawler = SafeCrawler(crawler.source, crawler.limits, opener=crawler._opener,
                              resolver=public_resolver, document_content_types=("application/pdf",),
                              on_document=lambda page, body: saved.append(body))
        report = crawler.crawl()
        self.assertEqual(saved, [])
        self.assertEqual(report.pages[0].outcome, "response-too-large")

    def test_robots_failure_is_fail_closed(self) -> None:
        crawler, opener = crawler_for({})

        report = crawler.crawl()

        self.assertEqual(report.stop_reason, "robots-unavailable")
        self.assertEqual(report.robots_status, "unavailable-fail-closed")
        self.assertEqual(report.requests_sent, 1)
        self.assertEqual(
            opener.requested, ["https://official.example/robots.txt"]
        )

    def test_redirect_to_robots_disallowed_path_is_not_requested(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200,
                    b"User-agent: *\nDisallow: /allowed/blocked\n",
                    content_type="text/plain",
                ),
                "https://official.example/allowed/start": FakeResponse(
                    302, headers={"Location": "/allowed/blocked"}
                ),
            }
        )

        report = crawler.crawl()

        self.assertEqual(report.pages, [])
        self.assertEqual(report.requests_sent, 2)
        self.assertNotIn("https://official.example/allowed/blocked", opener.requested)
        self.assertEqual(report.skipped[0].url, "https://official.example/allowed/blocked")
        self.assertEqual(report.skipped[0].reason, "robots-disallowed")

    def test_secondary_host_loads_and_reuses_its_own_robots_policy(self) -> None:
        primary_robots = b"User-agent: *\nDisallow: /allowed/open\n"
        secondary_robots = b"User-agent: *\nDisallow: /allowed/blocked\n"
        start = (
            b'<a href="https://secondary.example/allowed/open">open</a>'
            b'<a href="https://secondary.example/allowed/blocked">blocked</a>'
            b'<a href="https://secondary.example/allowed/another">another</a>'
        )
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    200, primary_robots, content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(200, start),
                "https://secondary.example/robots.txt": FakeResponse(
                    200, secondary_robots, content_type="text/plain"
                ),
                "https://secondary.example/allowed/open": FakeResponse(200),
                "https://secondary.example/allowed/another": FakeResponse(200),
            },
            allowed_hosts=("secondary.example",),
        )

        report = crawler.crawl()

        self.assertEqual(
            opener.requested,
            [
                "https://official.example/robots.txt",
                "https://official.example/allowed/start",
                "https://secondary.example/robots.txt",
                "https://secondary.example/allowed/open",
                "https://secondary.example/allowed/another",
            ],
        )
        self.assertEqual(len(report.pages), 3)
        self.assertEqual(report.requests_sent, 5)
        self.assertEqual(
            report.bytes_downloaded, len(primary_robots) + len(start) + len(secondary_robots)
        )
        self.assertEqual(report.robots_url, "https://official.example/robots.txt")
        self.assertEqual(report.robots_status, "loaded")
        self.assertEqual(
            report.to_dict()["robots_status_by_origin"],
            {"https://official.example": "loaded", "https://secondary.example": "loaded"},
        )
        self.assertEqual(report.skipped[0].reason, "robots-disallowed")

    def test_redirect_checks_policy_for_each_host(self) -> None:
        for origin in ("https://secondary.example",):
            with self.subTest(origin=origin):
                crawler, opener = crawler_for(
                    {
                        "https://official.example/robots.txt": FakeResponse(404),
                        "https://official.example/allowed/start": FakeResponse(
                            302, headers={"Location": f"{origin}/allowed/blocked"}
                        ),
                        f"{origin}/robots.txt": FakeResponse(
                            200,
                            b"User-agent: *\nDisallow: /allowed/blocked\n",
                            content_type="text/plain",
                        ),
                    },
                    allowed_hosts=("secondary.example",),
                )

                report = crawler.crawl()

                self.assertEqual(opener.requested[-1], f"{origin}/robots.txt")
                self.assertEqual(report.requests_sent, 3)
                self.assertEqual(report.pages, [])
                self.assertEqual(report.skipped[0].url, f"{origin}/allowed/blocked")
                self.assertEqual(report.skipped[0].reason, "robots-disallowed")

    def test_https_redirect_cannot_downgrade_to_http(self) -> None:
        crawler, opener = crawler_for({
            "https://official.example/robots.txt": FakeResponse(404),
            "https://official.example/allowed/start": FakeResponse(
                302, headers={"Location": "http://official.example/allowed/plain"}),
        })
        report = crawler.crawl()
        self.assertEqual(opener.requested,
                         ["https://official.example/robots.txt", "https://official.example/allowed/start"])
        self.assertEqual(report.pages, [])
        self.assertEqual(report.skipped[0].reason,
                 "redirect-out-of-scope: redirect downgrades HTTPS to HTTP")

    def test_secondary_robots_failure_is_cached_and_fail_closed(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(404),
                "https://official.example/allowed/start": FakeResponse(
                    200,
                    b'<a href="https://secondary.example/allowed/one">one</a>'
                    b'<a href="https://secondary.example/allowed/two">two</a>'
                    b'<a href="/allowed/local">local</a>',
                ),
                "https://official.example/allowed/local": FakeResponse(200),
            },
            allowed_hosts=("secondary.example",),
        )

        report = crawler.crawl()

        self.assertEqual(opener.requested.count("https://secondary.example/robots.txt"), 1)
        self.assertEqual(report.failures, 1)
        self.assertEqual(len(report.pages), 2)
        self.assertEqual([item.reason for item in report.skipped], ["robots-unavailable"] * 2)
        self.assertEqual(report.robots_status, "not-published")
        self.assertEqual(
            report.robots_status_by_origin["https://secondary.example"],
            "unavailable-fail-closed",
        )

    def test_robots_cache_is_reset_for_each_crawl(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(404),
                "https://official.example/allowed/start": FakeResponse(200),
            }
        )
        self.assertEqual(len(crawler.crawl().pages), 1)
        opener.responses["https://official.example/robots.txt"] = FakeResponse(
            200, b"User-agent: *\nDisallow: /\n", content_type="text/plain"
        )

        report = crawler.crawl()

        self.assertEqual(report.pages, [])
        self.assertEqual(report.requests_sent, 1)
        self.assertEqual(opener.requested.count("https://official.example/robots.txt"), 2)
        self.assertEqual(opener.requested.count("https://official.example/allowed/start"), 1)

    def test_secondary_robots_requests_and_redirects_share_request_budget(self) -> None:
        for redirect in (False, True):
            with self.subTest(redirect=redirect):
                crawler, opener = crawler_for(
                    {
                        "https://official.example/robots.txt": FakeResponse(404),
                        "https://official.example/allowed/start": FakeResponse(
                            302,
                            headers={"Location": "https://secondary.example/allowed/page"},
                        ),
                        "https://secondary.example/robots.txt": (
                            FakeResponse(302, headers={"Location": "/allowed/policy.txt"})
                            if redirect else FakeResponse(404)
                        ),
                    },
                    allowed_hosts=("secondary.example",),
                )
                crawler.limits = replace(crawler.limits, max_requests=3, max_pages=3)

                report = crawler.crawl()

                self.assertEqual(report.stop_reason, "request-limit")
                self.assertEqual(report.requests_sent, 3)
                self.assertEqual(len(opener.requested), 3)
                self.assertEqual(report.pages, [])
                self.assertEqual(
                    report.robots_status_by_origin["https://secondary.example"],
                    "budget-exhausted" if redirect else "not-published",
                )

    def test_secondary_robots_body_shares_total_byte_budget(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(200, b"#" * 20),
                "https://official.example/allowed/start": FakeResponse(
                    302, headers={"Location": "https://secondary.example/allowed/page"}
                ),
                "https://secondary.example/robots.txt": FakeResponse(
                    200, b"#" * 30, publish_length=False
                ),
            },
            allowed_hosts=("secondary.example",),
            max_response_bytes=30,
        )
        crawler.limits = replace(crawler.limits, max_total_bytes=40)

        report = crawler.crawl()

        self.assertEqual(report.stop_reason, "byte-limit")
        self.assertEqual(report.bytes_downloaded, 40)
        self.assertEqual(report.requests_sent, 3)
        self.assertEqual(opener.requested[-1], "https://secondary.example/robots.txt")
        self.assertEqual(report.pages, [])

    def test_robots_acquisition_obeys_duration_and_remaining_request_timeout(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(404),
                "https://official.example/allowed/start": FakeResponse(
                    302, headers={"Location": "https://secondary.example/allowed/page"}
                ),
                "https://secondary.example/robots.txt": FakeResponse(404),
            },
            allowed_hosts=("secondary.example",),
        )
        crawler.limits = replace(crawler.limits, max_duration_seconds=2)
        clock = [0.0]
        original_open = opener.open

        def timed_open(request, timeout=None):
            response = original_open(request, timeout=timeout)
            clock[0] += 0.75
            return response

        with patch("swisstip.ingestion.crawler.time.monotonic", side_effect=lambda: clock[0]):
            with patch.object(opener, "open", side_effect=timed_open):
                report = crawler.crawl()

        self.assertEqual(report.stop_reason, "duration-limit")
        self.assertEqual(opener.timeouts, [1, 1, 0.5])
        self.assertEqual(report.requests_sent, 3)
        self.assertEqual(report.pages, [])

    def test_content_and_robots_redirect_loops_remain_bounded(self) -> None:
        for policy_loop in (False, True):
            with self.subTest(policy_loop=policy_loop):
                loop_url = (
                    "https://official.example/robots.txt" if policy_loop
                    else "https://official.example/allowed/start"
                )
                responses = {"https://official.example/robots.txt": FakeResponse(404)}
                responses[loop_url] = FakeResponse(302, headers={"Location": loop_url})
                crawler, opener = crawler_for(responses)

                report = crawler.crawl()

                self.assertEqual(opener.requested.count(loop_url), 3)
                self.assertEqual(report.pages, [])
                self.assertEqual(
                    report.stop_reason,
                    "robots-unavailable" if policy_loop else "frontier-exhausted",
                )

    def test_robots_redirects_recheck_scope_and_query_restrictions(self) -> None:
        for target in (
            "https://outside.example/robots.txt",
            "https://official.example/outside/policy.txt",
            "https://official.example/robots.txt?policy=private",
        ):
            with self.subTest(target=target):
                crawler, opener = crawler_for(
                    {"https://official.example/robots.txt": FakeResponse(
                        302, headers={"Location": target}
                    )}
                )

                report = crawler.crawl()

                self.assertEqual(report.stop_reason, "robots-unavailable")
                self.assertEqual(opener.requested, ["https://official.example/robots.txt"])

    def test_redirected_robots_and_content_recheck_private_dns(self) -> None:
        for policy_redirect in (False, True):
            with self.subTest(policy_redirect=policy_redirect):
                redirect_url = (
                    "https://official.example/robots.txt" if policy_redirect
                    else "https://official.example/allowed/start"
                )
                target = (
                    "https://secondary.example/robots.txt" if policy_redirect
                    else "https://secondary.example/allowed/page"
                )
                responses = {"https://official.example/robots.txt": FakeResponse(404)}
                responses[redirect_url] = FakeResponse(302, headers={"Location": target})
                crawler, opener = crawler_for(
                    responses, allowed_hosts=("secondary.example",)
                )
                crawler._resolver = lambda host, *_args, **_kwargs: (  # noqa: SLF001
                    [(2, 1, 6, "", ("127.0.0.1", 443))]
                    if host == "secondary.example" else PUBLIC_DNS_RESULT
                )

                report = crawler.crawl()

                self.assertEqual(report.pages, [])
                self.assertFalse(any("secondary.example" in url for url in opener.requested))
                self.assertEqual(report.requests_sent, 1 if policy_redirect else 2)

    def test_content_dns_is_rechecked_after_new_policy_acquisition(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(404),
                "https://official.example/allowed/start": FakeResponse(
                    302, headers={"Location": "https://secondary.example/allowed/page"}
                ),
                "https://secondary.example/robots.txt": FakeResponse(404),
            },
            allowed_hosts=("secondary.example",),
        )

        def changing_resolver(host, *_args, **_kwargs):
            if (
                host == "secondary.example"
                and "https://secondary.example/robots.txt" in opener.requested
            ):
                return [(2, 1, 6, "", ("127.0.0.1", 443))]
            return PUBLIC_DNS_RESULT

        crawler._resolver = changing_resolver  # noqa: SLF001

        report = crawler.crawl()

        self.assertEqual(report.requests_sent, 3)
        self.assertEqual(report.pages, [])
        self.assertEqual(opener.requested[-1], "https://secondary.example/robots.txt")
        self.assertIn("non-public address", report.skipped[0].reason)

    def test_redirected_robots_policy_is_bound_to_original_origin(self) -> None:
        crawler, opener = crawler_for(
            {
                "https://official.example/robots.txt": FakeResponse(
                    302, headers={"Location": "https://secondary.example/allowed/policy.txt"}
                ),
                "https://secondary.example/allowed/policy.txt": FakeResponse(
                    200, b"User-agent: *\nAllow: /\n", content_type="text/plain"
                ),
                "https://official.example/allowed/start": FakeResponse(
                    302, headers={"Location": "https://secondary.example/allowed/page"}
                ),
                "https://secondary.example/robots.txt": FakeResponse(403),
            },
            allowed_hosts=("secondary.example",),
        )

        report = crawler.crawl()

        self.assertEqual(report.requests_sent, 4)
        self.assertEqual(report.pages, [])
        self.assertEqual(opener.requested[-1], "https://secondary.example/robots.txt")
        self.assertEqual(
            report.robots_status_by_origin,
            {"https://official.example": "loaded", "https://secondary.example": "access-denied"},
        )
        self.assertEqual(report.skipped[0].reason, "robots-disallowed")

    def test_secondary_robots_denied_or_incomplete_policy_blocks_content(self) -> None:
        for status, body, publish_length, expected_status in (
            (401, b"", True, "access-denied"),
            (503, b"", True, "unavailable-fail-closed"),
            (200, b"#" * 26, True, "unavailable-fail-closed"),
            (200, b"#" * 26, False, "unavailable-fail-closed"),
        ):
            with self.subTest(status=status, publish_length=publish_length):
                crawler, opener = crawler_for(
                    {
                        "https://official.example/robots.txt": FakeResponse(404),
                        "https://official.example/allowed/start": FakeResponse(
                            302, headers={"Location": "https://secondary.example/allowed/page"}
                        ),
                        "https://secondary.example/robots.txt": FakeResponse(
                            status, body, content_type="text/plain", publish_length=publish_length
                        ),
                    },
                    allowed_hosts=("secondary.example",),
                    max_response_bytes=25,
                )

                report = crawler.crawl()

                self.assertEqual(report.pages, [])
                self.assertEqual(opener.requested[-1], "https://secondary.example/robots.txt")
                self.assertEqual(report.robots_status_by_origin["https://secondary.example"], expected_status)
                self.assertEqual(report.bytes_downloaded, 0 if publish_length else 25)

    def test_secondary_robots_delay_cannot_weaken_existing_throttle(self) -> None:
        for delay in (1, 3):
            with self.subTest(delay=delay):
                crawler, _ = crawler_for(
                    {
                        "https://official.example/robots.txt": FakeResponse(
                            200, b"User-agent: *\nCrawl-delay: 2\n", content_type="text/plain"
                        ),
                        "https://official.example/allowed/start": FakeResponse(
                            302, headers={"Location": "https://secondary.example/allowed/page"}
                        ),
                        "https://secondary.example/robots.txt": FakeResponse(
                            200,
                            f"User-agent: *\nCrawl-delay: {delay}\n".encode(),
                            content_type="text/plain",
                        ),
                        "https://secondary.example/allowed/page": FakeResponse(200),
                    },
                    allowed_hosts=("secondary.example",),
                )
                clock = [0.0]

                def sleep(seconds):
                    clock[0] += seconds

                with patch("swisstip.ingestion.crawler.time.monotonic", side_effect=lambda: clock[0]):
                    with patch("swisstip.ingestion.crawler.time.sleep", side_effect=sleep) as mocked_sleep:
                        report = crawler.crawl()

                effective_delay = max(2, delay)
                self.assertEqual(report.effective_delay_seconds, effective_delay)
                self.assertEqual([call.args[0] for call in mocked_sleep.call_args_list], [2, 2, effective_delay])
                self.assertEqual(len(report.pages), 1)


if __name__ == "__main__":
    unittest.main()
