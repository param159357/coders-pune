"""Handover report generation in Markdown and PDF.

What goes in is decided by review state, not by the model. Approved items are
included; rejected items never are. Unreviewed items are excluded by default
and, if the user chooses to include them, are segregated into their own
section and labelled on every item, so an approved claim and an unreviewed one
can never be mistaken for each other.

Every included claim carries its evidence: the source filename, the location,
and the exact excerpt, plus whether that excerpt was verified against the
source. The report also carries what the reconstruction could *not* establish,
because the gaps are part of a truthful handover.

Nothing from the environment is read here, so no key, token, or path can reach
a report.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field
from typing import Any

from trace_app.models import (
    CATEGORY_ORDER,
    EvidenceStatus,
    ReviewStatus,
    category_label,
    evidence_label,
    format_timestamp,
    utc_now,
)

#: Defence in depth. Report content comes from the database, not the
#: environment, but anything matching these shapes is redacted before it can
#: reach a downloadable file.
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),            # Google API keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),          # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),       # Slack tokens
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),
    re.compile(
        r"(?i)\b(?:api[_\- ]?key|secret|password|passwd|token)\b\s*[:=]\s*\S+"
    ),
)

REDACTION = "[redacted]"


def redact(text: str | None) -> str:
    """Remove anything shaped like a credential."""
    cleaned = str(text or "")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTION, cleaned)
    return cleaned


@dataclass
class ReportOptions:
    """What the user chose to include."""

    include_unreviewed: bool = False
    include_evidence: bool = True


@dataclass
class ReportData:
    """Everything a report needs, already filtered by review state."""

    project: dict[str, Any]
    reconstruction: dict[str, Any]
    approved: list[dict[str, Any]] = field(default_factory=list)
    unreviewed: list[dict[str, Any]] = field(default_factory=list)
    rejected_count: int = 0
    missing_information: list[str] = field(default_factory=list)
    generated_at: str = ""

    @property
    def is_preview(self) -> bool:
        return bool(self.reconstruction.get("is_demo_preview"))

    @property
    def included_count(self) -> int:
        return len(self.approved) + len(self.unreviewed)


def collect_report_data(
    project: dict[str, Any],
    reconstruction: dict[str, Any],
    items: list[dict[str, Any]],
    missing_information: list[str],
    options: ReportOptions,
) -> ReportData:
    """Split reviewed items into what may be exported and what may not."""
    approved: list[dict[str, Any]] = []
    unreviewed: list[dict[str, Any]] = []
    rejected = 0

    for item in items:
        status = str(item.get("review_status"))
        if status == ReviewStatus.APPROVED:
            approved.append(item)
        elif status == ReviewStatus.REJECTED:
            rejected += 1  # never exported, under any option
        elif options.include_unreviewed:
            unreviewed.append(item)

    return ReportData(
        project=project,
        reconstruction=reconstruction,
        approved=approved,
        unreviewed=unreviewed,
        rejected_count=rejected,
        missing_information=list(missing_information),
        generated_at=utc_now(),
    )


def _group(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_category.setdefault(str(item["category"]), []).append(item)
    ordered = [
        (category, by_category.pop(category))
        for category in CATEGORY_ORDER
        if category in by_category
    ]
    ordered.extend(by_category.items())
    return ordered


def _evidence_lines(item: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in item.get("evidence") or []:
        state = "verified" if row.get("verified") else "NOT VERIFIED"
        location = row.get("location_label") or "location not recorded"
        lines.append(
            f"{redact(row.get('source_display_name'))} — {redact(location)} "
            f"({state}): “{redact(row.get('excerpt'))}”"
        )
    return lines


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def build_markdown(data: ReportData, options: ReportOptions) -> str:
    project = data.project
    lines: list[str] = []
    add = lines.append

    add(f"# Handover — {redact(project['name'])}")
    add("")
    if project.get("description"):
        add(redact(project["description"]))
        add("")

    add("## About this report")
    add("")
    add(f"- **Generated:** {format_timestamp(data.generated_at)}")
    if project.get("team_label"):
        add(f"- **Team:** {redact(project['team_label'])}")
    add(f"- **Sources analysed:** {data.reconstruction.get('source_count', 0)}")
    add(
        f"- **Reconstruction run:** "
        f"{format_timestamp(data.reconstruction.get('created_at'))}"
    )
    if data.is_preview:
        add(
            "- **Produced by:** deterministic keyword matching. "
            "**No AI model was called.**"
        )
    else:
        add(
            f"- **Produced by:** {redact(data.reconstruction.get('model_provider'))}"
            f" / {redact(data.reconstruction.get('model_name'))}"
        )
    add("")
    add("### Review scope")
    add("")
    add(f"- **Approved items included:** {len(data.approved)}")
    if options.include_unreviewed:
        add(
            f"- **Unreviewed items included:** {len(data.unreviewed)} "
            "— clearly marked below, and **not** approved by a reviewer"
        )
    else:
        add("- **Unreviewed items:** excluded from this report")
    add(f"- **Rejected items:** {data.rejected_count} — excluded from this report")
    add("")

    if data.is_preview:
        add(
            "> **This draft was not produced by an AI model.** It was produced "
            "by deterministic keyword matching, which quotes sentences "
            "containing category cue words and makes no judgement about them."
        )
        add("")

    if not data.approved and not data.unreviewed:
        add("## No content was approved for this handover")
        add("")
        add(
            "No handover item has been approved, so this report contains no "
            "claims. This is a true statement of the review state, not an "
            "error."
        )
        add("")
    else:
        add("---")
        add("")
        add("## Handover content")
        add("")
        for category, group in _group(data.approved):
            add(f"### {category_label(category)}")
            add("")
            for item in group:
                add(f"- {redact(item['text'])}")
                meta = []
                if item.get("owner"):
                    meta.append(f"Owner: {redact(item['owner'])}")
                if item.get("due_date"):
                    meta.append(f"Due: {redact(item['due_date'])}")
                meta.append(
                    f"Evidence: {evidence_label(str(item.get('evidence_status')))}"
                )
                add(f"  - *{' · '.join(meta)}*")
                if item.get("reviewer_note"):
                    add(f"  - *Reviewer note: {redact(item['reviewer_note'])}*")
                if options.include_evidence:
                    for line in _evidence_lines(item):
                        add(f"    - {line}")
            add("")

    if options.include_unreviewed and data.unreviewed:
        add("---")
        add("")
        add("## Unreviewed items — NOT APPROVED")
        add("")
        add(
            "No reviewer has accepted the items below. They are included "
            "because this report was generated with unreviewed content "
            "enabled. Confirm each one before relying on it."
        )
        add("")
        for category, group in _group(data.unreviewed):
            add(f"### {category_label(category)} (unreviewed)")
            add("")
            for item in group:
                add(f"- **[UNREVIEWED]** {redact(item['text'])}")
                add(
                    f"  - *Evidence: "
                    f"{evidence_label(str(item.get('evidence_status')))}*"
                )
                if options.include_evidence:
                    for line in _evidence_lines(item):
                        add(f"    - {line}")
            add("")

    add("---")
    add("")
    add("## Limitations and items requiring confirmation")
    add("")
    for entry in data.missing_information:
        add(f"- {redact(entry)}")
    weak = [
        item
        for item in data.approved
        if str(item.get("evidence_status")) != EvidenceStatus.SUPPORTED
    ]
    if weak:
        add(
            f"- {len(weak)} approved item(s) rest on evidence that is not fully "
            "supported. Each is labelled with its evidence status above."
        )
    unverified = sum(
        1
        for item in data.approved
        for row in (item.get("evidence") or [])
        if not row.get("verified")
    )
    if unverified:
        add(
            f"- {unverified} citation(s) in this report could not be matched to "
            "the text of the source they name, and are marked NOT VERIFIED."
        )
    add(
        "- Evidence references make verification possible. They do not "
        "guarantee that an interpretation drawn from the source is correct."
    )
    add(
        "- This handover reflects only the documents uploaded to this project. "
        "Anything not in those documents is not in this report."
    )
    add("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def _pdf_styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    accent = colors.HexColor("#1F5F8B")
    muted = colors.HexColor("#5A6872")

    return {
        "title": ParagraphStyle(
            "TraceTitle", parent=base["Title"], fontSize=20, leading=25,
            textColor=colors.HexColor("#1A2027"), alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "TraceSubtitle", parent=base["Normal"], fontSize=10.5, leading=15,
            textColor=muted, spaceAfter=14,
        ),
        "h2": ParagraphStyle(
            "TraceH2", parent=base["Heading2"], fontSize=13.5, leading=17,
            textColor=accent, spaceBefore=16, spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "TraceH3", parent=base["Heading3"], fontSize=11.5, leading=15,
            textColor=colors.HexColor("#1A2027"), spaceBefore=11, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "TraceBody", parent=base["Normal"], fontSize=10, leading=14.5,
            spaceAfter=3,
        ),
        "meta": ParagraphStyle(
            "TraceMeta", parent=base["Normal"], fontSize=8.8, leading=12.5,
            textColor=muted, leftIndent=14, spaceAfter=2,
        ),
        "evidence": ParagraphStyle(
            "TraceEvidence", parent=base["Normal"], fontSize=8.5, leading=12,
            textColor=colors.HexColor("#44525F"), leftIndent=26,
            spaceAfter=2, borderPadding=0,
        ),
        "warning": ParagraphStyle(
            "TraceWarning", parent=base["Normal"], fontSize=9.5, leading=13.5,
            textColor=colors.HexColor("#7A5312"),
            backColor=colors.HexColor("#FDF3E0"), borderPadding=7,
            spaceBefore=6, spaceAfter=10,
        ),
    }


def _escape(text: str | None) -> str:
    """Escape for ReportLab's mini-markup, after redaction."""
    return html.escape(redact(text))


