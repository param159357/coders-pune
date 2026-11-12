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
    ExtractionStatus,
    ReconstructionStatus,
    format_timestamp,
    reconstruction_label,
)
from trace_app.security.authz import (
    AccessDenied,
    Capability,
    Principal,
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    Role,
    assignable_roles,
)
from trace_app.services.auth_service import AuthError, AuthService
from trace_app.services.demo_service import DEMO_ACCOUNTS, DEMO_PASSWORD, DemoService
from trace_app.services.document_service import DocumentService, extraction_summary
from trace_app.services.extraction_service import segments_from_json
from trace_app.services.project_service import ProjectService, ValidationError
from trace_app.services.reconstruction_service import ReconstructionService
from trace_app.services.report_service import (
    ReportOptions,
    build_markdown,
    build_pdf,
    collect_report_data,
    report_filename,
)
from trace_app.services.review_service import (
    ReviewError,
    ReviewService,
    needs_attention,
)
from trace_app.utils.file_validation import human_size

PAGE_DASHBOARD = "Dashboard"
PAGE_WORKSPACE = "Project Workspace"
PAGE_REVIEW = "Review Handover"
PAGE_EXPORT = "Export"
PAGE_ACCESS = "Access"
NAV_PAGES = [
    PAGE_DASHBOARD, PAGE_WORKSPACE, PAGE_REVIEW, PAGE_EXPORT, PAGE_ACCESS,
]


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


def _auth_service() -> AuthService:
    return AuthService(_database())


def _current_user() -> dict | None:
    """The signed-in account, re-read so a removed account stops working."""
    user_id = st.session_state.get("user_id")
    return _auth_service().get_user(user_id) if user_id else None


def _principal(project_id: str | None = None) -> Principal:
    """The acting principal, derived from a stored membership.

    Looked up on every call, so revoking a role takes effect at once rather
    than whenever a session happens to expire.
    """
    user = _current_user()
    if user is None:
        raise AccessDenied("You must sign in to do that.")
    target = project_id or st.session_state.get("project_id")
    if not target:
        # No project in scope: a principal with no role, so every project-level
        # capability check fails closed.
        return Principal(
            user_id=str(user["id"]), email=str(user["email"]), role="", project_id=None
        )
    return _auth_service().principal_for(user, target)


def _document_service(project_id: str | None = None) -> DocumentService:
    return DocumentService(_database(), _principal(project_id))


def _reconstruction_service(project_id: str | None = None) -> ReconstructionService:
    return ReconstructionService(_database(), _principal(project_id))


def _review_service(project_id: str | None = None) -> ReviewService:
    return ReviewService(_database(), _principal(project_id))


def _demo_service() -> DemoService | None:
    user = _current_user()
    return DemoService(_database(), user) if user else None


def _goto(page: str, project_id: str | None = None) -> None:
    st.session_state["page"] = page
    if project_id is not None:
        st.session_state["project_id"] = project_id
    st.rerun()


def _open_project(project_id: str) -> None:
    """Open a project on the first screen this user's role can actually use.

    Sending a viewer to the workspace would bounce them straight back to the
    dashboard with no explanation, because their role cannot open source
    documents. Land them where their role begins instead.
    """
    st.session_state["project_id"] = project_id
    try:
        actor = _principal(project_id)
    except AccessDenied:
        st.session_state["page"] = PAGE_DASHBOARD
        st.rerun()
        return

    if actor.can(Capability.VIEW_SOURCES) or actor.can(Capability.UPLOAD_SOURCES):
        st.session_state["page"] = PAGE_WORKSPACE
    elif actor.can(Capability.VIEW_APPROVED):
        st.session_state["page"] = PAGE_REVIEW
    else:
        st.session_state["page"] = PAGE_ACCESS
    st.rerun()


def _workflow_state(project: dict) -> list[tuple[str, str, str]]:
    """Where this project stands, for the stepper. All counts come from rows."""
    documents = _document_service(project["id"])
    reconstructions = _reconstruction_service(project["id"])
    reviews = _review_service(project["id"])

    # A role without VIEW_SOURCES still gets a stepper, just without source
    # detail. A count is metadata; the documents themselves stay unreachable.
    if documents.can_read_sources():
        sources = documents.list_sources(project["id"])
    else:
        sources = [
            {"extraction_status": ExtractionStatus.EXTRACTED}
        ] * documents.source_count(project["id"])
    usable = [
        s
        for s in sources
        if s["extraction_status"]
        in (ExtractionStatus.EXTRACTED, ExtractionStatus.PARTIALLY_EXTRACTED)
    ]
    latest = reconstructions.latest(project["id"])
    ready = latest is not None and str(latest["status"]) == ReconstructionStatus.DRAFT_READY
    progress = reviews.progress(latest["id"]) if ready else None

    # Stage 1 -- sources
    if not sources:
        steps = [("Add sources", "No documents yet", "now")]
    else:
        detail = f"{len(usable)} of {len(sources)} usable"
        steps = [("Add sources", detail, "done" if usable else "now")]

    # Stage 2 -- reconstruct
    if not usable:
        steps.append(("Reconstruct", "Needs a readable source", "todo"))
    elif not latest:
        steps.append(("Reconstruct", "Not started", "now"))
    elif not ready:
        steps.append(("Reconstruct", reconstruction_label(str(latest["status"])), "now"))
    else:
        total = progress.total if progress else 0
        steps.append(("Reconstruct", f"{total} items proposed", "done"))

    # Stage 3 -- review
    if not ready or progress is None or progress.total == 0:
        steps.append(("Review", "Nothing to review yet", "todo"))
    elif progress.proposed:
        steps.append(("Review", f"{progress.proposed} awaiting a decision", "now"))
    else:
        steps.append(("Review", f"{progress.approved} approved", "done"))

    # Stage 4 -- handover. Only one step is ever "now", so the stepper reads as
    # a position rather than two competing current actions. Export stays
    # available while review is unfinished; the subtitle still says how much is
    # ready, so nothing is hidden.
    if not ready or progress is None or progress.approved == 0:
        steps.append(("Handover", "Approve items to export", "todo"))
    else:
        noun = "item" if progress.approved == 1 else "items"
        state = "now" if progress.proposed == 0 else "todo"
        steps.append(("Handover", f"{progress.approved} {noun} ready", state))

    return steps


