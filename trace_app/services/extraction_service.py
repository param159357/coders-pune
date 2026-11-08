"""Text extraction from PDF, DOCX, and TXT sources.

Extraction preserves *where* each piece of text came from. Every source is
reduced to an ordered list of :class:`Segment` records carrying a human-readable
location label ("page 3", "paragraph 12") and character offsets into the
reassembled full text. Evidence validation and the evidence viewer both depend
on these segments, so location is never guessed after the fact.

An image-only or scanned PDF yields no text. TRACE has no OCR, so such a file is
reported as ``no_text_extracted`` and is never labelled as successfully
extracted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from trace_app.models import ExtractionStatus

logger = logging.getLogger(__name__)

#: Segments shorter than this are dropped as layout noise (page numbers, etc).
_MIN_SEGMENT_CHARS = 2

#: Joined between segments when rebuilding the full text.
_JOINER = "\n\n"


@dataclass
class Segment:
    """A located block of text within a source document."""

    index: int
    location_label: str
    text: str
    start_offset: int
    end_offset: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractionResult:
    status: str
    full_text: str = ""
    segments: list[Segment] = field(default_factory=list)
    page_count: int | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.full_text)

    @property
    def succeeded(self) -> bool:
        return self.status in (
            ExtractionStatus.EXTRACTED,
            ExtractionStatus.PARTIALLY_EXTRACTED,
        )

    def segments_json(self) -> str:
        return json.dumps([segment.to_dict() for segment in self.segments])

    def notes_text(self) -> str | None:
        return " ".join(self.notes) if self.notes else None


def segments_from_json(raw: str | None) -> list[Segment]:
    """Rebuild segments stored on a source row. Never raises on bad data."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    segments: list[Segment] = []
    for entry in payload if isinstance(payload, list) else []:
        try:
            segments.append(
                Segment(
                    index=int(entry["index"]),
                    location_label=str(entry["location_label"]),
                    text=str(entry["text"]),
                    start_offset=int(entry["start_offset"]),
                    end_offset=int(entry["end_offset"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return segments


def _assemble(blocks: list[tuple[str, str]]) -> tuple[str, list[Segment]]:
    """Turn ``(location_label, text)`` pairs into full text plus offsets."""
    segments: list[Segment] = []
    pieces: list[str] = []
    cursor = 0
    for location_label, text in blocks:
        cleaned = text.strip()
        if len(cleaned) < _MIN_SEGMENT_CHARS:
            continue
        start = cursor
        end = start + len(cleaned)
        segments.append(
            Segment(
                index=len(segments),
                location_label=location_label,
                text=cleaned,
                start_offset=start,
                end_offset=end,
            )
        )
        pieces.append(cleaned)
        cursor = end + len(_JOINER)
    return _JOINER.join(pieces), segments


# --------------------------------------------------------------------------
# Per-format extractors
# --------------------------------------------------------------------------

def _extract_pdf(path: Path) -> ExtractionResult:
    try:
        import pymupdf  # PyMuPDF >= 1.24 exposes this name; `fitz` is the alias
    except ImportError:  # pragma: no cover - dependency is pinned
        import fitz as pymupdf

    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        logger.warning("PDF could not be opened: %s", exc)
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            error="This PDF could not be opened. It may be corrupt or malformed.",
        )

    try:
        if document.needs_pass:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                error=(
                    "This PDF is password-protected. Remove the password and "
                    "upload it again."
                ),
            )

        page_count = document.page_count
        if page_count == 0:
            return ExtractionResult(
                status=ExtractionStatus.NO_TEXT_EXTRACTED,
                page_count=0,
                error="This PDF contains no pages.",
            )

        blocks: list[tuple[str, str]] = []
        empty_pages = 0
        for page_number in range(page_count):
            try:
                text = document.load_page(page_number).get_text("text") or ""
            except Exception as exc:  # a single bad page must not lose the rest
                logger.warning("PDF page %s failed: %s", page_number + 1, exc)
                empty_pages += 1
                continue
            if text.strip():
                blocks.append((f"page {page_number + 1}", text))
            else:
                empty_pages += 1
    finally:
        document.close()

    full_text, segments = _assemble(blocks)

    if not segments:
        return ExtractionResult(
            status=ExtractionStatus.NO_TEXT_EXTRACTED,
            page_count=page_count,
            error=(
                f"No text could be extracted from this PDF ({page_count} "
                f"page{'s' if page_count != 1 else ''}). It is most likely a "
                "scanned or image-only document. TRACE does not perform OCR, "
                "so this file cannot be used as a source."
            ),
        )

    notes: list[str] = []
    status = ExtractionStatus.EXTRACTED
    if empty_pages:
        status = ExtractionStatus.PARTIALLY_EXTRACTED
        notes.append(
            f"{empty_pages} of {page_count} pages contained no extractable "
            "text and were skipped (no OCR is performed)."
        )

    return ExtractionResult(
        status=status,
        full_text=full_text,
        segments=segments,
        page_count=page_count,
        notes=notes,
    )


def _extract_docx(path: Path) -> ExtractionResult:
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError

    try:
        document = Document(str(path))
    except PackageNotFoundError:
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            error="This DOCX file could not be opened. It may be corrupt.",
        )
    except Exception as exc:
        logger.warning("DOCX could not be opened: %s", exc)
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            error="This DOCX file could not be read.",
        )

    blocks: list[tuple[str, str]] = []
    for position, paragraph in enumerate(document.paragraphs, start=1):
        if paragraph.text.strip():
            blocks.append((f"paragraph {position}", paragraph.text))

    for table_number, table in enumerate(document.tables, start=1):
        for row_number, row in enumerate(table.rows, start=1):
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(
                    (f"table {table_number}, row {row_number}", " | ".join(cells))
                )

    full_text, segments = _assemble(blocks)
    if not segments:
        return ExtractionResult(
            status=ExtractionStatus.NO_TEXT_EXTRACTED,
            error=(
                "This DOCX file contains no readable text. Text inside images "
                "cannot be extracted because TRACE does not perform OCR."
            ),
        )
    return ExtractionResult(
        status=ExtractionStatus.EXTRACTED, full_text=full_text, segments=segments
    )


