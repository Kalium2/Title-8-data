"""Single-worker, rate-limited HTTP with disk caching and robots checks."""

import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from .models import FetchedPage

LOG = logging.getLogger(__name__)
USER_AGENT = "CATitle8Scraper/1.0 (+local research data export)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/") or "/"
    if host in {"dir.ca.gov", "www.dir.ca.gov"}:
        host = "www.dir.ca.gov"
        path = path.lower()
    return urlunsplit(("https", host, path, "", ""))


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Cache:
    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, url: str) -> Path:
        key = hashlib.sha256(canonical_url(url).encode()).hexdigest()
        return self.directory / (key + ".json")

    def get(self, url: str) -> FetchedPage | None:
        try:
            value = json.loads(self.path(url).read_text(encoding="utf-8"))
            fields = {"url", "final_url", "fetched_at", "content_type", "sha256", "body_base64"}
            if not isinstance(value, dict) or set(value) != fields:
                return None
            if any(not isinstance(value[key], str) for key in fields):
                return None
            data = base64.b64decode(value.pop("body_base64"), validate=True)
            if value["sha256"] != hashlib.sha256(data).hexdigest():
                return None
            if canonical_url(value["url"]) != canonical_url(url):
                return None
            canonical_url(value["final_url"])  # Also reject malformed redirect metadata.
            return FetchedPage(**value, data=data)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, page: FetchedPage) -> None:
        value = {k: v for k, v in vars(page).items() if k != "data"}
        value["body_base64"] = base64.b64encode(page.data).decode("ascii")
        atomic_json(self.path(page.url), value)


class ScopedRedirects(HTTPRedirectHandler):
    def __init__(self, allowed, before_request=None):
        super().__init__()
        self.allowed = allowed
        self.before_request = before_request

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allowed(newurl):
            raise ValueError(f"Redirect outside source scope or robots policy: {newurl}")
        if self.before_request:
            self.before_request()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpClient:
    def __init__(self, source, cache: Cache, *, delay=1.0, timeout=30.0,
                 retries=3, refresh=False, user_agent=USER_AGENT):
        self.source = source
        self.cache = cache
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.refresh = refresh
        self.user_agent = user_agent
        self._last_request = None
        self._robots = None
        self._opener = build_opener(ScopedRedirects(self._allowed_url, self._wait))

    def _allowed_url(self, url):
        parts = urlsplit(url)
        root = urlsplit(self.source.ROOT_URL)
        return (parts.scheme == "https"
                and parts.netloc.lower() == root.netloc.lower()
                and (parts.path == "/robots.txt" or (
                    self.source.in_scope(url) and (
                        self._robots is None or self._robots.can_fetch(self.user_agent, url)))))

    def _wait(self):
        if self._last_request is not None:
            remaining = self.delay - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request = time.monotonic()

    @staticmethod
    def _retry_after(error, fallback):
        value = error.headers.get("Retry-After") if error.headers else None
        if value:
            try:
                return max(fallback, float(value))
            except ValueError:
                try:
                    date = parsedate_to_datetime(value)
                    return max(fallback, date.timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
        return fallback

    def _download(self, url: str) -> FetchedPage:
        for attempt in range(self.retries + 1):
            try:
                self._wait()
                request = Request(url, headers={"User-Agent": self.user_agent,
                                              "Accept": "text/html,text/plain;q=0.9"})
                with self._opener.open(request, timeout=self.timeout) as response:
                    data = response.read()
                    final_url = response.geturl()
                    if not self._allowed_url(final_url):
                        raise ValueError(f"Response leaves source scope: {final_url}")
                    return FetchedPage(url, final_url, utc_now(),
                                       response.headers.get_content_type(),
                                       hashlib.sha256(data).hexdigest(), data)
            except HTTPError as error:
                retryable = error.code in {408, 429, 500, 502, 503, 504}
                wait = self._retry_after(error, 2 ** attempt)
                error.close()
                if not retryable or attempt == self.retries:
                    raise
            except (URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise
                wait = 2 ** attempt
            LOG.warning("Retrying %s in %.1fs", url, wait)
            time.sleep(wait)
        raise AssertionError("unreachable")

    def _load_robots(self):
        root = urlsplit(self.source.ROOT_URL)
        url = urlunsplit((root.scheme, root.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser(url)
        try:
            response = self._download(url)
            parser.parse(response.data.decode("utf-8", errors="replace").splitlines())
        except HTTPError as error:
            if error.code == 404:
                parser.parse([])
            else:
                raise RuntimeError(f"Could not verify robots.txt: HTTP {error.code}") from error
        self._robots = parser
        advertised_delay = parser.crawl_delay(self.user_agent)
        if advertised_delay:
            self.delay = max(self.delay, advertised_delay)
        rate = parser.request_rate(self.user_agent)
        if rate and rate.requests:
            self.delay = max(self.delay, rate.seconds / rate.requests)
        LOG.info("Minimum request interval: %.1fs (including robots.txt policy)", self.delay)

    def fetch(self, url: str) -> FetchedPage:
        url = canonical_url(url)
        if not self.source.in_scope(url):
            raise ValueError(f"URL is outside source scope: {url}")
        if not self.refresh:
            cached = self.cache.get(url)
            if cached is not None and self._allowed_url(cached.final_url):
                return cached
        if self._robots is None:
            self._load_robots()
        if not self._robots.can_fetch(self.user_agent, url):
            raise PermissionError(f"robots.txt disallows {url}")
        page = self._download(url)
        allowed_types = getattr(self.source, "CONTENT_TYPES", {"text/html", "application/xhtml+xml"})
        if page.content_type not in allowed_types:
            raise ValueError(f"Unsupported source content type {page.content_type}: {url}")
        self.cache.put(page)
        return page
