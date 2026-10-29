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
        self.assertEqual(page.kind, "index")
        self.assertIn("Repealed effective 1991", page.text)
        self.assertNotIn("Agency disclaimer", page.text)
        self.assertNotIn("Back to Title 8", page.text)

    def test_repeal_in_history_marks_terminal_subchapter_without_heading_label(self):
        for notice in [
            'HISTORY 1. Repealer of Subchapter 8 filed 9-14-71.',
            'Repeal of this set of orders, containing Sections 8100 through 8114, was filed 8-29-57.',
            'Ed. Note. Subchapter 11 renumbered as Subchapter 13. Repeal of this set of orders was filed 12-19-49.',
        ]:
            data = ('<html><body><div class="t8_content"><div>Agency disclaimer</div>'
                    '<h1>Subchapter 8. Safety Rules</h1><p>' + notice + '</p>'
                    '<a href="/samples/search/query.htm">Go Back to Table of Contents</a>'
                    '</div></body></html>').encode()
            with self.subTest(notice=notice):
                page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/sub8.html')
                self.assertEqual(page.kind, 'index')
                self.assertEqual(page.children, [])
                self.assertTrue(page.title.endswith('[Repealed]'))
                self.assertIn(notice, page.text)
                self.assertNotIn('Agency disclaimer', page.text)

    def test_h5_and_combined_subchapter_appendix_headings_are_entries(self):
        for heading, path, expected in [
            ('<h1>Subchapter 4. Construction Safety Orders</h1>'
             '<h5>Appendix C: Helpful Construction Methods</h5>', 'sub4_c.html', 'Appendix C:'),
            ('<h1>Subchapter 2. Boiler Orders<br><br>Appendix 1</h1>', 'sb2ap1.html', 'Appendix 1'),
        ]:
            data = ('<html><body><div class="t8_content">' + heading +
                    '<p>Required construction details.</p><img src="plate.gif" alt="Plate C-1">'
                    '</div></body></html>').encode()
            with self.subTest(path=path):
                page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/' + path)
                self.assertEqual(page.kind, 'appendix')
                self.assertIn(expected, page.title)
                self.assertIn('Required construction details.', page.text)
                self.assertEqual(len(page.images), 1)

    def test_empty_terminal_toc_without_repeal_notice_is_rejected(self):
        for text in ['Temporarily unavailable.', 'HISTORY: Repealer of subsection (a) filed 1991.']:
            data = ('<html><body><h1>Subchapter 8. Safety Rules</h1><p>' + text + '</p></body></html>').encode()
            with self.subTest(text=text), self.assertRaises(ValueError):
                dir.parse_page(data, 'https://www.dir.ca.gov/title8/sub8.html')

    def test_legacy_appendices_with_copied_parent_heading_use_own_identity(self):
        for suffix, document_title, label in [
            ('a', 'California Code of Regulations, Title 8, Section Appendix A to Section 340.4-Sample Letter', ''),
            ('b', 'Untitled Document', 'Appendix B to Section 340.4-Sample Plan'),
            ('c', 'Untitled Document', 'Appendix C to Section 340.4-Sample Tag'),
        ]:
            data = ('<html><head><title>' + document_title + '</title></head><body>'
                    '<div class="t8_content"><h1>Section 340.4. Declaration of Abatement</h1>'
                    '<p><strong>' + label + '</strong></p><p>Required form content.</p>'
                    '</div></body></html>').encode()
            with self.subTest(suffix=suffix):
                page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/340_4' + suffix + '.html')
                self.assertEqual(page.kind, 'appendix')
                self.assertEqual(page.section, '340.4')
                self.assertTrue(page.title.startswith('Appendix ' + suffix.upper()))
                self.assertIn('Required form content.', page.text)
        ordinary = b'<html><head><title>Section 340.4. Appendix References</title></head><body><h1>Section 340.4. Declaration</h1><p>Appendix A contains the form.</p></body></html>'
        page = dir.parse_page(ordinary, 'https://www.dir.ca.gov/title8/340_4.html')
        self.assertEqual(page.kind, 'section')

    def test_scope_is_bounded_but_accepts_case_and_nonnumeric_entries(self):
        self.assertTrue(dir.in_scope("https://dir.ca.gov/Title8/sub4_A.html"))
        self.assertTrue(dir.in_scope("https://www.dir.ca.gov/t8/17000_Appendix_A.html"))
        self.assertFalse(dir.in_scope("https://evil.example/title8/3203.html"))
        self.assertFalse(dir.in_scope("https://www.dir.ca.gov/DIRRulemaking.html"))
        self.assertFalse(dir.in_scope("https://www.dir.ca.gov/title8/index/T8index.asp"))
        self.assertFalse(dir.in_scope("https://www.dir.ca.gov/title8/file.pdf"))

    def test_recordkeeping_appendix_allowlist_keeps_all_seven_toc_links(self):
        links = ''.join('<li><a href="' + path + '">' + label + '</a></li>'
                        for path, label in dir.SUPPLEMENTAL_APPENDICES.items())
        data = ('<html><body><div class="t8_content"><h1>Article 2. Employer Records</h1>'
                '<ul>' + links + '<li><a href="/DOSH/DoshReg/unrelated.pdf">Other guidance</a></li>'
                '</ul></div></body></html>').encode()
        page = dir.parse_page(data, 'https://www.dir.ca.gov/t8/ch7sb1a2.html')
        self.assertEqual(page.kind, 'index')
        self.assertEqual(len(page.children), 7)
        self.assertTrue(all(dir.in_scope(link.url) for link in page.children))
        self.assertEqual(sum(link.url.endswith('.pdf') for link in page.children), 4)
        self.assertFalse(dir.in_scope('https://www.dir.ca.gov/DOSH/DoshReg/unrelated.pdf'))
        self.assertFalse(dir.in_scope('https://www.dir.ca.gov/DOSH/DoshReg/not-a-regulation.htm'))

    def test_recordkeeping_html_appendix_uses_modern_main_content(self):
        for path in ['AppndxDFinal.htm', 'ApndxEFinal.htm', 'ApndxFFinal.htm']:
            data = ('<html><body><nav>Site navigation</nav><main><h1>Appendix D - Required Elements</h1>'
                    '<p>I. Employers may use an equivalent form.</p><p>NOTE: Authority cited: Section 6410.</p>'
                    '</main><footer>About DIR</footer></body></html>').encode()
            with self.subTest(path=path):
                page = dir.parse_page(data, 'https://www.dir.ca.gov/DOSH/DoshReg/' + path)
                self.assertEqual(page.kind, 'appendix')
                self.assertIn('Employers may use an equivalent form.', page.text)
                self.assertIn('Authority cited: Section 6410', page.text)
                self.assertNotIn('Site navigation', page.text)
                self.assertNotIn('About DIR', page.text)

    def test_bare_numbered_legacy_heading_matches_the_page_identifier(self):
        data = b'<html><body><h1>3317. Illumination.</h1><p>(a) Required illumination.</p></body></html>'
        page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/3317.html')
        self.assertEqual((page.kind, page.section), ('section', '3317'))
        self.assertEqual(page.title, '3317. Illumination.')
        self.assertIn('(a) Required illumination.', page.text)
        with self.assertRaises(ValueError):
            dir.parse_page(data, 'https://www.dir.ca.gov/title8/3318.html')

    def test_plain_appendix_label_preserves_content_outside_legacy_wrapper(self):
        data = b'<html><body><div class="t8_content"><p>Agency disclaimer</p><h1>Subchapter 18. Safety Orders</h1><p>Appendix A</p></div><div class="co_body"><p>Required safe practices.</p><p>HISTORY: New appendix filed 1988.</p></div></body></html>'
        page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/sb18a9_appa.html')
        self.assertEqual(page.kind, 'appendix')
        self.assertEqual(page.title, 'Appendix A')
        self.assertIn('Required safe practices.', page.text)
        self.assertIn('HISTORY: New appendix filed 1988.', page.text)
        self.assertNotIn('Agency disclaimer', page.text)

    def test_unclosed_heading_does_not_swallow_body_or_become_an_appendix(self):
        data = b'<html><body><h5>Section 5193. Bloodborne Pathogens.<ul><li><a href="guide.pdf">Guide</a></li></ul><div class="co_body"><p>(a) Required precautions.</p><strong>Appendix A - Declination</strong><p>Required statement.</p></div></body></html>'
        page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/5193.html')
        self.assertEqual((page.kind, page.section), ('section', '5193'))
        self.assertEqual(page.title, 'Section 5193. Bloodborne Pathogens.')
        self.assertIn('(a) Required precautions.', page.text)
        self.assertIn('Required statement.', page.text)

    def test_preformatted_appendix_heading_retains_the_following_form(self):
        data = b'<html><body><h1><pre>Section 5157. Confined Spaces, <strong>Appendix D-1. Entry Permit</strong>\nJob site: __________\n1. Atmospheric checks: __________\n</pre></h1></body></html>'
        page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/5157d1.html')
        self.assertEqual(page.kind, 'appendix')
        self.assertEqual(page.title, 'Appendix D-1. Entry Permit')
        self.assertIn('Job site:', page.text)
        self.assertIn('Atmospheric checks:', page.text)

    def test_figures_tables_and_annotations_are_named_source_attachments(self):
        for title, filename, attachment_type in [
            ('Figures IT-2 through IT-10', 'fig-it-2-it-10.html', 'figure'),
            ('Table S-1 Rated Capacity', 'sb7g13a101-table-s-1.html', 'table'),
            ('8 CA ADC T. 8, Div. 1, Chap. 4, Refs &amp; Annos.', 'sb5g2a15-refannos.html', 'reference annotations'),
        ]:
            data = ('<html><body><h1>' + title + '</h1><p>Source context.</p>'
                    '<img src="diagram.png" alt="Published illustration"></body></html>').encode()
            with self.subTest(title=title):
                page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/' + filename)
                self.assertEqual(page.kind, 'appendix')
                self.assertIsNone(page.section)
                self.assertIn('Source attachment type: ' + attachment_type, page.notes)
                self.assertEqual(len(page.images), 1)

    def test_table_of_contents_and_article_annotation_titles_remain_indexes(self):
        for title in ['Table of contents', 'Article 37. Confined Spaces (Refs &amp; Annos)']:
            data = ('<html><body><h1>' + title + '</h1><p><a href="3317.html">Section 3317</a></p>'
                    '</body></html>').encode()
            page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/sb4a37.html')
            self.assertEqual(page.kind, 'index')
            self.assertEqual(len(page.children), 1)

    def test_image_only_appendix_plates_and_addendum_do_not_duplicate_sections(self):
        data = b'<html><body><h1>Section 3326. Servicing Wheels.</h1><img src="chart.png" alt="Image 1 within Appendix A"></body></html>'
        page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/3326a.html')
        self.assertEqual(page.kind, 'appendix')
        self.assertEqual(page.title, 'Appendix A to Section 3326')
        self.assertEqual(len(page.images), 1)
        for title, filename in [('Section 4885. Plates.', '4885.plates.html'),
                                ('Section 5214. Inorganic Arsenic, Addendum to Section 5214', '5214ad.html')]:
            data = ('<html><body><h1>' + title + '</h1><p>Source attachment content.</p></body></html>').encode()
            page = dir.parse_page(data, 'https://www.dir.ca.gov/title8/' + filename)
            self.assertEqual(page.kind, 'appendix')
            self.assertIsNone(page.section)

    def test_unknown_pages_and_empty_entries_fail_closed(self):
        for data in [b'<html><h1>Access Denied</h1></html>', b'<html><h1>Section 9999. Unexpected empty section</h1></html>']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                dir.parse_page(data, "https://www.dir.ca.gov/title8/9999.html")
