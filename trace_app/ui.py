"""Presentation layer: design tokens, components, and status rendering.

The visual language follows one rule from the specification: TRACE should read
as a serious project-continuity tool, not a chatbot and not a dashboard full of
invented numbers. So there are no decorative charts and no vanity metrics here.
Every figure a component renders is passed in from a database query.

Status is always rendered as a *text label* with a colour, never colour alone,
so the evidence and review states stay legible to anyone.
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

# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

ACCENT = "#17567E"
ACCENT_SOFT = "#EAF1F7"

#: Each category gets its own colour and mark. Ten grey boxes in a column are
#: hard to scan; a consistent colour and a small icon let a reader find the
#: blockers without reading every heading. The marks are drawn as inline SVG
#: paths so they need no font and no network request.
CATEGORY_COLOURS: dict[str, str] = {
    "project_overview": "#17567E",
    "decision": "#1E9E68",
    "completed_work": "#2F8F5B",
    "pending_work": "#D08A16",
    "blocker": "#C2410C",
    "dependency": "#7A5CC4",
    "risk": "#D04545",
    "open_question": "#2C83BC",
    "responsibility": "#5C7089",
    "next_action": "#0E7C86",
}

#: 16x16 stroke paths, drawn with currentColor so they inherit the category.
_CATEGORY_PATHS: dict[str, str] = {
    "project_overview": "M3 3h10v10H3z M5.5 6h5 M5.5 8.5h5 M5.5 11h3",
    "decision": "M8 2v5 M8 7l-4 3 M8 7l4 3 M4 10v3 M12 10v3",
    "completed_work": "M3 8.5l3.2 3L13 5",
    "pending_work": "M8 3.2a4.8 4.8 0 1 0 0 9.6 4.8 4.8 0 0 0 0-9.6z M8 5.5V8l2 1.3",
    "blocker": "M8 2.6 2.6 8 8 13.4 13.4 8z M6 6l4 4 M10 6l-4 4",
    "dependency": "M6.4 9.6 9.6 6.4 M6 11.2 4.6 12.6a2.3 2.3 0 0 1-3.2-3.2L2.8 8"
                  " M13.2 8l1.4-1.4a2.3 2.3 0 0 0-3.2-3.2L10 4.8",
    "risk": "M8 2.8 1.8 13.2h12.4z M8 6.6v3 M8 11.2v.2",
    "open_question": "M8 2.6a5.4 5.4 0 1 0 0 10.8 5.4 5.4 0 0 0 0-10.8z"
                     " M6.4 6.3a1.7 1.7 0 1 1 1.9 1.9v1 M8.2 11v.2",
    "responsibility": "M8 8.4a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2z"
                      " M3.2 13.4a4.8 4.8 0 0 1 9.6 0",
    "next_action": "M2.8 8h9 M8.6 4.8 11.8 8l-3.2 3.2",
}


def category_colour(category: str | None) -> str:
    return CATEGORY_COLOURS.get(str(category or ""), "#6B7A8C")


def category_icon(category: str | None, size: int = 14) -> str:
    """An inline mark for a category, coloured to match it."""
    path = _CATEGORY_PATHS.get(str(category or ""))
    if not path:
        return ""
    colour = category_colour(category)
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 16 16" fill="none" '
        f'stroke="{colour}" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true" '
        f'style="vertical-align:-2px;flex:0 0 auto"><path d="{path}"/></svg>'
    )


def category_badge(category: str | None) -> str:
    """Icon plus label, in the category's own colour."""
    from trace_app.models import category_label

    colour = category_colour(category)
    return (
        f'<span class="tr-cat" style="color:{colour}">'
        f"{category_icon(category)}"
        f"<span>{esc(category_label(category))}</span></span>"
    )


def category_heading(category: str | None, count: int | None = None) -> str:
    """A section heading carrying the category's mark and, optionally, a count."""
    from trace_app.models import category_label

    colour = category_colour(category)
    tally = f' <span class="tr-chip">{count}</span>' if count is not None else ""
    # Streamlit rewrites the heading and wraps its text, which can swallow the
    # flex gap, so the mark carries its own spacing inline.
    mark = category_icon(category, 17).replace(
        'style="vertical-align:-2px;flex:0 0 auto"',
        'style="vertical-align:-3px;flex:0 0 auto;margin-right:.5rem"',
    )
    return (
        f'<h2 class="tr-cat-head" style="--cat:{colour}">'
        f"{mark}<span>{esc(category_label(category))}</span>{tally}</h2>"
    )

