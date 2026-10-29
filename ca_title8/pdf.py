"""Text extraction for the official recordkeeping PDF appendices.

Source-specific URL checks and appendix labels belong to the DIR adapter. This
module preserves extractable text without pretending to transcribe graphics or
reconstruct a form's visual layout.
"""
from html import escape
from io import BytesIO

from pypdf import PdfReader

from .models import Link, ParsedPage


def parse_pdf(data: bytes, url: str, title: str) -> ParsedPage:
    """Extract all PDF pages and retain a link to the complete source document."""
    if not data.startswith(b"%PDF-"):
        raise ValueError("Expected PDF bytes for recordkeeping appendix")
    if not title.strip():
        raise ValueError("PDF appendix title is empty")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF appendix cannot be extracted")
        page_count = len(reader.pages)
        if not page_count:
            raise ValueError("PDF appendix contains no pages")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise ValueError(f"Cannot extract PDF appendix: {exc}") from exc
    if not any(pages):
        raise ValueError("PDF appendix contains no extractable text; OCR is required")
    # A form-feed preserves page boundaries without adding synthetic law text.
    text = "\n\n\f\n\n".join(pages)
    empty = [str(index + 1) for index, value in enumerate(pages) if not value]
    notes = (
        f"PDF extraction provenance: text extracted from {page_count} page(s) "
        "using pypdf. No OCR or transcription of graphics was performed. "
        "The original PDF preserves the visual form, annotations, and non-text content."
    )
    if empty:
        notes += " Pages without extractable text: " + ", ".join(empty) + "."
    return ParsedPage(
        title=title.strip(),
        kind="appendix",
        text=text,
        html='<div class="pdf-text"><pre>' + escape(text) + "</pre></div>",
        references=[Link(url, f"Original PDF ({page_count} page(s))")],
        notes=notes,
    )
