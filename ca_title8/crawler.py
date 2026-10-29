import logging
import re
from collections import deque
from dataclasses import asdict

from . import __version__
from .http import canonical_url, utc_now

LOG = logging.getLogger(__name__)


def natural_key(value):
    return [(0, int(part)) if part.isdigit() else (1, part.lower())
            for part in re.split(r"(\d+)", value)]


def crawl(source, client, *, start_urls=None, max_pages=None,
          include_html=False, checkpoint=None):
    """Walk TOCs, not cross references; successful cached pages resume interrupted runs."""
    starts = [canonical_url(url) for url in (start_urls or [source.ROOT_URL])]
    for url in starts:
        if not source.in_scope(url):
            raise ValueError(f"Start URL is outside source scope: {url}")
    pending = deque((url, []) for url in dict.fromkeys(starts))
    scheduled = set(starts)
    visited = set()
    resolved = set()
    entries, indexes, errors = [], [], []
    started_at = utc_now()
    interrupted = False

    def snapshot():
        remaining = [url for url, _ in pending]
        full_tree = starts == [canonical_url(source.ROOT_URL)]
        return {
            "metadata": {
                "schema_version": "1.0",
                "scraper_version": __version__,
                "jurisdiction": "California",
                "code": "California Code of Regulations",
                "title": "8",
                "title_name": "Industrial Relations",
                "source": source.SOURCE_NAME,
                "source_root": source.ROOT_URL,
                "source_scope": source.SOURCE_SCOPE,
                "freshness_note": source.FRESHNESS_NOTE,
                "start_urls": starts,
                "started_at": started_at,
                "generated_at": utc_now(),
                "crawl_complete": not remaining and not errors and not interrupted,
                "covers_full_source_tree": full_tree and not remaining and not errors and not interrupted,
                "interrupted": interrupted,
                "pages_discovered": len(scheduled),
                "pages_processed": len(visited),
                "index_count": len(indexes),
                "entry_count": len(entries),
                "error_count": len(errors),
                "asset_policy": getattr(source, "ASSET_POLICY", "Images and linked documents are referenced by URL; not downloaded or OCRed."),
            },
            "entries": sorted(entries, key=lambda e: natural_key(e["section"] or e["title"])),
            "indexes": indexes,
            "errors": errors,
            "unvisited_urls": remaining,
        }

    try:
        while pending and (max_pages is None or len(visited) < max_pages):
            url, hierarchy = pending.popleft()
            visited.add(url)
            try:
                fetched = client.fetch(url)
                final_url = canonical_url(fetched.final_url)
                if final_url in resolved:
                    continue
                parsed = source.parse_page(fetched.data, fetched.final_url)
                if parsed.kind not in {"index", "section", "appendix"} or not parsed.title:
                    raise ValueError("Parser did not identify a supported Title 8 page")
                if (parsed.kind != "index" and not parsed.text.strip() and not parsed.images
                        and not re.search(r"repealed|reserved", parsed.title, re.I)):
                    raise ValueError("Regulation body is empty")
                resolved.add(final_url)
                if parsed.kind == "index":
                    if not parsed.children:
                        # Repealed headings may legitimately be terminal indexes.
                        if not re.search(r"repealed|reserved", parsed.title + parsed.text, re.I):
                            raise ValueError("Index has no recognized child links")
                    index = {"url": final_url, "title": parsed.title,
                             "hierarchy": hierarchy,
                             "children": [asdict(link) for link in parsed.children]}
                    if parsed.text:
                        index["text"] = parsed.text
                    if parsed.notes:
                        index["notes"] = parsed.notes
                    if include_html and parsed.html:
                        index["html"] = parsed.html
                    indexes.append(index)
                    next_hierarchy = hierarchy + [{"title": parsed.title, "url": final_url}]
                    for link in parsed.children:
                        child = canonical_url(link.url)
                        if not source.in_scope(child):
                            raise ValueError(f"Parser returned out-of-scope child: {child}")
                        if child not in scheduled:
                            scheduled.add(child)
                            pending.append((child, next_hierarchy))
                else:
                    entry = {
                        "id": final_url,
                        "section": parsed.section,
                        "citation": (parsed.title if parsed.kind == "appendix" else
                                     f"8 CCR § {parsed.section}" if parsed.section else None),
                        "title": parsed.title,
                        "kind": parsed.kind,
                        "status": ("repealed" if re.search(r"repealed", parsed.title, re.I)
                                   else "reserved" if re.search(r"reserved", parsed.title, re.I)
                                   else "not_marked_repealed_or_reserved"),
                        "url": final_url,
                        "hierarchy": hierarchy,
                        "text": parsed.text,
                        "notes": parsed.notes,
                        "references": [asdict(link) for link in parsed.references],
                        "images": [asdict(link) for link in parsed.images],
                        "fetched_at": fetched.fetched_at,
                        "source_sha256": fetched.sha256,
                    }
                    if include_html:
                        entry["html"] = parsed.html
                    entries.append(entry)
                LOG.info("[%d/%d] %s: %s", len(visited), len(scheduled), parsed.kind, parsed.title)
            except KeyboardInterrupt:
                visited.discard(url)
                pending.appendleft((url, hierarchy))
                raise
            except Exception as error:
                LOG.error("%s: %s", url, error)
                errors.append({"url": url, "error": f"{type(error).__name__}: {error}"})
            if checkpoint and len(visited) % 100 == 0:
                checkpoint(snapshot())
    except KeyboardInterrupt:
        interrupted = True
        LOG.warning("Interrupted; saving partial output. Rerun to resume from the cache.")
    return snapshot()