#: tone -> (text, background, border, dot)
_TONES: dict[str, tuple[str, str, str, str]] = {
    "neutral": ("#475569", "#F1F4F8", "#DDE3EB", "#94A3B8"),
    "positive": ("#0F6B45", "#E8F5EE", "#BFE2CE", "#1E9E68"),
    "caution": ("#8A5A0B", "#FDF4E3", "#F0DCB0", "#D08A16"),
    "negative": ("#A32B2B", "#FCEDED", "#F1CCCC", "#D04545"),
    "info": ("#17567E", "#EAF1F7", "#C7DCEB", "#2C83BC"),
}

_STATUS_TONES: dict[str, str] = {
    ExtractionStatus.UPLOADED: "neutral",
    ExtractionStatus.EXTRACTED: "positive",
    ExtractionStatus.PARTIALLY_EXTRACTED: "caution",
    ExtractionStatus.NO_TEXT_EXTRACTED: "caution",
    ExtractionStatus.FAILED: "negative",
    ReconstructionStatus.NOT_STARTED: "neutral",
    ReconstructionStatus.PROCESSING: "info",
    ReconstructionStatus.DRAFT_READY: "positive",
    ReconstructionStatus.FAILED: "negative",
    ReviewStatus.PROPOSED: "info",
    ReviewStatus.APPROVED: "positive",
    ReviewStatus.REJECTED: "negative",
    EvidenceStatus.SUPPORTED: "positive",
    EvidenceStatus.PARTIALLY_SUPPORTED: "caution",
    EvidenceStatus.CONFLICTING: "caution",
    EvidenceStatus.UNSUPPORTED: "negative",
    EvidenceStatus.NEEDS_REVIEW: "info",
}

