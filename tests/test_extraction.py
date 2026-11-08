"""Document extraction tests (spec section 14)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trace_app.models import ExtractionStatus
from trace_app.services.extraction_service import (
    extract_text,
    segments_from_json,
)
from trace_app.utils.file_validation import (
    FileValidationError,
    build_stored_path,
    get_extension,
    human_size,
    safe_display_name,
    validate_upload,
)


# --------------------------------------------------------------------------
# TXT
# --------------------------------------------------------------------------

def test_txt_extraction_splits_into_numbered_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("First paragraph.\n\nSecond paragraph.\n\nThird.", encoding="utf-8")
    result = extract_text(path, "txt")
    assert result.status == ExtractionStatus.EXTRACTED
    assert [s.location_label for s in result.segments] == [
        "paragraph 1",
        "paragraph 2",
        "paragraph 3",
    ]
    assert result.char_count == len(result.full_text)


def test_txt_offsets_point_at_the_exact_text(tmp_path: Path) -> None:
    """Offsets must index back into full_text exactly; evidence relies on it."""
    path = tmp_path / "notes.txt"
    path.write_text("Alpha block.\n\nBeta block.\n\nGamma block.", encoding="utf-8")
    result = extract_text(path, "txt")
    for segment in result.segments:
        assert (
            result.full_text[segment.start_offset : segment.end_offset] == segment.text
        )


def test_empty_txt_is_reported_as_no_text(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("   \n\n   \n", encoding="utf-8")
    result = extract_text(path, "txt")
    assert result.status == ExtractionStatus.NO_TEXT_EXTRACTED
    assert not result.succeeded
    assert result.error


def test_undecodable_bytes_are_flagged_not_silently_dropped(tmp_path: Path) -> None:
    path = tmp_path / "latin.txt"
    path.write_bytes("Caf\xe9 costs \xa35".encode("latin-1"))
    result = extract_text(path, "txt")
    assert result.status == ExtractionStatus.PARTIALLY_EXTRACTED
    assert result.notes and "UTF-8" in result.notes[0]


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def test_pdf_extraction_preserves_page_numbers(make_pdf) -> None:
    path = make_pdf(
        "brief.pdf",
        [
            "Initial release focuses on event discovery.",
            "Payment integration was postponed.",
            "Registration validation remains unfinished.",
        ],
    )
    result = extract_text(path, "pdf")
    assert result.status == ExtractionStatus.EXTRACTED
    assert result.page_count == 3
    assert [s.location_label for s in result.segments] == ["page 1", "page 2", "page 3"]
    assert "postponed" in result.segments[1].text


def test_image_only_pdf_is_never_reported_as_extracted(make_pdf) -> None:
    """A scanned PDF must not be labelled as successfully text-extracted."""
    path = make_pdf("scanned.pdf", ["", ""])
    result = extract_text(path, "pdf")
    assert result.status == ExtractionStatus.NO_TEXT_EXTRACTED
    assert result.status != ExtractionStatus.EXTRACTED
    assert not result.succeeded
    assert result.char_count == 0
    assert "OCR" in (result.error or "")


def test_pdf_with_some_blank_pages_is_partially_extracted(make_pdf) -> None:
    path = make_pdf("mixed.pdf", ["Only this page has text.", "", ""])
    result = extract_text(path, "pdf")
    assert result.status == ExtractionStatus.PARTIALLY_EXTRACTED
    assert result.succeeded
    assert result.notes and "2 of 3 pages" in result.notes[0]


def test_encrypted_pdf_fails_with_a_clear_message(make_pdf) -> None:
    path = make_pdf("locked.pdf", ["secret"], encrypt=True)
    result = extract_text(path, "pdf")
    assert result.status == ExtractionStatus.FAILED
    assert "password" in (result.error or "").lower()


def test_corrupt_pdf_fails_safely(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\nnot actually a pdf at all")
    result = extract_text(path, "pdf")
    assert result.status == ExtractionStatus.FAILED
    assert result.error


# --------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------

def test_docx_extraction_numbers_paragraphs_and_tables(make_docx) -> None:
    path = make_docx(
        "status.docx",
        ["The team selected option A.", "", "Validation is unfinished."],
        table=[["Task", "Owner"], ["Accessibility review", "Unassigned"]],
    )
    result = extract_text(path, "docx")
    assert result.status == ExtractionStatus.EXTRACTED
    labels = [s.location_label for s in result.segments]
    assert "paragraph 1" in labels
    assert any(label.startswith("table 1, row") for label in labels)
    assert "Accessibility review | Unassigned" in result.full_text


def test_empty_docx_is_reported_as_no_text(make_docx) -> None:
    path = make_docx("blank.docx", ["", "   "])
    result = extract_text(path, "docx")
    assert result.status == ExtractionStatus.NO_TEXT_EXTRACTED


def test_corrupt_docx_fails_safely(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.docx"
    path.write_bytes(b"PK\x03\x04" + b"garbage" * 20)
    result = extract_text(path, "docx")
    assert result.status == ExtractionStatus.FAILED
    assert result.error


# --------------------------------------------------------------------------
# Generic extractor behaviour
# --------------------------------------------------------------------------

def test_missing_file_fails_without_raising(tmp_path: Path) -> None:
    result = extract_text(tmp_path / "nope.txt", "txt")
    assert result.status == ExtractionStatus.FAILED


def test_unsupported_extension_fails_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "thing.csv"
    path.write_text("a,b,c", encoding="utf-8")
    result = extract_text(path, "csv")
    assert result.status == ExtractionStatus.FAILED


def test_segments_survive_a_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("One.\n\nTwo.", encoding="utf-8")
    result = extract_text(path, "txt")
    restored = segments_from_json(result.segments_json())
    assert [s.location_label for s in restored] == [
        s.location_label for s in result.segments
    ]
    assert [s.start_offset for s in restored] == [
        s.start_offset for s in result.segments
    ]


@pytest.mark.parametrize("bad", [None, "", "not json", "{}", '[{"index": 1}]'])
def test_malformed_segment_json_never_raises(bad) -> None:
    assert segments_from_json(bad) == []


# --------------------------------------------------------------------------
# Upload validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("../../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\config", "config"),
        ("C:\\Users\\someone\\notes.txt", "notes.txt"),
        ("with\x00nul.txt", "withnul.txt"),
        ("", "untitled"),
        (None, "untitled"),
    ],
)
def test_display_names_are_stripped_of_path_components(raw, expected) -> None:
    assert safe_display_name(raw) == expected


def test_valid_txt_upload_is_accepted() -> None:
    validated = validate_upload("notes.txt", b"hello world")
    assert validated.extension == "txt"
    assert validated.size_bytes == 11
    assert len(validated.content_hash) == 64


def test_identical_content_hashes_to_the_same_value() -> None:
    first = validate_upload("a.txt", b"same bytes")
    second = validate_upload("b.txt", b"same bytes")
    assert first.content_hash == second.content_hash


@pytest.mark.parametrize(
    "name, content",
    [
        ("script.exe", b"MZ\x90\x00"),
        ("data.csv", b"a,b,c"),
        ("archive.zip", b"PK\x03\x04"),
        ("noext", b"text"),
    ],
)
def test_disallowed_extensions_are_rejected(name, content) -> None:
    with pytest.raises(FileValidationError):
        validate_upload(name, content)


def test_empty_upload_is_rejected() -> None:
    with pytest.raises(FileValidationError, match="empty"):
        validate_upload("notes.txt", b"")


def test_oversized_upload_is_rejected(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    with pytest.raises(FileValidationError, match="over the"):
        validate_upload("notes.txt", b"this is definitely longer than ten bytes")


def test_a_file_that_is_not_really_a_pdf_is_rejected() -> None:
    """The declared extension alone is not trusted."""
    with pytest.raises(FileValidationError, match="PDF header"):
        validate_upload("fake.pdf", b"I am plain text pretending to be a PDF")


def test_a_legacy_doc_renamed_to_docx_is_rejected() -> None:
    with pytest.raises(FileValidationError, match="DOCX"):
        validate_upload("old.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")


def test_binary_content_in_a_txt_file_is_rejected() -> None:
    with pytest.raises(FileValidationError, match="binary"):
        validate_upload("notes.txt", b"text\x00\x01\x02binary")


def test_stored_paths_stay_inside_the_upload_directory(isolated_storage) -> None:
    path = build_stored_path("src_abc12345", "pdf")
    assert path.parent == isolated_storage.resolve()
    assert path.name == "src_abc12345.pdf"


@pytest.mark.parametrize("bad_id", ["../evil", "src/../../x", "a b", "src.1", ""])
def test_stored_paths_reject_unsafe_identifiers(bad_id, isolated_storage) -> None:
    with pytest.raises(FileValidationError):
        build_stored_path(bad_id, "txt")


def test_human_size_is_readable() -> None:
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


def test_get_extension_is_case_insensitive() -> None:
    assert get_extension("Report.PDF") == "pdf"
    assert get_extension("notes.TxT") == "txt"
