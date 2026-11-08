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
from trace_app.utils.file_validation import human_size

PAGE_DASHBOARD = "Dashboard"
PAGE_WORKSPACE = "Project Workspace"
NAV_PAGES = [PAGE_DASHBOARD, PAGE_WORKSPACE]


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
    else:
        _render_dashboard(service)


if __name__ == "__main__":
    main()
