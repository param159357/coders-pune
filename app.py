"""TRACE -- Project Knowledge and Handover System (Streamlit entry point).

Run with:  streamlit run app.py
"""

from __future__ import annotations

import html

import streamlit as st

import config
from database.connection import DatabaseError, get_connection
from database.schema import initialise_database
from trace_app import ui
from trace_app.models import (
    EVIDENCE_LABELS,
    ExtractionStatus,
    ReconstructionStatus,
    format_timestamp,
)
from trace_app.services.document_service import DocumentService, extraction_summary
from trace_app.services.extraction_service import segments_from_json
from trace_app.services.project_service import ProjectService, ValidationError
from trace_app.services.reconstruction_service import ReconstructionService
from trace_app.services.review_service import (
    ReviewError,
    ReviewService,
    needs_attention,
)
from trace_app.utils.file_validation import human_size

PAGE_DASHBOARD = "Dashboard"
PAGE_WORKSPACE = "Project Workspace"
PAGE_REVIEW = "Review Handover"
NAV_PAGES = [PAGE_DASHBOARD, PAGE_WORKSPACE, PAGE_REVIEW]


# --------------------------------------------------------------------------
# Shared resources
# --------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _database():
    """One shared connection per server process, with the schema applied."""
    config.ensure_directories()
    connection = get_connection()
    initialise_database(connection)
    return connection


def _project_service() -> ProjectService:
    return ProjectService(_database())


def _document_service() -> DocumentService:
    return DocumentService(_database())


def _reconstruction_service() -> ReconstructionService:
    return ReconstructionService(_database())


def _review_service() -> ReviewService:
    return ReviewService(_database())


def _goto(page: str, project_id: str | None = None) -> None:
    st.session_state["page"] = page
    if project_id is not None:
        st.session_state["project_id"] = project_id
    st.rerun()


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

def _render_sidebar() -> str:
    with st.sidebar:
        st.markdown(
            f'<div class="trace-brand">{config.APP_NAME}</div>'
            f'<div class="trace-brand-sub">{config.APP_TAGLINE}</div>',
            unsafe_allow_html=True,
        )

        current = st.session_state.get("page", PAGE_DASHBOARD)
        if current not in NAV_PAGES:
            current = PAGE_DASHBOARD
        page = st.radio(
            "Navigation",
            NAV_PAGES,
            index=NAV_PAGES.index(current),
            label_visibility="collapsed",
        )
        if page != current:
            st.session_state["page"] = page

        st.markdown(ui.divider(), unsafe_allow_html=True)

        # Honest reporting of AI configuration -- no key means no reconstruction.
        llm = config.get_llm_config()
        st.markdown('<div class="trace-meta-label">AI provider</div>', unsafe_allow_html=True)
        if llm.is_configured:
            st.markdown(
                ui.pill("Configured", "positive")
                + f'<div class="trace-note" style="margin-top:0.35rem">'
                f"{html.escape(llm.provider)} &middot; {html.escape(llm.model)}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                ui.pill("Not configured", "caution")
                + '<div class="trace-note" style="margin-top:0.35rem">'
                "Set <code>LLM_API_KEY</code> to enable AI reconstruction.</div>",
                unsafe_allow_html=True,
            )

        st.markdown(ui.divider(), unsafe_allow_html=True)
        st.markdown(
            f'<div class="trace-note">{html.escape(config.AI_DISCLAIMER)}</div>',
            unsafe_allow_html=True,
        )

    return st.session_state.get("page", PAGE_DASHBOARD)


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

def _render_create_project_form(service: ProjectService) -> None:
    with st.form("create_project", clear_on_submit=False):
        st.markdown("### Create a project workspace")
        name = st.text_input("Project name *", max_chars=120, placeholder="e.g. CampusConnect")
        description = st.text_area(
            "Description",
            max_chars=2000,
            placeholder="What is this project, and who is it for?",
            height=80,
        )
        team_label = st.text_input(
            "Team or organisation", max_chars=120, placeholder="Optional"
        )
        submitted = st.form_submit_button("Create project", type="primary")

    if not submitted:
        return
    try:
        project = service.create_project(name, description, team_label)
    except ValidationError as exc:
        st.error(str(exc))
    except DatabaseError as exc:
        st.error(f"The project could not be saved: {exc}")
    else:
        st.session_state["flash"] = f"Created project “{project['name']}”."
        _goto(PAGE_WORKSPACE, project["id"])


