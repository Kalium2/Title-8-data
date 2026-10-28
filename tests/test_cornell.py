import unittest

from ca_title8.sources.cornell import ROOT_URL, in_scope, parse_page


def page(title, active, compare=""):
    return (
        '<html><head><meta charset="utf-8"></head><body>'
        f'<h1 id="page_title">{title}</h1>'
        f'<div id="tab_default_1">{active}</div>'
        f'<div id="tab_point_in-time">{compare}</div></body></html>'
    ).encode()


class CornellTests(unittest.TestCase):
    def test_active_toc_only_deduplicates_and_keeps_appendices(self):
        data = page(
            "Title 8 - Industrial Relations",
            '<div class="toc"><ol>'
            '<li><a href="/regulations/california/title-8/division-1">Division 1</a></li>'
            '<li><a href="/regulations/california/title-8/division-1">Duplicate</a></li>'
            '<li><a href="/regulations/california/8-CCR-5194-Appendix-A">Appendix A</a></li>'
            '<li><a href="/regulations/california/title-9">Other title</a></li>'
            '</ol></div>',
            '<div class="toc"><a href="/regulations/california/8-CCR-99999">Old</a></div>',
        )
        parsed = parse_page(data, ROOT_URL)
        self.assertEqual(parsed.kind, "index")
        self.assertEqual([x.label for x in parsed.children], ["Division 1", "Appendix A"])

    def test_body_notes_tables_and_images_preserved_without_crossref_crawl(self):
        url = "https://www.law.cornell.edu/regulations/california/8-CCR-3203"
        data = page(
            "Cal. Code Regs. Tit. 8, § 3203 - Program",
            '<div class="statereg-text"><div>(a) Establish a <b>written</b> plan.</div>'
            '<div>(1) See <a href="8-CCR-3204">3204</a>.</div>'
            '<table><tr><th>Item</th><th>Limit</th></tr><tr><td>A</td><td>10</td></tr></table>'
            '<img src="/images/formula.gif" alt="Formula: x + y">'
            '<script>evil()</script></div><div class="statereg-notes">'
            '<h2>Notes</h2><div>Authority: section 6401.7.</div></div>',
            '<div class="statereg-text">Obsolete text.</div>',
        )
        parsed = parse_page(data, url)
        self.assertEqual(parsed.section, "3203")
        self.assertEqual(parsed.kind, "section")
        self.assertIn("a written plan", parsed.text)
        self.assertIn("Item\tLimit", parsed.text)
        self.assertIn("A\t10", parsed.text)
        self.assertIn("Formula: x + y", parsed.text)
        self.assertNotIn("evil()", parsed.text)
        self.assertNotIn("Obsolete", parsed.text)
        self.assertEqual(parsed.children, [])
        self.assertEqual(parsed.references[0].url, "https://www.law.cornell.edu/regulations/california/8-CCR-3204")
        self.assertEqual(parsed.images[0].url, "https://www.law.cornell.edu/images/formula.gif")
        self.assertIn("https://www.law.cornell.edu/images/formula.gif", parsed.html)
        self.assertIn("Authority: section 6401.7", parsed.notes)

    def test_repealed_empty_section_is_kept(self):
        data = page("Cal. Code Regs. Tit. 8, § 123 - [Repealed]", '<div class="statereg-text"></div>')
        parsed = parse_page(data, "https://www.law.cornell.edu/regulations/california/8-CCR-123")
        self.assertEqual(parsed.kind, "section")
        self.assertEqual(parsed.section, "123")

    def test_abbreviated_app_heading_is_an_appendix(self):
        data = page(
            "Cal. Code Regs. Tit. 8, § 5144 app A - Fit Testing Procedures (Mandatory)",
            '<div class="statereg-text"><div>A. Fit Testing Procedures</div></div>',
        )
        parsed = parse_page(data, "https://www.law.cornell.edu/regulations/california/8-CCR-5144-app-A")
        self.assertEqual(parsed.kind, "appendix")

    def test_empty_body_and_block_page_are_rejected(self):
        for data in [b"<h1>Access denied</h1>", page("§ 123 - Rule", '<div class="statereg-text"></div>')]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_page(data, "https://www.law.cornell.edu/regulations/california/8-CCR-123")

    def test_scope_exact_title_and_sections(self):
        for url in [ROOT_URL, ROOT_URL + "/division-3", "https://www.law.cornell.edu/regulations/california/8-CCR-5194-Appendix-A"]:
            with self.subTest(url=url):
                self.assertTrue(in_scope(url))
        for url in [ROOT_URL + "0", ROOT_URL.replace("www.law", "www.evil"), "https://www.law.cornell.edu/regulations/california/9-CCR-1", "ftp://www.law.cornell.edu/regulations/california/title-8"]:
            with self.subTest(url=url):
                self.assertFalse(in_scope(url))


if __name__ == "__main__":
    unittest.main()
