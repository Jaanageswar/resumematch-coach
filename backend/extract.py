"""Turn an uploaded .pdf or .txt into plain text.

Runs on the backend because a browser cannot execute pypdf. Files are parsed
entirely in memory and never written to disk.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_SUFFIXES = (".pdf", ".txt", ".text", ".md")


class ExtractionError(ValueError):
    """The file could not be read as text."""


def _tidy(text: str) -> str:
    """Normalise whitespace from a PDF text layer.

    PDF extraction produces ragged line breaks and stray blank lines; collapsing
    them makes the text usable both in a textarea and as an LLM prompt.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def extract_text(data: bytes, filename: str) -> tuple[str, int | None]:
    """Return (text, page_count). page_count is None for plain text files."""
    if not data:
        raise ExtractionError("the file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ExtractionError(
            f"file is {len(data) // 1024} KB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1024} KB"
        )

    lowered = (filename or "").lower()
    if not lowered.endswith(ALLOWED_SUFFIXES):
        raise ExtractionError("only .pdf and .txt files are supported")

    if lowered.endswith(".pdf"):
        return _extract_pdf(data)

    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return _tidy(data.decode(encoding)), None
        except UnicodeDecodeError:
            continue
    raise ExtractionError("could not decode the file as text")


def _extract_pdf(data: bytes) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"could not open the PDF: {exc}") from exc

    if reader.is_encrypted:
        # An empty user password is common for "protected" PDFs and costs
        # nothing to try before giving up.
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ExtractionError("the PDF is password protected") from exc

    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            # One unreadable page should not lose the whole document.
            pages.append("")

    text = _tidy("\n\n".join(pages))
    if not text:
        raise ExtractionError(
            "no text found - this PDF is probably a scan. "
            "There is no OCR here, so please paste the text instead."
        )
    return text, len(reader.pages)