def _render_project_card(project: dict, service: ProjectService) -> None:
    del service  # not needed yet; kept for signature stability
    name = html.escape(project["name"])
    tag = ui.demo_tag() if project.get("is_demo") else ""
    description = project.get("description") or "No description provided."

    with st.container():
        left, right = st.columns([5, 1.25], vertical_alignment="center")
        with left:
            st.markdown(
                f'<div class="trace-card-title">{name}{tag}</div>'
                f'<div class="trace-card-desc">{html.escape(description)}</div>',
                unsafe_allow_html=True,
            )
            source_count = project.get("source_count", 0)
            extracted = project.get("extracted_count", 0)
            sources_text = (
                "No sources yet"
                if source_count == 0
                else f"{source_count} source{'s' if source_count != 1 else ''}"
                f" &middot; {extracted} extracted"
            )
            st.markdown(
                '<div class="trace-note" style="margin-top:0.5rem">'
                + ui.reconstruction_pill(project.get("latest_reconstruction_status"))
                + f'<span style="margin-left:0.6rem">{sources_text}</span>'
                f'<span style="margin-left:0.6rem">Created '
                f"{html.escape(format_timestamp(project.get('created_at')))}</span>"
                "</div>",
                unsafe_allow_html=True,
            )
        with right:
            if st.button("Open", key=f"open_{project['id']}", width="stretch"):
                _goto(PAGE_WORKSPACE, project["id"])
    st.markdown(ui.divider(), unsafe_allow_html=True)


