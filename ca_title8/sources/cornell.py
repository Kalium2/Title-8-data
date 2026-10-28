"""Cornell LII's complete (quarterly snapshot) California Title 8 tree."""

from copy import deepcopy
import re
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from lxml import etree, html

from ca_title8.models import Link, ParsedPage

ROOT_URL = "https://www.law.cornell.edu/regulations/california/title-8"
SOURCE_NAME = "Cornell Legal Information Institute"
SOURCE_SCOPE = "California Code of Regulations, Title 8, Divisions 1, 2, and 3"
FRESHNESS_NOTE = (
    "Cornell describes this collection as a quarterly snapshot. It is not the "
    "current official compilation; newly effective amendments may be absent."
)
_TREE_PATH = "/regulations/california/title-8"
_SECTION_PATH = "/regulations/california/8-CCR-"
_BLOCK_TAGS = {
    "div", "p", "li", "ol", "ul", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "blockquote", "pre", "table", "tr", "dl", "dt", "dd",
}


def in_scope(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "www.law.cornell.edu":
        return False
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        return False
    path = unquote(parsed.path).rstrip("/")
    return (
        path == _TREE_PATH or path.startswith(_TREE_PATH + "/")
        or path.startswith(_SECTION_PATH) and len(path) > len(_SECTION_PATH)
    )


def _plain(element) -> str:
    """Keep inline words together and block/table boundaries visible."""
    def render(node):
        if not isinstance(node.tag, str):
            return ""
        tag = node.tag.lower()
        if tag in {"script", "style", "noscript"}:
            return ""
        if tag == "br":
            return "\n"
        if tag == "img":
            return node.get("alt", "")
        result = node.text or ""
        for child in node:
            result += render(child) + (child.tail or "")
        if tag in {"td", "th"}:
            return result.strip() + "\t"
        return "\n" + result + "\n" if tag in _BLOCK_TAGS else result

    raw = render(element)
    lines = [re.sub(r"[^\S\n\t]+", " ", line).strip(" \t") for line in raw.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _label(element) -> str:
    return re.sub(r"\s+", " ", element.text_content()).strip()


def _absolute(url: str, target: str) -> str | None:
    absolute = urldefrag(urljoin(url, target))[0]
    return absolute if urlsplit(absolute).scheme in {"http", "https"} else None


def _links(element, url: str, *, images: bool = False) -> list[Link]:
    results = []
    seen = set()
    for node in element.xpath(".//img[@src]" if images else ".//a[@href]"):
        target = _absolute(url, node.get("src" if images else "href"))
        if target and target not in seen:
            results.append(Link(target, node.get("alt", "") if images else _label(node)))
            seen.add(target)
    return results


def parse_page(data: bytes, url: str) -> ParsedPage:
    if not in_scope(url):
        raise ValueError(f"URL outside Cornell Title 8 scope: {url}")
    try:
        root = html.fromstring(data)
    except (etree.ParserError, ValueError) as exc:
        raise ValueError("Cannot parse Cornell HTML") from exc
    headings = root.xpath('//*[@id="page_title"]')
    tabs = root.xpath('//*[@id="tab_default_1"]')
    if len(headings) != 1 or len(tabs) != 1:
        raise ValueError("Cornell page lacks expected title and active regulations tab")
    title = _label(headings[0])
    active = tabs[0]
    if not title:
        raise ValueError("Cornell page has an empty title")
    tocs = active.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," toc ")]')
    if tocs:
        children = []
        seen = set()
        for toc in tocs:
            for link in _links(toc, url):
                if in_scope(link.url) and link.url.rstrip("/") != url.rstrip("/") and link.url not in seen:
                    children.append(link)
                    seen.add(link.url)
        if not children and not re.search(r"\b(?:repealed|reserved)\b", title, re.I):
            raise ValueError("Cornell table of contents contains no Title 8 children")
        return ParsedPage(title=title, kind="index", children=children)
    bodies = active.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," statereg-text ")]')
    if not bodies:
        raise ValueError("Cornell entry has no regulations body")
    container = html.Element("div")
    for body in bodies:
        container.append(deepcopy(body))
    for node in container.xpath('.//script | .//style | .//noscript'):
        node.drop_tree()
    references = _links(container, url)
    images = _links(container, url, images=True)
    for node in container.xpath('.//*[@href] | .//*[@src]'):
        for attr in ("href", "src"):
            if node.get(attr):
                absolute = _absolute(url, node.get(attr))
                if absolute:
                    node.set(attr, absolute)
                else:
                    node.attrib.pop(attr)
    section = None
    match = re.search(r"§\s*([^\s]+)(?:\s*-\s*|$)", title)
    if match:
        section = match.group(1).rstrip(",")
    elif urlsplit(url).path.startswith(_SECTION_PATH):
        section = unquote(urlsplit(url).path[len(_SECTION_PATH):])
    kind = "appendix" if (
        re.search(r"\b(?:appendi(?:x|ces)|app\.?)(?:\s|$)", title, re.I)
        or re.search(r"-(?:appendix|app)-", unquote(urlsplit(url).path), re.I)
    ) else "section"
    notes_nodes = active.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," statereg-notes ")]')
    text = _plain(container)
    if not text and not images and not re.search(r"\b(?:repealed|reserved)\b", title, re.I):
        raise ValueError("Cornell regulation body is empty")
    return ParsedPage(
        title=title, kind=kind, section=section, text=text,
        html=html.tostring(container, encoding="unicode"),
        references=references, images=images,
        notes="\n\n".join(_plain(note) for note in notes_nodes),
    )