def build_pdf(data: ReportData, options: ReportOptions) -> bytes:
    """Render the same content as the Markdown report into a PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    styles = _pdf_styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Handover — {redact(data.project['name'])}",
        author="TRACE",
    )

    flow: list[Any] = []
    flow.append(Paragraph(f"Handover — {_escape(data.project['name'])}", styles["title"]))
    if data.project.get("description"):
        flow.append(Paragraph(_escape(data.project["description"]), styles["subtitle"]))
    flow.append(HRFlowable(width="100%", thickness=0.6, color="#D6DDE4"))
    flow.append(Spacer(1, 10))

    produced = (
        "deterministic keyword matching (no AI model was called)"
        if data.is_preview
        else f"{data.reconstruction.get('model_provider')} / "
        f"{data.reconstruction.get('model_name')}"
    )
    for label, value in [
        ("Generated", format_timestamp(data.generated_at)),
        ("Sources analysed", str(data.reconstruction.get("source_count", 0))),
        ("Reconstruction run", format_timestamp(data.reconstruction.get("created_at"))),
        ("Produced by", produced),
        ("Approved items included", str(len(data.approved))),
        (
            "Unreviewed items",
            f"{len(data.unreviewed)} included, clearly marked"
            if options.include_unreviewed
            else "excluded",
        ),
        ("Rejected items", f"{data.rejected_count} — excluded"),
    ]:
        flow.append(
            Paragraph(f"<b>{_escape(label)}:</b> {_escape(value)}", styles["body"])
        )

    if data.is_preview:
        flow.append(
            Paragraph(
                "<b>This draft was not produced by an AI model.</b> It was "
                "produced by deterministic keyword matching, which quotes "
                "sentences containing category cue words and makes no "
                "judgement about them.",
                styles["warning"],
            )
        )

    if not data.approved and not data.unreviewed:
        flow.append(Paragraph("No content was approved", styles["h2"]))
        flow.append(
            Paragraph(
                "No handover item has been approved, so this report contains "
                "no claims. This is a true statement of the review state, not "
                "an error.",
                styles["body"],
            )
        )
    else:
        flow.append(Paragraph("Handover content", styles["h2"]))
        for category, group in _group(data.approved):
            flow.append(Paragraph(_escape(category_label(category)), styles["h3"]))
            for item in group:
                flow.append(Paragraph(f"• {_escape(item['text'])}", styles["body"]))
                meta = []
                if item.get("owner"):
                    meta.append(f"Owner: {item['owner']}")
                if item.get("due_date"):
                    meta.append(f"Due: {item['due_date']}")
                meta.append(
                    f"Evidence: {evidence_label(str(item.get('evidence_status')))}"
                )
                flow.append(Paragraph(_escape(" · ".join(meta)), styles["meta"]))
                if item.get("reviewer_note"):
                    flow.append(
                        Paragraph(
                            f"Reviewer note: {_escape(item['reviewer_note'])}",
                            styles["meta"],
                        )
                    )
                if options.include_evidence:
                    for line in _evidence_lines(item):
                        flow.append(Paragraph(_escape(line), styles["evidence"]))

    if options.include_unreviewed and data.unreviewed:
        flow.append(PageBreak())
        flow.append(Paragraph("Unreviewed items — NOT APPROVED", styles["h2"]))
        flow.append(
            Paragraph(
                "No reviewer has accepted the items below. Confirm each one "
                "before relying on it.",
                styles["warning"],
            )
        )
        for category, group in _group(data.unreviewed):
            flow.append(
                Paragraph(
                    f"{_escape(category_label(category))} (unreviewed)", styles["h3"]
                )
            )
            for item in group:
                flow.append(
                    Paragraph(
                        f"• <b>[UNREVIEWED]</b> {_escape(item['text'])}",
                        styles["body"],
                    )
                )
                flow.append(
                    Paragraph(
                        _escape(
                            "Evidence: "
                            + evidence_label(str(item.get("evidence_status")))
                        ),
                        styles["meta"],
                    )
                )
                if options.include_evidence:
                    for line in _evidence_lines(item):
                        flow.append(Paragraph(_escape(line), styles["evidence"]))

    flow.append(Spacer(1, 12))
    flow.append(HRFlowable(width="100%", thickness=0.6, color="#D6DDE4"))
    flow.append(Paragraph("Limitations and items requiring confirmation", styles["h2"]))
    limitations = list(data.missing_information)
    unverified = sum(
        1
        for item in data.approved
        for row in (item.get("evidence") or [])
        if not row.get("verified")
    )
    if unverified:
        limitations.append(
            f"{unverified} citation(s) in this report could not be matched to "
            "the text of the source they name, and are marked NOT VERIFIED."
        )
    limitations.append(
        "Evidence references make verification possible. They do not guarantee "
        "that an interpretation drawn from the source is correct."
    )
    limitations.append(
        "This handover reflects only the documents uploaded to this project. "
        "Anything not in those documents is not in this report."
    )
    for entry in limitations:
        flow.append(Paragraph(f"• {_escape(entry)}", styles["body"]))

    document.build(flow)
    return buffer.getvalue()


def report_filename(project_name: str, extension: str) -> str:
    """A safe, descriptive download filename."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(project_name or "handover")).strip("-")
    slug = (slug or "handover").lower()[:60]
    stamp = utc_now()[:10]
    return f"trace-handover-{slug}-{stamp}.{extension}"