def _render_dashboard(service: ProjectService) -> None:
    st.markdown(f"# {config.APP_NAME}")
    st.markdown(
        f'<div class="trace-card-desc" style="max-width:62ch;margin-bottom:0.3rem">'
        f"{html.escape(config.APP_DESCRIPTION)}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(ui.divider(), unsafe_allow_html=True)

    if flash := st.session_state.pop("flash", None):
        st.success(flash)

    try:
        projects = service.list_projects()
    except DatabaseError as exc:
        st.error(f"Projects could not be loaded: {exc}")
        return

    listing, creator = st.columns([1.65, 1], gap="large")

    with listing:
        st.markdown("## Projects")
        if not projects:
            st.markdown(
                '<div class="trace-card">'
                '<div class="trace-card-title">No projects yet</div>'
                '<div class="trace-card-desc">Create a project workspace to start '
                "adding source documents. Everything in TRACE belongs to a project, "
                "so sources and handover items stay isolated from each other.</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            for project in projects:
                _render_project_card(project, service)

    with creator:
        _render_create_project_form(service)


# --------------------------------------------------------------------------
# Project workspace
# --------------------------------------------------------------------------

def _render_workspace(service: ProjectService) -> None:
    project_id = st.session_state.get("project_id")
    if not project_id:
        st.markdown("# Project Workspace")
        st.info("Select a project from the Dashboard to open its workspace.")
        if st.button("Go to Dashboard"):
            _goto(PAGE_DASHBOARD)
        return

    try:
        project = service.require_project(project_id)
    except ValidationError as exc:
        st.error(str(exc))
        if st.button("Go to Dashboard"):
            st.session_state.pop("project_id", None)
            _goto(PAGE_DASHBOARD)
        return

    if flash := st.session_state.pop("flash", None):
        st.success(flash)

    tag = ui.demo_tag() if project.get("is_demo") else ""
    st.markdown(f"# {html.escape(project['name'])}{tag}", unsafe_allow_html=True)
    if project.get("description"):
        st.markdown(
            f'<div class="trace-card-desc" style="max-width:70ch">'
            f"{html.escape(project['description'])}</div>",
            unsafe_allow_html=True,
        )

    columns = st.columns(3)
    with columns[0]:
        st.markdown(
            ui.meta_block("Created", format_timestamp(project.get("created_at"))),
            unsafe_allow_html=True,
        )
    with columns[1]:
        st.markdown(
            ui.meta_block("Team", project.get("team_label") or "Not specified"),
            unsafe_allow_html=True,
        )
    with columns[2]:
        st.markdown(
            ui.meta_block("Project ID", project["id"]), unsafe_allow_html=True
        )

    st.markdown(ui.divider(), unsafe_allow_html=True)
    _render_sources_section(project)
    st.markdown(ui.divider(), unsafe_allow_html=True)
    _render_reconstruction_section(project)


# --------------------------------------------------------------------------
# Reconstruction
# --------------------------------------------------------------------------

def _render_reconstruction_section(project: dict) -> None:
    documents = _document_service()
    reconstructions = _reconstruction_service()

    st.markdown("## Reconstruction")

    usable = documents.usable_sources(project["id"])
    latest = reconstructions.latest(project["id"])
    status = str(latest["status"]) if latest else ReconstructionStatus.NOT_STARTED

    header, action = st.columns([3, 1.1], vertical_alignment="center")
    with header:
        detail = f"{len(usable)} source(s) with extracted text would be analysed."
        if latest:
            kind = (
                "Deterministic preview"
                if latest["is_demo_preview"]
                else f"{latest['model_provider']} / {latest['model_name']}"
            )
            detail = (
                f"Last run {format_timestamp(latest['created_at'])} · {kind} · "
                f"{latest['source_count']} source(s)"
            )
        st.markdown(
            ui.reconstruction_pill(status)
            + f'<div class="trace-note" style="margin-top:0.4rem">'
            f"{html.escape(detail)}</div>",
            unsafe_allow_html=True,
        )
    with action:
        label = "Run reconstruction" if not latest else "Run again"
        disabled = not usable
        if st.button(label, type="primary", disabled=disabled, width="stretch"):
            with st.spinner("Reading sources and checking evidence…"):
                outcome = reconstructions.run(project)
            st.session_state["reconstruction_outcome"] = {
                "ok": outcome.ok,
                "message": outcome.message,
                "notes": outcome.notes,
                "is_preview": outcome.is_preview,
            }
            st.rerun()

    if not usable:
        st.info(
            "Add at least one source with extractable text before running a "
            "reconstruction."
        )

    llm = config.get_llm_config()
    if not llm.is_configured:
        st.warning(
            "No AI provider is configured, so TRACE will produce a clearly "
            "labelled deterministic preview instead of a model-generated "
            "draft. The preview quotes sentences containing category cue "
            "words; it does not analyse or interpret them. Set LLM_API_KEY to "
            "enable AI reconstruction."
        )

    outcome = st.session_state.pop("reconstruction_outcome", None)
    if outcome:
        if not outcome["ok"]:
            st.error(outcome["message"])
        else:
            st.success(outcome["message"])
            if outcome["is_preview"]:
                st.warning(
                    "This draft was produced by deterministic keyword matching. "
                    "No AI model was called."
                )

    if latest and status == ReconstructionStatus.DRAFT_READY:
        _render_draft_summary(project, latest)


def _render_draft_summary(project: dict, latest: dict) -> None:
    del project
    reconstructions = _reconstruction_service()
    summary = reconstructions.evidence_summary(latest["id"])
    total = sum(summary.values())
    if not total:
        st.info(
            "The last run produced no items the sources support. Nothing was "
            "invented to fill the gap."
        )
        return

    st.markdown(
        f'<div class="trace-meta-label" style="margin-top:0.9rem">'
        f"Evidence status across {total} proposed item(s)</div>",
        unsafe_allow_html=True,
    )
    pills = " ".join(
        ui.evidence_pill(status) + f' <span class="trace-note">{count}</span>'
        for status, count in summary.items()
        if count
    )
    st.markdown(
        f'<div style="margin:0.45rem 0 0.2rem">{pills}</div>', unsafe_allow_html=True
    )

    missing = reconstructions.missing_information(latest)
    if missing:
        with st.expander(f"Missing information noted by this run ({len(missing)})"):
            for entry in missing:
                st.markdown(
                    f'<div class="trace-note" style="margin-bottom:0.45rem">• '
                    f"{html.escape(entry)}</div>",
                    unsafe_allow_html=True,
                )

    if latest.get("processing_notes"):
        with st.expander("What TRACE checked during this run"):
            for note in str(latest["processing_notes"]).split("\n"):
                if note.strip():
                    st.markdown(
                        f'<div class="trace-note" style="margin-bottom:0.4rem">• '
                        f"{html.escape(note.strip())}</div>",
                        unsafe_allow_html=True,
                    )


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

#: How much extracted text the preview shows before truncating.
_PREVIEW_SEGMENT_LIMIT = 12
_PREVIEW_CHAR_LIMIT = 900


def _render_upload_form(project: dict) -> None:
    documents = _document_service()
    st.markdown("### Add sources")
    st.markdown(
        f'<div class="trace-note">Accepted: PDF, DOCX, TXT &middot; up to '
        f"{config.MAX_UPLOAD_MB} MB per file. "
        f"{html.escape(config.UPLOAD_PRIVACY_WARNING)}</div>",
        unsafe_allow_html=True,
    )

    # The uploader key is rotated after each successful batch so Streamlit
    # clears the widget instead of re-submitting the same files on rerun.
    uploader_key = f"uploader_{project['id']}_{st.session_state.get('upload_round', 0)}"
    uploaded = st.file_uploader(
        "Upload documents",
        type=list(config.ALLOWED_EXTENSIONS),
        accept_multiple_files=True,
        key=uploader_key,
        label_visibility="collapsed",
    )

    if not uploaded:
        return
    if not st.button(
        f"Add {len(uploaded)} file{'s' if len(uploaded) != 1 else ''}",
        type="primary",
        key=f"add_{uploader_key}",
    ):
        return

    outcomes = []
    with st.spinner("Validating and extracting text…"):
        for item in uploaded:
            outcomes.append(documents.upload(project["id"], item.name, item.getvalue()))

    st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
    st.session_state["upload_outcomes"] = [
        {
            "accepted": outcome.accepted,
            "status": outcome.extraction_status,
            "message": outcome.message,
        }
        for outcome in outcomes
    ]
    st.rerun()


def _render_upload_outcomes() -> None:
    outcomes = st.session_state.pop("upload_outcomes", None)
    if not outcomes:
        return
    for outcome in outcomes:
        if not outcome["accepted"]:
            st.warning(outcome["message"])
        elif outcome["status"] in (
            ExtractionStatus.EXTRACTED,
            ExtractionStatus.PARTIALLY_EXTRACTED,
        ):
            st.success(outcome["message"])
        else:
            # Stored, but unusable as a source -- say so rather than imply success.
            st.warning(outcome["message"])


def _render_source_row(project: dict, source: dict) -> None:
    documents = _document_service()
    status = source.get("extraction_status")
    name = html.escape(str(source["display_name"]))

    header, action = st.columns([6, 1], vertical_alignment="center")
    with header:
        st.markdown(
            f'<div class="trace-card-title">{name}</div>'
            '<div class="trace-note" style="margin-top:0.3rem">'
            + ui.extraction_pill(status)
            + f'<span style="margin-left:0.6rem">'
            f"{html.escape(str(source['file_type']).upper())} &middot; "
            f"{html.escape(human_size(int(source['file_size'])))} &middot; "
            f"{int(source['char_count']):,} characters"
            + (
                f" &middot; {int(source['page_count'])} pages"
                if source.get("page_count")
                else ""
            )
            + f" &middot; added {html.escape(format_timestamp(source['uploaded_at']))}"
            "</span></div>",
            unsafe_allow_html=True,
        )
    with action:
        if st.button("Remove", key=f"del_{source['id']}", width="stretch"):
            ok, message = documents.delete_source(source["id"], project["id"])
            st.session_state["flash" if ok else "flash_warn"] = message
            st.rerun()

    if source.get("extraction_error"):
        st.warning(source["extraction_error"])
    if source.get("extraction_notes"):
        st.info(source["extraction_notes"])

    if int(source["char_count"]) > 0:
        with st.expander("Preview extracted text with source locations"):
            full = documents.get_source(source["id"], project["id"]) or {}
            segments = segments_from_json(full.get("segments_json"))
            if not segments:
                st.markdown(
                    f'<div class="trace-note">{html.escape((full.get("extracted_text") or "")[:_PREVIEW_CHAR_LIMIT])}</div>',
                    unsafe_allow_html=True,
                )
            else:
                for segment in segments[:_PREVIEW_SEGMENT_LIMIT]:
                    body = segment.text[:_PREVIEW_CHAR_LIMIT]
                    if len(segment.text) > _PREVIEW_CHAR_LIMIT:
                        body += " …"
                    st.markdown(
                        ui.pill(segment.location_label, "info")
                        + f'<div class="trace-note" style="margin:0.3rem 0 0.9rem">'
                        f"{html.escape(body)}</div>",
                        unsafe_allow_html=True,
                    )
                if len(segments) > _PREVIEW_SEGMENT_LIMIT:
                    st.markdown(
                        f'<div class="trace-note">… and '
                        f"{len(segments) - _PREVIEW_SEGMENT_LIMIT} further "
                        "locations not shown in this preview.</div>",
                        unsafe_allow_html=True,
                    )

    st.markdown(ui.divider(), unsafe_allow_html=True)


def _render_sources_section(project: dict) -> None:
    documents = _document_service()

    if warn := st.session_state.pop("flash_warn", None):
        st.warning(warn)
    _render_upload_outcomes()

    try:
        sources = documents.list_sources(project["id"])
    except DatabaseError as exc:
        st.error(f"Sources could not be loaded: {exc}")
        return

    summary = extraction_summary(sources)
    listing, uploader = st.columns([1.65, 1], gap="large")

    with listing:
        heading = f"## Sources ({summary['total']})" if summary["total"] else "## Sources"
        st.markdown(heading)
        if summary["total"]:
            parts = [f"{summary['usable']} usable for reconstruction"]
            if summary["no_text"]:
                parts.append(f"{summary['no_text']} with no extractable text")
            if summary["failed"]:
                parts.append(f"{summary['failed']} failed")
            st.markdown(
                f'<div class="trace-note" style="margin-bottom:0.9rem">'
                f"{html.escape(' · '.join(parts))}</div>",
                unsafe_allow_html=True,
            )

        if not sources:
            st.markdown(
                '<div class="trace-card">'
                '<div class="trace-card-title">No sources yet</div>'
                '<div class="trace-card-desc">Upload the project records you are '
                "authorised to use — briefs, meeting notes, status updates, issue "
                "lists. TRACE reconstructs the handover only from what you add "
                "here.</div></div>",
                unsafe_allow_html=True,
            )
        else:
            for source in sources:
                _render_source_row(project, source)

    with uploader:
        _render_upload_form(project)


# --------------------------------------------------------------------------
# Review handover
# --------------------------------------------------------------------------

_FILTER_ALL = "All items"
_FILTER_UNREVIEWED = "Awaiting review"
_FILTER_ATTENTION = "Needs a closer look"
_FILTER_APPROVED = "Approved"


def _render_evidence_block(item: dict) -> None:
    """Show the sources behind an item, verified and unverified alike."""
    evidence = item.get("evidence") or []
    if not evidence:
        st.markdown(
            '<div class="trace-note">No source evidence is attached to this '
            "item, so it is recorded as unsupported.</div>",
            unsafe_allow_html=True,
        )
        return

    verified = sum(1 for row in evidence if row["verified"])
    with st.expander(
        f"Evidence ({verified} of {len(evidence)} verified against the sources)"
    ):
        for row in evidence:
            tone = "positive" if row["verified"] else "negative"
            state = "Verified in source" if row["verified"] else "Not found in source"
            location = row.get("location_label") or "location not recorded"
            st.markdown(
                ui.pill(state, tone)
                + f'<span class="trace-note" style="margin-left:0.6rem">'
                f"{html.escape(str(row['source_display_name']))} &middot; "
                f"{html.escape(str(location))}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="trace-note" style="margin:0.4rem 0 0.2rem;'
                "padding:0.55rem 0.75rem;background:#F7F9FB;"
                'border-left:3px solid #C4DAE8;border-radius:3px">'
                f"&ldquo;{html.escape(str(row['excerpt']))}&rdquo;</div>",
                unsafe_allow_html=True,
            )
            if row.get("support_note"):
                st.markdown(
                    f'<div class="trace-note" style="margin-bottom:0.8rem;'
                    f'color:#8C2F2F">{html.escape(str(row["support_note"]))}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="margin-bottom:0.8rem"></div>', unsafe_allow_html=True
                )


def _render_review_item(item: dict, reconstruction_id: str) -> None:
    reviews = _review_service()
    item_id = item["id"]

    with st.container(border=True):
        st.markdown(
            ui.evidence_pill(item["evidence_status"])
            + " "
            + ui.review_pill(item["review_status"])
            + (
                '<span class="trace-note" style="margin-left:0.6rem">'
                "Worth checking before approval</span>"
                if needs_attention(item)
                and item["review_status"] == "proposed"
                else ""
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="margin:0.55rem 0 0.35rem;font-size:0.97rem;'
            f'line-height:1.5">{html.escape(str(item["text"]))}</div>',
            unsafe_allow_html=True,
        )

        if item.get("owner") or item.get("due_date"):
            bits = []
            if item.get("owner"):
                bits.append(f"Owner: {item['owner']}")
            if item.get("due_date"):
                bits.append(f"Due: {item['due_date']}")
            st.markdown(
                f'<div class="trace-note">{html.escape(" · ".join(bits))}</div>',
                unsafe_allow_html=True,
            )

        if item.get("reviewer_note"):
            st.markdown(
                f'<div class="trace-note" style="margin-top:0.3rem">'
                f"<strong>Note:</strong> {html.escape(str(item['reviewer_note']))}"
                "</div>",
                unsafe_allow_html=True,
            )

        _render_evidence_block(item)

        with st.expander("Edit this item or add a note"):
            new_text = st.text_area(
                "Statement",
                value=str(item["text"]),
                key=f"text_{item_id}",
                height=90,
            )
            new_note = st.text_input(
                "Reviewer note",
                value=str(item.get("reviewer_note") or ""),
                key=f"note_{item_id}",
                placeholder="Why this was changed, kept, or rejected",
            )
            if st.button("Save changes", key=f"save_{item_id}"):
                try:
                    reviews.edit_text(item_id, reconstruction_id, new_text)
                    reviews.set_note(item_id, reconstruction_id, new_note)
                except ReviewError as exc:
                    st.session_state["review_warn"] = str(exc)
                else:
                    st.session_state["review_flash"] = "Item updated."
                st.rerun()

            history = reviews.history(item_id)
            if history:
                st.markdown(
                    '<div class="trace-meta-label" style="margin-top:0.7rem">'
                    "Review history</div>",
                    unsafe_allow_html=True,
                )
                for event in history:
                    st.markdown(
                        f'<div class="trace-note">{html.escape(format_timestamp(event["created_at"]))}'
                        f" — {html.escape(str(event['action']))}"
                        + (
                            f": {html.escape(str(event['note']))}"
                            if event.get("note")
                            else ""
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )

        approve_col, reject_col, reopen_col, _ = st.columns([1, 1, 1, 2.6])
        status = str(item["review_status"])
        with approve_col:
            if st.button(
                "Approve",
                key=f"ok_{item_id}",
                disabled=status == "approved",
                width="stretch",
            ):
                reviews.approve(item_id, reconstruction_id)
                st.rerun()
        with reject_col:
            if st.button(
                "Reject",
                key=f"no_{item_id}",
                disabled=status == "rejected",
                width="stretch",
            ):
                reviews.reject(item_id, reconstruction_id)
                st.rerun()
        with reopen_col:
            if st.button(
                "Reopen",
                key=f"re_{item_id}",
                disabled=status == "proposed",
                width="stretch",
            ):
                reviews.reopen(item_id, reconstruction_id)
                st.rerun()


def _passes_filter(item: dict, choice: str) -> bool:
    if choice == _FILTER_UNREVIEWED:
        return item["review_status"] == "proposed"
    if choice == _FILTER_APPROVED:
        return item["review_status"] == "approved"
    if choice == _FILTER_ATTENTION:
        return needs_attention(item)
    return True


def _render_review(service: ProjectService) -> None:
    project_id = st.session_state.get("project_id")
    if not project_id:
        st.markdown("# Review Handover")
        st.info("Open a project from the Dashboard to review its handover.")
        return

    try:
        project = service.require_project(project_id)
    except ValidationError as exc:
        st.error(str(exc))
        return

    reconstructions = _reconstruction_service()
    reviews = _review_service()
    latest = reconstructions.latest(project_id)

    st.markdown(f"# Review Handover")
    st.markdown(
        f'<div class="trace-card-desc">{html.escape(project["name"])}</div>',
        unsafe_allow_html=True,
    )

    if latest is None or str(latest["status"]) != ReconstructionStatus.DRAFT_READY:
        st.markdown(ui.divider(), unsafe_allow_html=True)
        st.info(
            "There is no draft to review yet. Run a reconstruction from the "
            "Project Workspace first."
        )
        return

    if latest["is_demo_preview"]:
        st.warning(
            "This draft was produced by deterministic keyword matching, not by "
            "an AI model. It quotes sentences containing category cue words and "
            "makes no judgement about them."
        )

    progress = reviews.progress(latest["id"])
    st.markdown(ui.divider(), unsafe_allow_html=True)

    stats = st.columns(4)
    with stats[0]:
        st.markdown(ui.meta_block("Proposed", str(progress.total)), unsafe_allow_html=True)
    with stats[1]:
        st.markdown(ui.meta_block("Approved", str(progress.approved)), unsafe_allow_html=True)
    with stats[2]:
        st.markdown(ui.meta_block("Rejected", str(progress.rejected)), unsafe_allow_html=True)
    with stats[3]:
        st.markdown(
            ui.meta_block("Awaiting review", str(progress.proposed)),
            unsafe_allow_html=True,
        )

    if progress.total:
        st.progress(progress.fraction)
    if progress.proposed:
        st.markdown(
            f'<div class="trace-note">{progress.proposed} item(s) still need a '
            "decision. Only approved items are exported.</div>",
            unsafe_allow_html=True,
        )
    elif progress.total:
        st.markdown(
            '<div class="trace-note">Every item has been reviewed.</div>',
            unsafe_allow_html=True,
        )

    missing = reconstructions.missing_information(latest)
    if missing:
        with st.expander(f"Missing information noted by this run ({len(missing)})"):
            for entry in missing:
                st.markdown(
                    f'<div class="trace-note" style="margin-bottom:0.45rem">• '
                    f"{html.escape(entry)}</div>",
                    unsafe_allow_html=True,
                )

    if warn := st.session_state.pop("review_warn", None):
        st.warning(warn)
    if flash := st.session_state.pop("review_flash", None):
        st.success(flash)

    st.markdown(ui.divider(), unsafe_allow_html=True)
    choice = st.radio(
        "Show",
        [_FILTER_ALL, _FILTER_UNREVIEWED, _FILTER_ATTENTION, _FILTER_APPROVED],
        horizontal=True,
    )

    shown = 0
    for _, label, items in reviews.grouped_items(latest["id"]):
        visible = [item for item in items if _passes_filter(item, choice)]
        if not visible:
            continue
        shown += len(visible)
        st.markdown(f"## {label}")
        for item in visible:
            _render_review_item(item, latest["id"])

    if shown == 0:
        st.info("No items match this filter.")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title=f"{config.APP_NAME} -- {config.APP_TAGLINE}",
        page_icon=".",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(ui.GLOBAL_CSS, unsafe_allow_html=True)

    st.session_state.setdefault("page", PAGE_DASHBOARD)

    try:
        service = _project_service()
    except DatabaseError as exc:
        st.error(
            "TRACE could not open its database, so nothing can be saved in this "
            f"session. Details: {exc}"
        )
        return

    page = _render_sidebar()
    if page == PAGE_WORKSPACE:
        _render_workspace(service)
    elif page == PAGE_REVIEW:
        _render_review(service)
    else:
        _render_dashboard(service)


if __name__ == "__main__":
    main()