def _render_stepper(project: dict) -> None:
    try:
        st.markdown(ui.stepper(_workflow_state(project)), unsafe_allow_html=True)
    except DatabaseError:
        pass  # the stepper is orientation, never worth failing a page for


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

def _visible_pages() -> list[str]:
    """Pages this user can use, given their role on the project in scope.

    Hiding a page is a convenience, not the control: each screen behind these
    checks its own capabilities, and the services check again below that.
    """
    pages = [PAGE_DASHBOARD]
    project_id = st.session_state.get("project_id")
    if not project_id:
        return pages
    try:
        actor = _principal(project_id)
    except AccessDenied:
        return pages

    if actor.can(Capability.VIEW_SOURCES) or actor.can(Capability.UPLOAD_SOURCES):
        pages.append(PAGE_WORKSPACE)
    if actor.can(Capability.VIEW_APPROVED):
        pages.append(PAGE_REVIEW)
    if actor.can(Capability.EXPORT):
        pages.append(PAGE_EXPORT)
    pages.append(PAGE_ACCESS)
    return pages


def _render_sidebar() -> str:
    user = _current_user()
    with st.sidebar:
        st.markdown(ui.brand(), unsafe_allow_html=True)

        pages = _visible_pages()
        current = st.session_state.get("page", PAGE_DASHBOARD)
        if current not in pages:
            current = PAGE_DASHBOARD
            st.session_state["page"] = current
        page = st.radio(
            "Navigation", pages, index=pages.index(current),
            label_visibility="collapsed",
        )
        if page != current:
            st.session_state["page"] = page

        st.markdown(ui.divider(), unsafe_allow_html=True)

        if user:
            name = user.get("display_name") or user.get("email")
            st.markdown('<div class="tr-k">Signed in as</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="tr-v" style="margin:.2rem 0 .1rem">{ui.esc(name)}</div>'
                f'<div class="tr-note">{ui.esc(user.get("email"))}</div>',
                unsafe_allow_html=True,
            )
            project_id = st.session_state.get("project_id")
            if project_id:
                try:
                    actor = _principal(project_id)
                except AccessDenied:
                    actor = None
                if actor is not None and actor.role:
                    st.markdown(
                        '<div style="margin-top:.5rem">'
                        + ui.pill(f"Role: {actor.role_label}", "info")
                        + "</div>",
                        unsafe_allow_html=True,
                    )
            st.markdown('<div style="height:.5rem"></div>', unsafe_allow_html=True)
            if st.button("Sign out", width="stretch"):
                st.session_state.clear()
                st.rerun()

        st.markdown(ui.divider(), unsafe_allow_html=True)

        llm = config.get_llm_config()
        st.markdown('<div class="tr-k">AI provider</div>', unsafe_allow_html=True)
        if llm.is_configured:
            st.markdown(
                '<div style="margin-top:.4rem">'
                + ui.pill("Configured", "positive")
                + f'</div><div class="tr-note" style="margin-top:.4rem">'
                f"{html.escape(llm.provider)} &middot; "
                f"<code>{html.escape(llm.model)}</code></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="margin-top:.4rem">'
                + ui.pill("Not configured", "caution")
                + '</div><div class="tr-note" style="margin-top:.4rem">'
                "Reconstruction will use a clearly labelled deterministic "
                "preview. Set <code>LLM_API_KEY</code> for AI analysis.</div>",
                unsafe_allow_html=True,
            )

        st.markdown(ui.divider(), unsafe_allow_html=True)
        st.markdown(
            f'<div class="tr-note">{html.escape(config.AI_DISCLAIMER)}</div>',
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
    tag = ui.demo_tag() if project.get("is_demo") else ""
    description = project.get("description") or "No description provided."
    source_count = int(project.get("source_count") or 0)
    extracted = int(project.get("extracted_count") or 0)

    facts = [
        f"{source_count} source{'s' if source_count != 1 else ''}"
        if source_count
        else "No sources yet"
    ]
    if source_count:
        facts.append(f"{extracted} extracted")
    if project.get("member_role"):
        facts.append(ROLE_LABELS.get(project["member_role"], project["member_role"]))
    facts.append(f"Created {format_timestamp(project.get('created_at'))}")

    st.markdown(
        '<div class="tr-card tr-card-pad" style="margin-bottom:.35rem">'
        f'<div class="tr-title">{ui.esc(project["name"])}{tag}</div>'
        f'<div class="tr-desc" style="margin:.3rem 0 .7rem">{ui.esc(description)}</div>'
        '<div class="tr-meta-row">'
        + ui.reconstruction_pill(project.get("latest_reconstruction_status"))
        + "".join(ui.chip(fact) for fact in facts)
        + "</div></div>",
        unsafe_allow_html=True,
    )
    _, action = st.columns([4, 1])
    with action:
        if st.button("Open", key=f"open_{project['id']}", width="stretch"):
            _open_project(project["id"])
    st.markdown('<div style="height:.7rem"></div>', unsafe_allow_html=True)


def _render_dashboard(service: ProjectService) -> None:
    st.markdown(
        ui.page_head("Project handovers", config.APP_DESCRIPTION, eyebrow="TRACE"),
        unsafe_allow_html=True,
    )

    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    if warn := st.session_state.pop("flash_warn", None):
        st.warning(warn)

    # Only projects this user is a member of. A project they have no
    # membership on is never fetched, not merely hidden from the list.
    try:
        projects = _auth_service().projects_for(_current_user())
    except DatabaseError as exc:
        st.error(f"Projects could not be loaded: {exc}")
        return

    # Every figure here is counted from rows, never invented.
    if projects:
        st.markdown('<div style="height:1rem"></div>', unsafe_allow_html=True)
        st.markdown(
            ui.stats(
                [
                    ("Projects", len(projects)),
                    (
                        "Source documents",
                        sum(int(p.get("source_count") or 0) for p in projects),
                    ),
                    (
                        "Drafts ready",
                        sum(
                            1
                            for p in projects
                            if p.get("latest_reconstruction_status")
                            == ReconstructionStatus.DRAFT_READY
                        ),
                    ),
                ]
            ),
            unsafe_allow_html=True,
        )

    st.markdown(ui.divider(), unsafe_allow_html=True)
    listing, creator = st.columns([1.7, 1], gap="large")

    with listing:
        st.markdown("## Your projects")
        if not projects:
            st.markdown(
                '<div class="tr-card tr-card-pad">'
                '<div class="tr-title">No projects yet</div>'
                '<div class="tr-desc" style="margin-top:.35rem">Create a project '
                "workspace to start adding source documents. Everything in TRACE "
                "belongs to a project, so sources and handover items stay isolated "
                "from one another.</div>"
                '<div class="tr-note" style="margin-top:.7rem">New here? Load the '
                "CampusConnect demo on the right to see the whole workflow with "
                "synthetic data.</div></div>",
                unsafe_allow_html=True,
            )
        else:
            for project in projects:
                _render_project_card(project, service)

    with creator:
        _render_create_project_form(service)
        _render_demo_loader()


def _render_demo_loader() -> None:
    demo = _demo_service()
    if demo is None or not demo.available():
        return

    st.markdown('<div style="height:1rem"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="tr-panel">'
        '<div class="tr-title">Try the demo</div>'
        '<div class="tr-desc" style="margin:.35rem 0 .2rem">Loads '
        "<strong>CampusConnect</strong>, a fictional campus events portal, with "
        "four synthetic source documents.</div>"
        '<div class="tr-note" style="margin-top:.55rem">It describes no real '
        "institution. Nothing you have created is changed or removed.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown('<div style="height:.5rem"></div>', unsafe_allow_html=True)
    if st.button("Load the CampusConnect demo", width="stretch"):
        with st.spinner("Creating the demo project and extracting its sources…"):
            result = demo.load()
        if not result.ok:
            st.session_state["flash_warn"] = result.message
            st.rerun()
        else:
            st.session_state["flash"] = result.message
            _open_project(result.project["id"])


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
    st.markdown(
        ui.page_head(
            project["name"],
            project.get("description") or "",
            eyebrow="Project workspace",
            tag=tag,
        ),
        unsafe_allow_html=True,
    )
    st.markdown('<div style="height:1.1rem"></div>', unsafe_allow_html=True)
    _render_stepper(project)

    columns = st.columns(3)
    for column, (label, value) in zip(
        columns,
        [
            ("Created", format_timestamp(project.get("created_at"))),
            ("Team", project.get("team_label") or "Not specified"),
            ("Project ID", project["id"]),
        ],
    ):
        with column:
            st.markdown(ui.meta_block(label, value), unsafe_allow_html=True)

    st.markdown(ui.divider(), unsafe_allow_html=True)
    _render_sources_section(project)
    st.markdown(ui.divider(), unsafe_allow_html=True)
    _render_reconstruction_section(project)


# --------------------------------------------------------------------------
# Reconstruction
# --------------------------------------------------------------------------

def _render_reconstruction_section(project: dict) -> None:
    documents = _document_service(project["id"])
    reconstructions = _reconstruction_service(project["id"])

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
            + f'<div class="tr-note" style="margin-top:0.4rem">'
            f"{html.escape(detail)}</div>",
            unsafe_allow_html=True,
        )
    with action:
        label = "Run reconstruction" if not latest else "Run again"
        disabled = not usable
        if reconstructions.can_run() and st.button(
            label, type="primary", disabled=disabled, width="stretch"
        ):
            with st.spinner("Reading sources and checking evidence…"):
                outcome = reconstructions.run(project)
            st.session_state["reconstruction_outcome"] = {
                "ok": outcome.ok,
                "message": outcome.message,
                "notes": outcome.notes,
                "is_preview": outcome.is_preview,
            }
            st.rerun()

    if not reconstructions.can_run():
        st.markdown(
            ui.note("Your role cannot start a reconstruction on this project."),
            unsafe_allow_html=True,
        )
    elif not usable:
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
        f'<div class="tr-k" style="margin-top:0.9rem">'
        f"Evidence status across {total} proposed item(s)</div>",
        unsafe_allow_html=True,
    )
    pills = " ".join(
        ui.evidence_pill(status) + f' <span class="tr-note">{count}</span>'
        for status, count in summary.items()
        if count
    )
    st.markdown(
        f'<div style="margin:0.45rem 0 0.2rem">{pills}</div>', unsafe_allow_html=True
    )

    _render_verification_panel(latest)

    missing = reconstructions.missing_information(latest)
    if missing:
        with st.expander(f"Missing information noted by this run ({len(missing)})"):
            for entry in missing:
                st.markdown(
                    f'<div class="tr-note" style="margin-bottom:0.45rem">• '
                    f"{html.escape(entry)}</div>",
                    unsafe_allow_html=True,
                )

    if latest.get("processing_notes"):
        with st.expander("What TRACE checked during this run"):
            for note in str(latest["processing_notes"]).split("\n"):
                if note.strip():
                    st.markdown(
                        f'<div class="tr-note" style="margin-bottom:0.4rem">• '
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
    documents = _document_service(project["id"])
    if not documents.can_add_sources():
        st.markdown("### Sources")
        st.markdown(
            ui.note(
                "Your role can read the source documents but not add or remove "
                "them."
            ),
            unsafe_allow_html=True,
        )
        return
    st.markdown("### Add sources")
    st.markdown(
        f'<div class="tr-note">Accepted: PDF, DOCX, TXT &middot; up to '
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
    documents = _document_service(project["id"])
    status = source.get("extraction_status")

    facts = [
        str(source["file_type"]).upper(),
        human_size(int(source["file_size"])),
        f"{int(source['char_count']):,} characters",
    ]
    if source.get("page_count"):
        pages = int(source["page_count"])
        facts.append(f"{pages} page{'s' if pages != 1 else ''}")

    header, action = st.columns([5, 1.15], vertical_alignment="center")
    with header:
        st.markdown(
            f'<div class="tr-title">{ui.esc(source["display_name"])}</div>'
            '<div class="tr-meta-row" style="margin-top:.4rem">'
            + ui.extraction_pill(status)
            + "".join(ui.chip(fact) for fact in facts)
            + "</div>"
            f'<div class="tr-note" style="margin-top:.35rem">Added '
            f"{ui.esc(format_timestamp(source['uploaded_at']))}</div>",
            unsafe_allow_html=True,
        )
    with action:
        if documents.can_add_sources() and st.button(
            "Remove", key=f"del_{source['id']}", width="stretch"
        ):
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
                    ui.note((full.get("extracted_text") or "")[:_PREVIEW_CHAR_LIMIT]),
                    unsafe_allow_html=True,
                )
            else:
                for segment in segments[:_PREVIEW_SEGMENT_LIMIT]:
                    body = segment.text[:_PREVIEW_CHAR_LIMIT]
                    if len(segment.text) > _PREVIEW_CHAR_LIMIT:
                        body += " …"
                    st.markdown(
                        ui.pill(segment.location_label, "info", dot=False)
                        + ui.quote(body),
                        unsafe_allow_html=True,
                    )
                if len(segments) > _PREVIEW_SEGMENT_LIMIT:
                    st.markdown(
                        ui.note(
                            f"… and {len(segments) - _PREVIEW_SEGMENT_LIMIT} further "
                            "locations not shown in this preview."
                        ),
                        unsafe_allow_html=True,
                    )

    st.markdown(ui.divider(), unsafe_allow_html=True)


def _render_sources_section(project: dict) -> None:
    documents = _document_service(project["id"])
    if not documents.can_read_sources():
        st.markdown("## Sources")
        st.markdown(
            ui.note(
                "Your role cannot open the source documents behind this "
                "handover. You can still read the approved items and the "
                "evidence quoted in them."
            ),
            unsafe_allow_html=True,
        )
        return

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
                f'<div class="tr-note" style="margin-bottom:0.9rem">'
                f"{html.escape(' · '.join(parts))}</div>",
                unsafe_allow_html=True,
            )

        if not sources:
            st.markdown(
                '<div class="tr-card">'
                '<div class="tr-title">No sources yet</div>'
                '<div class="tr-desc">Upload the project records you are '
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
            ui.note(
                "No source evidence is attached to this item, so it is recorded "
                "as unsupported."
            ),
            unsafe_allow_html=True,
        )
        return

    verified = sum(1 for row in evidence if row["verified"])
    label = f"Evidence — {verified} of {len(evidence)} verified against the sources"
    with st.expander(label):
        for row in evidence:
            ok = bool(row["verified"])
            st.markdown(
                '<div class="tr-meta-row">'
                + ui.pill(
                    "Verified in source" if ok else "Not found in source",
                    "positive" if ok else "negative",
                )
                + ui.source_ref(
                    row.get("source_display_name") or "unknown source",
                    row.get("location_label") or "location not recorded",
                )
                + "</div>"
                + ui.quote(row.get("excerpt") or "", verified=ok),
                unsafe_allow_html=True,
            )
            if row.get("support_note"):
                st.markdown(
                    f'<div class="tr-note" style="color:#A32B2B;margin-bottom:.9rem">'
                    f"{ui.esc(row['support_note'])}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="height:.7rem"></div>', unsafe_allow_html=True
                )


