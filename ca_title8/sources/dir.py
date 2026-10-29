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
CONTENT_TYPES = {"text/html", "application/xhtml+xml", "application/pdf"}
ASSET_POLICY = (
    "Designated recordkeeping PDF appendices are downloaded and their text is extracted. "
    "Other images and linked documents are referenced by URL; not downloaded or OCRed."
)
_BRANCHES = {"query.htm", "querydwc.htm", "querywcab.htm", "querydlse.htm", "querysip.htm", "querydlsr.htm", "querycac.htm", "queryod.htm"}
SUPPLEMENTAL_APPENDICES = {
    "/dosh/doshreg/apndxa300final.pdf": "Appendix A - Cal/OSHA Form 300",
    "/dosh/doshreg/apndxb300afinal.pdf": "Appendix B - Cal/OSHA Form 300A",
    "/dosh/doshreg/apndxc301final.pdf": "Appendix C - Cal/OSHA Form 301",
    "/dosh/doshreg/appndxdfinal.htm": "Appendix D - Required Elements for the Cal/OSHA Form 300",
    "/dosh/doshreg/apndxefinal.htm": "Appendix E - Required Elements for the Cal/OSHA Form 300A",
    "/dosh/doshreg/apndxffinal.htm": "Appendix F - Required Elements for the Cal/OSHA Form 301",
    "/dosh/doshreg/apndxg300afinal.pdf": "Appendix G - Optional Worksheet for the Cal/OSHA Form 300A",
}
_SECTION = re.compile(r"^\s*(?:§+\s*|Sections?\s+)(\d+(?:\.\d+)*(?:[A-Za-z])?(?:\s*[-–]\s*\d+(?:\.\d+)*)?)(?:[.\s:]|$)", re.I)
_BARE_SECTION = re.compile(r"^\s*(\d+(?:\.\d+)*)\.\s+")
_NAV = re.compile(r"^(?:go\s+back|back\s+to|return\s+to|new\s+query|skip\s+to|search|home\b)", re.I)
_BLOCKS = {"p", "div", "section", "li", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr"}
_HEADING_BLOCKS = _BLOCKS | {"pre", "dl", "dt", "dd", "img"}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _heading_label(element) -> str:
    """Read only the heading prefix when malformed HTML nests its body in it."""
    pieces = []
    def walk(node):
        if node.text:
            pieces.append(node.text)
        for child in node:
            if not isinstance(child.tag, str):
                continue
            if child.tag.lower() in _HEADING_BLOCKS:
                return True
            if child.tag.lower() == "br":
                pieces.append(" ")
            elif walk(child):
                return True
            if child.tail:
                pieces.append(child.tail)
        return False
    walk(element)
    return _clean("".join(pieces))


def in_scope(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname not in {"www.dir.ca.gov", "dir.ca.gov"}:
        return False
    path = parts.path.lower()
    if path in SUPPLEMENTAL_APPENDICES:
        return True
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
    def clear_heading_prefix(parent):
        parent.text = None
        for child in list(parent):
            if isinstance(child.tag, str) and child.tag.lower() in _HEADING_BLOCKS:
                return
            if any(isinstance(node.tag, str) and node.tag.lower() in _HEADING_BLOCKS
                   for node in child.iterdescendants()):
                clear_heading_prefix(child)
                return
            parent.remove(child)
    def trim(parent):
        parent.text = None
        for child in list(parent):
            if child is target:
                if any(isinstance(node.tag, str) and node.tag.lower() in _HEADING_BLOCKS
                       for node in child.iterdescendants()):
                    clear_heading_prefix(child)
                else:
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
    path = urlsplit(url).path.lower()
    if path in SUPPLEMENTAL_APPENDICES and path.endswith(".pdf"):
        from ca_title8.pdf import parse_pdf
        return parse_pdf(data, url, SUPPLEMENTAL_APPENDICES[path])
    try:
        root = html.fromstring(data)
    except (etree.ParserError, ValueError) as exc:
        raise ValueError(f"Invalid DIR HTML: {url}") from exc
    containers = root.xpath("//main | //*[@role='main']")
    bodies = root.xpath("//body")
    # Legacy DIR markup sometimes closes t8_content before the regulation's
    # actual paragraphs. Trimming the legacy body at the published heading
    # keeps those following paragraphs and history instead of losing them.
    container = containers[0] if containers else bodies[0] if bodies else root
    headings = container.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6")
    title_nodes = root.xpath("//title")
    document_title = _clean(title_nodes[0].text_content()) if title_nodes else ""
    section_heading = next((h for h in headings if _SECTION.match(_heading_label(h))), None)
    filename = urlsplit(url).path.rsplit("/", 1)[-1].lower()
    if section_heading is None and re.fullmatch(r"\d+(?:_\d+)*\.html?", filename):
        expected_section = re.sub(r"\.html?$", "", filename).replace("_", ".")
        section_heading = next((h for h in headings
                                if (bare := _BARE_SECTION.match(_heading_label(h)))
                                and bare.group(1) == expected_section), None)
    appendix_heading = next((h for h in headings if re.search(r"\bAppendix\b", _heading_label(h), re.I)), None)
    appendix_filename = (re.search(r"(?:appendix|(?:^|[_-])app[a-z0-9])", filename, re.I)
                         or re.fullmatch(r"\d+(?:_\d+)*[a-z](?:_?\d+)?\.html?", filename, re.I))
    if appendix_heading is None and section_heading is None and appendix_filename:
        # Respirator questionnaires and shipbuilding appendices have a plain
        # paragraph or chapter-article label instead of a heading element.
        appendix_heading = next((node for node in container.xpath(
            ".//p | .//div[contains(concat(' ',normalize-space(@class),' '),' chapter-article ')] | .//strong | .//b")
            if not node.xpath(".//a[@href]")
            and re.match(r"^Appendix\s+[A-Z0-9]\b", _clean(_text(node)), re.I)), None)
    if appendix_heading is None and "appendix" in filename and headings:
        appendix_heading = headings[-1]
    attachment_heading = next((h for h in headings
                               if re.match(r"^(?:Figures?|Tables?)\s+(?!of\b)[A-Z0-9]", _heading_label(h), re.I)
                               or "refannos" in filename and re.search(r"\bRefs?\s*&\s*Annos?\b", _heading_label(h), re.I)), None)
    entry_heading = (section_heading if section_heading is not None else
                     appendix_heading if appendix_heading is not None else attachment_heading)
    if entry_heading is not None:
        title = _heading_label(entry_heading) if entry_heading in headings else _clean(_text(entry_heading))
        match = _SECTION.match(title) or _BARE_SECTION.match(title)
        section = match.group(1).replace(" ", "") if match else None
        kind = "appendix" if appendix_heading is entry_heading or attachment_heading is entry_heading else "section"
        body = _body_after(container, entry_heading)
        _sanitize(body, url)
        text = _text(body)
        source_notes = []
        attachment_type = None
        if kind == "section" and re.search(r"\bplates\b", title, re.I) and ".plates." in filename:
            kind, section, attachment_type = "appendix", None, "plates"
        elif kind == "section" and re.search(r"\bAddendum\b", title, re.I):
            kind, section, attachment_type = "appendix", None, "addendum"
        # A few legacy appendices copy the parent section's h1 verbatim.
        # Their own identity appears only in the document title or the first
        # body label. Require the parent's numeric filename plus a letter, so
        # an ordinary section mentioning an appendix stays a section.
        if kind == "section" and section and re.fullmatch(
                re.escape(section.replace(".", "_")) + r"[a-z](?:_\d+)?\.html?", filename, re.I):
            appendix_label = re.search(r"\bAppendix\s+[A-Z0-9][^\n]*", document_title, re.I)
            if appendix_label:
                title = appendix_label.group(0).strip()
                kind = "appendix"
            elif re.match(r"^Appendix\s+[A-Z0-9]\b", text, re.I):
                labels = [node for node in body.xpath(".//strong | .//b | .//p")
                          if re.match(r"^Appendix\s+[A-Z0-9]\b", _clean(_text(node)), re.I)]
                title = _clean(_text(labels[0])) if labels else text.splitlines()[0]
                kind = "appendix"
            else:
                image_labels = body.xpath(".//img/@alt")
                image_label = next((re.search(r"\bwithin\s+(Appendix\s+[A-Z0-9-]+)\b", label, re.I)
                                    for label in image_labels if re.search(r"\bwithin\s+Appendix\b", label, re.I)), None)
                if image_label:
                    title = image_label.group(1) + " to Section " + section
                    kind = "appendix"
                    source_notes.append("Appendix identity is stated in the source image alternative text; the page heading repeats the parent section.")
        if not text and not body.xpath(".//img | .//table"):
            # A headed repealed/reserved entry can legitimately have no body.
            if not re.search(r"repealed|reserved", title, re.I):
                raise ValueError(f"Empty DIR regulation body: {url}")
        images = [Link(img.get("src"), _clean(img.get("alt", ""))) for img in body.xpath(".//img[@src]")]
        if attachment_heading is entry_heading:
            attachment_type = ("figure" if re.match(r"^Figures?\b", title, re.I) else
                               "table" if re.match(r"^Tables?\b", title, re.I) else "reference annotations")
        if attachment_type:
            source_notes.append("Source attachment type: " + attachment_type + ". Preserved under its published title; no section number assigned.")
        return ParsedPage(title=title, kind=kind, section=section, text=text,
                          html=html.tostring(body, encoding="unicode"), references=_links(body, url), images=images,
                          notes="\n".join(source_notes))
    title = _heading_label(headings[0]) if headings else document_title
    children = _links(container, url, scoped=True)
    # Ignore cross-department/back links: indexes are bounded by these paths.
    if urlsplit(url).path.lower().startswith(("/title8/", "/t8/")):
        children = [link for link in children
                    if urlsplit(link.url).path.lower().startswith(("/title8/", "/t8/"))
                    or urlsplit(link.url).path.lower() in SUPPLEMENTAL_APPENDICES]
    if children:
        return ParsedPage(title=title, kind="index", children=children)
    # Older terminal TOCs put their repeal notice in history below an ordinary
    # subchapter heading, rather than labelling the heading itself as repealed.
    clean = _body_after(container, headings[0]) if headings else copy.deepcopy(container)
    _sanitize(clean, url)
    terminal_text = _text(clean)
    historical_heading = re.match(r"^(?:Chapter|Subchapter|Group|Article)\s+\d", title, re.I)
    labelled_terminal = re.search(r"\b(?:repealed|reserved)\b", title, re.I)
    body_repeal = re.search(
        r"\brepeal(?:er)?\s+of\s+(?:(?:this|the)\s+set\b|(?:Subchapter|Chapter|Group|Article)\s+\d)",
        terminal_text, re.I)
    if title and (labelled_terminal or historical_heading and body_repeal):
        if not labelled_terminal:
            title += " [Repealed]"
        return ParsedPage(title=title, kind="index", text=terminal_text,
                          html=html.tostring(clean, encoding="unicode"))
    raise ValueError(f"Unrecognized DIR index or regulation page: {url}")
