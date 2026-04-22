from __future__ import annotations

from pathlib import Path


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_text_pdf(path: Path, *, pages: list[list[str]]) -> Path:
    """Write a minimal text PDF with one text stream per page."""

    objects: list[str] = []

    def add_object(payload: str) -> int:
        objects.append(payload)
        return len(objects)

    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []

    for lines in pages:
        stream_lines = ["BT", "/F1 11 Tf", "1 0 0 1 50 780 Tm", "14 TL"]
        first = True
        for raw_line in lines:
            escaped = _pdf_escape(raw_line)
            if first:
                stream_lines.append(f"({escaped}) Tj")
                first = False
            else:
                stream_lines.append("T*")
                stream_lines.append(f"({escaped}) Tj")
        stream_lines.append("ET")

        stream = "\n".join(stream_lines) + "\n"
        content_obj = (
            f"<< /Length {len(stream.encode('latin-1', 'ignore'))} >>\n"
            f"stream\n{stream}endstream"
        )
        content_id = add_object(content_obj)

        page_obj = (
            "<< /Type /Page /Parent {PAGES_ID} 0 R "
            "/MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        page_id = add_object(page_obj)
        page_ids.append(page_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    pages_id = add_object(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>")

    for page_id in page_ids:
        objects[page_id - 1] = objects[page_id - 1].replace("{PAGES_ID}", str(pages_id))

    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    chunks = ["%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    position = len(chunks[0].encode("latin-1"))

    for idx, payload in enumerate(objects, start=1):
        block = f"{idx} 0 obj\n{payload}\nendobj\n"
        offsets.append(position)
        chunks.append(block)
        position += len(block.encode("latin-1"))

    xref_pos = position
    chunks.append("xref\n")
    chunks.append(f"0 {len(objects) + 1}\n")
    chunks.append("0000000000 65535 f \n")
    for off in offsets[1:]:
        chunks.append(f"{off:010d} 00000 n \n")

    chunks.append(f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n")
    chunks.append(f"startxref\n{xref_pos}\n%%EOF\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(chunks).encode("latin-1"))
    return path


def afranga_sample_pages() -> list[list[str]]:
    page1 = [
        "Account Statement",
        "Reporting year: 2025",
        "for the period between 2025-01-01 till 2025-12-31",
        "Income from interest received 200.00 EUR",
        "Income from late interest received 10.00 EUR",
        "Bonuses 100.00 EUR",
        "Income/loss from secondary market discount/premium -5.00 EUR",
    ]
    page2 = [
        "Appendix No. 1",
        "Break-down of income earned by Borrower country and income type:",
        "Period / Country / Income Type Currency Gross Amount WHT Percentage WHT Net Amount",
        "BULGARIA",
        "Stick Credit AD, company number 202557159 registered in BULGARIA",
        "Income from interest EUR 50.00 10% 5.00 45.00",
        "Income from late interest EUR 5.00 10% 0.50 4.50",
        "Total 55.00 5.50 49.50",
        "LATVIA",
        "Lat Cred Ltd, company number 4411223344 registered in LATVIA",
        "Income from interest EUR 80.00 5% 4.00 76.00",
        "Total 80.00 4.00 76.00",
        "Total 135.00 9.50 125.50",
    ]
    return [page1, page2]