_CSS_SOURCE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --tr-accent: #17567E;
    --tr-accent-dark: #11425F;
    --tr-ink: #0F172A;
    --tr-ink-2: #475569;
    --tr-ink-3: #6B7A8C;
    --tr-line: #E3E8EE;
    --tr-line-soft: #EDF1F5;
    --tr-surface: #FFFFFF;
    --tr-surface-2: #F7F9FB;
    --tr-radius: 12px;
    --tr-shadow: 0 1px 2px rgba(15,23,42,.04), 0 1px 3px rgba(15,23,42,.06);
    --tr-shadow-lift: 0 2px 4px rgba(15,23,42,.05), 0 6px 16px rgba(15,23,42,.07);
  }

  html, body, [class*="st-"], button, input, textarea, select {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-feature-settings: 'cv05' 1, 'ss01' 1;
  }
  /* Streamlit draws its icons as ligatures in a Material font, and its icon
     spans also carry st- classes. Without this they inherit Inter and render
     as raw ligature names ("arrow_right") instead of glyphs. */
  span[data-testid="stIconMaterial"],
  [class*="material-symbols"], .material-icons {
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                 'Material Icons' !important;
    font-feature-settings: 'liga' !important;
  }

  .stApp { background: var(--tr-surface-2); }
  .block-container {
    padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1240px;
  }
  #MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }

  h1, h2, h3, h4 { color: var(--tr-ink); letter-spacing: -.018em; }
  h1 { font-size: 1.95rem !important; font-weight: 700 !important; line-height: 1.2 !important; }
  h2 { font-size: 1.22rem !important; font-weight: 650 !important; margin: .2rem 0 .6rem !important; }
  h3 { font-size: 1.02rem !important; font-weight: 620 !important; }
  p, li, label, span { color: var(--tr-ink-2); }

  /* ---------- sidebar ---------- */
  section[data-testid="stSidebar"] {
    background: #FFFFFF; border-right: 1px solid var(--tr-line);
  }
  section[data-testid="stSidebar"] .block-container { padding-top: 1.6rem; }
  .tr-brand {
    display: flex; align-items: center; gap: .55rem; margin-bottom: .15rem;
  }
  .tr-brand-mark {
    width: 26px; height: 26px; border-radius: 7px; flex: 0 0 26px;
    background: linear-gradient(135deg, var(--tr-accent), #2C83BC);
    display: grid; place-items: center;
    color: #fff; font-size: .72rem; font-weight: 700; letter-spacing: .02em;
  }
  .tr-brand-name {
    font-size: 1.18rem; font-weight: 700; letter-spacing: .09em;
    color: var(--tr-ink);
  }
  .tr-brand-sub {
    font-size: .76rem; color: var(--tr-ink-3); line-height: 1.4;
    margin: 0 0 1.1rem 0;
  }

  /* ---------- generic surfaces ---------- */
  .tr-card {
    background: var(--tr-surface); border: 1px solid var(--tr-line);
    border-radius: var(--tr-radius); padding: 1.05rem 1.2rem;
    box-shadow: var(--tr-shadow); margin-bottom: .75rem;
  }
  .tr-card-pad { padding: 1.3rem 1.4rem; }
  .tr-panel {
    background: var(--tr-surface); border: 1px solid var(--tr-line);
    border-radius: var(--tr-radius); padding: 1.25rem 1.35rem;
    box-shadow: var(--tr-shadow);
  }
  .tr-title { font-size: 1.04rem; font-weight: 640; color: var(--tr-ink); }
  .tr-desc  { font-size: .89rem; color: var(--tr-ink-2); line-height: 1.5; }
  .tr-note  { font-size: .81rem; color: var(--tr-ink-3); line-height: 1.5; }
  .tr-rule  { border-top: 1px solid var(--tr-line); margin: 1.5rem 0 1.2rem; }

  /* ---------- page header ---------- */
  .tr-page-head { margin-bottom: .2rem; }
  .tr-eyebrow {
    font-size: .7rem; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; color: var(--tr-accent); margin-bottom: .3rem;
  }
  .tr-lede {
    font-size: .95rem; color: var(--tr-ink-2); line-height: 1.6; max-width: 66ch;
  }

  /* ---------- pills ---------- */
  .tr-pill {
    display: inline-flex; align-items: center; gap: .36rem;
    padding: .16rem .6rem .16rem .5rem; border-radius: 999px;
    font-size: .745rem; font-weight: 600; line-height: 1.55;
    border: 1px solid transparent; white-space: nowrap;
  }
  .tr-dot { width: 6px; height: 6px; border-radius: 50%; flex: 0 0 6px; }

  /* ---------- stat tiles ---------- */
  .tr-stats { display: flex; gap: .7rem; flex-wrap: wrap; margin: .2rem 0 .3rem; }
  .tr-stat {
    flex: 1 1 0; min-width: 118px; background: var(--tr-surface);
    border: 1px solid var(--tr-line); border-radius: 10px; padding: .7rem .85rem;
  }
  .tr-stat-v {
    font-size: 1.5rem; font-weight: 680; color: var(--tr-ink);
    line-height: 1.15; font-variant-numeric: tabular-nums;
  }
  .tr-stat-l {
    font-size: .705rem; font-weight: 600; letter-spacing: .055em;
    text-transform: uppercase; color: var(--tr-ink-3); margin-top: .15rem;
  }

  /* ---------- workflow stepper ---------- */
  .tr-steps {
    display: flex; align-items: stretch; gap: 0; background: var(--tr-surface);
    border: 1px solid var(--tr-line); border-radius: var(--tr-radius);
    overflow: hidden; box-shadow: var(--tr-shadow); margin-bottom: 1.1rem;
  }
  .tr-step {
    flex: 1 1 0; padding: .7rem .5rem .7rem .95rem; position: relative;
    border-right: 1px solid var(--tr-line-soft); min-width: 0;
  }
  .tr-step:last-child { border-right: 0; }
  .tr-step-n {
    display: inline-grid; place-items: center; width: 19px; height: 19px;
    border-radius: 50%; font-size: .68rem; font-weight: 700; margin-right: .4rem;
    vertical-align: middle;
  }
  .tr-step-t {
    font-size: .8rem; font-weight: 600; vertical-align: middle;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .tr-step-s {
    font-size: .715rem; margin-top: .22rem; margin-left: 1.55rem;
    color: var(--tr-ink-3); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis;
  }
  .tr-step-done .tr-step-n { background: #E8F5EE; color: #0F6B45; }
  .tr-step-done .tr-step-t { color: var(--tr-ink); }
  .tr-step-now { background: linear-gradient(180deg, #F3F8FC 0%, #FFFFFF 100%); }
  .tr-step-now .tr-step-n { background: var(--tr-accent); color: #fff; }
  .tr-step-now .tr-step-t { color: var(--tr-accent); font-weight: 680; }
  .tr-step-todo .tr-step-n { background: #F1F4F8; color: #94A3B8; }
  .tr-step-todo .tr-step-t { color: #94A3B8; }

  /* ---------- segmented progress ---------- */
  .tr-bar {
    display: flex; height: 7px; border-radius: 999px; overflow: hidden;
    background: #EDF1F5; margin: .55rem 0 .4rem;
  }
  .tr-bar-seg { height: 100%; }
  .tr-legend { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: .35rem; }
  .tr-legend-i {
    display: inline-flex; align-items: center; gap: .35rem;
    font-size: .765rem; color: var(--tr-ink-2);
  }

  /* ---------- evidence ---------- */
  .tr-quote {
    font-size: .835rem; color: #334155; line-height: 1.6;
    background: #F7F9FB; border-left: 3px solid #C7DCEB;
    border-radius: 0 6px 6px 0; padding: .6rem .8rem; margin: .4rem 0 .25rem;
  }
  .tr-quote-bad { border-left-color: #F1CCCC; background: #FDF6F6; }
  .tr-src {
    font-size: .765rem; color: var(--tr-ink-3); font-weight: 500;
  }
  .tr-src strong { color: var(--tr-ink-2); font-weight: 600; }

  /* ---------- category identity ---------- */
  .tr-cat {
    display: inline-flex; align-items: center; gap: .32rem;
    font-size: .745rem; font-weight: 650; letter-spacing: .01em;
  }
  h2.tr-cat-head {
    display: flex !important; align-items: center !important;
    gap: .5rem !important;
    margin: 1.7rem 0 .75rem !important; padding: .1rem 0 .1rem .65rem;
    border-left: 3px solid var(--cat, var(--tr-accent));
  }
  h2.tr-cat-head svg { flex: 0 0 auto; }
  /* ---------- hero ---------- */
  .tr-hero {
    background: linear-gradient(135deg, #17567E 0%, #1F6F96 55%, #2C83BC 100%);
    border-radius: 14px; padding: 1.8rem 2rem; color: #fff;
    margin-bottom: 1.4rem;
  }
  /* Streamlit rewrites an injected heading: it strips inline styles, adds an
     id, and wraps the text in its own span. Colouring only the h1 therefore
     leaves the visible text dark on the blue. Colour the descendants too. */
  .tr-hero h1, .tr-hero h1 * {
    color: #fff !important; margin: 0 0 .4rem !important;
  }
  .tr-hero h1 a, .tr-hero h1 svg { display: none !important; }
  .tr-hero p, .tr-hero p * {
    color: rgba(255,255,255,.9) !important; font-size: .97rem;
    line-height: 1.6; margin: 0; max-width: 62ch;
  }
  .tr-hero-eyebrow {
    font-size: .68rem; font-weight: 700; letter-spacing: .13em;
    text-transform: uppercase; color: rgba(255,255,255,.72);
    margin-bottom: .5rem;
  }
  .tr-hero-points { display: flex; gap: 1.6rem; flex-wrap: wrap;
                    margin-top: 1.1rem; }
  .tr-hero-point { font-size: .82rem; color: rgba(255,255,255,.92) !important;
                   display: flex; align-items: center; gap: .4rem; }
  .tr-hero-point svg { flex: 0 0 auto; }

  /* ---------- stat tiles get their accent back ---------- */
  .tr-stat { position: relative; overflow: hidden; }
  .tr-stat::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--tr-accent); opacity: .55;
  }

  /* ---------- item card ---------- */
  .tr-item-text {
    font-size: .955rem; color: var(--tr-ink); line-height: 1.55;
    margin: .5rem 0 .4rem;
  }
  .tr-meta-row { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
  .tr-attention {
    font-size: .745rem; color: #8A5A0B; font-weight: 500;
  }

  /* ---------- metadata ---------- */
  .tr-k {
    font-size: .695rem; text-transform: uppercase; letter-spacing: .06em;
    color: var(--tr-ink-3); font-weight: 650;
  }
  .tr-v { font-size: .9rem; color: var(--tr-ink); }

  .tr-tag {
    display: inline-block; font-size: .655rem; font-weight: 700;
    letter-spacing: .07em; color: #8A5A0B; background: #FDF4E3;
    border: 1px solid #F0DCB0; border-radius: 4px; padding: .1rem .4rem;
    margin-left: .45rem; vertical-align: middle;
  }
  .tr-chip {
    display: inline-block; font-size: .68rem; font-weight: 650;
    color: var(--tr-ink-3); background: #F1F4F8; border-radius: 5px;
    padding: .1rem .38rem; letter-spacing: .03em;
  }

  /* ---------- Streamlit widget polish ---------- */
  .stButton > button {
    border-radius: 8px; font-weight: 550; font-size: .875rem;
    border: 1px solid var(--tr-line); transition: all .12s ease;
    box-shadow: 0 1px 1px rgba(15,23,42,.03);
  }
  .stButton > button:hover:not(:disabled) {
    border-color: #C7D3E0; box-shadow: var(--tr-shadow); transform: translateY(-1px);
  }
  /* Streamlit renders a button's label as a <p> inside the button, which
     otherwise inherits the generic body text colour -- leaving a dark label
     on a dark primary button. Inheriting from the button keeps every button
     kind (primary, secondary, form submit) correct without naming each one. */
  .stButton button *, .stFormSubmitButton button *,
  .stDownloadButton button *, .stLinkButton a * {
    color: inherit !important;
  }
  .stButton > button[kind="primary"] {
    background: var(--tr-accent); border-color: var(--tr-accent); color: #fff;
  }
  .stButton > button[kind="primary"]:hover:not(:disabled) {
    background: var(--tr-accent-dark); border-color: var(--tr-accent-dark);
  }
  .stDownloadButton > button { border-radius: 8px; font-weight: 550; }

  div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 11px !important;
    transition: box-shadow .14s ease;
  }
  div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    box-shadow: var(--tr-shadow);
  }
  div[data-testid="stExpander"] {
    border: 1px solid var(--tr-line) !important; border-radius: 10px !important;
    background: var(--tr-surface); box-shadow: none;
  }
  div[data-testid="stExpander"] summary { font-size: .845rem; font-weight: 550; }
  div[data-testid="stExpander"] summary:hover { color: var(--tr-accent); }

  div[data-testid="stForm"] {
    border: 1px solid var(--tr-line); border-radius: var(--tr-radius);
    padding: 1.2rem 1.3rem; background: var(--tr-surface);
    box-shadow: var(--tr-shadow);
  }

  div[data-testid="stFileUploaderDropzone"] {
    border: 1.5px dashed #CBD5E1; border-radius: 10px; background: #FBFCFD;
  }
  div[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--tr-accent); }

  div[data-testid="stAlert"] { border-radius: 9px; font-size: .875rem; }

  .stTextInput input, .stTextArea textarea {
    border-radius: 8px !important; font-size: .885rem !important;
  }
  .stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--tr-accent) !important;
    box-shadow: 0 0 0 2px rgba(23,86,126,.12) !important;
  }

  div[data-testid="stRadio"] label { font-size: .86rem; }
  hr { margin: 1.1rem 0; border-color: var(--tr-line); }

  /* ---- Navigation rail -------------------------------------------------
     Streamlit draws page navigation as a radio group, and a column of radio
     circles reads as a form control rather than as navigation. These rules
     hide the circle and style each option as a nav row: a tinted pill with a
     left accent bar for the current page.

     Scoped to .st-key-tr-nav, the class Streamlit emits for the keyed
     container, so no other radio in the app is touched. The selectors follow
     the structure Streamlit actually renders, which was read from the live
     DOM rather than guessed:
         label[data-testid="stRadioOption"][data-selected]
           > span            (visually hidden <input>)
           > div             (flex row)
               > div         (the circle)
               > div > p     (the label text)
     data-selected is used for the active state in preference to
     :has(input:checked) because it is the attribute Streamlit maintains. */
  .st-key-tr-nav div[role="radiogroup"] {
    gap: 1px !important; display: flex; flex-direction: column;
  }
  .st-key-tr-nav label[data-testid="stRadioOption"] {
    padding: .44rem .6rem; border-radius: 8px; margin: 0 !important;
    border-left: 2px solid transparent; transition: background .12s ease;
  }
  .st-key-tr-nav label[data-testid="stRadioOption"]:hover {
    background: rgba(23,86,126,.055);
  }
  .st-key-tr-nav label[data-testid="stRadioOption"] > div > div:first-child {
    display: none !important;
  }
  .st-key-tr-nav label[data-testid="stRadioOption"] p {
    font-size: .875rem !important; font-weight: 500 !important;
    color: var(--tr-muted) !important;
  }
  .st-key-tr-nav label[data-testid="stRadioOption"][data-selected="true"] {
    background: rgba(23,86,126,.085); border-left-color: var(--tr-accent);
  }
  .st-key-tr-nav label[data-testid="stRadioOption"][data-selected="true"] p {
    color: var(--tr-accent) !important; font-weight: 650 !important;
  }

  /* ---- Segmented filter ------------------------------------------------
     The review filter is a short, mutually exclusive set, so it reads better
     as a segmented control than as a row of circles. Same scoping and the
     same DOM structure as the nav rail above. */
  .st-key-tr-filter div[role="radiogroup"] {
    gap: .3rem !important; flex-wrap: wrap;
  }
  .st-key-tr-filter label[data-testid="stRadioOption"] {
    padding: .3rem .72rem; border-radius: 999px; margin: 0 !important;
    border: 1px solid var(--tr-line); background: #fff;
    transition: background .12s ease, border-color .12s ease;
  }
  .st-key-tr-filter label[data-testid="stRadioOption"]:hover {
    border-color: var(--tr-accent);
  }
  .st-key-tr-filter label[data-testid="stRadioOption"] > div > div:first-child {
    display: none !important;
  }
  .st-key-tr-filter label[data-testid="stRadioOption"] p {
    font-size: .8rem !important; font-weight: 500 !important;
    color: var(--tr-muted) !important;
  }
  .st-key-tr-filter label[data-testid="stRadioOption"][data-selected="true"] {
    background: var(--tr-accent); border-color: var(--tr-accent);
  }
  .st-key-tr-filter label[data-testid="stRadioOption"][data-selected="true"] p {
    color: #fff !important; font-weight: 600 !important;
  }
</style>
"""


#: Streamlit renders this through a Markdown parser, and in Markdown a blank
#: line ends an HTML block -- which would dump the rest of the stylesheet onto
#: the page as visible text. The source above keeps blank lines for
#: readability; they are stripped here so the <style> element stays intact.
GLOBAL_CSS = "\n".join(
    line for line in _CSS_SOURCE.splitlines() if line.strip()
)


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def pill(label: str, tone: str = "neutral", dot: bool = True) -> str:
    """A labelled status pill. The label is always present, never colour alone."""
    text, background, border, dot_colour = _TONES.get(tone, _TONES["neutral"])
    marker = (
        f'<span class="tr-dot" style="background:{dot_colour}"></span>' if dot else ""
    )
    return (
        f'<span class="tr-pill" style="color:{text};background:{background};'
        f'border-color:{border}">{marker}{esc(label)}</span>'
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


def tone_for(value: str | None) -> str:
    return _STATUS_TONES.get(value or "", "neutral")


def brand() -> str:
    return (
        '<div class="tr-brand">'
        '<span class="tr-brand-mark">TR</span>'
        '<span class="tr-brand-name">TRACE</span>'
        "</div>"
        '<div class="tr-brand-sub">Project Knowledge &amp; Handover System</div>'
    )


def hero(title: str, lede: str, eyebrow: str = "", points: list[str] | None = None) -> str:
    """A full-width banner for the top of an entry screen."""
    bullets = ""
    if points:
        bullets = (
            '<div class="tr-hero-points">'
            + "".join(
                f'<span class="tr-hero-point">'
                '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" '
                'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
                'stroke-linejoin="round"><path d="M3 8.5l3.2 3L13 5"/></svg>'
                f"{esc(point)}</span>"
                for point in points
            )
            + "</div>"
        )
    return (
        '<div class="tr-hero">'
        + (f'<div class="tr-hero-eyebrow">{esc(eyebrow)}</div>' if eyebrow else "")
        # Streamlit's own heading colour beats the stylesheet here, so the
        # colour is set inline where nothing can override it.
        + f'<h1 style="color:#fff !important">{esc(title)}</h1>'
        + f'<p style="color:rgba(255,255,255,.9) !important">{esc(lede)}</p>'
        + f"{bullets}</div>"
    )


def page_head(title: str, lede: str = "", eyebrow: str = "", tag: str = "") -> str:
    """A consistent page title block."""
    parts = ['<div class="tr-page-head">']
    if eyebrow:
        parts.append(f'<div class="tr-eyebrow">{esc(eyebrow)}</div>')
    parts.append(f"<h1>{esc(title)}{tag}</h1>")
    if lede:
        parts.append(f'<div class="tr-lede">{esc(lede)}</div>')
    parts.append("</div>")
    return "".join(parts)


def stats(items: list[tuple[str, object]]) -> str:
    """A row of figures. Every value must come from a real query."""
    tiles = "".join(
        f'<div class="tr-stat"><div class="tr-stat-v">{esc(value)}</div>'
        f'<div class="tr-stat-l">{esc(label)}</div></div>'
        for label, value in items
    )
    return f'<div class="tr-stats">{tiles}</div>'


#: The product's four stages, used for the workflow stepper.
WORKFLOW = ("Add sources", "Reconstruct", "Review", "Handover")


def stepper(states: list[tuple[str, str, str]]) -> str:
    """Render the workflow position.

    ``states`` is ``(title, subtitle, state)`` where state is done / now / todo.
    Subtitles carry real counts, so the stepper doubles as a status summary.
    """
    cells = []
    for index, (title, subtitle, state) in enumerate(states, start=1):
        css = {"done": "tr-step-done", "now": "tr-step-now"}.get(state, "tr-step-todo")
        mark = "✓" if state == "done" else str(index)
        cells.append(
            f'<div class="tr-step {css}">'
            f'<span class="tr-step-n">{mark}</span>'
            f'<span class="tr-step-t">{esc(title)}</span>'
            f'<div class="tr-step-s">{esc(subtitle)}</div>'
            "</div>"
        )
    return f'<div class="tr-steps">{"".join(cells)}</div>'


def segmented_bar(segments: list[tuple[str, int, str]], total: int) -> str:
    """A proportional bar with a text legend. ``segments`` is (label, count, tone)."""
    if total <= 0:
        return ""
    bars, legend = [], []
    for label, count, tone in segments:
        if count <= 0:
            continue
        _, _, _, colour = _TONES.get(tone, _TONES["neutral"])
        bars.append(
            f'<div class="tr-bar-seg" style="width:{count / total * 100:.4f}%;'
            f'background:{colour}"></div>'
        )
        legend.append(
            f'<span class="tr-legend-i"><span class="tr-dot" '
            f'style="background:{colour}"></span>{esc(label)} '
            f"<strong>{count}</strong></span>"
        )
    return (
        f'<div class="tr-bar">{"".join(bars)}</div>'
        f'<div class="tr-legend">{"".join(legend)}</div>'
    )


def meta_block(label: str, value: str) -> str:
    return f'<div class="tr-k">{esc(label)}</div><div class="tr-v">{esc(value)}</div>'


def quote(excerpt: str, verified: bool = True) -> str:
    css = "tr-quote" if verified else "tr-quote tr-quote-bad"
    return f'<div class="{css}">&ldquo;{esc(excerpt)}&rdquo;</div>'


def source_ref(filename: str, location: str) -> str:
    return (
        f'<span class="tr-src"><strong>{esc(filename)}</strong> &middot; '
        f"{esc(location)}</span>"
    )


def demo_tag() -> str:
    return '<span class="tr-tag">SYNTHETIC DEMO</span>'


def chip(text: str) -> str:
    return f'<span class="tr-chip">{esc(text)}</span>'


def divider() -> str:
    return '<div class="tr-rule"></div>'


def note(text: str) -> str:
    return f'<div class="tr-note">{esc(text)}</div>'
