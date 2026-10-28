"""Regression checks for actual official DIR page layouts and crawl boundaries."""
from pathlib import Path
import unittest

from ca_title8.sources import dir

FIXTURES = Path(__file__).parent / "fixtures"


class DirParserTests(unittest.TestCase):
    def fixture(self, name, path):
        return dir.parse_page((FIXTURES / name).read_bytes(), "https://www.dir.ca.gov/" + path)

    def test_root_covers_all_eight_departments(self):
        page = self.fixture("dir_root.html", "counters/t8index.htm")
        self.assertEqual(page.kind, "index")
        self.assertEqual(len(page.children), 8)
        urls = {link.url for link in page.children}
        self.assertIn("https://www.dir.ca.gov/samples/search/querydwc.htm", urls)
        self.assertIn("https://www.dir.ca.gov/samples/search/queryod.htm", urls)
        self.assertFalse(any("Rulemaking" in url for url in urls))

    def test_section_retains_history_without_navigation_or_disclaimer(self):
        page = self.fixture("dir_section.html", "title8/3203.html")
        self.assertEqual((page.kind, page.section), ("section", "3203"))
        self.assertEqual(page.children, [])
        self.assertTrue(page.text.startswith("(a) Effective July 1, 1991"))
        self.assertIn("HISTORY", page.text)
        self.assertIn("operative 7-1-2020", page.text)
        self.assertNotIn("Go Back", page.text)
        self.assertNotIn("no representation or warranty", page.text)
        self.assertNotIn("<script", page.html)

    def test_legacy_article_links_all_sections_without_backlinks(self):
        page = self.fixture("dir_article.html", "title8/sb7g16a107.html")
        self.assertEqual(page.kind, "index")
        self.assertEqual(len(page.children), 29)
        self.assertTrue(all("/title8/" in link.url for link in page.children))
        self.assertFalse(any("query.htm" in link.url for link in page.children))

    def test_mandatory_appendix_and_alternate_t8_path(self):
        page = self.fixture("dir_appendix.html", "title8/5144a.html")
        self.assertEqual(page.kind, "appendix")
        self.assertIn("Fit Testing Procedures", page.title)
        self.assertIn("OSHA-Accepted Fit Test Protocols", page.text)
        self.assertIn("Register 2024", page.text)
        other = self.fixture("dir_t8_appendix.html", "t8/17000_Appendix_A.html")
        self.assertEqual(other.kind, "appendix")
        self.assertIn("Disclosure Categories", other.text)

    def test_appendix_heading_below_subchapter_heading(self):
        data = b'<html><body><div class="t8_content"><h1>Subchapter 4. Construction</h1><h2>Appendix A. Table</h2><p>Required values:</p><table><tr><td>One</td><td>Two</td></tr><tr><td>Three</td><td>Four</td></tr></table><img src="img/a.png" alt="Diagram"><p><a href="3203.html">3203</a></p><p><a href="sub4.html">Go Back to Subchapter 4</a></p></div></body></html>'
        page = dir.parse_page(data, "https://www.dir.ca.gov/title8/sub4_A.html")
        self.assertEqual(page.kind, "appendix")
        self.assertEqual(page.title, "Appendix A. Table")
        self.assertIn("One | Two", page.text)
        self.assertIn("Three | Four", page.text)
        self.assertIn("<table>", page.html)
        self.assertEqual(page.images[0].url, "https://www.dir.ca.gov/title8/img/a.png")
        self.assertEqual([link.url for link in page.references], ["https://www.dir.ca.gov/title8/3203.html"])
        self.assertEqual(page.children, [])

    def test_repealed_terminal_index_is_retained(self):
        data = b'<html><body><div class="t8_content"><div>Agency disclaimer</div><h1>Subchapter 8. Safety Rules [Repealed]</h1><p>Repealed effective 1991.</p><a href="/samples/search/query.htm">Back to Title 8</a></div></body></html>'
        page = dir.parse_page(data, "https://www.dir.ca.gov/title8/sub8.html")
        self.assertEqual(page.kind, "section")
        self.assertIn("Repealed effective 1991", page.text)
        self.assertNotIn("Agency disclaimer", page.text)
        self.assertNotIn("Back to Title 8", page.text)

    def test_scope_is_bounded_but_accepts_case_and_nonnumeric_entries(self):
        self.assertTrue(dir.in_scope("https://dir.ca.gov/Title8/sub4_A.html"))
        self.assertTrue(dir.in_scope("https://www.dir.ca.gov/t8/17000_Appendix_A.html"))
        self.assertFalse(dir.in_scope("https://evil.example/title8/3203.html"))
        self.assertFalse(dir.in_scope("https://www.dir.ca.gov/DIRRulemaking.html"))
        self.assertFalse(dir.in_scope("https://www.dir.ca.gov/title8/index/T8index.asp"))
        self.assertFalse(dir.in_scope("https://www.dir.ca.gov/title8/file.pdf"))

    def test_unknown_pages_and_empty_entries_fail_closed(self):
        for data in [b'<html><h1>Access Denied</h1></html>', b'<html><h1>Section 9999. Unexpected empty section</h1></html>']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                dir.parse_page(data, "https://www.dir.ca.gov/title8/9999.html")
