"""Offline checks for traversal, checkpoint output, caching, and HTTP policy."""

import hashlib
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser
from urllib.request import Request

from ca_title8.crawler import crawl
from ca_title8.http import Cache, HttpClient, ScopedRedirects
from ca_title8.models import FetchedPage, Link, ParsedPage


ROOT = 'https://example.test/title8'


def fetched(url=ROOT, data=b'example body', final_url=None):
    return FetchedPage(url, final_url or url, '2026-10-28T00:00:00+00:00',
                       'text/html', hashlib.sha256(data).hexdigest(), data)


def source(pages=None):
    pages = pages or {}
    return SimpleNamespace(
        ROOT_URL=ROOT,
        SOURCE_NAME='Fixture',
        SOURCE_SCOPE='fixture scope',
        FRESHNESS_NOTE='fixture snapshot',
        in_scope=lambda url: url == ROOT or url.startswith(ROOT + '/'),
        parse_page=lambda data, url: pages[url],
    )


class CrawlTests(unittest.TestCase):
    def test_deduplicates_links_and_redirects_but_keeps_hierarchy(self):
        pages = {
            ROOT: ParsedPage('Title 8', 'index', children=[
                Link(ROOT + '/division', 'Division'),
                Link(ROOT + '/division#repeat', 'Duplicate division')]),
            ROOT + '/division': ParsedPage('Division 1', 'index', children=[
                Link(ROOT + '/2', 'Second'), Link(ROOT + '/1', 'First'),
                Link(ROOT + '/alias', 'Alias'), Link(ROOT, 'Back to root')]),
            ROOT + '/1': ParsedPage('First', 'section', section='1', text='One.'),
            ROOT + '/2': ParsedPage('Second', 'section', section='2', text='Two.'),
        }
        client = Mock()
        client.fetch.side_effect = lambda url: fetched(
            url, final_url=ROOT + '/1' if url.endswith('/alias') else url)
        result = crawl(source(pages), client)
        self.assertEqual([row['section'] for row in result['entries']], ['1', '2'])
        self.assertEqual(client.fetch.call_count, 5)
        self.assertEqual(result['metadata']['pages_discovered'], 5)
        self.assertTrue(result['metadata']['crawl_complete'])
        self.assertTrue(result['metadata']['covers_full_source_tree'])
        self.assertEqual([part['title'] for part in result['entries'][0]['hierarchy']],
                         ['Title 8', 'Division 1'])

    def test_fetch_failure_is_reported_and_siblings_still_run(self):
        pages = {
            ROOT: ParsedPage('Title 8', 'index', children=[
                Link(ROOT + '/broken', 'Missing'), Link(ROOT + '/good', 'Good')]),
            ROOT + '/good': ParsedPage('Good', 'section', section='2', text='Body'),
        }
        client = Mock()
        def fetch(url):
            if url.endswith('/broken'):
                raise TimeoutError('network timed out')
            return fetched(url)
        client.fetch.side_effect = fetch
        result = crawl(source(pages), client)
        self.assertEqual(result['metadata']['entry_count'], 1)
        self.assertEqual(result['metadata']['error_count'], 1)
        self.assertFalse(result['metadata']['crawl_complete'])
        self.assertFalse(result['metadata']['covers_full_source_tree'])
        self.assertEqual(result['errors'][0]['url'], ROOT + '/broken')
        self.assertEqual(result['unvisited_urls'], [])

    def test_page_limit_exposes_unvisited_urls_and_marks_partial(self):
        pages = {ROOT: ParsedPage('Title 8', 'index', children=[
            Link(ROOT + '/1', 'One'), Link(ROOT + '/2', 'Two')])}
        client = Mock()
        client.fetch.side_effect = fetched
        result = crawl(source(pages), client, max_pages=1)
        self.assertEqual(client.fetch.call_count, 1)
        self.assertFalse(result['metadata']['crawl_complete'])
        self.assertEqual(result['unvisited_urls'], [ROOT + '/1', ROOT + '/2'])
        self.assertEqual(result['metadata']['pages_discovered'], 3)

    def test_subtree_completion_does_not_claim_full_source_coverage(self):
        url = ROOT + '/1'
        pages = {url: ParsedPage('One', 'section', section='1', text='Body', html='<p>Body</p>')}
        client = Mock()
        client.fetch.side_effect = fetched
        result = crawl(source(pages), client, start_urls=[url], include_html=True)
        self.assertTrue(result['metadata']['crawl_complete'])
        self.assertFalse(result['metadata']['covers_full_source_tree'])
        self.assertEqual(result['entries'][0]['html'], '<p>Body</p>')

    def test_interruption_preserves_current_page_for_resumption(self):
        client = Mock()
        client.fetch.side_effect = KeyboardInterrupt()
        result = crawl(source(), client)
        self.assertTrue(result['metadata']['interrupted'])
        self.assertFalse(result['metadata']['crawl_complete'])
        self.assertEqual(result['unvisited_urls'], [ROOT])
        self.assertEqual(result['metadata']['pages_processed'], 0)

    def test_unknown_empty_index_is_an_error_but_repealed_index_is_valid(self):
        for title, expected_errors in [('Unexpected empty index', 1), ('Article [Repealed]', 0)]:
            with self.subTest(title=title):
                client = Mock()
                client.fetch.side_effect = fetched
                result = crawl(source({ROOT: ParsedPage(title, 'index')}), client)
                self.assertEqual(result['metadata']['error_count'], expected_errors)

    def test_empty_repealed_section_is_preserved_but_unlabelled_empty_body_fails(self):
        for title, expected_errors in [('§ 123 - [Repealed]', 0), ('§ 123 - Rule', 1)]:
            with self.subTest(title=title):
                client = Mock()
                client.fetch.side_effect = fetched
                result = crawl(source({ROOT: ParsedPage(title, 'section', section='123')}), client)
                self.assertEqual(result['metadata']['error_count'], expected_errors)
                if not expected_errors:
                    self.assertEqual(result['entries'][0]['status'], 'repealed')


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Cache(Path(self.temp.name))

    def test_roundtrip_and_dir_alias_share_cache_key(self):
        page = fetched('https://www.dir.ca.gov/title8/3203.html')
        self.cache.put(page)
        self.assertEqual(self.cache.get(page.url), page)
        self.assertEqual(self.cache.path(page.url),
                         self.cache.path('http://DIR.ca.gov/Title8/3203.html#section'))

    def test_corrupt_payload_or_hash_is_a_cache_miss(self):
        page = fetched()
        for mutation in [lambda value: value.update(sha256='bad hash'),
                         lambda value: value.update(body_base64='bad base64!'),
                         lambda value: value.update(url=ROOT + '/different'),
                         lambda value: value.pop('fetched_at')]:
            with self.subTest(mutation=mutation):
                self.cache.put(page)
                path = self.cache.path(ROOT)
                value = json.loads(path.read_text())
                mutation(value)
                path.write_text(json.dumps(value))
                self.assertIsNone(self.cache.get(ROOT))
        self.cache.path(ROOT).write_text('this is not JSON')
        self.assertIsNone(self.cache.get(ROOT))

    def test_malformed_cache_url_is_a_cache_miss(self):
        for field in ['url', 'final_url']:
            with self.subTest(field=field):
                self.cache.put(fetched())
                path = self.cache.path(ROOT)
                value = json.loads(path.read_text())
                value[field] = 42
                path.write_text(json.dumps(value))
                self.assertIsNone(self.cache.get(ROOT))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Cache(Path(self.temp.name))
        self.client = HttpClient(source(), self.cache, delay=0, retries=1)

    def robots(self, lines):
        self.client._download = Mock(return_value=fetched(
            'https://example.test/robots.txt', data=lines.encode()))
        self.client._load_robots()

    def test_robots_applies_stricter_delay_and_request_rate(self):
        self.client.delay = 1
        self.robots('User-agent: *\nCrawl-delay: 3\nRequest-rate: 1/5\nAllow: /\n')
        self.assertEqual(self.client.delay, 5)

    def test_robots_disallow_prevents_page_request(self):
        self.robots('User-agent: *\nDisallow: /title8/private\n')
        self.client._download.reset_mock()
        with self.assertRaises(PermissionError):
            self.client.fetch(ROOT + '/private')
        self.client._download.assert_not_called()

    def test_robots_404_allows_fetch_but_403_fails_closed(self):
        for status, should_fail in [(404, False), (403, True)]:
            with self.subTest(status=status):
                client = HttpClient(source(), self.cache, delay=0)
                error = HTTPError('https://example.test/robots.txt', status, 'error', {}, None)
                client._download = Mock(side_effect=[error, fetched()])
                if should_fail:
                    with self.assertRaises(RuntimeError):
                        client.fetch(ROOT)
                    self.assertEqual(client._download.call_count, 1)
                else:
                    self.assertEqual(client.fetch(ROOT), fetched())
                    self.assertEqual(client._download.call_count, 2)
                self.cache.path(ROOT).unlink(missing_ok=True)

    def test_redirect_to_robots_forbidden_path_is_rejected(self):
        self.robots('User-agent: *\nDisallow: /title8/private\n')
        handler = next(item for item in self.client._opener.handlers
                       if isinstance(item, ScopedRedirects))
        with self.assertRaises((PermissionError, ValueError)):
            handler.redirect_request(Request(ROOT), None, 302, 'Found', {},
                                     ROOT + '/private')

    def test_cached_page_is_read_without_network(self):
        page = fetched()
        self.cache.put(page)
        self.client._opener = Mock()
        self.assertEqual(self.client.fetch(ROOT), page)
        self.client._opener.open.assert_not_called()

    def test_429_honors_retry_after_and_retries_successfully(self):
        headers = Message()
        headers['Retry-After'] = '7'
        error = HTTPError(ROOT, 429, 'rate limited', headers, None)
        response = Mock()
        response.read.return_value = b'<html>body</html>'
        response.geturl.return_value = ROOT
        response.headers.get_content_type.return_value = 'text/html'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        self.client._opener = Mock()
        self.client._opener.open.side_effect = [error, response]
        with patch('ca_title8.http.time.sleep') as sleep, patch.object(self.client, '_wait'):
            page = self.client._download(ROOT)
        self.assertEqual(page.data, b'<html>body</html>')
        sleep.assert_called_once_with(7.0)
        self.assertEqual(self.client._opener.open.call_count, 2)

    def test_404_is_not_retried(self):
        self.client._opener = Mock()
        self.client._opener.open.side_effect = HTTPError(ROOT, 404, 'missing', {}, None)
        with patch('ca_title8.http.time.sleep') as sleep, patch.object(self.client, '_wait'):
            with self.assertRaises(HTTPError):
                self.client._download(ROOT)
        self.client._opener.open.assert_called_once()
        sleep.assert_not_called()

    def test_non_html_and_out_of_scope_requests_are_rejected(self):
        self.client._robots = RobotFileParser()
        self.client._robots.parse(['User-agent: *', 'Allow: /'])
        self.client._download = Mock(return_value=FetchedPage(
            ROOT, ROOT, 'now', 'application/pdf', 'hash', b'%PDF'))
        with self.assertRaises(ValueError):
            self.client.fetch(ROOT)
        with self.assertRaises(ValueError):
            self.client.fetch('https://unrelated.test/')
        self.assertIsNone(self.cache.get(ROOT))


if __name__ == '__main__':
    unittest.main()
