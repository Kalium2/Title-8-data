# Cal/OSHA exports

Collected from the [official DIR Cal/OSHA table of contents](https://www.dir.ca.gov/samples/search/query.htm). Includes current and repealed entries exposed by that collection.

- [Sections and appendices](cal-osha-sections.json.gz): **4,357** entries with full text, source HTML, history, references, and fetch provenance.
- [Citation index](cal-osha-citations.json.gz): **40,016** section, appendix, and numbered subdivision records with parent links.
- [Coverage report](cal-osha-coverage.json): remaining source errors, ambiguous citation labels, and scope limits.
- [Manifest and checksums](cal-osha-manifest.json).

The two large exports are gzip-compressed JSON. Download them using GitHub's Download raw file button, then decompress:

```bash
gzip -dk cal-osha-sections.json.gz cal-osha-citations.json.gz
```

This snapshot has **1 unavailable official source page(s)** and **24 citation indexing issue(s)**, including **23 unresolved labels**. The full text of downloaded sections remains available even where a nested label needs review. Consult the coverage report before treating the citation index as exhaustive. Appendices remain whole documents; their form fields are not invented as formal CCR subdivision paths. Images are referenced rather than OCRed; designated recordkeeping PDF appendices have extracted text and original PDF links.

Individual examples: [4999(a)](4999-a.json), [full 4999](4999.json), [DIR samples](sample-dir.json), [Cornell samples](sample.json).
