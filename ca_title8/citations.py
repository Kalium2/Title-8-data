"""Turn DIR section exports into searchable section and subdivision citations.

The original section text is retained once. Subdivision ``text`` contains its
own paragraphs, including continuations, and excludes child subdivisions. Parent
links provide context without copying an entire ancestor into every record.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urldefrag

from lxml import etree, html

from .http import atomic_json, utc_now
from .sources.dir import _text


_PARENS = re.compile(r"^((?:\(\s*[A-Za-z0-9]+\s*\))+)" )
_DOT = re.compile(r"^([A-Za-z]|\d+)\.(?=\s|$)")
_PART = re.compile(r"(?:^|\n)\s*(Part\s+[IVXLCDM]+|Part\s+\d+)\.?\s", re.I)
_ROMAN = re.compile(r"^[ivxlcdm]+$", re.I)


def _has(node, name):
    return name in (node.get("class") or "").split()


def _anchor(node):
    ids = node.xpath('.//a[@id]/@id | .//a[@name]/@name')
    if ids:
        return ids[0]
    for item in [node, *node.iterancestors()]:
        if item.get("id"):
            return item.get("id")
    return None


def _designators(text, *, appendix=False):
    if appendix:
        part = _PART.search(text)
        if part:
            return [re.sub(r"\s+", " ", part.group(1)).title()]
    match = _PARENS.match(text)
    if match:
        if text[match.end():].startswith((',', ';', '.')):
            return []  # A continued cross-reference such as "(f), provided ...".
        values = [p.strip() for p in re.findall(r"\(([^)]+)\)", match.group(1))]
        if not all(p.isdigit() or len(p) == 1 or _ROMAN.fullmatch(p)
                   or len(p) <= 2 and p == p[0] * len(p) for p in values):
            return []
        return ["(" + p + ")" for p in values]
    match = _DOT.match(text)
    if match:
        return [match.group(1) + '.']
    colon = re.match(r'^(\d+):\s*', text)
    return [colon.group(1) + ':'] if colon else []


def _rank(token):
    if token.startswith("Part "):
        return -1
    value = token.strip("().:")
    if token.startswith("("):
        if value.isdigit():
            return 1
        return 0 if value.islower() else 2
    if value.isdigit():
        return 3
    return 4 if value.islower() else 2


def _indent(node):
    for item in [node, *node.iterancestors()]:
        match = re.search(r"\bco_indentLeft(\d+)\b", item.get("class") or "")
        if match:
            return int(match.group(1))
    return 0


def _style(token):
    value = token.strip('().:')
    if token.startswith('('):
        return 'number_paren' if value.isdigit() else 'lower_paren' if value.islower() else 'upper_paren'
    return ('number_colon' if token.endswith(':') else 'number_dot') if value.isdigit() else 'lower_dot' if value.islower() else 'upper_dot'


def _name(base, path, appendix):
    if appendix:
        return base + " / " + " / ".join(path) if path else base
    return base + "".join(path)


def _paragraphs(root, appendix):
    from .citation_paragraphs import paragraphs
    bodies = [n for n in root.iter() if isinstance(n.tag, str) and _has(n, "co_body")]
    bodies = [n for n in bodies if not any(_has(a, "co_body") for a in n.iterancestors())]
    scope = bodies[0] if len(bodies) == 1 else root
    structured = any(_has(n, "co_paragraphText") for n in scope.iter() if isinstance(n.tag, str))
    if structured:
        nodes = [n for n in scope.iter() if isinstance(n.tag, str)
                 and (_has(n, "co_paragraphText") or appendix and _has(n, "co_headtext"))
                 and not any(_has(a, "co_paragraphText") for a in n.iterancestors())]
        table_nodes = [n for n in paragraphs(scope) if n.get('data-citation-table-row') == 'true']
        if table_nodes:
            order = {n: i for i, n in enumerate(scope.iter())}
            table_indexes = {int(n.get('data-source-order')) for n in table_nodes}
            nodes = [n for n in nodes if not any(a.tag == 'tr' and order.get(a) in table_indexes for a in n.iterancestors())]
            nodes += table_nodes
            nodes.sort(key=lambda n: int(n.get('data-source-order')) if n.get('data-source-order') else order[n])
    else:
        nodes = paragraphs(scope)
    return nodes, structured, bool(bodies)


def build_citation_export(section_export: dict) -> dict:
    """Return normalized citations plus explicit unresolved labels and errors.

    Legal labels, source nesting, and the usual Title 8 label sequence establish
    parentage. A conflicting or duplicate path is reported instead of overwriting
    another citation. The section record always retains the complete source text.
    """
    citations = []
    issues = []
    unresolved = []
    source_items = []
    seen_roots = {}
    structured_count = legacy_count = no_labels_count = 0
    inline_toc_pages = []

    for entry in section_export.get("entries", []):
        appendix = entry.get("kind") == "appendix"
        source_url = urldefrag(entry.get("url") or entry.get("id") or "")[0]
        base = (entry.get("citation") or entry.get("title") or source_url) if appendix else (
            f"8 CCR § {entry['section']}" if entry.get("section") else entry.get("citation") or entry.get("title") or source_url)
        root_key = source_url if appendix else base
        prior = seen_roots.get(root_key)
        if prior is not None:
            if prior["text"] == entry.get("text", ""):
                prior.setdefault("alternate_source_urls", []).append(source_url)
                continue
            issues.append({"code": "conflicting_section", "url": source_url, "citation": base,
                           "error": "Different source pages use the same section citation with different text."})
        root_id = source_url
        common = {key: deepcopy(entry[key]) for key in ("section", "status", "fetched_at", "source_sha256") if key in entry}
        record = {**common, "id": root_id, "citation": base, "title": entry.get("title", base),
                  "kind": "appendix" if appendix else "section", "designators": [],
                  "parent_id": None, "parent_citation": None, "ancestor_citations": [],
                  "url": source_url, "source_url": source_url,
                  "text": entry.get("text", ""), "notes": entry.get("notes", ""),
                  "references": deepcopy(entry.get("references", [])), "images": deepcopy(entry.get("images", []))}
        citations.append(record)
        seen_roots[root_key] = record
        if appendix:
            # Form fields, questionnaire question numbers and restarted lists in
            # appendices do not define globally addressable CCR subdivisions.
            # Preserve the full appendix without inventing statutory paths.
            record["subdivision_policy"] = "whole_appendix_preserved"
            no_labels_count += 1
            continue
        markup = entry.get("html")
        if not markup:
            if re.search(r"repealed|reserved", entry.get("title", ""), re.I):
                no_labels_count += 1
                continue
            issues.append({"code": "missing_html", "url": source_url, "citation": base,
                           "error": "Subdivision extraction requires an export made with --include-html."})
            continue
        try:
            root = html.fromstring(markup)
        except (etree.ParserError, ValueError) as exc:
            issues.append({"code": "invalid_html", "url": source_url, "citation": base, "error": str(exc)})
            continue
        nodes, structured, bounded = _paragraphs(root, appendix)
        contents = next((i for i, n in enumerate(nodes[:12]) if re.fullmatch(r'(?:table of )?contents', _text(n), re.I)), None)
        if contents is not None:
            first = next((i for i in range(contents + 1, len(nodes)) if _designators(_text(nodes[i]))), None)
            if first is not None:
                first_labels = _designators(_text(nodes[first]))
                repeated = next((i for i in range(first + 1, len(nodes)) if _designators(_text(nodes[i])) == first_labels), None)
                if repeated is not None:
                    nodes = nodes[repeated:]
                    inline_toc_pages.append(source_url)
        structured_count += int(structured)
        legacy_count += int(not structured)
        local = {(): record}
        owners = {}
        stack = []  # (label rank, path)
        last = record
        labelled = 0
        part_context = ()
        has_definition_heading = any(re.match(r'^\([a-z]+\)\s*Definitions\b', _text(n), re.I) for n in nodes)
        definition_mode = bool(re.search(r"\bdefinitions?\b", entry.get("title", ""), re.I)) or has_definition_heading or sum(
                bool(re.match(r'^[A-Za-z“][^.\n]{1,90}?\s+means\s', _text(n), re.I)) for n in nodes) > 3
        definition_scope = () if re.search(r"\bdefinitions?\b", entry.get("title", ""), re.I) else None
        definition_context = None
        definition_parent = record
        record_indent = {}
        styles = {}
        letter_l_numbers = set()
        local_list = None
        source_scope = None
        letter_l_nodes = [n for n in nodes if _designators(_text(n)) == ['(l)']]

        def report(code, message, node, text, labels):
            anchor = _anchor(node)
            row = {"source_url": source_url, "url": source_url + "#" + anchor if anchor else source_url,
                   "section_citation": base, "designators": labels, "text": text,
                   "reason": message, "code": code}
            if code == 'duplicate_citation':
                row['candidate_citation'] = _name(base, path, appendix)
            unresolved.append(row)
            issues.append({"code": code, "url": row["url"], "citation": base, "error": message})

        for node in nodes:
            text = _text(node)
            if not text:
                continue
            # History is usually outside co_body. Stop explicitly in legacy pages
            # and malformed exports as well, so filing-list numbers aren't laws.
            if re.match(r"^(?:HISTORY|CREDITS|AUTHORITY(?: CITED)?|SOURCE)(?:\s*:|\s*$)", text, re.I):
                break
            if re.match(r"^(?:Note\s*:\s*)?Authority cited\s*:", text, re.I):
                continue
            if any(any(name in (a.get("class") or "").lower() for name in ("co_history", "co_credits", "co_footnote"))
                   for a in [node, *node.iterancestors()]):
                continue
            labels = _designators(text, appendix=appendix)
            if labels and re.search(r'\b(?:subsections?|sections?)\s*$', last.get('text', ''), re.I):
                labels = []  # A cross-reference wrapped onto the next source paragraph.
            containing = [a for a in node.iterancestors() if _has(a, "co_subsection")]
            owner = containing[0] if containing else None
            context = next((owners[a] for a in containing[1:] if a in owners), None)
            if re.fullmatch(r'Appendix\s+[A-Z0-9]+\.?', text, re.I):
                source_scope = {'kind': 'embedded_appendix', 'context': text, 'parent_citation': base}
            elif re.match(r'^Footnotes?\s+to\s+Table\b', text, re.I):
                source_scope = {'kind': 'table_footnote', 'context': text, 'parent_citation': base}
            elif re.match(r'^Table\s+(?:[IVXLCDM]+|\d+)\.', text, re.I) and len(text) > 300:
                source_scope = {'kind': 'table_item', 'context': text[:180], 'parent_citation': last['citation']}
            if source_scope is not None:
                source_items.append({**source_scope, 'source_url': source_url, 'designators': labels, 'text': text})
                continue
            if not labels:
                continuation_path = next((owners[a] for a in containing if a in owners), None)
                target = local.get(continuation_path, last) if continuation_path is not None else last
                if target is not record:
                    target["text"] += "\n" + text
                if re.match(r'^\s*(?:NOTES?|EXCEPTIONS?)\b(?:.*:|\s*$)', text, re.I):
                    kind = 'exception_item' if text.lower().startswith('exception') else 'note_item'
                    local_list = {'kind': kind, 'heading': text, 'parent': target, 'last_number': 0,
                                  'style': None, 'last_letter': None,
                                  'last_item_intro': False, 'nested_last_letter': None,
                                  'number_policy': 'restart' if re.match(r'^(?:NOTES|EXCEPTIONS?)\b', text, re.I) else 'collision'}
                    inline = re.search(r'(?:[:;]\s*)([a-zA-Z])\.\s+(.+)$', text)
                    if inline:
                        letter = inline.group(1)
                        local_list['style'] = 'lower_dot' if letter.islower() else 'upper_dot'
                        local_list['last_letter'] = letter
                        source_items.append({'kind': kind, 'source_url': source_url,
                                             'parent_citation': target['citation'], 'context': text,
                                             'designators': [letter + '.'], 'text': letter + '. ' + inline.group(2)})
                    numeric_inline = re.search(r'(?:[:;]\s*)(\(\d+\)|\d+\.)\s*(.+)$', text)
                    if numeric_inline:
                        number_label = numeric_inline.group(1)
                        local_list['style'] = _style(number_label)
                        local_list['last_number'] = int(number_label.strip('().'))
                        source_items.append({'kind': kind, 'source_url': source_url,
                                             'parent_citation': target['citation'], 'context': text,
                                             'designators': [number_label], 'text': number_label + ' ' + numeric_inline.group(2)})
                if definition_mode and definition_scope is not None:
                    heading = re.match(r'''^([A-Za-z“][^.\n]{1,90}?)(?:\.[”"']?(?:\s|$)|\s+means\s)''', text, re.I)
                    if heading:
                        definition_context = heading.group(1)
                        definition_parent = target
                continue
            scope_letter = definition_scope[0].strip('()') if definition_scope else ''
            next_scope = chr(ord(scope_letter) + 1) if len(scope_letter) == 1 else None
            owns_numbered_children = owner is not None and any(
                _designators(_text(n)) == ['(1)'] for n in owner.iterdescendants()
                if isinstance(n.tag, str) and _has(n, 'co_paragraphText'))
            starts_short_heading = bool(re.match(r'^\([a-z]+\)\s+[^.\n]{1,85}\.', text))
            leaves_definition = (definition_context and len(labels) == 1 and labels[0] == f'({next_scope})'
                                 and (owns_numbered_children or starts_short_heading)
                                 and not (owner is not None and context and len(context) > len(definition_scope or ())))
            if definition_context and not leaves_definition:
                # Numbered lists below a named definition are source-local. The
                # same displayed number restarts under other definitions.
                anchor = _anchor(node)
                source_items.append({"kind": "definition_item", "source_url": source_url,
                                     "url": source_url + "#" + anchor if anchor else source_url,
                                     "parent_citation": definition_parent["citation"],
                                     "definition": definition_context, "designators": labels, "text": text})
                if definition_parent is not record:
                    definition_parent["text"] += "\n" + text
                continue
            elif definition_context:
                definition_context = None
            token = labels[0]
            style = _style(token)
            value = token.strip('().:')
            letter_l_number = False
            if token == '(l)' and re.match(r'^\(l\)\s+Exposure\s+(?:Measurements?|Monitoring)\b', text, re.I):
                # These two official pages display a letter ell for the first
                # numbered item under Recordkeeping. Retain that display label;
                # offer the numeric-one path only as an explicitly marked alias.
                style = 'number_paren'
                letter_l_number = True
            elif token == '(l)' and len(letter_l_nodes) > 1 and node is not letter_l_nodes[-1] and not starts_short_heading:
                style = 'number_paren'
                letter_l_number = True
            table_row = node.get('data-citation-table-row') == 'true'
            if table_row and (style.endswith('_dot') or style == 'lower_paren' and _ROMAN.fullmatch(value)):
                source_items.append({'kind': 'table_item', 'source_url': source_url,
                                     'parent_citation': last['citation'], 'designators': labels, 'text': text})
                continue
            if style == 'number_dot' and len(token.rstrip('.')) > 3:
                source_items.append({'kind': 'cross_reference_list_item', 'source_url': source_url,
                                     'parent_citation': last['citation'], 'designators': labels, 'text': text})
                if re.search(r'which reads\s*:', last.get('text', ''), re.I):
                    source_scope = {'kind': 'incorporated_section_text', 'context': text,
                                    'referenced_citation': '8 CCR § ' + token.rstrip('.'), 'parent_citation': base}
                continue
            if style == 'lower_paren' and _ROMAN.fullmatch(value) and (len(value) > 1 or value in {'i', 'v', 'x'}):
                root_paths = [p for _, p in stack if len(p) == 1 and styles.get(p) == 'lower_paren']
                root_path = root_paths[-1] if root_paths else None
                numeric_active = any(styles.get(p) == 'number_paren' for _, p in stack)
                owns_number = owner is not None and any(
                    _designators(_text(n)) == ['(1)'] for n in owner.iterdescendants()
                    if isinstance(n.tag, str) and _has(n, 'co_paragraphText'))
                is_next_root = bool(root_path and len(root_path[0].strip('()')) == 1 and len(value) == 1
                                    and ord(value) == ord(root_path[0].strip('()')) + 1)
                root_indent = record_indent.get(root_path, 0)
                introduced_list = bool(last.get('text', '').rstrip().endswith(':'))
                if len(value) > 1 or ((context is not None and numeric_active)
                                      or numeric_active and (_indent(node) > root_indent or introduced_list)) and not (is_next_root and (owns_number or starts_short_heading)):
                    style = 'roman_paren'
            same = [(r, p) for r, p in stack if styles.get(p) == style]
            if style == 'lower_paren':
                parent_path, rank = (), 0
            elif same:
                rank, previous_path = same[-1]
                parent_path = previous_path[:-1]
            elif context is not None and context in local:
                # Old DIR exports sometimes close a numbered wrapper before its
                # capital-letter children; a broader DOM ancestor then remains.
                conventional = {'lower_paren': 0, 'number_paren': 1, 'upper_paren': 2,
                                'number_dot': 3, 'lower_dot': 4}
                previous = stack[-1][1] if stack else ()
                if (previous and previous[:len(context)] == context and len(previous) > len(context)
                        and conventional.get(style, -1) > conventional.get(styles.get(previous), 99)):
                    parent_path = previous
                    rank = stack[-1][0] + 1
                else:
                    parent_path = context
                    rank = next((r for r, p in stack if p == context), len(context) - 1) + 1
            elif stack:
                parent_path = stack[-1][1]
                rank = stack[-1][0] + 1
            else:
                parent_path, rank = (), 0
                if not structured and token not in {'(1)', '1.'} and local_list is None:
                    report('ambiguous_flat_start', 'Legacy page starts with a nested-looking label without parent context.', node, text, labels)
                    continue
            path = parent_path + tuple(labels)
            if (local_list is None and len(labels) == 1 and token in {'(1)', '1.'} and stack
                    and (styles.get(stack[-1][1]) in {'upper_paren', 'upper_dot'} or last.get('text', '').rstrip().endswith(':'))
                    and (path in local or last.get('text', '').rstrip().endswith(':'))):
                parent_path = stack[-1][1]
                path = parent_path + tuple(labels)
            if local_list and len(labels) == 1 and value.isdigit():
                number = int(value)
                same_list_style = (local_list['style'] in {None, style}
                                   or {local_list['style'], style} <= {'number_dot', 'number_colon'})
                if same_list_style and ((local_list['last_number'] or number == 1) and local_list['number_policy'] == 'restart' and number > local_list['last_number']
                        or local_list['number_policy'] == 'collision' and path in local):
                    source_items.append({'kind': local_list['kind'], 'source_url': source_url,
                                         'parent_citation': local_list['parent']['citation'],
                                         'context': local_list['heading'], 'designators': labels, 'text': text})
                    local_list['last_number'] = number
                    local_list['style'] = style
                    local_list['last_item_intro'] = text.rstrip().endswith(':')
                    if local_list['parent'] is not record:
                        local_list['parent']['text'] += '\n' + text
                    continue
                local_list = None
            elif local_list and labels:
                style_order = {'lower_paren': 0, 'number_paren': 1, 'upper_paren': 2,
                               'number_dot': 3, 'number_colon': 3, 'lower_dot': 4, 'upper_dot': 2}
                alphabetic_exception = (local_list['kind'] == 'exception_item' and len(value) == 1 and value.isalpha()
                                        and local_list['style'] in {None, style}
                                        and (local_list['last_letter'] is None and value.lower() == 'a'
                                             or local_list['last_letter'] is not None and ord(value) > ord(local_list['last_letter'])))
                nested_alphabet = (local_list['last_item_intro'] and len(value) == 1 and value.isalpha()
                                   and (local_list['nested_last_letter'] is None or ord(value) > ord(local_list['nested_last_letter'])))
                if nested_alphabet or alphabetic_exception or (local_list['style'] is not None and style_order.get(style, -1)
                        > style_order.get(local_list['style'], 99)):
                    source_items.append({'kind': local_list['kind'], 'source_url': source_url,
                                         'parent_citation': local_list['parent']['citation'],
                                         'context': local_list['heading'], 'designators': labels, 'text': text})
                    if local_list['parent'] is not record:
                        local_list['parent']['text'] += '\n' + text
                    if alphabetic_exception:
                        local_list['style'] = style
                        local_list['last_letter'] = value
                    if nested_alphabet:
                        local_list['nested_last_letter'] = value
                    continue
                local_list = None
            if path in local and table_row:
                source_items.append({'kind': 'table_item', 'source_url': source_url,
                                     'parent_citation': last['citation'], 'designators': labels, 'text': text})
                continue
            if path in local and context is not None and context in local and context + tuple(labels) not in local:
                # Repeated numbering can return from an inner list to an outer
                # list of the same style. In that case a distinct, explicit DOM
                # parent resolves a collision without changing source labels.
                parent_path = context
                path = parent_path + tuple(labels)
            # Compact source labels such as (m)(1) explicitly establish their
            # intermediate parent even when it has no separate heading paragraph.
            for length in range(len(parent_path) + 1, len(path)):
                prefix = path[:length]
                if prefix in local:
                    continue
                parent = local[prefix[:-1]]
                citation = _name(base, prefix, appendix)
                fallback = hashlib.sha256((source_url + '\0' + citation).encode()).hexdigest()[:16]
                implicit = {**common, 'id': source_url + '::' + fallback, 'citation': citation,
                            'title': entry.get('title', base), 'kind': 'subsection', 'designators': list(prefix),
                            'parent_id': parent['id'], 'parent_citation': parent['citation'],
                            'ancestor_citations': parent['ancestor_citations'] + [parent['citation']],
                            'url': source_url, 'source_url': source_url, 'text': '', 'notes': '',
                            'implicit_heading': True, 'extraction_method': 'explicit_compound_label'}
                citations.append(implicit)
                local[prefix] = implicit
                styles[prefix] = _style(prefix[-1])
                record_indent[prefix] = _indent(node)
                while stack and stack[-1][0] >= len(prefix) - 1:
                    stack.pop()
                stack.append((len(prefix) - 1, prefix))
            parent_path = path[:-1]
            rank = next((r for r, p in stack if p == parent_path), len(parent_path) - 1) + 1
            if path in local:
                prior_record = local[path]
                if prior_record['text'] == text:
                    anchor = _anchor(node)
                    alternate = source_url + '#' + anchor if anchor else source_url
                    if alternate != prior_record['url']:
                        prior_record.setdefault('alternate_source_urls', []).append(alternate)
                    continue
                if owner is not None and owners.get(owner) == path:
                    prior_record["text"] += "\n" + text
                    last = prior_record
                    continue
                report("duplicate_citation", "A displayed citation path occurs more than once; neither text is discarded.", node, text, labels)
                continue
            if parent_path not in local:
                report("missing_parent", "Parent citation could not be resolved.", node, text, labels)
                continue
            parent = local[parent_path]
            citation = _name(base, path, appendix)
            anchor = _anchor(node)
            fallback = hashlib.sha256((source_url + "\0" + citation).encode()).hexdigest()[:16]
            row = {**common, "id": source_url + "::" + fallback, "citation": citation,
                   "title": entry.get("title", base), "kind": "appendix_subdivision" if appendix else "subsection",
                   "designators": list(path), "parent_id": parent["id"], "parent_citation": parent["citation"],
                   "ancestor_citations": parent["ancestor_citations"] + [parent["citation"]],
                   "url": source_url + "#" + anchor if anchor else source_url,
                   "source_url": source_url, "text": text, "notes": "",
                   "extraction_method": "publisher_structure_and_labels" if structured else "conventional_label_sequence"}
            citations.append(row)
            local[path] = row
            record_indent[path] = _indent(node)
            styles[path] = style if len(labels) == 1 else _style(labels[-1])
            if letter_l_number:
                letter_l_numbers.add(path)
            if any(path[:len(p)] == p for p in letter_l_numbers):
                alias_path = list(path)
                for p in letter_l_numbers:
                    if path[:len(p)] == p:
                        alias_path[len(p) - 1] = '(1)'
                row['citation_aliases'] = [_name(base, alias_path, appendix)]
                row['source_label_note'] = 'Publisher displays a letter ell in a numbered-item position; literal citation retained, numeric-one spelling supplied as an alias.'
            if len(path) == 1:
                definition_scope = path if definition_mode and re.search(r'\bdefinitions?\b', text, re.I) else None
            last = row
            labelled += 1
            while stack and stack[-1][0] >= rank:
                stack.pop()
            stack.append((rank, path))
            if owner is not None:
                owners[owner] = path
        if not labelled:
            no_labels_count += 1

    metadata = deepcopy(section_export.get("metadata", {}))
    inherited_errors = deepcopy(section_export.get("errors", []))
    metadata.update({"generated_at": utc_now(), "export_type": "normalized_citations", "citation_schema_version": "1.0",
                     "citation_count": len(citations), "section_record_count": sum(c["kind"] in {"section", "appendix"} for c in citations),
                     "subdivision_count": sum(c["kind"] not in {"section", "appendix"} for c in citations),
                     "structured_page_count": structured_count, "legacy_page_count": legacy_count,
                     "pages_without_subdivision_labels": no_labels_count, "unresolved_count": len(unresolved),
                     "extraction_error_count": len(issues), "error_count": len(issues) + len(inherited_errors),
                     "source_local_item_count": len(source_items),
                     "inline_toc_pages_skipped": inline_toc_pages,
                     "appendix_policy": "Complete appendix text retained as one record; internal form fields and restarted lists are not asserted to be formal CCR subdivisions.",
                     "citation_extraction_complete": not issues,
                     "crawl_complete": bool(metadata.get("crawl_complete")) and not issues,
                     "covers_full_source_tree": bool(metadata.get("covers_full_source_tree")) and not issues,
                     "text_policy": "Sections and appendices retain complete source text once. Subdivision text includes own paragraphs and continuations, excludes children, and uses parent links for context."})
    return {"metadata": metadata, "citations": citations, "errors": inherited_errors + issues,
            "unresolved": unresolved, "source_items": source_items,
            "unvisited_urls": deepcopy(section_export.get("unvisited_urls", []))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_citation_export(json.loads(args.input.read_text(encoding="utf-8")))
        result["metadata"]["source_section_export"] = args.input.name
        atomic_json(args.output, result)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    meta = result["metadata"]
    print(f"Saved {meta['citation_count']} citations ({meta['subdivision_count']} subdivisions); "
          f"{meta['unresolved_count']} unresolved labels; {meta['error_count']} errors to {args.output}")
    return 1 if meta["error_count"] else 0 if meta["crawl_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
