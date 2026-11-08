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
from trace_app.models import format_timestamp
from trace_app.services.project_service import ProjectService, ValidationError

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
    st.markdown("## Sources")
    st.info(
        "Document upload and text extraction are the next milestone and are not "
        "implemented yet."
    )


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
