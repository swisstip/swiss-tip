"""A deliberately small, bounded web crawler for source discovery demos.

The crawler is sequential and conservative by design. It is intended for the
operator-triggered ingestion path, never for request-time MCP execution.
"""

import hashlib
import ipaddress
import posixpath
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Callable, Iterable


# The "Mozilla/5.0 (compatible; <bot>)" form is the common convention for
# self-identifying crawlers. Some official CDNs (www.ch.ch) answer any other
# form with an error page and HTTP 200.
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; SwissTIPDemoCrawler/0.1)"
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def robots_agent(user_agent: str) -> str:
    """The product token robots.txt groups are matched against.

    ``RobotFileParser`` matches the text before the first slash, which is
    ``Mozilla`` for the compatible form; the crawler's own name must match.
    """

    match = re.search(r"\(compatible;\s*([^;/)\s]+)", user_agent)
    return match.group(1) if match else user_agent


class CrawlConfigurationError(ValueError):
    """Raised when a source or crawl limit is unsafe or inconsistent."""


class CrawlBudgetReached(RuntimeError):
    """Raised internally when a hard crawl budget is exhausted."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ScopeViolation(RuntimeError):
    """Raised when a URL or redirect leaves the configured source scope."""


class RobotsViolation(RuntimeError):
    """Raised before a content hop that its origin's robots policy forbids."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(reason)
        self.url = url
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    """The acquisition-relevant subset of the specification's source contract."""

    source_id: str
    start_url: str
    allowed_hosts: tuple[str, ...] = ()
    allowed_path_prefixes: tuple[str, ...] = ("/",)
    canonical_authority: str | None = None
    jurisdiction: str | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.start_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise CrawlConfigurationError("start_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise CrawlConfigurationError("credentials are not allowed in URLs")
        try:
            port = parsed.port
        except ValueError as exc:
            raise CrawlConfigurationError("start_url contains an invalid port") from exc
        expected_port = 443 if parsed.scheme.lower() == "https" else 80
        if port not in {None, expected_port}:
            raise CrawlConfigurationError("only the scheme's standard port is allowed")
        if not self.source_id.strip():
            raise CrawlConfigurationError("source_id cannot be empty")
        if not self.allowed_path_prefixes:
            raise CrawlConfigurationError("at least one allowed path prefix is required")
        if any(not prefix.startswith("/") for prefix in self.allowed_path_prefixes):
            raise CrawlConfigurationError("allowed path prefixes must start with '/'")
        for host in self.allowed_hosts:
            if not host or any(character in host for character in "/:@?#* "):
                raise CrawlConfigurationError(
                    "allowed hosts must be exact host names without ports or wildcards"
                )

    @property
    def effective_allowed_hosts(self) -> frozenset[str]:
        start_host = urllib.parse.urlsplit(self.start_url).hostname
        assert start_host is not None
        return frozenset({start_host.lower(), *(host.lower() for host in self.allowed_hosts)})


@dataclass(frozen=True, slots=True)
class CrawlLimits:
    """Hard limits; defaults intentionally favor a low-impact demonstration."""

    max_depth: int = 1
    max_pages: int = 20
    max_requests: int = 30
    max_total_bytes: int = 5_000_000
    max_response_bytes: int = 1_000_000
    max_duration_seconds: float = 60.0
    request_timeout_seconds: float = 10.0
    delay_seconds: float = 1.0
    max_redirects: int = 3
    max_links_per_page: int = 100
    max_queued_urls: int = 200
    max_failures: int = 5

    def __post_init__(self) -> None:
        integer_limits = {
            "max_depth": self.max_depth,
            "max_pages": self.max_pages,
            "max_requests": self.max_requests,
            "max_total_bytes": self.max_total_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_redirects": self.max_redirects,
            "max_links_per_page": self.max_links_per_page,
            "max_queued_urls": self.max_queued_urls,
            "max_failures": self.max_failures,
        }
        for name, value in integer_limits.items():
            minimum = 0 if name in {"max_depth", "max_redirects"} else 1
            if value < minimum:
                raise CrawlConfigurationError(f"{name} must be at least {minimum}")
        if self.max_pages > self.max_requests:
            raise CrawlConfigurationError("max_pages cannot exceed max_requests")
        if self.max_response_bytes > self.max_total_bytes:
            raise CrawlConfigurationError(
                "max_response_bytes cannot exceed max_total_bytes"
            )
        for name, value in {
            "max_duration_seconds": self.max_duration_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
        }.items():
            if value <= 0:
                raise CrawlConfigurationError(f"{name} must be greater than zero")
        if self.delay_seconds < 0:
            raise CrawlConfigurationError("delay_seconds cannot be negative")


@dataclass(slots=True)
class CrawledPage:
    requested_url: str
    final_url: str
    depth: int
    status: int
    content_type: str
    bytes_downloaded: int
    elapsed_ms: int
    retrieved_at: str
    sha256: str | None = None
    title: str | None = None
    links_found: int = 0
    etag: str | None = None
    last_modified: str | None = None
    outcome: str = "fetched"


@dataclass(slots=True)
class SkippedUrl:
    url: str
    reason: str
    depth: int | None = None


@dataclass(slots=True)
class CrawlReport:
    source_id: str
    start_url: str
    started_at: str
    finished_at: str = ""
    stop_reason: str = "frontier-exhausted"
    requests_sent: int = 0
    bytes_downloaded: int = 0
    failures: int = 0
    robots_url: str | None = None
    robots_status: str = "not-checked"
    robots_status_by_origin: dict[str, str] = field(default_factory=dict)
    effective_delay_seconds: float = 0.0
    pages: list[CrawledPage] = field(default_factory=list)
    skipped: list[SkippedUrl] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _FetchResult:
    requested_url: str
    final_url: str
    status: int
    headers: object
    body: bytes
    bytes_downloaded: int
    elapsed_ms: int
    outcome: str = "fetched"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _LinkExtractor(HTMLParser):
    def __init__(self, max_links: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_links = max_links
        self.links: list[str] = []
        self.links_found = 0
        self.link_limit_reached = False
        self.no_follow = False
        self._inside_title = False
        self._title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs}
        if tag.lower() == "a" and attributes.get("href"):
            rel = {item.lower() for item in (attributes.get("rel") or "").split()}
            if "nofollow" not in rel:
                self.links_found += 1
                if len(self.links) < self.max_links:
                    self.links.append(attributes["href"] or "")
                else:
                    self.link_limit_reached = True
        elif tag.lower() == "title":
            self._inside_title = True
        elif tag.lower() == "meta" and (attributes.get("name") or "").lower() == "robots":
            directives = {
                part.strip().lower()
                for part in (attributes.get("content") or "").split(",")
            }
            self.no_follow = "nofollow" in directives or "none" in directives

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        if self._inside_title and len(self._title) < 300:
            self._title += data[: 300 - len(self._title)]

    @property
    def title(self) -> str | None:
        value = " ".join(self._title.split())
        return value[:300] or None


class SafeCrawler:
    """Breadth-first crawler with enforced scope, etiquette, and traffic budgets.

    Optional ``on_page`` receives accepted, parsed HTML and its original bytes
    after metadata hashing. It enables snapshot storage without a second fetch;
    storage failures propagate to the caller. The default retains metadata only.
    ``document_content_types`` and ``on_document`` opt into saving additional
    complete responses (for example PDFs), without traversing their contents.
    """

    def __init__(
        self,
        source: SourceDefinition,
        limits: CrawlLimits | None = None,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        allow_query_strings: bool = False,
        allow_private_networks: bool = False,
        opener: urllib.request.OpenerDirector | None = None,
        resolver: Callable[..., Iterable[tuple]] = socket.getaddrinfo,
        on_page: Callable[[CrawledPage, bytes], None] | None = None,
        document_content_types: tuple[str, ...] = (),
        on_document: Callable[[CrawledPage, bytes], None] | None = None,
    ) -> None:
        if not user_agent.strip():
            raise CrawlConfigurationError("user_agent cannot be empty")
        self.source = source
        self.limits = limits or CrawlLimits()
        self.user_agent = user_agent
        self.allow_query_strings = allow_query_strings
        self.allow_private_networks = allow_private_networks
        self._opener = opener or urllib.request.build_opener(_NoRedirectHandler())
        self._resolver = resolver
        self._on_page = on_page
        self._document_content_types = frozenset(document_content_types)
        self._on_document = on_document
        self._report = CrawlReport(
            source_id=source.source_id,
            start_url=source.start_url,
            started_at="",
        )
        self._started_monotonic = 0.0
        self._last_request_monotonic: float | None = None
        self._robots_by_origin: dict[
            str, urllib.robotparser.RobotFileParser | None
        ] = {}
        self._effective_delay_seconds = self.limits.delay_seconds

    def crawl(self) -> CrawlReport:
        """Crawl the configured source until the frontier or a hard limit is reached."""

        self._report = CrawlReport(
            source_id=self.source.source_id,
            start_url=self.source.start_url,
            started_at=datetime.now(UTC).isoformat(),
        )
        self._started_monotonic = time.monotonic()
        self._last_request_monotonic = None
        self._robots_by_origin = {}
        self._effective_delay_seconds = self.limits.delay_seconds
        self._report.effective_delay_seconds = self._effective_delay_seconds

        try:
            start_url = self._normalize_url(self.source.start_url)
            if urllib.parse.urlsplit(start_url).query and not self.allow_query_strings:
                raise CrawlConfigurationError(
                    "start_url contains a query string but query crawling is disabled"
                )
            self._validate_network_target(start_url)
            self._load_robots(start_url)
            if self._report.robots_status == "unavailable-fail-closed":
                self._report.stop_reason = "robots-unavailable"
                return self._finish()

            frontier: deque[tuple[str, int]] = deque([(start_url, 0)])
            known = {start_url}

            while frontier:
                self._check_common_budgets()
                if len(self._report.pages) >= self.limits.max_pages:
                    raise CrawlBudgetReached("page-limit")
                if self._report.failures >= self.limits.max_failures:
                    raise CrawlBudgetReached("failure-limit")

                url, depth = frontier.popleft()
                try:
                    result = self._fetch(url, self.limits.max_response_bytes)
                except RobotsViolation as exc:
                    self._skip(exc.url, exc.reason, depth)
                    continue
                except ScopeViolation as exc:
                    self._skip(url, f"redirect-out-of-scope: {exc}", depth)
                    continue
                except (OSError, urllib.error.URLError) as exc:
                    self._report.failures += 1
                    self._skip(url, f"request-failed: {type(exc).__name__}", depth)
                    continue

                page = self._page_from_result(result, depth)
                self._report.pages.append(page)
                if result.status == 429:
                    self._report.failures += 1
                    page.outcome = "rate-limited"
                    raise CrawlBudgetReached("server-rate-limit")
                if result.status == 503:
                    self._report.failures += 1
                    page.outcome = "server-unavailable"
                    raise CrawlBudgetReached("server-unavailable")
                if result.status >= 400:
                    self._report.failures += 1
                    page.outcome = "http-error"
                    continue
                if result.outcome != "fetched":
                    page.outcome = result.outcome
                    continue
                if not self._is_html(page.content_type):
                    page.outcome = "non-html"
                    if page.content_type in self._document_content_types:
                        page.sha256 = hashlib.sha256(result.body).hexdigest()
                        if self._on_document is not None:
                            self._on_document(page, result.body)
                    continue

                extractor = _LinkExtractor(self.limits.max_links_per_page)
                try:
                    extractor.feed(self._decode_html(result.body, result.headers))
                except Exception:
                    self._report.failures += 1
                    page.outcome = "html-parse-error"
                    continue

                page.sha256 = hashlib.sha256(result.body).hexdigest()
                page.title = extractor.title
                page.links_found = extractor.links_found
                if self._on_page is not None:
                    self._on_page(page, result.body)
                if depth >= self.limits.max_depth or extractor.no_follow:
                    continue

                if extractor.link_limit_reached:
                    self._skip(url, "per-page-link-limit", depth)
                for href in extractor.links:
                    candidate = self._candidate_url(result.final_url, href, depth + 1)
                    if candidate is None or candidate in known:
                        continue
                    if len(known) >= self.limits.max_queued_urls:
                        self._skip(candidate, "queued-url-limit", depth + 1)
                        break
                    known.add(candidate)
                    frontier.append((candidate, depth + 1))

        except CrawlBudgetReached as exc:
            self._report.stop_reason = exc.reason
        except (CrawlConfigurationError, ScopeViolation) as exc:
            self._report.stop_reason = "unsafe-start-url"
            self._skip(self.source.start_url, str(exc), 0)

        return self._finish()

    def _finish(self) -> CrawlReport:
        self._report.finished_at = datetime.now(UTC).isoformat()
        return self._report

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    def _record_robots_status(self, origin: str, status: str) -> None:
        self._report.robots_status_by_origin[origin] = status
        if self._report.robots_url == f"{origin}/robots.txt":
            self._report.robots_status = status

    def _load_robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        origin = self._origin(url)
        if origin in self._robots_by_origin:
            return self._robots_by_origin[origin]
        robots_url = f"{origin}/robots.txt"
        if self._report.robots_url is None:
            self._report.robots_url = robots_url
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            result = self._fetch(
                robots_url,
                min(512_000, self.limits.max_response_bytes),
                allow_robots_path=True,
            )
        except CrawlBudgetReached:
            self._record_robots_status(origin, "budget-exhausted")
            raise
        except (OSError, ScopeViolation, urllib.error.URLError):
            self._report.failures += 1
            self._record_robots_status(origin, "unavailable-fail-closed")
            self._robots_by_origin[origin] = None
            return None

        if result.status == 200 and result.outcome == "fetched":
            parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
            self._record_robots_status(origin, "loaded")
            crawl_delay = parser.crawl_delay(robots_agent(self.user_agent))
            request_rate = parser.request_rate(robots_agent(self.user_agent))
            declared_delays = [self._effective_delay_seconds]
            if crawl_delay is not None:
                declared_delays.append(float(crawl_delay))
            if request_rate is not None and request_rate.requests > 0:
                declared_delays.append(request_rate.seconds / request_rate.requests)
            self._effective_delay_seconds = max(declared_delays)
            self._report.effective_delay_seconds = self._effective_delay_seconds
        elif result.status in {404, 410}:
            parser.parse([])
            self._record_robots_status(origin, "not-published")
        elif result.status in {401, 403}:
            parser.parse(["User-agent: *", "Disallow: /"])
            self._record_robots_status(origin, "access-denied")
        else:
            self._report.failures += 1
            self._record_robots_status(origin, "unavailable-fail-closed")
            self._robots_by_origin[origin] = None
            return None
        self._robots_by_origin[origin] = parser
        return parser

    def _robots_can_fetch(self, url: str) -> bool:
        parser = self._load_robots(url)
        return parser is not None and parser.can_fetch(robots_agent(self.user_agent), url)

    def _candidate_url(self, base_url: str, href: str, depth: int) -> str | None:
        try:
            candidate = self._normalize_url(urllib.parse.urljoin(base_url, href))
        except CrawlConfigurationError:
            return None
        if not self._is_in_scope(candidate):
            self._skip(candidate, "out-of-scope", depth)
            return None
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.query and not self.allow_query_strings:
            self._skip(self._redact_query(candidate), "query-string-disabled", depth)
            return None
        return candidate

    def _normalize_url(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise CrawlConfigurationError("URL is not absolute HTTP(S)")
        if parsed.username or parsed.password:
            raise CrawlConfigurationError("credentials are not allowed in URLs")
        try:
            port_number = parsed.port
        except ValueError as exc:
            raise CrawlConfigurationError("URL contains an invalid port") from exc
        expected_port = 443 if scheme == "https" else 80
        if port_number not in {None, expected_port}:
            raise CrawlConfigurationError("only the scheme's standard port is allowed")
        host = parsed.hostname.lower()
        serialized_host = f"[{host}]" if ":" in host else host
        path = parsed.path or "/"
        normalized_path = posixpath.normpath(path)
        if path.endswith("/") and not normalized_path.endswith("/"):
            normalized_path += "/"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        return urllib.parse.urlunsplit(
            (scheme, serialized_host, normalized_path, parsed.query, "")
        )

    def _is_in_scope(self, url: str) -> bool:
        parsed = urllib.parse.urlsplit(url)
        decoded_path = urllib.parse.unquote(parsed.path, errors="replace")
        scope_path = posixpath.normpath(decoded_path)
        if decoded_path.endswith("/") and not scope_path.endswith("/"):
            scope_path += "/"

        def path_matches(prefix: str) -> bool:
            normalized_prefix = posixpath.normpath(prefix)
            if normalized_prefix == "/":
                return True
            return scope_path == normalized_prefix or scope_path.startswith(
                f"{normalized_prefix.rstrip('/')}/"
            )

        return (
            parsed.hostname is not None
            and parsed.hostname.lower() in self.source.effective_allowed_hosts
            and any(path_matches(prefix) for prefix in self.source.allowed_path_prefixes)
        )

    def _validate_network_target(self, url: str, *, allow_robots_path: bool = False) -> None:
        parsed = urllib.parse.urlsplit(url)
        robots_exception = allow_robots_path and parsed.path == "/robots.txt"
        host_allowed = (
            parsed.hostname is not None
            and parsed.hostname.lower() in self.source.effective_allowed_hosts
        )
        if not self._is_in_scope(url) and not (robots_exception and host_allowed):
            raise ScopeViolation(f"{url} is outside the source allowlist")
        if self.allow_private_networks:
            return
        assert parsed.hostname is not None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = self._resolver(parsed.hostname, port, type=socket.SOCK_STREAM)
        resolved = {item[4][0] for item in addresses}
        if not resolved:
            raise ScopeViolation(f"{parsed.hostname} did not resolve")
        for address in resolved:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ScopeViolation(
                    f"{parsed.hostname} resolves to non-public address {address}"
                )

    def _fetch(
        self,
        initial_url: str,
        response_limit: int,
        *,
        allow_robots_path: bool = False,
    ) -> _FetchResult:
        requested_url = initial_url
        current_url = initial_url
        redirects = 0
        started = time.monotonic()

        while True:
            self._check_common_budgets()
            self._validate_network_target(
                current_url, allow_robots_path=allow_robots_path
            )
            # Policy acquisition uses the same request, byte, duration and redirect
            # budgets, but must not recursively try to load its own robots policy.
            if not allow_robots_path:
                origin = self._origin(current_url)
                policy_cached = origin in self._robots_by_origin
                if not self._robots_can_fetch(current_url):
                    status = self._report.robots_status_by_origin[origin]
                    reason = (
                        "robots-unavailable"
                        if status == "unavailable-fail-closed"
                        else "robots-disallowed"
                    )
                    raise RobotsViolation(current_url, reason)
                # A newly loaded policy can consume the remaining budgets or
                # involve another origin. Recheck before the content request.
                self._check_common_budgets()
                if not policy_cached:
                    self._validate_network_target(current_url)
            self._throttle()
            self._check_common_budgets()
            self._report.requests_sent += 1
            request = urllib.request.Request(
                current_url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                },
                method="GET",
            )
            try:
                response = self._opener.open(request, timeout=self._request_timeout())
            except urllib.error.HTTPError as exc:
                response = exc

            status = int(response.getcode())
            if status in REDIRECT_CODES and response.headers.get("Location"):
                response.close()
                if redirects >= self.limits.max_redirects:
                    raise ScopeViolation("redirect limit reached")
                try:
                    next_url = self._normalize_url(
                        urllib.parse.urljoin(current_url, response.headers["Location"])
                    )
                except CrawlConfigurationError as exc:
                    raise ScopeViolation(str(exc)) from exc
                next_path = urllib.parse.urlsplit(next_url).path
                robots_exception = allow_robots_path and next_path == "/robots.txt"
                if not self._is_in_scope(next_url) and not robots_exception:
                    raise ScopeViolation(self._redact_query(next_url))
                if urllib.parse.urlsplit(next_url).query and not self.allow_query_strings:
                    raise ScopeViolation("redirect contains a disabled query string")
                current_url = next_url
                redirects += 1
                continue

            try:
                headers = response.headers
                content_type = headers.get_content_type()
                content_encoding = (headers.get("Content-Encoding") or "identity").lower()
                if status >= 400:
                    body, outcome, downloaded = b"", "http-error-without-body", 0
                elif content_encoding not in {"", "identity"}:
                    body, outcome, downloaded = b"", "content-encoding-skipped", 0
                elif not allow_robots_path and content_type not in {
                    "text/html",
                    "application/xhtml+xml",
                } | self._document_content_types:
                    body, outcome, downloaded = b"", "content-type-skipped", 0
                else:
                    body, outcome, downloaded = self._read_response(
                        response, response_limit
                    )
            finally:
                response.close()
            return _FetchResult(
                requested_url=requested_url,
                final_url=current_url,
                status=status,
                headers=headers,
                body=body,
                bytes_downloaded=downloaded,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                outcome=outcome,
            )

    def _read_response(
        self, response, response_limit: int
    ) -> tuple[bytes, str, int]:  # type: ignore[no-untyped-def]
        content_length = response.headers.get("Content-Length")
        remaining_total = self.limits.max_total_bytes - self._report.bytes_downloaded
        if remaining_total <= 0:
            raise CrawlBudgetReached("byte-limit")
        if content_length:
            try:
                announced = int(content_length)
            except ValueError:
                announced = 0
            if announced > response_limit:
                return b"", "response-too-large", 0
            if announced > remaining_total:
                raise CrawlBudgetReached("byte-limit")

        maximum = min(response_limit, remaining_total)
        chunks: list[bytes] = []
        received = 0
        while received < maximum:
            if self._elapsed() >= self.limits.max_duration_seconds:
                raise CrawlBudgetReached("duration-limit")
            chunk = response.read(min(64 * 1024, maximum - received))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            self._report.bytes_downloaded += len(chunk)
        if received == remaining_total and remaining_total < response_limit:
            raise CrawlBudgetReached("byte-limit")
        if received == response_limit and not content_length:
            return b"", "response-limit-reached", received
        return b"".join(chunks), "fetched", received

    def _page_from_result(self, result: _FetchResult, depth: int) -> CrawledPage:
        content_type = result.headers.get_content_type() if result.headers else ""
        return CrawledPage(
            requested_url=result.requested_url,
            final_url=result.final_url,
            depth=depth,
            status=result.status,
            content_type=content_type,
            bytes_downloaded=result.bytes_downloaded,
            elapsed_ms=result.elapsed_ms,
            retrieved_at=datetime.now(UTC).isoformat(),
            etag=result.headers.get("ETag"),
            last_modified=result.headers.get("Last-Modified"),
            outcome=result.outcome,
        )

    @staticmethod
    def _is_html(content_type: str) -> bool:
        return content_type in {"text/html", "application/xhtml+xml"}

    @staticmethod
    def _decode_html(body: bytes, headers: object) -> str:
        charset = None
        if hasattr(headers, "get_content_charset"):
            charset = headers.get_content_charset()  # type: ignore[attr-defined]
        return body.decode(charset or "utf-8", errors="replace")

    def _throttle(self) -> None:
        now = time.monotonic()
        if self._last_request_monotonic is not None:
            wait = self._effective_delay_seconds - (now - self._last_request_monotonic)
            if wait > 0:
                if self._elapsed() + wait >= self.limits.max_duration_seconds:
                    raise CrawlBudgetReached("duration-limit")
                time.sleep(wait)
        self._last_request_monotonic = time.monotonic()

    def _request_timeout(self) -> float:
        remaining = self.limits.max_duration_seconds - self._elapsed()
        if remaining <= 0:
            raise CrawlBudgetReached("duration-limit")
        return min(self.limits.request_timeout_seconds, remaining)

    def _check_common_budgets(self) -> None:
        if self._report.requests_sent >= self.limits.max_requests:
            raise CrawlBudgetReached("request-limit")
        if self._report.bytes_downloaded >= self.limits.max_total_bytes:
            raise CrawlBudgetReached("byte-limit")
        if self._elapsed() >= self.limits.max_duration_seconds:
            raise CrawlBudgetReached("duration-limit")

    def _elapsed(self) -> float:
        return time.monotonic() - self._started_monotonic

    def _skip(self, url: str, reason: str, depth: int | None) -> None:
        self._report.skipped.append(
            SkippedUrl(url=self._redact_query(url), reason=reason, depth=depth)
        )

    @staticmethod
    def _redact_query(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        query = "<redacted>" if parsed.query else ""
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, query, "")
        )