def _render_review_item(item: dict, reconstruction_id: str) -> None:
    reviews = _review_service()
    item_id = item["id"]
    status = str(item["review_status"])

    with st.container(border=True):
        attention = (
            '<span class="tr-attention">Worth checking before approval</span>'
            if needs_attention(item) and status == "proposed"
            else ""
        )
        st.markdown(
            '<div class="tr-meta-row">'
            + ui.evidence_pill(item["evidence_status"])
            + ui.review_pill(item["review_status"])
            + attention
            + "</div>"
            f'<div class="tr-item-text">{ui.esc(item["text"])}</div>',
            unsafe_allow_html=True,
        )

        extras = []
        if item.get("owner"):
            extras.append(f"Owner: {item['owner']}")
        if item.get("due_date"):
            extras.append(f"Due: {item['due_date']}")
        if extras:
            st.markdown(
                '<div class="tr-meta-row" style="margin-bottom:.4rem">'
                + "".join(ui.chip(extra) for extra in extras)
                + "</div>",
                unsafe_allow_html=True,
            )
        if item.get("reviewer_note"):
            st.markdown(
                f'<div class="tr-note" style="margin-bottom:.5rem">'
                f"<strong>Reviewer note:</strong> "
                f"{ui.esc(item['reviewer_note'])}</div>",
                unsafe_allow_html=True,
            )

        _render_evidence_block(item)

        if not reviews.can_review():
            st.markdown(
                ui.note("Your role is read-only on this handover."),
                unsafe_allow_html=True,
            )
            return

        with st.expander("Edit this item or add a note"):
            new_text = st.text_area(
                "Statement", value=str(item["text"]), key=f"text_{item_id}", height=90
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
                    '<div class="tr-k" style="margin-top:.8rem">Review history</div>',
                    unsafe_allow_html=True,
                )
                for event in history:
                    detail = (
                        f": {ui.esc(event['note'])}" if event.get("note") else ""
                    )
                    st.markdown(
                        f'<div class="tr-note">'
                        f"{ui.esc(format_timestamp(event['created_at']))} — "
                        f"<strong>{ui.esc(event['action'])}</strong>{detail}</div>",
                        unsafe_allow_html=True,
                    )

        approve, reject, reopen, _ = st.columns([1, 1, 1, 2.4])
        with approve:
            if st.button(
                "Approve", key=f"ok_{item_id}",
                disabled=status == "approved", width="stretch",
                type="primary" if status == "proposed" else "secondary",
            ):
                reviews.approve(item_id, reconstruction_id)
                st.rerun()
        with reject:
            if st.button(
                "Reject", key=f"no_{item_id}",
                disabled=status == "rejected", width="stretch",
            ):
                reviews.reject(item_id, reconstruction_id)
                st.rerun()
        with reopen:
            if st.button(
                "Reopen", key=f"re_{item_id}",
                disabled=status == "proposed", width="stretch",
            ):
                reviews.reopen(item_id, reconstruction_id)
                st.rerun()


