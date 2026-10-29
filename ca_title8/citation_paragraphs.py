"""Recover ordered paragraphs from legacy DIR markup and table rows.

Synthetic nodes keep a position in the original DOM so callers can merge table
rows with publisher paragraph nodes. Text between block elements is retained;
looking only at leaf ``p``/``div`` elements would lose that text.
"""
from __future__ import annotations

import re

from lxml import html


_BLOCKS = {"p", "div", "li", "ul", "ol", "section", "article", "blockquote",
           "pre", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"script", "style", "noscript", "iframe", "nav", "footer", "form"}


def _normal(text):
    return re.sub(r"\s+", " ", text).strip()


def paragraphs(root):
    """Return detached logical paragraph elements in source order.

    ``data-source-order`` is an index into the input's ``root.iter()`` traversal.
    Table rows carry ``data-citation-table-row=true`` and retain neighboring
    cells (for example a numbered fee description and its amount) together.
    ``data-source-container-order`` identifies a text/tail's owning container.
    """
    positions = {node: index for index, node in enumerate(root.iter())}
    result = []
    chunks = []
    origin = root
    position = positions[root]
    container = root
    anchor = None

    def find_anchor(node):
        if not isinstance(node.tag, str):
            return None
        if node.get("id") or node.get("name"):
            return node.get("id") or node.get("name")
        for parent in node.iterancestors():
            if parent.get("id"):
                return parent.get("id")
        return None

    def append(text, node, owner=None, *, tail=False):
        nonlocal origin, position, container, anchor
        if not text:
            return
        if not chunks:
            origin = node
            position = positions.get(node, 0)
            container = owner if owner is not None else node
            anchor = find_anchor(node)
        chunks.append(text)

    def flush():
        nonlocal chunks, anchor
        text = _normal("".join(chunks))
        if text:
            element = html.Element("p")
            element.text = text
            element.set("data-source-order", str(position))
            element.set("data-source-container-order", str(positions.get(container, position)))
            if anchor:
                element.set("id", anchor)
            if isinstance(origin.tag, str) and origin.tag.lower().startswith("h") and origin.tag[1:].isdigit():
                element.set("data-source-heading", origin.tag.lower())
            result.append(element)
        chunks = []
        anchor = None

    def table(table_node):
        flush()
        rows = table_node.xpath(".//tr")
        rows = [row for row in rows
                if next((a for a in row.iterancestors() if isinstance(a.tag, str) and a.tag.lower() == "table"), None)
                is table_node]
        if not rows:
            append(" ".join(table_node.itertext()), table_node)
            flush()
            return
        for row in rows:
            cells = [cell for cell in row if isinstance(cell.tag, str) and cell.tag.lower() in {"td", "th"}]
            if not cells:
                continue
            text = " | ".join(_normal(" ".join(cell.itertext())) for cell in cells)
            if not text.strip(" |"):
                continue
            element = html.Element("p")
            element.text = text
            element.set("data-citation-table-row", "true")
            element.set("data-source-order", str(positions[row]))
            element.set("data-source-container-order", str(positions[table_node]))
            source_anchor = find_anchor(row)
            if not source_anchor:
                ids = row.xpath('.//a[@id]/@id | .//a[@name]/@name | .//*[@id]/@id')
                source_anchor = ids[0] if ids else None
            if source_anchor:
                element.set("id", source_anchor)
            result.append(element)

    def walk(node, owner=None):
        nonlocal anchor
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()
        if tag in _SKIP:
            return
        if tag in {"br", "hr"}:
            flush()
            return
        if tag == "table":
            table(node)
            return
        block = tag in _BLOCKS
        if block:
            flush()
            owner = node
        if tag == "a" and (node.get("id") or node.get("name")):
            anchor = node.get("id") or node.get("name")
        append(node.text, node, owner)
        for child in node:
            walk(child, owner)
            last = list(child.iter())[-1]
            append(child.tail, last, owner, tail=True)
        if block:
            flush()

    walk(root)
    flush()
    return result
