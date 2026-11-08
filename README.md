# TRACE — Project Knowledge and Handover System

**Team SAMURAI** · Built for the TRACE hackathon (Round 2)

TRACE turns scattered project records into a source-backed, reviewable handover
so the next person can continue work without guessing.

> **Honesty note:** the section [Implementation status](#9-implementation-status)
> states exactly what is built and verified, and
> [Current limitations](#10-current-limitations) states what is not. Nothing is
> listed as working unless it was actually run.

---

## 1. The problem

When someone leaves a team, changes role, or hands over a project, the knowledge
that made their work possible often leaves with them. Decisions, the reasoning
behind those decisions, unfinished work, blockers, dependencies, risks, and
responsibilities end up scattered across documents nobody reads again.

TRACE reconstructs that knowledge from authorised source documents, links every
factual claim back to the exact place it came from, flags what is missing or
contradictory, and requires a human to approve each item before it becomes part
of a handover report.

**TRACE is not a summariser.** A summariser produces fluent text you have to
trust. TRACE produces claims you can verify, each one carrying a source, a
location, and a short exact excerpt — plus an honest label when the evidence is
weak, absent, or self-contradictory.

## 2. Core principles

1. **Evidence before eloquence** — factual claims need traceable source support.
2. **Human control** — AI proposes; a person reviews and approves.
3. **Visible uncertainty** — missing, ambiguous, unsupported, and conflicting
   information is labelled, not smoothed over.
4. **Permission-aware use** — only authorised material is ingested.
5. **Honest claims** — an unimplemented or untested feature is never presented
   as complete.

## 3. Intended workflow

```
Add Sources  →  Reconstruct  →  Verify Evidence  →  Human Review  →  Handover
```

For this MVP, "Add Sources" means manual upload of PDF, DOCX, or TXT files.
There are **no** Slack, GitHub, Teams, or email integrations.

## 4. Architecture and stack

A **modular monolith**: one Streamlit application with clearly separated
modules. No microservices, no queues, no vector database, no separate frontend
— none of that would make this MVP more reliable.

| Area | Technology | Responsibility |
|---|---|---|
| Language | Python 3.11+ | Application logic |
| UI | Streamlit | Web interface |
| Database | SQLite | Persistence |
| PDF | PyMuPDF (`fitz`) | PDF text extraction with page numbers |
| DOCX | `python-docx` | Paragraph and table extraction |
| TXT | Python standard library | Plain text extraction |
| Validation | Pydantic | Structured model-output validation |
| AI | Google Gemini behind a provider abstraction | Knowledge reconstruction |
| Reports | ReportLab | PDF handover export |
| Tests | pytest | Critical logic tests |
| Hosting | Streamlit Community Cloud | Public demo |

### Repository structure

```text
coders-pune/
├── app.py                     # Streamlit entry point and screen routing
├── config.py                  # Settings, paths, and LLM configuration
├── requirements.txt
├── .env.example               # Placeholder variable names only
├── .streamlit/config.toml     # Theme and upload limit
├── database/
│   ├── connection.py          # Connection handling, foreign keys, transactions
│   ├── schema.py              # Explicit schema + versioned migrations
│   └── repositories.py        # Parameterised data access
├── trace_app/
│   ├── models.py              # Status enums, display labels, ID/time helpers
│   ├── ui.py                  # Theme, status pills, shared presentation
│   ├── ai/
│   │   ├── provider.py           # Provider abstraction + Gemini client
│   │   ├── prompts.py            # Evidence rules and injection defences
│   │   ├── schemas.py            # Pydantic validation of model output
│   │   └── offline_preview.py    # Rule-based draft when no key is set
│   ├── services/
│   │   ├── project_service.py        # Project validation and retrieval
│   │   ├── document_service.py       # Upload orchestration and deletion
│   │   ├── extraction_service.py     # PDF/DOCX/TXT text + location extraction
│   │   ├── evidence_service.py       # Verifies every citation against sources
│   │   ├── reconstruction_service.py # The reconstruction pipeline
│   │   ├── review_service.py         # Edit, approve, reject, audit trail
│   │   └── report_service.py         # Markdown and PDF handover export
│   └── utils/
│       ├── file_validation.py    # Type, size, magic-byte checks; safe paths
│       └── text_chunking.py      # Deterministic chunking with overlap
└── tests/                     # pytest suite
```

> **Why `trace_app/` and not `trace/`?** A top-level package named `trace` would
> shadow Python's standard-library `trace` module, which was verified to happen
> in this environment. The package is renamed to avoid that; responsibilities
> are otherwise as specified.

## 5. Local setup

```bash
git clone https://github.com/param159357/coders-pune.git
cd coders-pune

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # then edit .env and add your key
```

### How evidence verification works

This is the part that makes TRACE more than a summariser, so it is worth
stating precisely. For every citation the model returns:

1. **The source must be real.** The ID is looked up among *this project's*
   sources only. An invented ID, or one belonging to another project, is
   discarded and never reaches the database.
2. **The quotation must be real.** The excerpt must occur in that source's
   extracted text, compared with whitespace and case normalised. A paraphrase
   does not count.
3. **The location must be real.** The page or paragraph is recomputed from the
   segment the quotation was actually found in. A wrong page number is
   corrected rather than displayed.

The resulting evidence status can only be **lowered** relative to what the
model claimed, never raised:

| Model claimed | What verified | Stored as |
|---|---|---|
| `supported` | nothing | `unsupported` |
| `supported` | some citations | `partially_supported` |
| `supported` | all citations | `supported` |
| `conflicting` | one side only | `needs_review` |
| `needs_review` | all citations | `needs_review` (never upgraded) |

An owner or a due date is dropped entirely unless something verified supports
it. A model that labels everything "supported" therefore gains nothing by it.

## 6. Environment variables

TRACE reads settings from the process environment first, then from Streamlit's
secrets manager — so the same code works locally with `.env` and on Streamlit
Community Cloud with its Secrets UI.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `LLM_PROVIDER` | no | `gemini` | Which provider to call |
| `LLM_API_KEY` | for AI features | — | Provider API key |
| `LLM_MODEL` | no | `gemini-3.6-flash` | Model identifier |
| `LLM_BASE_URL` | no | — | Override the provider endpoint |
| `TRACE_DB_PATH` | no | `data/trace.db` | SQLite file location |
| `TRACE_MAX_UPLOAD_MB` | no | `10` | Per-file upload ceiling |

Google retires models over time — `gemini-2.5-flash` already returns *"no
longer available to new users"*. If you hit that, list what your key can use
and set `LLM_MODEL` accordingly:

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200" \
  -H "x-goog-api-key: $LLM_API_KEY" | grep '"name"'
```

**The app starts and runs without an API key.** It shows a clear "Not
configured" state and simply does not offer AI reconstruction. No output is ever
presented as having come from a live model when it did not.

**Never commit a key.** `.env` and `.streamlit/secrets.toml` are gitignored. If a
key is ever committed by accident, rotate it — deleting the line is not enough,
because the value stays in git history.

### Reliability of the model call

Three things were added after watching the real API misbehave during
development, each for a reason worth stating:

- **Model reasoning is disabled** (`thinking_budget=0`). Gemini 3.x reasons
  before answering by default, and the thinking tokens come out of the same
  output budget. On the CampusConnect demo, thinking enabled returned a
  truncated 2.4 KB reply in 27.8 s; disabled, a complete 12.5 KB reply in
  11.3 s. Reconstruction is structured extraction with exact quoting, not open
  reasoning, so the reasoning was costing output and buying nothing.
- **Bounded retries** (3 attempts, 2 s then 5 s) on availability failures
  only. A rejected key, a retired model, or an unparseable reply are
  permanent and fail immediately.
- **A model fallback chain.** Google's shared capacity returned 502 or 503
  often enough to fail 2 of 4 live runs during development. If the configured
  model stays unavailable, TRACE tries `gemini-3.5-flash`, then
  `gemini-flash-latest`, then `gemini-3.1-flash-lite`. The model that actually
  produced the draft is recorded on the reconstruction and shown in the
  report, so a handover never names a model that did not write it, and the
  substitution is disclosed in the run notes rather than hidden.

## 7. Running the app

```bash
streamlit run app.py
```

Then open <http://localhost:8501>.

### The demo workflow

On the dashboard, press **Load the CampusConnect demo**. This creates a
synthetic project and uploads its four source documents through the ordinary
upload path, so what you see is the real extraction pipeline, not a staged
result. Then:

1. **Project Workspace** — check the extraction status of each source and
   preview its text with the page or paragraph labels TRACE recorded.
2. Press **Run reconstruction**.
3. **Review Handover** — items appear grouped by category. Open *Evidence* on
   any item to see the source, the location, the exact excerpt, and whether
   that excerpt was verified against the source.
4. Edit an item, approve some, reject one. Note that nothing is approved for
   you.
5. **Export** — download the handover as Markdown or PDF. Only approved items
   are included.

Two things worth checking deliberately, because they are the point of the
product:

- No source states a launch date or an owner for the accessibility review.
  TRACE reports their absence instead of inventing them.
- `meeting_notes.txt` records an agreed event capacity of 500 and
  `development_status.txt` records an implemented limit of 300. Both are
  surfaced; TRACE does not pick a winner.

## 8. Running the tests

```bash
python -m pytest tests/ -q          # quiet
python -m pytest tests/ -v          # per-test detail
```

Tests use temporary SQLite files and never touch `data/trace.db`.

## 9. Deployment

### Streamlit Community Cloud

1. Push this repository to GitHub.
2. At <https://share.streamlit.io>, create an app pointing at this repository
   with `app.py` as the entry point.
3. Open **Settings → Secrets** and add:

   ```toml
   LLM_PROVIDER = "gemini"
   LLM_API_KEY  = "your-real-key"
   LLM_MODEL    = "gemini-2.5-flash"
   ```

4. Deploy, then open the public URL in a fresh browser session (or a private
   window) and complete the demo workflow above to confirm a visitor can use
   it without any local setup.

**No secret belongs in the repository.** `.env` and `.streamlit/secrets.toml`
are gitignored. If a key is ever committed, rotate it — removing the line does
not remove it from git history.

### A note on storage

Streamlit Community Cloud does not guarantee a persistent disk. The SQLite
database and uploaded files live in `data/`, which can be lost when the
container restarts. TRACE therefore does **not** promise durable storage. The
demo is loaded on request rather than seeded on startup, so a restart never
silently discards work a visitor created — it just means they start again.

If durable storage matters, deploy somewhere with a real attached volume and
point `TRACE_DB_PATH` and `TRACE_UPLOAD_DIR` at it.

### Before deploying

- Install from `requirements.txt` in a clean environment and confirm the app
  starts.
- Run `python -m pytest tests/ -q`.
- Confirm no developer-only absolute path is required (there are none; all
  paths derive from `config.PROJECT_ROOT`).
- Confirm `sample_data/` is present so the demo button works.
- Confirm no key or private file is exposed by `git ls-files`.

## 10. Implementation status

This table is updated as each milestone is finished and verified. "Verified"
means the tests were actually executed and the app was actually run.

| Milestone | Status |
|---|---|
| 1 — Foundation: schema, project create/list, dashboard | **Done, verified** |
| 2 — Document ingestion: upload, validation, extraction | **Done, verified** |
| 3 — AI reconstruction: provider, schemas, evidence checking | **Done, verified against the live Gemini API** |
| 4 — Review: edit, approve, reject, evidence inspection | **Done, verified** |
| 5 — Export: Markdown and PDF handover report | **Done, verified** |
| 6 — CampusConnect demo and end-to-end test | **Done, verified** |
| 6 — Public deployment | **Not done** — needs a Gemini key and a deploy |

## 11. Current limitations

Stated plainly, because an honest limitation is worth more than an overclaim.

- **Not deployed yet.** There is no public URL. Everything below has been
  verified locally only.

- **Single-user demo.** There is no authentication, no user accounts, and no
  role-based access control. Anyone who can open the app can see every project
  in that deployment.
- **SQLite on ephemeral hosting.** Streamlit Community Cloud does not guarantee
  a persistent disk. Data can be lost when the container restarts. TRACE does
  not promise long-term persistence.
- **No OCR.** An image-only or scanned PDF yields no text, and TRACE reports it
  as `No text found` rather than claiming a successful extraction.
- **Upload ceiling of 10 MB per file**, configurable via `TRACE_MAX_UPLOAD_MB`.
  Only `.pdf`, `.docx`, and `.txt` are accepted; the declared extension is not
  trusted on its own, as the leading bytes are checked too.
- **Deduplication is byte-exact.** Two files with identical content are caught,
  but a lightly edited near-duplicate is not.
- **No external integrations.** Slack, GitHub, Teams, and email ingestion are
  not implemented.
- **Evidence matching is exact, not semantic.** A quotation must occur in the
  source after whitespace and case are normalised. A correctly paraphrased
  claim is therefore marked unsupported rather than accepted — a deliberate
  choice to fail safe.
- Further limitations are added here as later milestones land.

## 12. AI assistance and external services

- Built with **Claude Code** (Claude Opus 5) acting on a written specification.
- Knowledge reconstruction calls the **Google Gemini** API at runtime
  (`gemini-3.6-flash` by default); the key is supplied by the operator and is
  never committed.
- No other external service receives document content.

## 13. Security and privacy notes

- Use synthetic, public, anonymised, or explicitly permissioned files only.
- Do not upload confidential material to a public demo.
- Uploaded documents are not publicly browsable, and project/source
  relationships are validated on retrieval so one project cannot read another's
  sources.
- Uploaded document text is treated as **untrusted data**, never as
  instructions, so a document cannot redirect the model or exfiltrate secrets.
- Secrets, local databases, and uploaded files are excluded from version
  control.

## 14. Planned Round 3 improvements

Planned, **not** implemented in Round 2:

1. **Authorised GitHub integration** — ingest commits, pull requests, and issues
   through an authorised connection, preserving source links and permissions.
2. **Role-aware handover views** — distinct views for incoming developers and
   project managers, enforced by real access checks rather than hidden UI.
3. **Knowledge relationship map** — navigable, evidence-backed relationships
   between decisions, tasks, blockers, dependencies, and owners.

---

*Synthetic demonstration data in this repository describes a fictional project
and no real institution.*