def _render_verification_panel(latest: dict) -> None:
    """Show what evidence checking actually did.

    This is the product's whole claim -- that a model cannot make an
    unsupported statement look supported -- so it belongs on the page as
    figures, not buried in an expander.
    """
    counts = _reconstruction_service().verification_counts(latest)
    if not counts or not counts.get("evidence_total"):
        return

    total = int(counts.get("evidence_total", 0))
    verified = int(counts.get("evidence_verified", 0))
    rejected = int(counts.get("fabricated_source_ids", 0)) + int(
        counts.get("excerpt_not_found", 0)
    )
    downgraded = int(counts.get("items_downgraded", 0))
    corrected = int(counts.get("locations_corrected", 0))

    st.markdown(
        '<div class="tr-k" style="margin-top:.5rem">What TRACE checked</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        ui.stats(
            [
                ("Citations checked", total),
                ("Matched the source", verified),
                ("Rejected", rejected),
                ("Items downgraded", downgraded),
            ]
        ),
        unsafe_allow_html=True,
    )

    detail = [
        f"Every one of the {total} citation(s) the draft produced was checked "
        "against the text of the source it named."
    ]
    if rejected:
        detail.append(
            f"{rejected} did not hold up and are not treated as support."
        )
    if corrected:
        detail.append(
            f"{corrected} location reference(s) were corrected to where the "
            "quotation actually appears."
        )
    if downgraded:
        detail.append(
            f"{downgraded} item(s) had their evidence status lowered because "
            "the evidence did not support the claim as stated."
        )
    else:
        detail.append("No item needed its evidence status lowered in this run.")
    st.markdown(ui.note(" ".join(detail)), unsafe_allow_html=True)


