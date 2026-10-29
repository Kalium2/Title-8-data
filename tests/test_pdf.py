"""Small, self-contained PDF fixtures exercise actual text extraction."""
import unittest

from lxml import html

from ca_title8.pdf import parse_pdf
from ca_title8.sources import dir as dir_source


def pdf_fixture(page_texts):
    """Build a minimal valid PDF, with no extra test dependencies."""
    page_ids = [4 + 2 * i for i in range(len(page_texts))]
    kids = " ".join(f"{number} 0 R" for number in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Count {len(page_texts)} /Kids [{kids}] >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, text in zip(page_ids, page_texts):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 40 740 Td ({escaped}) Tj ET".encode("latin-1")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    result = b"%PDF-1.4\n"
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{index} 0 obj\n".encode() + value + b"\nendobj\n"
    start = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    result += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return result


class PdfTests(unittest.TestCase):
    url = "https://www.dir.ca.gov/DOSH/DoshReg/ApndxA300Final.pdf"

    def test_all_pages_and_source_provenance_preserved(self):
        result = parse_pdf(pdf_fixture(["First <page> & record", "Second (page) record"]), self.url, "Appendix A")
        self.assertEqual(result.kind, "appendix")
        self.assertIsNone(result.section)
        self.assertIn("First <page> & record", result.text)
        self.assertIn("\f", result.text)
        self.assertIn("Second (page) record", result.text)
        self.assertIn("&lt;page&gt; &amp; record", result.html)
        self.assertEqual(html.fromstring(result.html).text_content(), result.text)
        self.assertEqual(result.references[0].url, self.url)
        self.assertIn("2 page(s)", result.notes)
        self.assertIn("No OCR", result.notes)

    def test_missing_text_rejected_and_empty_page_reported(self):
        with self.assertRaisesRegex(ValueError, "no extractable text"):
            parse_pdf(pdf_fixture([""]), self.url, "Appendix A")
        result = parse_pdf(pdf_fixture(["", "Second page text"]), self.url, "Appendix A")
        self.assertIn("Pages without extractable text: 1.", result.notes)

    def test_wrong_corrupt_or_untitled_document_rejected(self):
        for data, title in [(b"<html>Access denied</html>", "Appendix A"),
                            (b"%PDF-1.4\nnot a PDF", "Appendix A"),
                            (pdf_fixture(["text"]), "")]:
            with self.subTest(data=data[:30], title=title), self.assertRaises(ValueError):
                parse_pdf(data, self.url, title)

    def test_allowlisted_pdf_dispatches_as_legal_appendix(self):
        result = dir_source.parse_page(pdf_fixture(["Cal/OSHA Form 300"]), self.url)
        self.assertEqual(result.kind, "appendix")
        self.assertIn("Appendix A", result.title)
        self.assertIn("Cal/OSHA Form 300", result.text)
        with self.assertRaises(ValueError):
            dir_source.parse_page(pdf_fixture(["An unrelated document"]),
                                  "https://www.dir.ca.gov/DOSH/DoshReg/unrelated.pdf")


if __name__ == "__main__":
    unittest.main()
