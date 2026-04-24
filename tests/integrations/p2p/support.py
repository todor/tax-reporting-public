from __future__ import annotations

from pathlib import Path


def _pdf_escape(text: str) -> str:
    normalized = (
        text.replace("€", "EUR")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )
    return normalized.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


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