#: Shown on the synthetic demo so a first-time visitor knows what is worth
#: checking. Each point is something the demo data was built to prove.
_DEMO_POINTERS = (
    "<strong>Nothing is invented.</strong> No source states a launch date or "
    "an owner for the accessibility review. Look under "
    "<em>Missing information</em> — TRACE reports their absence instead of "
    "filling the gap.",
    "<strong>Contradictions are preserved, not resolved.</strong> The meeting "
    "notes record an agreed event capacity of 500; the development status "
    "records an implemented limit of 300. Look for the item marked "
    "<em>Conflicting</em> — both sources are kept and neither is chosen.",
    "<strong>Citations are verified, not trusted.</strong> Open <em>Evidence</em> "
    "on any item: each citation names its source and location and shows the "
    "exact quotation. A quotation that is not in the source is marked "
    "<em>Not found in source</em> and the claim is downgraded.",
)


def _render_demo_pointers(project: dict) -> None:
    if not project.get("is_demo"):
        return
    with st.expander("What to look for in this demo", expanded=False):
        for point in _DEMO_POINTERS:
            st.markdown(
                f'<div class="tr-desc" style="margin-bottom:.6rem">• {point}</div>',
                unsafe_allow_html=True,
            )


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
        st.markdown(ui.page_head("Review handover", eyebrow="Review"), unsafe_allow_html=True)
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

    st.markdown(
        ui.page_head(
            project["name"],
            "AI proposes; you decide. Every item needs a decision, and only "
            "approved items reach the handover.",
            eyebrow="Review handover",
            tag=ui.demo_tag() if project.get("is_demo") else "",
        ),
        unsafe_allow_html=True,
    )
    st.markdown('<div style="height:1.1rem"></div>', unsafe_allow_html=True)
    _render_stepper(project)

    if latest is None or str(latest["status"]) != ReconstructionStatus.DRAFT_READY:
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
    if progress.total:
        st.markdown(
            ui.segmented_bar(
                [
                    ("Approved", progress.approved, "positive"),
                    ("Rejected", progress.rejected, "negative"),
                    ("Awaiting review", progress.proposed, "info"),
                ],
                progress.total,
            ),
            unsafe_allow_html=True,
        )
        if reviews.sees_only_approved():
            summary = (
                f"{progress.approved} approved item(s). Your role sees the "
                "approved handover only."
            )
        elif progress.proposed:
            summary = (
                f"{progress.proposed} of {progress.total} item(s) still need a "
                "decision. Only approved items are exported."
            )
        else:
            summary = f"Every one of the {progress.total} items has been reviewed."
        st.markdown(ui.note(summary), unsafe_allow_html=True)

    st.markdown(ui.divider(), unsafe_allow_html=True)
    _render_verification_panel(latest)
    _render_demo_pointers(project)

    missing = reconstructions.missing_information(latest)
    if missing:
        with st.expander(f"Missing information noted by this run ({len(missing)})"):
            for entry in missing:
                st.markdown(
                    f'<div class="tr-note" style="margin-bottom:.45rem">• '
                    f"{ui.esc(entry)}</div>",
                    unsafe_allow_html=True,
                )

    if warn := st.session_state.pop("review_warn", None):
        st.warning(warn)
    if flash := st.session_state.pop("review_flash", None):
        st.success(flash)

    st.markdown(ui.divider(), unsafe_allow_html=True)
    # Only offer filters that can return something for this role.
    if reviews.sees_only_approved():
        choice = _FILTER_ALL
    else:
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
        st.markdown(
            f'<h2 style="margin-top:1.5rem">{ui.esc(label)} '
            f'<span class="tr-chip">{len(visible)}</span></h2>',
            unsafe_allow_html=True,
        )
        for item in visible:
            _render_review_item(item, latest["id"])

    if shown == 0:
        st.info("No items match this filter.")


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def _render_export(service: ProjectService) -> None:
    project_id = st.session_state.get("project_id")
    if not project_id:
        st.markdown(
            ui.page_head("Export handover", eyebrow="Export"),
            unsafe_allow_html=True,
        )
        st.info("Open a project from the Dashboard to export its handover.")
        return

    try:
        project = service.require_project(project_id)
    except ValidationError as exc:
        st.error(str(exc))
        return

    reconstructions = _reconstruction_service()
    reviews = _review_service()
    latest = reconstructions.latest(project_id)

    st.markdown(
        ui.page_head(
            project["name"],
            "Approved items only, each carrying the source, location and exact "
            "quotation it rests on.",
            eyebrow="Export handover",
            tag=ui.demo_tag() if project.get("is_demo") else "",
        ),
        unsafe_allow_html=True,
    )
    st.markdown('<div style="height:1.1rem"></div>', unsafe_allow_html=True)
    _render_stepper(project)

    if latest is None or str(latest["status"]) != ReconstructionStatus.DRAFT_READY:
        st.info(
            "There is nothing to export yet. Run a reconstruction and review "
            "the result first."
        )
        return

    progress = reviews.progress(latest["id"])
    st.markdown(
        ui.stats(
            [
                ("Approved — exported", progress.approved),
                ("Awaiting review", progress.proposed),
                ("Rejected — never exported", progress.rejected),
            ]
        ),
        unsafe_allow_html=True,
    )

    st.markdown(ui.divider(), unsafe_allow_html=True)
    st.markdown("### What to include")
    include_unreviewed = st.checkbox(
        "Also include items still awaiting review",
        value=False,
        help=(
            "Off by default. When on, unreviewed items appear in a separate "
            "section and every one is marked [UNREVIEWED]."
        ),
    )
    include_evidence = st.checkbox(
        "Include evidence references for each claim", value=True
    )

    if include_unreviewed and progress.proposed:
        st.warning(
            f"{progress.proposed} item(s) nobody has approved will be included "
            "in a clearly marked section. Rejected items are still excluded."
        )
    if progress.approved == 0 and not include_unreviewed:
        st.info(
            "No item has been approved yet, so the report will say so rather "
            "than contain claims. Approve items on the Review Handover screen "
            "first."
        )

    options = ReportOptions(
        include_unreviewed=include_unreviewed, include_evidence=include_evidence
    )
    data = collect_report_data(
        project=project,
        reconstruction=latest,
        items=reviews.list_items(latest["id"]),
        missing_information=reconstructions.missing_information(latest),
        options=options,
    )

    try:
        markdown = build_markdown(data, options)
    except Exception:  # pragma: no cover - defensive
        st.error("The handover report could not be generated.")
        return

    st.markdown(ui.divider(), unsafe_allow_html=True)
    st.markdown("### Download")
    st.markdown(
        f'<div class="tr-note">{data.included_count} item(s) will be '
        f"included. {data.rejected_count} rejected item(s) are excluded.</div>",
        unsafe_allow_html=True,
    )

    downloads = st.columns(2)
    with downloads[0]:
        st.download_button(
            "Download Markdown",
            data=markdown.encode("utf-8"),
            file_name=report_filename(project["name"], "md"),
            mime="text/markdown",
            width="stretch",
        )
    with downloads[1]:
        try:
            pdf_bytes = build_pdf(data, options)
        except Exception:  # pragma: no cover - defensive
            st.error("The PDF could not be generated. The Markdown export still works.")
        else:
            st.download_button(
                "Download PDF",
                data=pdf_bytes,
                file_name=report_filename(project["name"], "pdf"),
                mime="application/pdf",
                type="primary",
                width="stretch",
            )

    with st.expander("Preview the report"):
        st.markdown(markdown)


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

