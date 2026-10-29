import json
from pathlib import Path
import unittest

from ca_title8.citations import build_citation_export


def sub(text, children="", anchor=""):
    return ('<div class="co_contentBlock co_subsection"><div class="co_paragraph">'
            f'<div class="co_paragraphText"><a id="{anchor}"></a>{text}</div></div>{children}</div>')


def export(body, section="4999", *, appendix=False, legacy=False):
    markup = body if legacy else '<div class="co_section"><div class="co_body">' + body + '</div><h2>History</h2><div class="co_paragraphText">1. Amendment filed.</div></div>'
    return {"metadata": {"crawl_complete": True, "covers_full_source_tree": True},
            "entries": [{"id": 'https://www.dir.ca.gov/title8/' + section + '.html',
                         "url": 'https://www.dir.ca.gov/title8/' + section + '.html',
                         "section": section, "title": 'Handling Loads', "kind": 'appendix' if appendix else 'section',
                         "text": 'Full original source text including history.', "html": markup,
                         "fetched_at": "2026-10-28T00:00:00+00:00", "source_sha256": "abc"}],
            "errors": [], "unvisited_urls": []}


class CitationTests(unittest.TestCase):
    def by_name(self, result):
        return {c['citation']: c for c in result['citations']}

    def test_direct_text_parent_chain_and_actual_anchor(self):
        body = sub('(a) Riggers shall be qualified.', anchor='a') + sub('(b) Size of load.', sub('(1) Determine weight.', sub('(A) Weigh it.', anchor='b1A'), 'b1'), 'b')
        result = build_citation_export(export(body))
        rows = self.by_name(result)
        self.assertTrue(result['metadata']['citation_extraction_complete'])
        self.assertEqual(rows['8 CCR § 4999(a)']['text'], '(a) Riggers shall be qualified.')
        self.assertEqual(rows['8 CCR § 4999(b)']['text'], '(b) Size of load.')
        self.assertEqual(rows['8 CCR § 4999(b)(1)(A)']['parent_citation'], '8 CCR § 4999(b)(1)')
        self.assertEqual(rows['8 CCR § 4999(b)(1)(A)']['ancestor_citations'], ['8 CCR § 4999', '8 CCR § 4999(b)', '8 CCR § 4999(b)(1)'])
        self.assertTrue(rows['8 CCR § 4999(b)(1)(A)']['url'].endswith('#b1A'))
        self.assertNotIn('8 CCR § 49991.', rows)
        self.assertEqual(rows['8 CCR § 4999']['text'], 'Full original source text including history.')

    def test_continuations_and_exceptions_belong_to_own_container(self):
        note = '<div class="co_paragraph"><div class="co_paragraphText">Exception: A permitted condition.</div></div>'
        body = sub('(a) General rule.', sub('(1) Child requirement.', note))
        rows = self.by_name(build_citation_export(export(body)))
        self.assertEqual(rows['8 CCR § 4999(a)']['text'], '(a) General rule.')
        self.assertIn('Exception:', rows['8 CCR § 4999(a)(1)']['text'])

    def test_malformed_dom_does_not_lose_parent_before_fourth_item(self):
        body = sub('(a) Main.', sub('(1) One.') + sub('(2) Two.') + sub('(3) Three.'))
        body += '<div class="co_paragraphText">Exception: Outside the prior wrapper.</div>'
        body += sub('(4) Four.', sub('(A) Nested.')) + sub('(b) Next main.')
        result = build_citation_export(export(body, '3203'))
        rows = self.by_name(result)
        self.assertIn('8 CCR § 3203(a)(4)(A)', rows)
        self.assertEqual(rows['8 CCR § 3203(b)']['parent_citation'], '8 CCR § 3203')
        self.assertEqual(result['errors'], [])

    def test_deeper_dotted_designators_and_roman_parentheses(self):
        body = sub('(a) Main.', sub('(1) Number.', sub('(A) Capital.', sub('1. Number dot.', sub('a. Lower dot.')))))
        rows = self.by_name(build_citation_export(export(body)))
        self.assertIn('8 CCR § 4999(a)(1)(A)1.a.', rows)
        body = sub('(a) Main.', sub('(1) Number.', sub('(A) Capital.', sub('(i) Roman.'))))
        rows = self.by_name(build_citation_export(export(body)))
        self.assertEqual(rows['8 CCR § 4999(a)(1)(A)(i)']['parent_citation'], '8 CCR § 4999(a)(1)(A)')

    def test_duplicate_labels_are_reported_without_overwriting(self):
        result = build_citation_export(export(sub('(a) First.') + sub('(a) Conflicting second.')))
        self.assertFalse(result['metadata']['citation_extraction_complete'])
        self.assertEqual(result['unresolved'][0]['code'], 'duplicate_citation')
        self.assertEqual(result['unresolved'][0]['text'], '(a) Conflicting second.')
        self.assertEqual(self.by_name(result)['8 CCR § 4999(a)']['text'], '(a) First.')

    def test_legacy_flat_labels_and_history_are_handled(self):
        body = '<p>(a) Main.</p><p>(1) Child.</p><p>(A) Grandchild.</p><p>(b) Second main.</p><p>HISTORY</p><p>1. Filing history.</p>'
        result = build_citation_export(export(body, legacy=True))
        rows = self.by_name(result)
        self.assertIn('8 CCR § 4999(a)(1)(A)', rows)
        self.assertNotIn('8 CCR § 4999(b)1.', rows)
        self.assertEqual(result['errors'], [])

    def test_ambiguous_flat_start_is_reported(self):
        result = build_citation_export(export('<p>(A) Unknown parent.</p>', legacy=True))
        self.assertEqual(result['unresolved'][0]['code'], 'ambiguous_flat_start')
        self.assertFalse(result['metadata']['crawl_complete'])

    def test_appendix_does_not_collide_with_same_section_number(self):
        normal = export(sub('(a) Normal.'), '341.16')
        appendix = export(sub('A. Appendix item.'), '341.16', appendix=True)['entries'][0]
        appendix['url'] = appendix['id'] = 'https://www.dir.ca.gov/title8/341_16a.html'
        appendix['title'] = '§341.16. Approval ... Appendix A'
        normal['entries'].append(appendix)
        result = build_citation_export(normal)
        self.assertEqual(result['metadata']['section_record_count'], 2)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['citations'][-1]['kind'], 'appendix')
        self.assertEqual(result['citations'][-1]['subdivision_policy'], 'whole_appendix_preserved')

    def test_named_definition_lists_are_not_global_legal_citations(self):
        body = sub('(a) Definitions.',
                   ''.join(f'<div class="co_paragraphText">Term {n}. Definition.</div>' for n in range(4)) +
                   '<div class="co_paragraphText">Emergency Medical Services.</div>' + sub('(1) First named list.') +
                   '<div class="co_paragraphText">Separate Definition.</div>' + sub('(1) Restarted named list.'))
        source = export(body, '1504')
        source['entries'][0]['title'] = '§1504. Definitions.'
        result = build_citation_export(source)
        rows = self.by_name(result)
        self.assertNotIn('8 CCR § 1504(a)(1)', rows)
        self.assertEqual(len(result['source_items']), 2)
        self.assertIn('Restarted named list.', rows['8 CCR § 1504(a)']['text'])
        self.assertEqual(result['errors'], [])

    def test_no_space_after_designator_is_preserved(self):
        result = build_citation_export(export(sub('(a)The rule.')))
        self.assertIn('8 CCR § 4999(a)', self.by_name(result))

    def test_roman_before_capital_and_malformed_dom_use_indent(self):
        body = sub('(a) Main.', sub('(1) Number.', sub('(i) Roman.', sub('(A) Capital.'))))
        body += '<div class="co_subsection"><div class="co_paragraphText co_indentLeft4">(ii) Next roman.</div></div>'
        # Real 340.4 has the numeric parent indented2 and the roman indented4.
        body = body.replace('(1) Number.', '(1) Number.').replace('<div class="co_paragraphText"><a id=""></a>(1)', '<div class="co_paragraphText co_indentLeft2"><a id=""></a>(1)')
        body = body.replace('<div class="co_paragraphText"><a id=""></a>(i)', '<div class="co_paragraphText co_indentLeft4"><a id=""></a>(i)')
        body = body.replace('<div class="co_paragraphText"><a id=""></a>(A)', '<div class="co_paragraphText co_indentLeft6"><a id=""></a>(A)')
        result = build_citation_export(export(body))
        rows = self.by_name(result)
        self.assertEqual(rows['8 CCR § 4999(a)(1)(i)(A)']['parent_citation'], '8 CCR § 4999(a)(1)(i)')
        self.assertEqual(rows['8 CCR § 4999(a)(1)(ii)']['parent_citation'], '8 CCR § 4999(a)(1)')

    def test_missing_html_and_upstream_errors_prevent_completion(self):
        source = export('')
        source['entries'][0].pop('html')
        source['errors'] = [{'url': 'missing', 'error': '404'}]
        result = build_citation_export(source)
        self.assertEqual(result['metadata']['error_count'], 2)
        self.assertFalse(result['metadata']['crawl_complete'])

    def test_real_4999_sample_when_present(self):
        path = Path(__file__).resolve().parents[1] / 'data' / '4999.json'
        if not path.exists():
            self.skipTest('Live data artifact is optional in installed distributions')
        result = build_citation_export(json.loads(path.read_text()))
        rows = self.by_name(result)
        self.assertEqual(result['errors'], [])
        self.assertEqual(rows['8 CCR § 4999(a)']['text'], '(a) The qualified person (rigger) shall be trained and capable of safely performing the rigging operation. All loads shall be rigged by a qualified person (rigger) or by a trainee under the direct visual supervision of a qualified person (rigger).')
        self.assertIn('8 CCR § 4999(b)(1)(A)', rows)

    def test_inline_contents_does_not_create_duplicate_citations(self):
        body = '<div class="co_paragraphText">Contents</div>' + sub('(a) Scope') + sub('(b) Definitions')
        body += sub('(a) Scope.', sub('(1) Actual requirement.')) + sub('(b) Definitions.')
        result = build_citation_export(export(body))
        rows = self.by_name(result)
        self.assertEqual(result['errors'], [])
        self.assertEqual(rows['8 CCR § 4999(a)']['text'], '(a) Scope.')
        self.assertEqual(len(result['metadata']['inline_toc_pages_skipped']), 1)

    def test_numbered_note_is_local_and_does_not_consume_next_formal_item(self):
        body = '<p>(a) General.</p><p>(1) First.</p><p>(2) Second.</p><p>(3) Third.</p>'
        body += '<p>NOTE: Alternate equipment is permitted providing:</p>'
        body += '<p>(1) Note one.</p><p>(2) Note two.</p><p>(3) Note three.</p><p>(4) Formal four.</p>'
        result = build_citation_export(export(body, legacy=True))
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['source_items']), 3)
        self.assertIn('8 CCR § 4999(a)(4)', self.by_name(result))
        self.assertIn('Note three.', self.by_name(result)['8 CCR § 4999(a)(3)']['text'])

    def test_numbered_exceptions_restart_within_explicit_context(self):
        body = '<p>(a) Main.</p><p>(1) Formal one.</p><p>EXCEPTIONS to section 4999(a)(1):</p>'
        body += '<p>(1) Exception one.</p><p>(2) Exception two.</p><p>(3) Exception three.</p><p>(2) Formal two.</p>'
        result = build_citation_export(export(body, legacy=True))
        self.assertEqual(result['errors'], [])
        self.assertEqual([x['kind'] for x in result['source_items']], ['exception_item'] * 3)
        self.assertEqual(self.by_name(result)['8 CCR § 4999(a)(2)']['text'], '(2) Formal two.')

    def test_compound_relative_and_absolute_labels_create_empty_parents(self):
        body = sub('(a) Main.', sub('(1) First.')) + sub('(2)(A) Combined relative.') + sub('(m)(1) Combined absolute.')
        result = build_citation_export(export(body))
        rows = self.by_name(result)
        self.assertEqual(result['errors'], [])
        self.assertEqual(rows['8 CCR § 4999(a)(2)(A)']['parent_citation'], '8 CCR § 4999(a)(2)')
        self.assertTrue(rows['8 CCR § 4999(a)(2)']['implicit_heading'])
        self.assertEqual(rows['8 CCR § 4999(m)(1)']['parent_citation'], '8 CCR § 4999(m)')

    def test_definition_subsection_preserves_local_lowercase_lists(self):
        named = ''.join(f'<div class="co_paragraphText">Term {i}. Definition.</div>' for i in range(4))
        named += '<div class="co_paragraphText">Novel pathogen. Either:</div>'
        named += sub('(1) Evidence.') + sub('(2) Criteria.', sub('(a) New.') + sub('(b) Variant.') + sub('(c) Introduced, or') + sub('(d) Unknown.'))
        body = sub('(a) Scope.') + sub('(b) Definitions.', named) + sub('(c) Requirements.', sub('(1) Follow the rule.'))
        result = build_citation_export(export(body))
        self.assertEqual(result['errors'], [])
        rows = self.by_name(result)
        self.assertEqual(rows['8 CCR § 4999(c)']['text'], '(c) Requirements.')
        self.assertIn('8 CCR § 4999(c)(1)', rows)
        self.assertIn('(d) Unknown.', rows['8 CCR § 4999(b)']['text'])

    def test_table_roman_rows_are_source_local_but_numeric_fees_formal(self):
        table = '<table><tr><td>(i) Task one</td><td>Protection</td></tr><tr><td>(ii) Task two</td><td>Protection</td></tr></table>'
        body = sub('(a) Tasks.') + table + sub('(b) Fees.') + '<table><tr><td>(1) Inspection</td><td>$10</td></tr></table>'
        result = build_citation_export(export(body))
        rows = self.by_name(result)
        self.assertEqual(result['errors'], [])
        self.assertNotIn('8 CCR § 4999(i)', rows)
        self.assertIn('8 CCR § 4999(b)(1)', rows)
        self.assertEqual(len(result['source_items']), 2)

    def test_short_next_top_level_i_heading_survives_bad_source_nesting(self):
        body = sub('(h) Medical.', sub('(10) Last medical.', sub('(i) Training.')))
        body += sub('(1) Training requirement.')
        result = build_citation_export(export(body))
        self.assertEqual(result['errors'], [])
        rows = self.by_name(result)
        self.assertEqual(rows['8 CCR § 4999(i)']['parent_citation'], '8 CCR § 4999')
        self.assertEqual(rows['8 CCR § 4999(i)(1)']['parent_citation'], '8 CCR § 4999(i)')


if __name__ == '__main__':
    unittest.main()
