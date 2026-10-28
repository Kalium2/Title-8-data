from dataclasses import dataclass, field


@dataclass
class Link:
    url: str
    label: str


@dataclass
class ParsedPage:
    title: str
    kind: str
    section: str | None = None
    text: str = ""
    html: str = ""
    children: list[Link] = field(default_factory=list)
    references: list[Link] = field(default_factory=list)
    images: list[Link] = field(default_factory=list)
    notes: str = ""


@dataclass
class FetchedPage:
    url: str
    final_url: str
    fetched_at: str
    content_type: str
    sha256: str
    data: bytes
