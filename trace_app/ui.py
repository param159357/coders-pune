"""Shared presentation helpers: theme, status pills, and section headers.

Status is always rendered as a text label (optionally with a colour), never as
colour alone -- see spec section 10.
"""

from __future__ import annotations

import html

from trace_app.models import (
    EvidenceStatus,
    ExtractionStatus,
    ReconstructionStatus,
    ReviewStatus,
    evidence_label,
    extraction_label,
    reconstruction_label,
    review_label,
)

#: Restrained palette. One accent, plus muted semantic tones for status.
ACCENT = "#1F5F8B"

_TONES: dict[str, tuple[str, str, str]] = {
    # tone name -> (text colour, background, border)
    "neutral": ("#44525F", "#EFF2F5", "#D6DDE4"),
    "positive": ("#1B5E3F", "#E7F4EC", "#BFE0CC"),
    "caution": ("#7A5312", "#FDF3E0", "#EFD9AC"),
    "negative": ("#8C2F2F", "#FBECEC", "#EFC9C9"),
    "info": ("#1F5F8B", "#E9F1F7", "#C4DAE8"),
}

_STATUS_TONES: dict[str, str] = {
    # Source extraction
    ExtractionStatus.UPLOADED: "neutral",
    ExtractionStatus.EXTRACTED: "positive",
    ExtractionStatus.PARTIALLY_EXTRACTED: "caution",
    ExtractionStatus.NO_TEXT_EXTRACTED: "caution",
    ExtractionStatus.FAILED: "negative",
    # Reconstruction
    ReconstructionStatus.NOT_STARTED: "neutral",
    ReconstructionStatus.PROCESSING: "info",
    ReconstructionStatus.DRAFT_READY: "positive",
    ReconstructionStatus.FAILED: "negative",
    # Review
    ReviewStatus.PROPOSED: "info",
    ReviewStatus.APPROVED: "positive",
    ReviewStatus.REJECTED: "negative",
    # Evidence
    EvidenceStatus.SUPPORTED: "positive",
    EvidenceStatus.PARTIALLY_SUPPORTED: "caution",
    EvidenceStatus.CONFLICTING: "caution",
    EvidenceStatus.UNSUPPORTED: "negative",
    EvidenceStatus.NEEDS_REVIEW: "info",
}

GLOBAL_CSS = f"""
<style>
  /* Tighten Streamlit's default chrome into a denser product layout. */
  .block-container {{ padding-top: 2.1rem; padding-bottom: 3rem; max-width: 1180px; }}
  #MainMenu, footer {{ visibility: hidden; }}

  h1, h2, h3, h4 {{ letter-spacing: -0.011em; }}
  h1 {{ font-size: 1.85rem !important; font-weight: 650 !important; }}
  h2 {{ font-size: 1.28rem !important; font-weight: 620 !important; margin-top: 0.4rem; }}
  h3 {{ font-size: 1.04rem !important; font-weight: 600 !important; }}

  /* Product wordmark in the sidebar */
  .trace-brand {{ font-size: 1.32rem; font-weight: 680; letter-spacing: 0.055em;
                  color: {ACCENT}; margin-bottom: 0.1rem; }}
  .trace-brand-sub {{ font-size: 0.78rem; color: #6B7A88; line-height: 1.35;
                      margin-bottom: 0.85rem; }}

  /* Status pill -- always carries a text label */
  .trace-pill {{ display: inline-block; padding: 0.13rem 0.55rem; border-radius: 999px;
                 font-size: 0.74rem; font-weight: 600; line-height: 1.5;
                 border: 1px solid transparent; white-space: nowrap; }}

  /* Small label above a value */
  .trace-meta-label {{ font-size: 0.7rem; text-transform: uppercase;
                       letter-spacing: 0.06em; color: #7A8794; font-weight: 600; }}
  .trace-meta-value {{ font-size: 0.92rem; color: #1A2027; }}

  /* Card used for project rows and item blocks */
  .trace-card {{ border: 1px solid #E2E8ED; border-radius: 10px; padding: 1rem 1.15rem;
                 background: #FFFFFF; margin-bottom: 0.7rem; }}
  .trace-card-title {{ font-size: 1.04rem; font-weight: 620; color: #1A2027;
                       margin-bottom: 0.18rem; }}
  .trace-card-desc {{ font-size: 0.88rem; color: #5A6872; line-height: 1.45; }}

  .trace-divider {{ border-top: 1px solid #E8EDF1; margin: 1.45rem 0 1.15rem; }}
  .trace-note {{ font-size: 0.82rem; color: #6B7A88; line-height: 1.5; }}

  /* Synthetic-data marker */
  .trace-demo-tag {{ display: inline-block; font-size: 0.68rem; font-weight: 700;
                     letter-spacing: 0.07em; color: #7A5312; background: #FDF3E0;
                     border: 1px solid #EFD9AC; border-radius: 4px;
                     padding: 0.08rem 0.4rem; margin-left: 0.45rem;
                     vertical-align: middle; }}
</style>
"""


def pill(label: str, tone: str = "neutral") -> str:
    """Return HTML for a labelled status pill."""
    text, background, border = _TONES.get(tone, _TONES["neutral"])
    safe = html.escape(str(label))
    return (
        f'<span class="trace-pill" style="color:{text};background:{background};'
        f'border-color:{border}">{safe}</span>'
    )


def _status_pill(value: str | None, label_text: str) -> str:
    return pill(label_text, _STATUS_TONES.get(value or "", "neutral"))


def extraction_pill(value: str | None) -> str:
    return _status_pill(value, extraction_label(value))


def reconstruction_pill(value: str | None) -> str:
    return _status_pill(value, reconstruction_label(value))


def review_pill(value: str | None) -> str:
    return _status_pill(value, review_label(value))


def evidence_pill(value: str | None) -> str:
    return _status_pill(value, evidence_label(value))


def meta_block(label: str, value: str) -> str:
    """A small uppercase label above a value, for metadata rows."""
    return (
        f'<div class="trace-meta-label">{html.escape(label)}</div>'
        f'<div class="trace-meta-value">{html.escape(str(value))}</div>'
    )


def demo_tag() -> str:
    return '<span class="trace-demo-tag">SYNTHETIC DEMO</span>'


def divider() -> str:
    return '<div class="trace-divider"></div>'
