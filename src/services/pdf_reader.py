from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader


class PdfReaderError(Exception):
    """Raised when PDF text extraction fails."""


def normalize_pdf_text(text: str) -> str:
    """Normalize extracted PDF text for stable regex parsing."""
    normalized = text.replace("\u00a0", " ")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\t\f\v]+", " ", normalized)
    normalized = re.sub(r"[ ]{2,}", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def read_pdf_pages(path: str | Path) -> list[str]:
    """Return normalized text per page from a machine-generated PDF."""
    pdf_path = Path(path).expanduser().resolve()
    if not pdf_path.exists():
        raise PdfReaderError(f"PDF file does not exist: {pdf_path}")

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        raise PdfReaderError(f"failed to open PDF: {pdf_path}") from exc

    pages: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            raise PdfReaderError(
                f"failed to extract text from PDF page {page_index}: {pdf_path}"
            ) from exc
        pages.append(normalize_pdf_text(page_text))

    if not pages:
        raise PdfReaderError(f"PDF has no pages: {pdf_path}")

    if all(page == "" for page in pages):
        raise PdfReaderError(
            f"PDF has no extractable text (machine-readable text required): {pdf_path}"
        )

    return pages


def read_pdf_text(path: str | Path) -> str:
    """Return normalized concatenated text from all PDF pages."""
    return "\n\n".join(page for page in read_pdf_pages(path) if page != "")


__all__ = [name for name in globals() if not name.startswith("__")]
