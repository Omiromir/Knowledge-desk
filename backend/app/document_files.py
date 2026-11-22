"""Extract bounded, searchable text from supported upload formats."""

import logging
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, closing
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from pypdf import PdfReader, PdfWriter
from ingest import normalize

from .domain import NotFoundError
from .pdf_ocr import open_scan
from .summarizer import SummaryError

logger = logging.getLogger(__name__)
TEXT_EXTENSIONS = {".txt", ".md"}
FILE_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}
MAX_PLAIN_BYTES = 64 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024
MAX_SCANNED_PAGES = 20


def extension_for(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if any(char in filename for char in ("/", "\\", "\x00")) or extension not in FILE_EXTENSIONS:
        raise SummaryError("upload_format", "Choose a PDF, DOCX, TXT or Markdown file.")
    return extension


def extract_pdf_page(content: bytes, number: int) -> bytes:
    reader = PdfReader(BytesIO(content))
    if number > len(reader.pages):
        raise NotFoundError("PDF page not found.")
    writer = PdfWriter()
    writer.add_page(reader.pages[number - 1])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def pdf_parts(file: BytesIO) -> Iterator[str]:
    reader = PdfReader(file)
    if reader.is_encrypted:
        raise SummaryError("upload_pdf_locked", "Upload an unlocked copy of the PDF.")
    if len(reader.pages) > 150:
        raise SummaryError("upload_pdf_pages", "Split the PDF into files of at most 150 pages.")
    with ExitStack() as stack:
        recognize = None
        scanned = 0
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip() or "/Contents" not in page:
                yield text
                continue
            scanned += 1
            if scanned > MAX_SCANNED_PAGES:
                raise SummaryError("upload_ocr_pages", "Split scans into files of at most 20 scanned pages.")
            if recognize is None:
                recognize = stack.enter_context(open_scan(file.getvalue()))
            yield recognize(index)


def word_parts(file: BytesIO) -> Iterator[str]:
    # Bound decompression before python-docx reads the package parts.
    with ZipFile(file) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024:
            raise SummaryError("upload_word_package", "Split or compress the Word document.")
    file.seek(0)
    for block in Document(file).iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    yield " | ".join(cells)
        else:
            yield block.text


def collect_parts(parts: Iterable[str]) -> list[tuple[int, str]]:
    paragraphs = []
    size = 0
    for number, part in enumerate(parts, 1):
        paragraph = part.replace("\x00", " ").strip()
        if not paragraph:
            continue
        size += len(paragraph.encode("utf-8")) + (2 if paragraphs else 0)
        if size > MAX_EXTRACTED_BYTES:
            raise SummaryError("upload_text_too_large", "Split the document into smaller files.")
        paragraphs.append((number, paragraph))
    return paragraphs


def read_file(filename: str, content: bytes) -> tuple[str, list[tuple[int, int, int]]]:
    extension = extension_for(filename)
    spans = []
    if extension in TEXT_EXTENSIONS:
        try:
            text = content.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            raise SummaryError("upload_encoding", "Save the file as UTF-8.") from None
        if "\x00" in text:
            raise SummaryError("upload_encoding", "The file must contain UTF-8 text.")
    else:
        try:
            file = BytesIO(content)
            parts = pdf_parts(file) if extension == ".pdf" else word_parts(file)
            with closing(parts):
                paragraphs = collect_parts(parts)
            text = "\n\n".join(part for _, part in paragraphs)
        except SummaryError:
            raise
        except Exception as exc:
            logger.info("Could not read %s upload: %s", extension, type(exc).__name__)
            raise SummaryError("upload_unreadable", "Open the file and save a fresh copy.") from None
        if not text and extension == ".pdf":
            raise SummaryError("upload_pdf_no_text", "No text could be read. Try a clearer scan.")
        if extension == ".pdf":
            offset = 0
            for number, part in paragraphs:
                length = len(normalize(part))
                spans.append((number, offset, offset + length))
                offset += length + 2
    if not text:
        raise SummaryError("upload_empty", "The file has no text.")
    return text, spans