def _sign_in_user(user: dict) -> None:
    st.session_state["user_id"] = str(user["id"])
    st.session_state["page"] = PAGE_DASHBOARD
    st.session_state.pop("project_id", None)
    st.rerun()


def _render_auth_screen() -> None:
    """Sign in or create an account. Nothing else is reachable until then."""
    st.markdown(
        ui.page_head("Sign in to TRACE", config.APP_DESCRIPTION, eyebrow="TRACE"),
        unsafe_allow_html=True,
    )
    st.markdown('<div style="height:1rem"></div>', unsafe_allow_html=True)

    auth = _auth_service()
    left, right = st.columns([1, 1], gap="large")

    with left:
        with st.form("sign_in"):
            st.markdown("### Sign in")
            email = st.text_input("Email", key="si_email")
            password = st.text_input("Password", type="password", key="si_password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            try:
                user = auth.authenticate(email, password)
            except AuthError as exc:
                st.error(str(exc))
            else:
                _sign_in_user(user)

    with right:
        with st.form("sign_up"):
            st.markdown("### Create an account")
            new_name = st.text_input("Your name", key="su_name")
            new_email = st.text_input("Email", key="su_email")
            new_password = st.text_input(
                "Password", type="password", key="su_password",
                help="At least 8 characters.",
            )
            created = st.form_submit_button("Create account")
        if created:
            try:
                user = auth.register(new_email, new_password, new_name)
            except AuthError as exc:
                st.error(str(exc))
            else:
                _sign_in_user(user)

    st.markdown(ui.divider(), unsafe_allow_html=True)
    st.markdown(
        '<div class="tr-panel">'
        '<div class="tr-title">Roles in TRACE</div>'
        '<div class="tr-desc" style="margin-top:.35rem">A project owner grants '
        "each member one of these. The checks run on the server, so a role "
        "without permission cannot retrieve the content by any route &mdash; "
        "not merely have the button hidden.</div>"
        + "".join(
            f'<div style="margin-top:.7rem"><strong>{ui.esc(ROLE_LABELS[r])}</strong>'
            f'<div class="tr-note">{ui.esc(ROLE_DESCRIPTIONS[r])}</div></div>'
            for r in Role
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    # Shown only once the demo has been loaded on this deployment, so a judge
    # can see the access checks refuse things rather than take it on trust.
    if auth.find_by_email(DEMO_ACCOUNTS[0][0]) is not None:
        st.markdown('<div style="height:.8rem"></div>', unsafe_allow_html=True)
        lines = "\n".join(
            f"- `{email}` — {ROLE_LABELS[role]}"
            for email, _, role in DEMO_ACCOUNTS
        )
        st.info(
            "**Demo accounts are loaded on this deployment.** Sign in as any of "
            f"them with the password `{DEMO_PASSWORD}` to see the same project "
            f"through a different role:\n\n{lines}\n\n"
            "They exist only to demonstrate access control on synthetic data."
        )


# --------------------------------------------------------------------------
# Access (members)
# --------------------------------------------------------------------------

def _render_access(service: ProjectService) -> None:
    project_id = st.session_state.get("project_id")
    if not project_id:
        st.markdown(ui.page_head("Access", eyebrow="Access"), unsafe_allow_html=True)
        st.info("Open a project from the Dashboard to see who can access it.")
        return

    try:
        project = service.require_project(project_id)
        actor = _principal(project_id)
    except (ValidationError, AccessDenied) as exc:
        st.error(str(exc))
        return

    auth = _auth_service()
    st.markdown(
        ui.page_head(
            project["name"],
            "Who can see this handover, and what each of them is allowed to do.",
            eyebrow="Access",
            tag=ui.demo_tag() if project.get("is_demo") else "",
        ),
        unsafe_allow_html=True,
    )
    st.markdown('<div style="height:1.1rem"></div>', unsafe_allow_html=True)

    if warn := st.session_state.pop("access_warn", None):
        st.warning(warn)
    if flash := st.session_state.pop("access_flash", None):
        st.success(flash)

    members = auth.list_members(project_id)
    st.markdown(f"## Members ({len(members)})")
    for member in members:
        is_self = member["user_id"] == actor.user_id
        row, action = st.columns([5, 1.2], vertical_alignment="center")
        with row:
            name = member.get("display_name") or member["email"]
            st.markdown(
                f'<div class="tr-title">{ui.esc(name)}'
                + (' <span class="tr-chip">you</span>' if is_self else "")
                + "</div>"
                '<div class="tr-meta-row" style="margin-top:.35rem">'
                + ui.pill(ROLE_LABELS.get(member["role"], member["role"]), "info")
                + ui.chip(member["email"])
                + "</div>"
                f'<div class="tr-note" style="margin-top:.3rem">'
                f'{ui.esc(ROLE_DESCRIPTIONS.get(member["role"], ""))}</div>',
                unsafe_allow_html=True,
            )
        with action:
            removable = (
                actor.can(Capability.MANAGE_MEMBERS)
                and not is_self
                and member["role"] != str(Role.OWNER)
            )
            if removable and st.button(
                "Remove", key=f"rm_{member['user_id']}", width="stretch"
            ):
                try:
                    auth.revoke_access(actor, project_id, member["user_id"])
                except AuthError as exc:
                    st.session_state["access_warn"] = str(exc)
                else:
                    st.session_state["access_flash"] = f"Removed {member['email']}."
                st.rerun()
        st.markdown(ui.divider(), unsafe_allow_html=True)

    if not actor.can(Capability.MANAGE_MEMBERS):
        st.info(
            "Only the project owner can change who has access. You are signed "
            f"in as **{actor.role_label}**."
        )
        return

    st.markdown("## Grant access")
    with st.form("grant_role"):
        st.markdown(
            ui.note(
                "The person needs a TRACE account already. Ask them to sign up, "
                "then add them here."
            ),
            unsafe_allow_html=True,
        )
        email = st.text_input("Their email")
        role = st.selectbox(
            "Role", assignable_roles(),
            format_func=lambda r: f"{ROLE_LABELS[r]} — {ROLE_DESCRIPTIONS[r]}",
        )
        granted = st.form_submit_button("Grant access", type="primary")
    if granted:
        try:
            target = auth.grant_role(actor, project_id, email, role)
        except (AuthError, AccessDenied) as exc:
            st.session_state["access_warn"] = str(exc)
        else:
            st.session_state["access_flash"] = (
                f"{target['email']} is now {ROLE_LABELS[role]} on this project."
            )
        st.rerun()


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

    # Nothing is reachable without an account. This gates the interface; the
    # services enforce their own checks regardless of what the UI shows.
    if _current_user() is None:
        st.session_state.pop("project_id", None)
        _render_auth_screen()
        return

    page = _render_sidebar()
    try:
        if page == PAGE_WORKSPACE:
            _render_workspace(service)
        elif page == PAGE_REVIEW:
            _render_review(service)
        elif page == PAGE_EXPORT:
            _render_export(service)
        elif page == PAGE_ACCESS:
            _render_access(service)
        else:
            _render_dashboard(service)
    except AccessDenied as exc:
        # A role reached a screen it cannot use. Say so plainly, rather than
        # showing a traceback or, worse, partial content.
        st.error(str(exc))
        st.markdown(
            ui.note(
                "If you think you should have access, ask the project owner to "
                "change your role."
            ),
            unsafe_allow_html=True,
        )

if __name__ == "__main__":
    main()
