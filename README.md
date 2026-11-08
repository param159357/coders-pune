# TRACE — Project Knowledge and Handover System

**Team SAMURAI** · Built for the TRACE hackathon (Round 2)

TRACE turns scattered project records into a source-backed, reviewable handover
so the next person can continue work without guessing.

> **Build status:** this repository is under active development. The section
> [Implementation status](#implementation-status) states exactly what is built
> and tested right now. Nothing is listed as working unless it has been run.

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
│   │   └── review_service.py         # Edit, approve, reject, audit trail
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
| `LLM_MODEL` | no | `gemini-2.5-flash` | Model identifier |
| `LLM_BASE_URL` | no | — | Override the provider endpoint |
| `TRACE_DB_PATH` | no | `data/trace.db` | SQLite file location |
| `TRACE_MAX_UPLOAD_MB` | no | `10` | Per-file upload ceiling |

**The app starts and runs without an API key.** It shows a clear "Not
configured" state and simply does not offer AI reconstruction. No output is ever
presented as having come from a live model when it did not.

**Never commit a key.** `.env` and `.streamlit/secrets.toml` are gitignored. If a
key is ever committed by accident, rotate it — deleting the line is not enough,
because the value stays in git history.

## 7. Running the app

```bash
streamlit run app.py
```

Then open <http://localhost:8501>.

## 8. Running the tests

```bash
python -m pytest tests/ -q          # quiet
python -m pytest tests/ -v          # per-test detail
```

Tests use temporary SQLite files and never touch `data/trace.db`.

## 9. Implementation status

This table is updated as each milestone is finished and verified. "Verified"
means the tests were actually executed and the app was actually run.

| Milestone | Status |
|---|---|
| 1 — Foundation: schema, project create/list, dashboard | **Done, verified** |
| 2 — Document ingestion: upload, validation, extraction | **Done, verified** |
| 3 — AI reconstruction: provider, schemas, evidence checking | **Done** (live Gemini call not yet verified — see limitations) |
| 4 — Review: edit, approve, reject, evidence inspection | **Done, verified** |
| 5 — Export: Markdown and PDF handover report | Not started |
| 6 — CampusConnect demo, end-to-end test, deployment | Not started |

## 10. Current limitations

Stated plainly, because an honest limitation is worth more than an overclaim.

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
- **The live Gemini call has not been exercised yet.** The provider, its error
  handling, and the whole pipeline are covered by tests using a fake provider,
  and the offline path runs the identical validation and verification code. The
  real HTTP call to Gemini still needs one run against a real key.
- **Evidence matching is exact, not semantic.** A quotation must occur in the
  source after whitespace and case are normalised. A correctly paraphrased
  claim is therefore marked unsupported rather than accepted — a deliberate
  choice to fail safe.
- Further limitations are added here as later milestones land.

## 11. AI assistance and external services

- Built with **Claude Code** (Claude Opus 5) acting on a written specification.
- Knowledge reconstruction calls the **Google Gemini** API at runtime; the key
  is supplied by the operator and is never committed.
- No other external service receives document content.

## 12. Security and privacy notes

- Use synthetic, public, anonymised, or explicitly permissioned files only.
- Do not upload confidential material to a public demo.
- Uploaded documents are not publicly browsable, and project/source
  relationships are validated on retrieval so one project cannot read another's
  sources.
- Uploaded document text is treated as **untrusted data**, never as
  instructions, so a document cannot redirect the model or exfiltrate secrets.
- Secrets, local databases, and uploaded files are excluded from version
  control.

## 13. Planned Round 3 improvements

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
