import unittest

from lxml import html

from ca_title8.citation_paragraphs import paragraphs


class CitationParagraphTests(unittest.TestCase):
    def parse(self, markup):
        return paragraphs(html.fromstring(markup))

    def test_legacy_break_separated_wrapper_text_is_not_lost(self):
        nodes = self.parse('<div><br>(a) Public records are available.<br><br>'
                           '(b) Copies may be requested.<br><br>(c) Costs apply.</div>')
        self.assertEqual([n.text for n in nodes], [
            '(a) Public records are available.', '(b) Copies may be requested.', '(c) Costs apply.'])

    def test_initial_container_text_and_later_paragraphs_are_retained(self):
        nodes = self.parse('<div>(a) The Director designates the Commission. '
                           '<p>(b) Each insurer shall pay.</p><p>(c) The annual fee.</p></div>')
        self.assertEqual([n.text for n in nodes], [
            '(a) The Director designates the Commission.', '(b) Each insurer shall pay.', '(c) The annual fee.'])

    def test_nested_blocks_keep_own_text_and_tails_without_duplication(self):
        nodes = self.parse('<div>(a) Parent introduction.<div>(1) Child.</div>'
                           'Parent continuation.<p>(b) Next.</p></div>')
        self.assertEqual([n.text for n in nodes], [
            '(a) Parent introduction.', '(1) Child.', 'Parent continuation.', '(b) Next.'])
        self.assertEqual(nodes[0].get('data-source-container-order'), nodes[2].get('data-source-container-order'))

    def test_inline_markup_does_not_split_a_sentence(self):
        nodes = self.parse('<div><p>(a) A <b>qualified</b> person shall '
                           '<a href="/title8/4999.html">rig</a> the load.</p></div>')
        self.assertEqual([n.text for n in nodes], ['(a) A qualified person shall rig the load.'])

    def test_numbered_fee_table_rows_retain_description_and_amount(self):
        root = html.fromstring('<div><p>(a) Inspection fees:</p><table><tbody>'
            '<tr id="fee1"><td><div class="co_paragraph"><div class="co_leaderTableFullWidth">'
            '<div class="co_leaderWrapper">(1) Hand dumbwaiters</div></div></div></td>'
            '<td><div class="co_paragraphText">$ 120.00 per unit</div></td></tr>'
            '<tr><td><div class="co_leaderWrapper">(2) Power dumbwaiters</div></td>'
            '<td><div class="co_paragraphText">$ 225.00 per unit</div></td></tr>'
            '</tbody></table><p>(b) New installations.</p></div>')
        nodes = paragraphs(root)
        self.assertEqual([n.text for n in nodes], ['(a) Inspection fees:',
            '(1) Hand dumbwaiters | $ 120.00 per unit', '(2) Power dumbwaiters | $ 225.00 per unit',
            '(b) New installations.'])
        rows = [n for n in nodes if n.get('data-citation-table-row')]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].get('id'), 'fee1')
        self.assertEqual(int(rows[0].get('data-source-order')), list(root.iter()).index(root.xpath('.//tr')[0]))

    def test_history_headings_are_available_as_stop_markers(self):
        nodes = self.parse('<div><p>(a) The rule.</p><h2>HISTORY</h2><p>1. Filed 1-1-2000.</p></div>')
        self.assertEqual(nodes[1].text, 'HISTORY')
        self.assertEqual(nodes[1].get('data-source-heading'), 'h2')

    def test_scripts_and_comments_are_not_exported(self):
        nodes = self.parse('<div><p>(a) Rule.<!-- comment --><script>danger()</script> Continue.</p></div>')
        self.assertEqual([n.text for n in nodes], ['(a) Rule. Continue.'])


if __name__ == '__main__':
    unittest.main()
