"""Parse the official DIR collection (CCR Title 8, Division 1)."""
from __future__ import annotations

import copy
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from lxml import etree, html

from ca_title8.models import Link, ParsedPage

ROOT_URL = "https://www.dir.ca.gov/counters/t8index.htm"
SOURCE_NAME = "California Department of Industrial Relations"
SOURCE_SCOPE = "Title 8, Division 1: the collection linked from DIR's eight department indexes. Divisions 2 and 3 are outside this source."
FRESHNESS_NOTE = "Official agency-hosted HTML, retrieved at crawl time. DIR does not warrant that every page is current or accurate."
_BRANCHES = {"query.htm", "querydwc.htm", "querywcab.htm", "querydlse.htm", "querysip.htm", "querydlsr.htm", "querycac.htm", "queryod.htm"}
_SECTION = re.compile(r"^\s*(?:§+\s*|Sections?\s+)(\d+(?:\.\d+)*(?:[A-Za-z])?(?:\s*[-–]\s*\d+(?:\.\d+)*)?)(?:[.\s:]|$)", re.I)
_NAV = re.compile(r"^(?:go\s+back|back\s+to|return\s+to|new\s+query|skip\s+to|search|home\b)", re.I)
_BLOCKS = {"p", "div", "section", "li", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr"}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def in_scope(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname not in {"www.dir.ca.gov", "dir.ca.gov"}:
        return False
    path = parts.path.lower()
    if path == "/counters/t8index.htm":
        return True
    if path.startswith("/samples/search/"):
        return path.rsplit("/", 1)[-1] in _BRANCHES
    if path.startswith(("/title8/", "/t8/")):
        # Legacy index/T8index.asp now redirects to a separate search form.
        return path.endswith((".htm", ".html")) and "/index/" not in path
    return False


def _url(base: str, value: str) -> str:
    return urldefrag(urljoin(base, value))[0]


def _links(container, base: str, *, scoped: bool = False) -> list[Link]:
    result, seen = [], set()
    for a in container.xpath(".//a[@href]"):
        href = a.get("href", "").strip()
        if not href or href.startswith("#"):
            continue
        url, label = _url(base, href), _clean(a.text_content())
        if not label:
            label = _clean(" ".join(a.xpath(".//img/@alt")))
        if urlsplit(url).scheme not in {"http", "https"} or (scoped and (not in_scope(url) or _NAV.match(label))):
            continue
        key = url.lower() if scoped else url
        if key not in seen:
            seen.add(key)
            result.append(Link(url, label))
    return result


def _text(element) -> str:
    """Keep block and row boundaries; inline citations stay in their sentences."""
    pieces = []
    def walk(node):
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()
        if tag in _BLOCKS or tag == "br":
            pieces.append("\n")
        if node.text:
            pieces.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                pieces.append(child.tail)
        if tag in {"td", "th"}:
            pieces.append("\t|\t")
        elif tag in _BLOCKS:
            pieces.append("\n")
    walk(element)
    return "\n".join(_clean(line) for line in "".join(pieces).splitlines() if _clean(line))


def _body_after(container, heading):
    """Copy content after a heading, preserving all following table/section markup."""
    nodes = list(container.iter())
    result = copy.deepcopy(container)
    target = list(result.iter())[nodes.index(heading)]
    ancestors = set(target.iterancestors())
    def trim(parent):
        parent.text = None
        for child in list(parent):
            if child is target:
                parent.text = child.tail
                parent.remove(child)
                return True
            if child in ancestors:
                trim(child)
                return True
            parent.remove(child)
        return False
    trim(result)
    return result


def _sanitize(container, base: str):
    for node in list(container.xpath(".//script | .//style | .//noscript | .//form | .//input | .//nav | .//footer | .//iframe | .//comment()")):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for anchor in list(container.xpath(".//a[@href]")):
        label = _clean(anchor.text_content())
        if _NAV.match(label):
            anchor.drop_tree()
        else:
            anchor.set("href", urljoin(base, anchor.get("href")))
    for img in container.xpath(".//img[@src]"):
        img.set("src", urljoin(base, img.get("src")))
    for node in container.iter():
        if isinstance(node.tag, str):
            for attr in list(node.attrib):
                if attr.lower().startswith("on"):
                    del node.attrib[attr]


def parse_page(data: bytes, url: str) -> ParsedPage:
    if not in_scope(url):
        raise ValueError(f"Outside DIR Title 8 scope: {url}")
    try:
        root = html.fromstring(data)
    except (etree.ParserError, ValueError) as exc:
        raise ValueError(f"Invalid DIR HTML: {url}") from exc
    containers = root.xpath("//main | //*[@role='main'] | //div[contains(concat(' ',normalize-space(@class),' '),' t8_content ')]")
    container = containers[0] if containers else root
    headings = container.xpath(".//h1 | .//h2 | .//h3")
    title_nodes = root.xpath("//title")
    document_title = _clean(title_nodes[0].text_content()) if title_nodes else ""
    section_heading = next((h for h in headings if _SECTION.match(_clean(h.text_content()))), None)
    appendix_heading = next((h for h in headings if re.match(r"^Appendix\b", _clean(h.text_content()), re.I)), None)
    filename = urlsplit(url).path.rsplit("/", 1)[-1].lower()
    if appendix_heading is None and "appendix" in filename and headings:
        appendix_heading = headings[-1]
    entry_heading = section_heading if section_heading is not None else appendix_heading
    if entry_heading is not None:
        title = _clean(entry_heading.text_content())
        match = _SECTION.match(title)
        section = match.group(1).replace(" ", "") if match else None
        kind = "appendix" if appendix_heading is entry_heading else "section"
        body = _body_after(container, entry_heading)
        _sanitize(body, url)
        text = _text(body)
        if not text and not body.xpath(".//img | .//table"):
            # A headed repealed/reserved entry can legitimately have no body.
            if not re.search(r"repealed|reserved", title, re.I):
                raise ValueError(f"Empty DIR regulation body: {url}")
        images = [Link(img.get("src"), _clean(img.get("alt", ""))) for img in body.xpath(".//img[@src]")]
        return ParsedPage(title=title, kind=kind, section=section, text=text,
                          html=html.tostring(body, encoding="unicode"), references=_links(body, url), images=images)
    title = _clean(headings[0].text_content()) if headings else document_title
    children = _links(container, url, scoped=True)
    # Ignore cross-department/back links: indexes are bounded by these paths.
    if urlsplit(url).path.lower().startswith(("/title8/", "/t8/")):
        children = [link for link in children if urlsplit(link.url).path.lower().startswith(("/title8/", "/t8/"))]
    if children:
        return ParsedPage(title=title, kind="index", children=children)
    # Preserve terminal repealed TOCs, whose historical range is meaningful.
    if title and re.search(r"repealed|reserved", title, re.I):
        clean = _body_after(container, headings[0]) if headings else copy.deepcopy(container)
        _sanitize(clean, url)
        return ParsedPage(title=title, kind="section", text=_text(clean), html=html.tostring(clean, encoding="unicode"))
    raise ValueError(f"Unrecognized DIR index or regulation page: {url}")
