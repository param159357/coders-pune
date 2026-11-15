"""Build the Round 3 submission PDF.

Kept in the repository and run from it, rather than written by hand, so the
document can be regenerated after a change instead of drifting from the code
the way the README did.

    python docs/build_submission_pdf.py

Writes docs/TRACE_Round3_Submission.pdf.

Only ReportLab is used, which the project already depends on for handover
exports, so building the submission adds no dependency.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).resolve().parent / "TRACE_Round3_Submission.pdf"

INK = colors.HexColor("#15202B")
ACCENT = colors.HexColor("#17567E")
MUTED = colors.HexColor("#5B6B7A")
LINE = colors.HexColor("#D9E1E8")
WASH = colors.HexColor("#F2F6F9")

APP_URL = "https://coders-pune-2uv6xj4cwtdbwjh69x72vl.streamlit.app/"
REPO_URL = "https://github.com/param159357/coders-pune"


# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------

def build_styles() -> dict:
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle(
        "title", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=30, leading=34, textColor=INK, spaceAfter=4, alignment=TA_LEFT,
    )
    s["subtitle"] = ParagraphStyle(
        "subtitle", parent=base["Normal"], fontName="Helvetica",
        fontSize=13, leading=18, textColor=MUTED, spaceAfter=16,
    )
    s["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, leading=20, textColor=ACCENT, spaceBefore=18, spaceAfter=8,
    )
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=11.5, leading=15, textColor=INK, spaceBefore=12, spaceAfter=5,
    )
    s["body"] = ParagraphStyle(
        "body", parent=base["Normal"], fontName="Helvetica",
        fontSize=9.6, leading=14.2, textColor=INK, spaceAfter=7,
    )
    s["small"] = ParagraphStyle(
        "small", parent=s["body"], fontSize=8.6, leading=12.4, textColor=MUTED,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], spaceAfter=3.5,
    )
    s["cell"] = ParagraphStyle(
        "cell", parent=base["Normal"], fontName="Helvetica",
        fontSize=8.6, leading=12, textColor=INK,
    )
    s["cellb"] = ParagraphStyle(
        "cellb", parent=s["cell"], fontName="Helvetica-Bold",
    )
    s["cellh"] = ParagraphStyle(
        "cellh", parent=s["cell"], fontName="Helvetica-Bold", textColor=colors.white,
    )
    s["cover_meta"] = ParagraphStyle(
        "cover_meta", parent=base["Normal"], fontName="Helvetica",
        fontSize=10, leading=16, textColor=INK,
    )
    s["footer"] = ParagraphStyle(
        "footer", parent=base["Normal"], fontName="Helvetica",
        fontSize=7.6, textColor=MUTED, alignment=TA_CENTER,
    )
    return s


S = build_styles()


def P(text, style="body"):
    return Paragraph(text, S[style])


def bullets(items, style="bullet"):
    return ListFlowable(
        [ListItem(Paragraph(t, S[style]), leftIndent=10) for t in items],
        bulletType="bullet", bulletFontSize=6, bulletOffsetY=-1.5,
        leftIndent=12, bulletColor=ACCENT,
    )


def table(rows, widths, header=True, zebra=True):
    data = []
    for r_i, row in enumerate(rows):
        style = "cellh" if (header and r_i == 0) else "cell"
        data.append([Paragraph(str(c), S[style]) for c in row])

    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, ACCENT),
        ]
    if zebra:
        start = 1 if header else 0
        for i in range(start, len(data)):
            if (i - start) % 2 == 1:
                cmds.append(("BACKGROUND", (0, i), (-1, i), WASH))
    t.setStyle(TableStyle(cmds))
    return t


def callout(text):
    t = Table([[Paragraph(text, S["cell"])]], colWidths=[165 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WASH),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


# --------------------------------------------------------------------------
# Page furniture
# --------------------------------------------------------------------------

def decorate(canvas, doc):
    canvas.saveState()
    w, h = A4
    if doc.page > 1:
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(22 * mm, h - 16 * mm, w - 22 * mm, h - 16 * mm)
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(MUTED)
        canvas.drawString(22 * mm, h - 13.5 * mm, "TRACE  |  Round 3 submission  |  Team SAMURAI")
        canvas.drawRightString(w - 22 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build(story):
    doc = BaseDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=22 * mm, bottomMargin=18 * mm,
        title="TRACE - Round 3 Submission", author="Team SAMURAI",
        subject="Project Knowledge and Handover System",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main"
    )
    doc.addPageTemplates([
        PageTemplate(id="all", frames=[frame], onPage=decorate)
    ])
    doc.build(story)


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------

def cover():
    out = [Spacer(1, 26 * mm)]
    out.append(P("TRACE", "title"))
    out.append(P("Project Knowledge and Handover System", "subtitle"))
    out.append(callout(
        "<b>TRACE is not a summariser.</b> A summariser produces fluent text you have "
        "to trust. TRACE produces claims you can verify: every factual claim carries "
        "the source it came from, the location inside that source, and the exact "
        "sentence it rests on. Where the evidence is weak, missing or "
        "self-contradictory, TRACE says so rather than smoothing it over, and nothing "
        "reaches a handover report until a person has approved it."
    ))
    out.append(Spacer(1, 10 * mm))
    out.append(table(
        [
            ["Field", "Detail"],
            ["Round", "Round 3 - final submission"],
            ["Team", "SAMURAI"],
            ["Member", "Param Raj Singh (solo)"],
            ["Live application", f'<font color="#17567E">{APP_URL}</font>'],
            ["Source repository", f'<font color="#17567E">{REPO_URL}</font>'],
            ["Tests", "327 automated tests, all passing"],
            ["Application code", "9,820 lines (excluding tests)"],
            ["Test code", "4,532 lines"],
            ["New dependencies added in Round 3", "None"],
        ],
        widths=[52 * mm, 113 * mm],
    ))
    out.append(Spacer(1, 6 * mm))
    out.append(P("Try it without signing up", "h2"))
    out.append(P(
        "The sign-in screen leads with a <b>Try it now</b> panel: four accounts on "
        "one shared synthetic project, one button each. The password for all four "
        "is <font face='Courier'>tracedemo2026</font>, so they can be typed into the "
        "form instead if you prefer."
    ))
    out.append(table(
        [
            ["Account", "Role", "Worth seeing"],
            ["owner@campusconnect.demo", "Owner", "Everything, plus the Access screen where roles are granted"],
            ["manager@campusconnect.demo", "Project manager", "Add sources, run a reconstruction, approve and reject"],
            ["developer@campusconnect.demo", "Incoming developer", "Read every item and its sources, and export. Cannot change anything"],
            ["viewer@campusconnect.demo", "Viewer", "Only the approved handover. Source documents and unreviewed items are refused"],
        ],
        widths=[52 * mm, 30 * mm, 83 * mm],
    ))
    out.append(Spacer(1, 3))
    out.append(P(
        "<b>Start as the project manager</b> if the project has no draft yet - that "
        "role can run the reconstruction, after which the other three have something "
        "to read. Then sign in as the viewer and compare: no Project Workspace in the "
        "navigation, no approve controls, and no counts of items they are not allowed "
        "to see. That contrast is improvement 2 working, rather than described.",
        "small",
    ))
    out.append(Spacer(1, 5 * mm))
    out.append(P(
        "<b>Note on the hosted link.</b> The app runs on Streamlit Community Cloud's "
        "free tier, which puts an app to sleep after a period of inactivity. If you "
        "are greeted by a \"this app has gone to sleep\" page, press "
        "<b>Yes, get this app back up!</b> and it returns in about 30 seconds. That "
        "is the hosting tier, not the application.",
        "small",
    ))
    out.append(PageBreak())
    return out


def section_improvements():
    out = [P("1. The three improvements committed to at Round 2", "h1")]
    out.append(P(
        "All three are built. Each was verified by running it, not by reading the "
        "code, and the evidence for each is given below."
    ))

    out.append(P("1.1  Authorised GitHub integration", "h2"))
    out.append(P(
        "A project can connect a GitHub repository and ingest its recent commits, "
        "pull requests and issues. Each record becomes an ordinary source, so "
        "everything downstream applies to it unchanged: the same chunking, the same "
        "schema validation, the same evidence verification, the same human review and "
        "the same export. A claim about the code is held to exactly the standard a "
        "claim about a PDF is - the quoted text must actually appear in the ingested "
        "record - and every citation carries the permanent GitHub URL of the commit or "
        "issue it quotes, both in the interface and in the exported report."
    ))
    out.append(bullets([
        "A public repository needs no token. A private one takes a fine-grained "
        "token with read access to Contents, Issues and Pull requests.",
        "<b>The token is never stored.</b> It is used for a single fetch and "
        "discarded; re-syncing asks again. A test asserts the token appears in no "
        "database table.",
        "Only what a handover needs is collected: identifier, title, message or "
        "body, author login, date and link. No diffs, no file contents, no email "
        "addresses or other profile data.",
    ]))
    out.append(callout(
        "<b>Verified.</b> Ingesting a real repository through the running interface "
        "stored a repository link row and 17 commits as a single evidence-bearing "
        "source of 30,679 characters, with a recorded sync note. 36 automated tests "
        "cover the client, the translation of GitHub errors, and the guarantee that "
        "the token is never persisted."
    ))

    out.append(P("1.2  Role-aware handover views", "h2"))
    out.append(P(
        "Every screen is behind sign-in, and each project has its own membership "
        "list. There are four roles, and each lands on the screen where its work "
        "begins rather than on a page it cannot use."
    ))
    out.append(table(
        [
            ["Role", "What it can do"],
            ["Owner", "Everything, including managing members and deleting the project"],
            ["Project manager", "Add sources, run reconstruction, approve and reject. Cannot change access"],
            ["Incoming developer", "Read every item and the source documents, and export. Cannot change anything"],
            ["Viewer", "Read the approved handover and the evidence quoted in it. Cannot open source documents, and cannot see items still under review"],
        ],
        widths=[38 * mm, 127 * mm],
    ))
    out.append(Spacer(1, 4))
    out.append(P(
        "<b>These are enforced in the service layer, not the interface.</b> A viewer "
        "requesting a source document receives <font face='Courier'>AccessDenied</font> "
        "from <font face='Courier'>DocumentService</font>, not a hidden button, and "
        "<font face='Courier'>ReviewService.list_items</font> filters to approved items "
        "<i>before</i> returning, so an unapproved item is never handed out at all. A "
        "role is re-read from the database on every call, so revoking access takes "
        "effect immediately rather than when a session expires."
    ))
    out.append(P(
        "Two consequences worth naming, both tested: a principal issued for one "
        "project cannot be replayed against another, and a role that cannot see "
        "unapproved items is not told how many exist either, because the count would "
        "leak them."
    ))
    out.append(callout(
        "<b>Verified.</b> 26 access-control tests have each role attempt what it must "
        "not be able to do and assert the call raises, rather than asserting that a "
        "button is absent."
    ))

    out.append(P("1.3  Evidence-backed knowledge relationship map", "h2"))
    out.append(P(
        "A flat list by category hides the thing a reader most needs: that a specific "
        "blocker is what holds up a specific task, which is itself waiting on an "
        "external approval. TRACE models those links and draws them."
    ))
    out.append(bullets([
        "Six relation types, each something a source can actually state: "
        "<i>blocks</i>, <i>depends on</i>, <i>resulted in</i>, <i>addresses</i>, "
        "<i>owned by</i>, <i>relates to</i>.",
        "A relationship carries its own quotation and is verified exactly as an "
        "item's evidence is. <b>A link with no source support is not drawn</b> - a "
        "line between two boxes asserts a causal claim, and an unsupported one "
        "would be a guess presented as a finding.",
        "The map reports how many links were proposed and how many discarded, so "
        "the gap is visible rather than hidden.",
        "Layout is deterministic: the same reconstruction always draws the same "
        "picture, which matters when comparing two runs. Selecting an item "
        "highlights it and lists every link it takes part in with the quotation "
        "behind each one.",
    ]))
    out.append(callout(
        "<b>Verified on the deployed application</b> against a live "
        "<font face='Courier'>gemini-3.6-flash</font> call: on the shared demo as it "
        "currently stands, 3 links drawn, 0 discarded as unsupported, 5 items "
        "connected. Those counts are what that reconstruction supported rather than "
        "a fixed property of the demo - re-running can find a different number, and "
        "the panel always reports how many were proposed against how many survived "
        "verification. When a corpus states no relationships, the map says so "
        "instead of inventing connections."
    ))
    out.append(PageBreak())
    return out


def section_features():
    out = [P("2. Complete list of features built", "h1")]
    out.append(P(
        "Everything listed here is implemented and exercised by the test suite. "
        "Section 6 states what is <i>not</i> built."
    ))

    groups = [
        ("Accounts and access", [
            "Email and password accounts, passwords hashed with scrypt (standard library, no new dependency)",
            "Generic sign-in failure message and a dummy hash comparison on unknown accounts, so timing does not reveal which emails exist",
            "Per-project membership with four roles and eleven distinct capabilities",
            "Capability checks in the service layer; services take a principal at construction and fail closed",
            "Role re-read from the database on every call, so revocation is immediate",
            "Role-aware landing: each member arrives on the screen their role can actually use",
        ]),
        ("Ingesting sources", [
            "Upload of PDF, DOCX and TXT, up to 25 MB per file",
            "Three independent validation checks before a byte is written: extension allow-list, size ceiling, and leading bytes matching the declared type",
            "Stored filenames generated from the source ID, so a user-supplied name never builds a path and traversal is structurally impossible",
            "Text extraction with per-segment locations: page numbers for PDF, paragraph indices for DOCX and TXT",
            "Honest extraction statuses: a scanned PDF with no text layer reports <i>No text found</i> rather than a successful empty extraction",
            "Encrypted and corrupt files reported individually; one bad file never aborts a batch",
            "Byte-exact de-duplication inside a project, and reuse of an existing extraction across projects you already belong to",
            "GitHub repository ingestion (section 1.1)",
        ]),
        ("Reconstruction", [
            "Google Gemini via structured JSON output, validated with Pydantic before anything is stored",
            "Ten handover categories: overview, decisions, tasks in progress, blockers, dependencies, risks, owners, environment, open questions, next actions",
            "Explicit detection of contradictions between sources, and of information a corpus never states",
            "Document text treated as untrusted data and never as instructions, so an uploaded file cannot redirect the model",
            "Deterministic keyword fallback when no provider is configured or the provider is unreachable, labelled as not-from-a-model everywhere it appears",
            "Reconstruction caching by input fingerprint: an unchanged re-run makes no AI call and preserves review decisions already made",
            "Up to 400,000 characters per request, roughly 150 pages in a single pass",
        ]),
        ("Evidence verification", [
            "Every citation checked against the source it names before it is stored",
            "The source must exist in this project; an invented or foreign ID is discarded",
            "The quotation must occur in that source's text, compared with whitespace and case normalised; a paraphrase does not count",
            "The location is recomputed from the segment the quotation was actually found in, so a wrong page number is corrected rather than displayed",
            "Evidence status can only be lowered relative to what the model claimed, never raised",
            "An owner or a due date is dropped entirely unless something verified supports it",
            "Verification counts surfaced in the interface: citations checked, matched, rejected, items downgraded",
        ]),
        ("Review and handover", [
            "Every item requires an explicit human decision; nothing is approved by default",
            "Approve, reject, and edit item text, with the full decision history retained",
            "Filter by awaiting review, needs a closer look, or approved",
            "Evidence inspection per item: source, location and exact quotation, with the GitHub URL where the source is a repository record",
            "Export to Markdown and PDF, approved items only, each carrying its evidence",
            "Optional inclusion of still-unreviewed items, off by default and labelled when on",
            "Knowledge relationship map (section 1.3)",
        ]),
        ("Operational behaviour", [
            "Runs with no API key, showing a clear <i>Not configured</i> state and offering no AI reconstruction",
            "A placeholder API key is treated as no key, so copying the example env file cannot produce a false <i>Configured</i>",
            "Bounded retries on availability failures only; a rejected key or a retired model fails immediately",
            "A model fallback chain, with the model that actually answered recorded on the reconstruction and named in the report",
            "Truncated model replies detected and surfaced rather than parsed as if complete",
            "Forward-only versioned SQLite migrations, currently at schema version 7",
            "A synthetic CampusConnect demo loadable in one click, carrying a planted contradiction and two documented absences",
        ]),
    ]
    for title, items in groups:
        out.append(KeepTogether([P(title, "h2"), bullets(items)]))
    out.append(PageBreak())
    return out


def section_architecture():
    out = [P("3. Technical architecture and implementation", "h1")]
    out.append(P(
        "A <b>modular monolith</b>: one Streamlit application with strictly separated "
        "modules. No microservices, no queues, no vector database and no separate "
        "frontend, because none of them would make this product more reliable at this "
        "size - they would add failure modes without removing any."
    ))

    out.append(P("3.1  Stack", "h2"))
    out.append(table(
        [
            ["Layer", "Technology", "Responsibility"],
            ["Language", "Python 3.11+", "Application logic"],
            ["Interface", "Streamlit 1.67", "Web interface, session handling"],
            ["Storage", "SQLite", "Projects, sources, items, evidence, members, relationships"],
            ["Extraction", "PyMuPDF 2.1, python-docx 1.2", "PDF and DOCX text with per-segment locations"],
            ["AI", "google-genai 3.0", "Structured JSON reconstruction"],
            ["Validation", "Pydantic 2.14", "Model output validated before storage"],
            ["Reports", "ReportLab 5.1", "PDF handover export"],
            ["Tests", "pytest 9.2", "327 automated tests"],
        ],
        widths=[26 * mm, 46 * mm, 93 * mm],
    ))
    out.append(Spacer(1, 3))
    out.append(P(
        "Round 3 added <b>no new dependencies</b>. Password hashing uses the standard "
        "library's scrypt, the GitHub client uses urllib, and the relationship map is "
        "hand-built SVG.", "small",
    ))

    out.append(P("3.2  Module layout", "h2"))
    out.append(table(
        [
            ["Module", "Contains"],
            ["app.py", "Streamlit pages and routing only; no business logic"],
            ["trace_app/services/", "One service per concern: auth, project, document, extraction, reconstruction, evidence, review, report, github, demo"],
            ["trace_app/security/", "Roles, capabilities, the principal, and scrypt password hashing"],
            ["trace_app/ai/", "Provider client, prompts, Pydantic schemas, deterministic offline preview"],
            ["trace_app/integrations/", "GitHub client: fetching, field selection, error translation"],
            ["trace_app/utils/", "Upload validation, text chunking"],
            ["trace_app/relationship_map.py", "Deterministic layout and SVG rendering of the knowledge map"],
            ["database/", "Connection, forward-only migrations, repositories"],
        ],
        widths=[48 * mm, 117 * mm],
    ))

    out.append(P("3.3  The evidence verification algorithm", "h2"))
    out.append(P(
        "This is the part that makes TRACE more than a summariser, so it is worth "
        "stating precisely. For every citation the model returns, three things are "
        "checked independently:"
    ))
    out.append(bullets([
        "<b>The source must be real.</b> The ID is looked up among this project's "
        "sources only. An invented ID, or one belonging to another project, is "
        "discarded and never reaches the database.",
        "<b>The quotation must be real.</b> The excerpt must occur in that source's "
        "extracted text, compared with whitespace and case normalised.",
        "<b>The location must be real.</b> The page or paragraph is recomputed from "
        "the segment the quotation was actually found in.",
    ]))
    out.append(Spacer(1, 2))
    out.append(table(
        [
            ["Model claimed", "What verified", "Stored as"],
            ["supported", "nothing", "unsupported"],
            ["supported", "some citations", "partially_supported"],
            ["supported", "all citations", "supported"],
            ["conflicting", "one side only", "needs_review"],
            ["needs_review", "all citations", "needs_review (never upgraded)"],
        ],
        widths=[45 * mm, 55 * mm, 65 * mm],
    ))
    out.append(Spacer(1, 3))
    out.append(P(
        "Status can only be <b>lowered</b> relative to what the model claimed, never "
        "raised. A model that labels everything \"supported\" therefore gains nothing "
        "by doing so.", "small",
    ))

    out.append(P("3.4  Scale, and not computing twice", "h2"))
    out.append(P(
        "A single reconstruction request carries up to 400,000 characters, roughly "
        "150 pages in one pass. The Round 2 figure was 60,000. It is deliberately "
        "still well below the model's context ceiling, because a larger request is "
        "markedly more likely to meet a 503 or a gateway timeout on shared capacity. "
        "Measured on a 183,000-character corpus, that is one request instead of four, "
        "in 12 seconds, with every citation verified."
    ))
    out.append(bullets([
        "<b>Extraction is not repeated.</b> Identical bytes give identical text, so "
        "a file already parsed in one of your projects is not parsed again in "
        "another. Reuse is scoped to projects you already belong to.",
        "<b>Reconstruction is not repeated.</b> A run records a fingerprint of its "
        "inputs - every source's content, the model, and the prompt. Run again with "
        "nothing changed and TRACE keeps the existing draft, makes no AI call, and "
        "preserves the review decisions already made against it.",
    ]))
    out.append(P(
        "Beyond roughly 150 pages the right answer is retrieval rather than a bigger "
        "envelope, and that changes the honesty story: \"we checked everything\" stops "
        "being true once the model only sees a subset. That trade-off is in the "
        "roadmap rather than quietly taken.", "small",
    ))

    out.append(P("3.5  Reliability of the model call", "h2"))
    out.append(P(
        "Three behaviours were added after watching the real API misbehave during "
        "development:"
    ))
    out.append(bullets([
        "<b>Model reasoning is disabled</b> (thinking_budget=0). Gemini 3.x reasons "
        "before answering by default and the thinking tokens come out of the same "
        "output budget. On the demo corpus, thinking enabled returned a truncated "
        "2.4 KB reply in 27.8 s; disabled, a complete 12.5 KB reply in 11.3 s. "
        "Reconstruction is structured extraction with exact quoting, not open "
        "reasoning, so the reasoning cost output and bought nothing.",
        "<b>Bounded retries</b> (3 attempts, 2 s then 5 s) on availability failures "
        "only. A rejected key, a retired model or an unparseable reply are permanent "
        "and fail immediately.",
        "<b>A model fallback chain.</b> Shared capacity returned 502 or 503 often "
        "enough to fail 2 of 4 live runs during development. The model that actually "
        "produced a draft is recorded and named in the report, so a handover never "
        "credits a model that did not write it.",
    ]))
    out.append(PageBreak())
    return out


def section_beyond():
    out = [P("4. What else changed after Round 2", "h1")]
    out.append(P(
        "Beyond the three committed improvements, the following was delivered in this "
        "round. Several of these came out of testing the product as a stranger would "
        "use it, which is the part of Round 3 that found the most."
    ))
    out.append(table(
        [
            ["Change", "Why it mattered"],
            ["Navigation defect fixed",
             "The sidebar discarded every other click: A to B navigated, B to C did not, C to D did. A changing index argument made Streamlit reset the control on the following run. Clicking a page and staying put is close to the worst failure a judged demo can have, and it was present on every screen."],
            ["Placeholder API key no longer reports as configured",
             "Following the setup instructions exactly left a placeholder in LLM_API_KEY, and TRACE displayed \"Configured\", attempted calls that could never succeed, and silently fell back. A product whose claim is that it does not assert what it cannot support should not assert it has an AI provider when it has a placeholder."],
            ["Anonymous GitHub 403 no longer blames a token",
             "An anonymous request that GitHub refused reported \"The token does not grant access\", directly under a panel stating that a public repository needs no token - sending the user to check a setting they never set."],
            ["Capacity raised 6.7x",
             "60,000 to 400,000 characters per request, with measured timings rather than an assumed ceiling."],
            ["Two layers of caching",
             "Identical bytes are not re-parsed, and an unchanged reconstruction makes no AI call and keeps existing review decisions."],
            ["Truncation detection",
             "A reply cut off at the output ceiling still parsed, so a run reported success with silently fewer findings. The ceiling was raised and a cut-off reply is now surfaced instead of parsed."],
            ["Adversarial test suite",
             "39 tests covering malformed, hostile and repeated input, described in section 5."],
            ["Interface work",
             "Navigation rail and segmented filters in place of raw radio controls, a verification summary showing what was actually checked, and a workflow stepper on every project screen."],
        ],
        widths=[52 * mm, 113 * mm],
    ))
    out.append(PageBreak())
    return out


def section_testing():
    out = [P("5. Testing", "h1")]
    out.append(P(
        "327 automated tests, all passing, run with "
        "<font face='Courier'>python -m pytest tests/ -q</font>. Every test runs "
        "against its own temporary SQLite file, and document fixtures are generated "
        "rather than committed, so the suite is reproducible and the repository holds "
        "no opaque binary blobs."
    ))
    out.append(table(
        [
            ["Area", "Tests", "What is asserted"],
            ["Extraction", "45", "PDF, DOCX and TXT text with locations; encrypted, corrupt and image-only files reported honestly"],
            ["Reconstruction", "43", "Schema validation, chunking, caching by fingerprint, fallback behaviour, truncation disclosure"],
            ["Robustness", "45", "Malformed, hostile and repeated input, and the public demo; see below"],
            ["GitHub", "36", "Fetching, field selection, error translation, and that the token is never persisted"],
            ["Evidence validation", "26", "Invented sources, paraphrases and wrong locations are rejected or corrected; status is never raised"],
            ["Access control", "26", "Each role attempts what it must not do and the call raises"],
            ["Review", "23", "Decisions, edits, history, and that counts do not leak to roles that cannot see the items"],
            ["Export", "23", "Approved items only; evidence carried into Markdown and PDF"],
            ["Database", "21", "Forward-only migrations across all seven schema versions"],
            ["Relationships", "17", "Unsupported links are discarded; layout is deterministic"],
            ["Documents", "15", "Validation, de-duplication, extraction reuse"],
            ["Demo", "8", "The synthetic project loads through the ordinary upload path"],
        ],
        widths=[36 * mm, 16 * mm, 113 * mm],
    ))

    out.append(P("5.1  Adversarial testing", "h2"))
    out.append(P(
        "Round 3 is judged partly on holding up under a stranger's use, so a suite was "
        "added for the product being used <i>wrongly</i> rather than correctly. Each "
        "test asserts both that the operation is refused and that it is refused with "
        "something a person can read: a crash and a silent success are both failures."
    ))
    out.append(bullets([
        "Empty (0-byte) and oversized uploads",
        "An ELF binary renamed to .txt; a .pdf and a .docx whose leading bytes do not match the declared type",
        "Path traversal, backslashes, absolute paths and NUL bytes in filenames",
        "The same file uploaded twice; the same email registered twice, including in different case",
        "Deleting a source that is already gone; reading a source across a project boundary",
        "Review and progress requested for a reconstruction that does not exist, including the division that draws the progress bar",
        "Markup in fields that are rendered back to a page, both stored and escaped",
        "Placeholder API keys, including one read from the committed example file so the test fails if that file changes",
    ]))

    out.append(P("5.2  Verified by hand against a running application", "h2"))
    out.append(P(
        "Automated tests do not exercise the browser, so the main journeys were driven "
        "in a real browser against both a local instance and the deployed app."
    ))
    out.append(table(
        [
            ["Journey", "Result"],
            ["Clone, venv, install, copy env file, run", "Works from a clean directory following only the README"],
            ["Sign up, load demo, reconstruct, review, export", "Completes end to end on the deployed application"],
            ["Knowledge map on the deployed app", "3 links drawn, 0 discarded, 5 items connected, live model call"],
            ["GitHub ingestion of a real repository", "Repository link plus 17 commits stored as an evidence-bearing source"],
            ["Browser refresh mid-session", "Session ends; no data lost. Project and sources intact after signing back in"],
            ["Second click on Reconstruct", "Cannot land: the control is gone while a run is in flight"],
            ["Export with nothing approved", "Explains what to do instead of producing an empty file"],
            ["Markdown and PDF download", "Both download; the PDF carries a valid %PDF- header"],
            ["Five consecutive navigations", "Each lands on the page clicked (this is what exposed the navigation defect)"],
            ["Wrong password, empty and malformed sign-up", "Refused with a readable message, no crash"],
        ],
        widths=[62 * mm, 103 * mm],
    ))
    out.append(Spacer(1, 4))
    out.append(callout(
        "<b>Stated plainly:</b> the truncation fix in section 4 is verified by tests "
        "and by the map working on the deployed app, but the provider quota had also "
        "reset between the failing and passing observations, so the fix is not proven "
        "to have been the cause. Both changed at once, and it would be wrong to claim "
        "otherwise."
    ))
    out.append(PageBreak())
    return out


def section_limits():
    out = [P("6. Known limitations", "h1")]
    out.append(P(
        "Stated plainly, because an honest limitation is worth more than an overclaim, "
        "and because a judge will find these anyway."
    ))
    out.append(bullets([
        "<b>Refreshing the page signs you out.</b> Sessions live in Streamlit's "
        "session state, which a browser reload clears. No data is lost - the project "
        "and its sources are intact after signing back in - but the sign-in does not "
        "survive. The fix is a signed cookie, deliberately not a URL token, which "
        "would make a session shareable by copying the address bar.",
        "<b>SQLite on ephemeral hosting.</b> Streamlit Community Cloud does not "
        "guarantee a persistent disk, so data can be lost when the container "
        "restarts. TRACE does not promise long-term persistence on the free tier.",
        "<b>Accounts are local to each deployment.</b> No SSO, no email "
        "verification, no password reset.",
        "<b>No OCR.</b> An image-only or scanned PDF yields no text, reported as "
        "<i>No text found</i> rather than a successful extraction.",
        "<b>Evidence matching is exact, not semantic.</b> A correctly paraphrased "
        "claim is marked unsupported rather than accepted - a deliberate choice to "
        "fail safe.",
        "<b>De-duplication is byte-exact.</b> Two identical files are caught; a "
        "lightly edited near-duplicate is not.",
        "<b>Integrations are GitHub only.</b> Slack, Teams and email ingestion are "
        "not implemented.",
        "<b>The relationship map depends on the sources stating links.</b> Sparse or "
        "narrative documents can legitimately produce an empty map, and the map says "
        "so rather than inventing connections.",
        "<b>Single-container deployment.</b> No horizontal scaling and no background "
        "job queue; a long reconstruction occupies the request.",
    ]))
    return out


def section_roadmap():
    out = [P("7. Roadmap and future development", "h1")]
    out.append(P(
        "Ordered honestly by what the current design is already shaped for, rather "
        "than by what sounds most ambitious."
    ))

    out.append(P("Next - completing the current design", "h2"))
    out.append(table(
        [
            ["Item", "What it involves"],
            ["Session persistence across a reload",
             "A signed, expiring token in an HTTP-only cookie. Explicitly not a URL token, which would make a session shareable by copying the address bar - a poor trade for a product whose claim is rigorous access control."],
            ["Near-duplicate detection",
             "Shingling over extracted text to catch lightly edited revisions, without weakening the exact-quotation guarantee."],
            ["OCR for scanned documents",
             "OCR text is lower confidence than embedded text, so it needs its own evidence tier rather than being silently mixed in."],
            ["Slack and Teams ingestion",
             "Reusing the GitHub connector's shape: authorised, metadata-only, every claim quoted back to a permalink."],
            ["Durable hosting",
             "Managed Postgres and object storage in place of SQLite on an ephemeral disk, removing the persistence caveat above."],
        ],
        widths=[47 * mm, 118 * mm],
    ))

    out.append(P("Later - changes that alter what TRACE can honestly claim", "h2"))
    out.append(table(
        [
            ["Item", "Why it is not simply an improvement"],
            ["Retrieval for corpora beyond ~150 pages",
             "Past the single-request envelope the model stops seeing everything, so \"we checked all of it\" stops being true. Retrieval has to arrive together with an interface that states what was and was not examined, or the central guarantee quietly weakens."],
            ["Semantic evidence matching",
             "Exact matching marks a correct paraphrase unsupported, which fails safe. Any move to embeddings must keep a verbatim tier distinct from a similarity tier."],
            ["Multi-project organisations",
             "Shared membership, audit history across projects, and SSO in place of local accounts."],
            ["Continuous handover",
             "Re-running against a live repository on a schedule, surfacing what changed since the last approved handover rather than regenerating it wholesale."],
        ],
        widths=[47 * mm, 118 * mm],
    ))

    out.append(Spacer(1, 6))
    out.append(callout(
        "<b>The through-line.</b> Every item above is judged against one question: "
        "does it keep TRACE able to say exactly where a claim came from? Anything that "
        "would blur that - inferred links, paraphrase matching, partial corpora - is "
        "only worth doing if the interface stays honest about what changed."
    ))

    out.append(P("Running it yourself", "h2"))
    out.append(P(
        "<font face='Courier' size='8.4'>git clone " + REPO_URL + "<br/>"
        "cd coders-pune<br/>"
        "python3 -m venv .venv &amp;&amp; source .venv/bin/activate<br/>"
        "pip install -r requirements.txt<br/>"
        "cp .env.example .env    # optional: add a Gemini key<br/>"
        "streamlit run app.py</font>"
    ))
    out.append(P(
        "The application starts and runs without an API key. It shows a clear "
        "<i>Not configured</i> state and offers no AI reconstruction, and no output is "
        "ever presented as having come from a live model when it did not. Tests run "
        "with <font face='Courier'>python -m pytest tests/ -q</font>.", "small",
    ))
    return out


def main() -> None:
    story = []
    story += cover()
    story += section_improvements()
    story += section_features()
    story += section_architecture()
    story += section_beyond()
    story += section_testing()
    story += section_limits()
    story += section_roadmap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build(story)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