def _extract_txt(path: Path) -> ExtractionResult:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("TXT could not be read: %s", exc)
        return ExtractionResult(
            status=ExtractionStatus.FAILED, error="This file could not be read."
        )

    notes: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        notes.append(
            "Some characters could not be decoded as UTF-8 and were replaced."
        )

    # Blank lines separate paragraphs; this keeps references stable and
    # reproducible for the same input.
    blocks = [
        (f"paragraph {position}", chunk)
        for position, chunk in enumerate(text.split("\n\n"), start=1)
        if chunk.strip()
    ]

    full_text, segments = _assemble(blocks)
    if not segments:
        return ExtractionResult(
            status=ExtractionStatus.NO_TEXT_EXTRACTED,
            error="This text file is empty or contains only whitespace.",
        )

    status = (
        ExtractionStatus.PARTIALLY_EXTRACTED if notes else ExtractionStatus.EXTRACTED
    )
    return ExtractionResult(
        status=status, full_text=full_text, segments=segments, notes=notes
    )


_EXTRACTORS = {"pdf": _extract_pdf, "docx": _extract_docx, "txt": _extract_txt}


def extract_text(path: str | Path, extension: str) -> ExtractionResult:
    """Extract text and location metadata from a stored source file.

    This never raises for a bad document: every failure is returned as an
    ``ExtractionResult`` with a user-facing message, so one broken upload
    cannot take down the workspace.
    """
    file_path = Path(path)
    extractor = _EXTRACTORS.get(extension.lower())
    if extractor is None:
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            error=f"Unsupported file type: .{extension}",
        )
    if not file_path.is_file():
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            error="The stored file for this source is missing.",
        )

    try:
        return extractor(file_path)
    except Exception as exc:  # last-resort guard; details stay in the log
        logger.exception("Unexpected extraction failure: %s", exc)
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            error="This document could not be processed.",
        )
