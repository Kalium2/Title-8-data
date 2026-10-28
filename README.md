# California Title 8 → JSON

A Python scraper for the **California Code of Regulations, Title 8 — Industrial Relations**. It follows published tables of contents, rather than guessing section numbers. The default source includes all three divisions, including appendices and repealed entries exposed by that source.

## Sources and coverage

| Option | Collection | Currency and limits |
| --- | --- | --- |
| `--source cornell` (default) | [Cornell LII's Title 8 tree](https://www.law.cornell.edu/regulations/california/title-8): Divisions 1, 2 (ALRB), and 3 (PERB) | A third-party quarterly snapshot. It can lag current agency regulations. |
| `--source dir` | [California DIR's Title 8 collection](https://www.dir.ca.gov/counters/t8index.htm): all eight department indexes, including Cal/OSHA, workers' compensation, labor standards, apprenticeship, and self-insurance | Official agency-hosted pages for Division 1. Does not include ALRB or PERB. |

**Neither option guarantees every regulation currently in force.** Cornell supplies the full-title tree, but the snapshot can omit recent changes. DIR supplies its own Division 1 collection. A completed crawl means all discovered TOC pages were processed successfully; it does not establish legal currency or verify the publisher's completeness. The output records these limits explicitly.

For example, when inspected in October 2026, Cornell still listed PERB Chapter 9 as repealed, while [PERB's official regulations page](https://perb.ca.gov/laws-and-regulations/regulations/) linked regulations effective July 1, 2026. The [OAL CCR page](https://oal.ca.gov/publications/ccr/) identifies the official statewide compilation. This scraper does not crawl Westlaw's interactive service.

## Install

Requires Python 3.10 or newer.

```bash
git clone https://github.com/Kalium2/Title-8-data.git
cd Title-8-data
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Scrape the full title snapshot

```bash
python -m ca_title8 --output data/title8.json --include-html
```

The scraper reads `robots.txt`, honors its crawl delay, retries transient failures and HTTP 429 responses, and reuses successfully downloaded pages. Cornell currently advertises a **10-second crawl delay**. A full title crawl involves thousands of pages and can take many hours. There is one request worker.

To collect the official DIR Division 1 pages separately:

```bash
python -m ca_title8 --source dir --output data/title8-dir.json --include-html
```

To try one section first:

```bash
python -m ca_title8 \
  --start-url https://www.law.cornell.edu/regulations/california/8-CCR-3203 \
  --output data/section-3203.json --include-html
```

To inspect a small part of the tree:

```bash
python -m ca_title8 --max-pages 10 --output data/partial.json
```

`--start-url` can be repeated. Subtree runs are explicitly marked as not covering the full source tree. A page limit leaves unvisited URLs in the output and exits with code 2.

## Resume and refresh

Rerun the same command to resume using `.cache/title8/<source>/`. The scraper reconstructs discovery from cached TOCs, skips downloads it already has, and retries previously failed downloads. Ctrl-C saves a partial JSON file. Output also checkpoints every 100 processed pages and is replaced atomically.

Use `--refresh` to redownload all pages for a new snapshot, or choose a new `--cache-dir` to keep snapshots separate. Without `--refresh`, a resumed export can mix retrieval dates; each entry includes its original `fetched_at` value. Cached successful HTTP responses that fail parsing also remain cached; refresh them after fixing a source problem.

Other options: `--delay SECONDS`, `--timeout SECONDS`, `--retries N`, `--user-agent TEXT`, `--quiet`. The delay cannot reduce a source's advertised robots delay. TLS verification and environment proxy settings remain enabled.

## JSON structure

The top-level object contains:

- `metadata`: source, scope, freshness note, retrieval times, counts, `crawl_complete`, and `covers_full_source_tree`.
- `entries`: regulation sections and appendices, sorted by section identifier.
- `indexes`: the discovered TOC hierarchy and child links, including terminal repealed headings.
- `errors`: URLs that could not be fetched or recognized. An error always prevents a complete result.
- `unvisited_urls`: work left after a page limit or interruption.

Each entry includes its section identifier, citation, title, kind, marked repeal/reservation status, source URL, hierarchy, regulation text, notes, references, images, fetch timestamp, and source response SHA-256. `--include-html` also retains regulation HTML, preserving table structure. Treat stored HTML as source content and sanitize it before rendering in a web application. Status describes labels in the source heading; it is not a legal determination.

Images, PDF attachments, and other linked documents are included as URLs; their content is not downloaded or OCRed. This means image-only figures and incorporated documents do not become searchable text. Cross-references are recorded but are not followed as discovery links. TOC links, not numerical gaps, define the collection.

`crawl_complete: true` describes the requested traversal, including a successful one-section run. `covers_full_source_tree: true` additionally requires starting at the source root. Always check the source scope and freshness note before describing the dataset as complete.

Exit codes: **0** requested traversal completed; **1** fetch/parse/write error; **2** page limit left work; **130** interrupted. A failed crawl still writes its collected entries and error report when output is writable.

## Verify

```bash
python -m unittest discover -s tests -v
```

Tests use local fixtures and mock HTTP responses; they do not require network access. The included exports are small live-page demonstrations, not full-title exports:

- [`data/sample.json`](data/sample.json): two entries from Cornell.
- [`data/sample-dir.json`](data/sample-dir.json): three entries from DIR.
