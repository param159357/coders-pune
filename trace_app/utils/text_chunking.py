"""Deterministic chunking that preserves source and location metadata.

The same input always produces the same chunks, so a reconstruction can be
reasoned about and reproduced. Chunks are built from the located segments
produced during extraction, never by blindly slicing characters, so every
chunk can still say which pages or paragraphs it covers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trace_app.services.extraction_service import Segment, segments_from_json

#: Target size of one chunk, in characters.
DEFAULT_CHUNK_CHARS = 6000

#: Segments repeated at the start of the next chunk, so a statement split
#: across a boundary is still visible in full to at least one request.
DEFAULT_OVERLAP_SEGMENTS = 1

#: Ceiling on the source text sent in a single model request.
DEFAULT_REQUEST_CHARS = 60000


@dataclass
class Chunk:
    """A contiguous run of segments from one source."""

    source_id: str
    source_display_name: str
    index: int
    text: str
    location_labels: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def location_range(self) -> str:
        if not self.location_labels:
            return "unknown location"
        if len(self.location_labels) == 1:
            return self.location_labels[0]
        return f"{self.location_labels[0]} to {self.location_labels[-1]}"


def _segments_for(source: dict[str, Any]) -> list[Segment]:
    """Segments for a source, falling back to one whole-document segment."""
    segments = segments_from_json(source.get("segments_json"))
    if segments:
        return segments
    text = str(source.get("extracted_text") or "").strip()
    if not text:
        return []
    return [
        Segment(
            index=0,
            location_label="whole document",
            text=text,
            start_offset=0,
            end_offset=len(text),
        )
    ]


def chunk_source(
    source: dict[str, Any],
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_segments: int = DEFAULT_OVERLAP_SEGMENTS,
) -> list[Chunk]:
    """Split one source into labelled chunks."""
    segments = _segments_for(source)
    if not segments:
        return []

    source_id = str(source["id"])
    display_name = str(source.get("display_name", ""))

    chunks: list[Chunk] = []
    current: list[Segment] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        chunks.append(
            Chunk(
                source_id=source_id,
                source_display_name=display_name,
                index=len(chunks),
                text="\n\n".join(
                    f"[{segment.location_label}] {segment.text}" for segment in current
                ),
                location_labels=[segment.location_label for segment in current],
            )
        )
        # Carry the tail forward so a boundary-straddling statement survives.
        overlap = current[-overlap_segments:] if overlap_segments > 0 else []
        current = list(overlap)
        current_chars = sum(len(segment.text) for segment in current)

    for segment in segments:
        # A single oversized segment becomes its own chunk rather than being
        # dropped or silently truncated.
        if current and current_chars + len(segment.text) > chunk_chars:
            flush()
        current.append(segment)
        current_chars += len(segment.text)

    # Flush the remainder, without re-adding the overlap as a trailing chunk.
    if current:
        chunks.append(
            Chunk(
                source_id=source_id,
                source_display_name=display_name,
                index=len(chunks),
                text="\n\n".join(
                    f"[{segment.location_label}] {segment.text}" for segment in current
                ),
                location_labels=[segment.location_label for segment in current],
            )
        )

    # Drop a final chunk that only repeats the previous chunk's overlap.
    if len(chunks) >= 2 and chunks[-1].text in chunks[-2].text:
        chunks.pop()
    return chunks


def chunk_sources(
    sources: list[dict[str, Any]],
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_segments: int = DEFAULT_OVERLAP_SEGMENTS,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for source in sources:
        chunks.extend(chunk_source(source, chunk_chars, overlap_segments))
    return chunks


def batch_chunks(
    chunks: list[Chunk], request_chars: int = DEFAULT_REQUEST_CHARS
) -> list[list[Chunk]]:
    """Group chunks into model requests that stay under a character budget."""
    if not chunks:
        return []
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_chars = 0
    for chunk in chunks:
        if current and current_chars + chunk.char_count > request_chars:
            batches.append(current)
            current, current_chars = [], 0
        current.append(chunk)
        current_chars += chunk.char_count
    if current:
        batches.append(current)
    return batches


def render_batch(chunks: list[Chunk]) -> str:
    """Render a batch for the prompt, labelling each block with its real IDs.

    Source text is wrapped in explicit delimiters and introduced as data, so
    instructions embedded in a document are visibly not part of the task.
    """
    blocks: list[str] = []
    for chunk in chunks:
        blocks.append(
            f"<source id=\"{chunk.source_id}\" "
            f"filename=\"{chunk.source_display_name}\" "
            f"covers=\"{chunk.location_range}\">\n"
            f"{chunk.text}\n"
            f"</source>"
        )
    return "\n\n".join(blocks)
