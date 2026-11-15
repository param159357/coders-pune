# Round 3 form answers — TRACE, Team SAMURAI

Paste-ready text for each field of the submission form. One section per field.

---

## FIELD: Short fields

- **Name of the hackathon:** CodersInPune
- **Team Name:** SAMURAI  *(exactly as in Rounds 1 and 2)*
- **Team Leader — Full Name:** Param Raj Singh
- **Team Leader — Email:** the same address used in Rounds 1 and 2
- **Team Members (one per line):** Param Raj Singh
- **The final working project link:**
  `https://coders-pune-2uv6xj4cwtdbwjh69x72vl.streamlit.app/`
- **The final source-code repository:**
  `https://github.com/param159357/coders-pune`
- **Google Drive link to the final product PDF:** upload
  `TRACE_Round3_Submission.pdf`, set sharing to "Anyone with the link" as
  Viewer, then paste the link

---

## FIELD: The complete list of features

Accounts and access
Email and password sign-in, passwords hashed with scrypt
One-click demo sign-in as each of four roles, credentials shown on the sign-in screen
Per-project membership with four roles: owner, project manager, incoming developer, viewer
Eleven distinct capabilities enforced in the service layer, not by hiding buttons
Role re-read from the database on every call, so revoking access takes effect immediately
Role-aware landing: each member arrives on the screen their role can actually use
Access screen for granting and revoking roles, restricted to the project owner

Adding sources
Upload PDF, DOCX and TXT, up to 25 MB per file
Three validation checks before a byte is stored: extension, size, and leading bytes matching the declared type
Stored filenames generated from the source ID, so a user-supplied name can never build a path
Text extraction with per-segment locations: page numbers for PDF, paragraph indices for DOCX and TXT
Honest extraction statuses: a scanned PDF with no text layer reports "No text found"
Per-file reporting, so one encrypted or corrupt file never aborts a batch
Byte-exact duplicate detection within a project
Reuse of an existing extraction across projects you already belong to, so the same file is never parsed twice
GitHub repository ingestion: commits, pull requests and issues as evidence-bearing sources
Private repositories via a fine-grained token that is used once and never stored
Preview of any source's extracted text with its source locations

Reconstruction
Google Gemini via structured JSON output, validated with Pydantic before anything is stored
Ten handover categories: overview, decisions, tasks in progress, blockers, dependencies, risks, owners, environment, open questions, next actions
Explicit detection of contradictions between sources
Explicit reporting of information the sources never state
Document text treated as untrusted data, so an uploaded file cannot redirect the model
Deterministic keyword fallback when no provider is configured or the provider is unreachable, labelled as not-from-a-model everywhere it appears
Reconstruction caching by input fingerprint: an unchanged re-run makes no AI call and keeps existing review decisions
"Re-run even if unchanged" override
Up to 400,000 characters per request, roughly 150 pages in a single pass
Bounded retries on availability failures only, plus a model fallback chain
The model that actually answered is recorded and named in the report

Evidence verification
Every citation checked against the source it names before it is stored
Invented source IDs, and IDs belonging to another project, are discarded
The quotation must occur in the named source, compared with whitespace and case normalised
The location is recomputed from the segment the quotation was found in, so a wrong page number is corrected
Evidence status can only be lowered relative to what the model claimed, never raised
An owner or a due date is dropped unless something verified supports it
A verification summary showing citations checked, matched, rejected and items downgraded

Review and handover
Every item requires an explicit human decision; nothing is approved by default
Approve, reject, and edit item text, with the decision history retained
Filter by awaiting review, needs a closer look, or approved
Evidence inspection per item: source, location and exact quotation
Permanent GitHub URL shown for any claim drawn from a repository record
Workflow stepper on every project screen showing where the project stands
Export to Markdown and to PDF, approved items only
Optional inclusion of still-unreviewed items, off by default and labelled when on
Report preview before downloading

Knowledge relationship map
Six relation types a source can state: blocks, depends on, resulted in, addresses, owned by, relates to
Every link carries its own quotation and is verified exactly as an item's evidence is
Links with no source support are discarded and never drawn
Counts of how many links were proposed against how many survived verification
Deterministic layout in lanes by category, so the same reconstruction always draws the same picture
Select any item to highlight it, read its evidence, and list every link it takes part in

Operational
Runs with no API key, showing a clear "Not configured" state and offering no AI reconstruction
A placeholder API key is treated as no key, so copying the example env file cannot produce a false "Configured"
Forward-only versioned SQLite migrations, currently at schema version 7
One-click synthetic CampusConnect demo carrying a planted contradiction and two documented absences

---

## FIELD: Technical architecture and implementation summary

**Shape.** A modular monolith: one Streamlit application with strictly separated
modules. No microservices, no queues, no vector database, no separate frontend.
At this size each of those would add failure modes without removing any.

**Components and how they talk to each other.** `app.py` holds Streamlit pages
and routing only, and contains no business logic. Everything it needs it asks of
a service: auth, project, document, extraction, reconstruction, evidence, review,
report, github, demo. Services never talk to each other through the UI — they
take a `Principal` at construction and reach the database through a repository
layer, so a capability check cannot be skipped by calling a different entry
point. `database/` owns the connection, forward-only versioned migrations
(currently schema 7) and the repositories. `trace_app/security/` owns roles,
capabilities and scrypt password hashing. `trace_app/ai/` owns the provider
client, prompts, Pydantic schemas and the deterministic offline preview.
`trace_app/integrations/github.py` is a self-contained client.

**Data flow.** A file is validated (extension, size, magic bytes), stored under a
generated name, and extracted into text plus *located segments* — tuples of
(index, location label, text, start offset, end offset, optional URL) whose
offsets index exactly back into the reassembled text. A GitHub record enters the
same pipeline at the same point, which is why ingesting a repository required no
change to review, verification or export. Segments are chunked to a character
budget and sent to the model, which returns structured JSON. Pydantic validates
the shape; the evidence service then verifies the content; only what survives is
written. Review decisions attach to stored items, and export reads only approved
ones.

**Models and APIs.** Google Gemini (`gemini-3.6-flash` by default) called through
`google-genai` with structured JSON output and `thinking_budget=0`. Measured on
the demo corpus, thinking enabled returned a truncated 2.4 KB reply in 27.8 s;
disabled, a complete 12.5 KB reply in 11.3 s — reconstruction is structured
extraction with exact quoting, not open reasoning. Three retries on availability
failures only, then a fallback chain; a rejected key or retired model fails
immediately. The GitHub REST API is called over urllib with no SDK. Both are
optional: the app runs and is honest about it with neither configured.

**Infrastructure.** Streamlit Community Cloud, deploying from the public GitHub
repository on push. SQLite on the container's disk. The Gemini key lives in
Streamlit's secrets manager and is read through the same accessor as local
environment variables, so one code path serves both. Python 3.11+, seven
dependencies, none added in Round 3 — scrypt, hmac, secrets and urllib are all
standard library, and the relationship map is hand-built SVG.

**The hardest problem we solved: making a model's output checkable rather than
trusted.**

It is easy to ask a model to cite its sources. The hard part is that a citation
is itself model output, so it can be wrong in three independent ways — the source
ID can be invented, the quotation can be a paraphrase the model believes it read,
and the page number can be plausible but false. Checking only the first two still
ships wrong locations to the person who most needs them.

The third is the one that forced the design. To recompute a location you need to
know, for any character position in the text the model was shown, which page or
paragraph of the original it came from — and that text has been extracted,
concatenated, chunked, and reassembled before the model ever sees it. So
extraction does not return a string. It returns located segments carrying start
and end offsets that survive every later transformation, and chunking is built to
preserve them rather than to produce clean prose. Verification then finds the
quotation in the reassembled text, maps its offset back to the segment it
genuinely lies in, and writes *that* location — correcting the model rather than
displaying what it claimed.

The second design decision is that verification is one-directional: a status can
only be lowered against what the model asserted, never raised. A model that
labels everything "supported" gains nothing by doing so, which removes the
incentive the whole product would otherwise be exposed to. The same machinery
runs over relationship links, which is why an unsupported line is never drawn
between two boxes.

---

## FIELD: Testing details

**What was tested, and how**

Four layers, in increasing order of how much they caught.

*Unit and integration tests — 329 automated tests, all passing* (`python -m pytest
tests/ -q`). Every test runs against its own temporary SQLite file, and PDF/DOCX
fixtures are generated at run time rather than committed, so the suite is
reproducible and the repository holds no opaque binaries. Breakdown: robustness
46, extraction 45, reconstruction 43, GitHub 36, evidence validation 26, access
control 26, review 23, export 23, database 21, relationships 17, documents 15,
demo 8. These are mostly integration tests rather than isolated units: they drive
a real service against a real database and assert on what was stored.

*Adversarial testing.* A suite for the product being used wrongly rather than
correctly: 0-byte and oversized uploads, an ELF binary renamed to .txt, a .pdf and
a .docx whose leading bytes contradict the extension, path traversal and NUL bytes
in filenames, the same file uploaded twice, the same email registered twice and in
different case, deleting a source that is already gone, reading a source across a
project boundary, review and progress requested for a reconstruction that does not
exist (including the division that draws the progress bar), and markup in fields
rendered back to a page. Each asserts both that the operation is refused and that
it is refused with something a person can read — a crash and a silent success are
both failures.

*Manual browser testing against a running app*, local and deployed, driven with
Playwright. Main journeys end to end: clone/venv/install/run from a clean
directory following only the README; sign up, load the demo, reconstruct, review,
approve, export; GitHub repository ingestion; the knowledge map; and the same
project opened as each of the four roles.

*Access-control testing as an attacker rather than a user.* Each role attempts
what it must not be able to do, and the test asserts the service call raises —
not that a button is hidden.

**What testing found, and what was fixed**

Most of the real defects came from the browser, not the test suite:

1. *Navigation dropped every other click.* Going A→B worked, B→C silently did
   nothing, C→D worked. On every screen. A changing `index=` argument made
   Streamlit reset the control on the following run and discard the click. The
   widget is now bound to its own session key with an `on_change` callback.
   Clicking a page and staying put is close to the worst failure a judged demo
   can have, and the test suite could never have caught it.
2. *A placeholder API key reported as "Configured".* Following our own README
   (`cp .env.example .env`) left `LLM_API_KEY=your-api-key-here`, and
   `is_configured` was `bool(api_key)` — so the product claimed an AI provider,
   attempted calls that could never succeed, and silently degraded. A placeholder
   is now treated as the absence of a key. One test reads the committed
   `.env.example` so the rule cannot drift from the file.
3. *An access-control leak.* The verification panel printed "24 citations
   checked" to a viewer who is shown none of those items — reporting the volume
   of content they are not allowed to see. Restricted roles now get the assurance
   that every claim shown was checked, without the count.
4. *A dead-end instruction.* The empty review state told every role to "Run a
   reconstruction from the Project Workspace", but a viewer has no Project
   Workspace and would be refused the action. Roles that cannot reconstruct are
   now told who can.
5. *GitHub blamed a token nobody supplied.* An anonymous 403 reported "The token
   does not grant access", directly below a panel saying a public repository
   needs no token. The anonymous case now names both causes worth checking.
6. *A stale module cache took the whole deployed app down.* Streamlit Community
   Cloud redeployed `app.py` while keeping an older copy of an imported module in
   memory; a `from ... import new_symbol` then failed at startup and every page
   became an error page. `app.py` now imports the module and reaches through it,
   so a stale cache costs one panel rather than the product, and a test pins the
   import style.
7. *Truncated model replies were parsed as if complete.* A reply cut off at the
   output ceiling still parsed, so a run reported success with silently fewer
   findings. The ceiling was raised and a cut-off reply is now surfaced.
8. *Documentation that contradicted the product.* The README still said "Round 2",
   "There are no GitHub integrations", "Not deployed yet. There is no public URL",
   and listed all three improvements as "Planned, not implemented". Corrected,
   along with a wrong upload-limit default and a stale model fallback chain.

**What is still imperfect**

- **Refreshing the page signs you out.** Sessions live in Streamlit's session
  state, which a reload clears. No data is lost — the project and its sources are
  intact after signing back in — but the sign-in is not. The fix is a signed
  cookie; we deliberately did not use a URL token, which would make a session
  shareable by copying the address bar and would undermine the access-control
  work that is improvement 2.
- **The truncation fix is not proven to be the cause.** The knowledge map was
  intermittently empty, we raised the output ceiling and added truncation
  detection, and the map now works on the deployed app — but the provider quota
  also reset between the failing and passing observations. Both changed at once,
  so we cannot claim causation.
- **SQLite on ephemeral hosting.** Streamlit Community Cloud does not guarantee a
  persistent disk; data can be lost when the container restarts.
- **The hosted app sleeps** after inactivity on the free tier. It returns in about
  30 seconds via the "get this app back up" button.
- **No OCR.** A scanned PDF yields no text and is reported as "No text found".
- **Evidence matching is exact, not semantic.** A correctly paraphrased claim is
  marked unsupported rather than accepted — a deliberate choice to fail safe.
- **De-duplication is byte-exact**; a lightly edited near-duplicate is not caught.
- **Integrations are GitHub only.**
- **Roughly 150 pages per reconstruction.** Beyond that the honest answer is
  retrieval, which changes what we can claim to have checked.
- **No load or concurrency testing.** Single-container deployment, no horizontal
  scaling and no background job queue; a long reconstruction occupies the request.
- **The shared demo project is shared.** A visitor may find items another visitor
  already decided on. Re-running the reconstruction returns every item to
  "proposed".

---

## FIELD: Improvement 1 — what you committed to, and what you delivered

**Committed at Round 2:** "Authorised GitHub integration — ingest commits, pull
requests, and issues through an authorised connection, preserving source links and
permissions."

**Delivered: yes, in full.**

A project can connect a GitHub repository and ingest its recent commits, pull
requests and issues. Each record becomes an ordinary source, so everything
downstream applies to it unchanged — the same chunking, the same Pydantic schema
validation, the same evidence verification, the same human review, the same
export. A claim about the code is held to exactly the standard a claim about a PDF
is: the quoted text must actually appear in the ingested record, or the citation
is discarded.

On the two specifics we named:

- *Preserving source links.* Every citation drawn from a repository carries the
  permanent GitHub URL of the commit, pull request or issue it quotes, both in the
  interface and in the exported handover report.
- *Preserving permissions.* A public repository needs no token. A private one
  takes a fine-grained personal access token with read access to Contents, Issues
  and Pull requests. **The token is never stored** — it is used for a single fetch
  and discarded, and re-syncing asks again. A test asserts the token appears in no
  database table. Only what a handover needs is collected: identifier, title,
  message or body, author login, date and link. No diffs, no file contents, no
  email addresses or other profile data.

**How to verify it:** open the Project Workspace, expand "Connect a GitHub
repository", enter an `owner/name`, press Ingest repository. 36 automated tests
cover the client, the field selection, the translation of GitHub errors and the
token-never-persisted guarantee. Verified by hand against a real repository: the
ingest stored a repository-link row and 17 commits as a single evidence-bearing
source of 30,679 characters, with a recorded sync note.

---

## FIELD: Improvement 2 — what you committed to, and what you delivered

**Committed at Round 2:** "Role-aware handover views — distinct views for incoming
developers and project managers, enforced by real access checks rather than hidden
UI."

**Delivered: yes, and wider than promised.** We committed to two roles and built
four: owner, project manager, incoming developer and viewer. Each has its own
capability set, and each lands on the screen where its work begins rather than on
a page it cannot use.

| Role | What it can do |
|---|---|
| Owner | Everything, including managing members and deleting the project |
| Project manager | Add sources, run reconstruction, approve and reject. Cannot change access |
| Incoming developer | Read every item and the source documents, and export. Cannot change anything |
| Viewer | Read the approved handover and the evidence quoted in it. Cannot open source documents, and cannot see items still under review |

On the part we explicitly promised — "enforced by real access checks rather than
hidden UI" — the checks run in the service layer, not the interface. A viewer
requesting a source document receives `AccessDenied` from `DocumentService`, not a
hidden button, and `ReviewService.list_items` filters to approved items *before*
returning, so an unapproved item is never handed out at all. A role is re-read
from the database on every call, so revoking access takes effect immediately
rather than when a session expires. 26 access-control tests have each role attempt
what it must not do and assert the call raises.

Two consequences worth naming, both tested: a principal issued for one project
cannot be replayed against another, and a role that cannot see unapproved items is
not told how many exist either — reporting the count would leak it. Testing found
one place that broke that rule (the verification panel printed a citation count to
viewers); it is fixed.

**How to verify it:** the sign-in screen offers one-click sign-in as each of the
four roles on the same project. Sign in as the project manager, then as the
viewer, and compare: the viewer has no Project Workspace in the navigation, no
approve controls, and no counts of items they cannot see.

---

## FIELD: Improvement 3 — what you committed to, and what you delivered

**Committed at Round 2:** "Knowledge relationship map — navigable, evidence-backed
relationships between decisions, tasks, blockers, dependencies, and owners."

**Delivered: yes, in full.**

Handover items are a flat list by category, which hides the thing a reader most
needs: that a specific blocker is what holds up a specific task, which is itself
waiting on an external approval. TRACE now models those links and draws them.

- Six relation types, each something a source can actually state: *blocks*,
  *depends on*, *resulted in*, *addresses*, *owned by*, *relates to* — covering
  the decisions, tasks, blockers, dependencies and owners we named.
- On "evidence-backed": a relationship carries its own quotation and is verified
  exactly as an item's evidence is. **A link with no source support is not
  drawn.** A line between two boxes asserts a causal claim, and an unsupported one
  would be a guess presented as a finding. The map reports how many links were
  proposed and how many were discarded, so the gap is visible rather than hidden.
- On "navigable": selecting any item highlights it, shows its evidence, and lists
  every link it takes part in with the quotation behind each one. Items are laid
  out in lanes by category and the layout is deterministic — the same
  reconstruction always draws the same picture, which matters when comparing runs.

**How to verify it:** open Knowledge Map on a reconstructed project. Verified on
the deployed application against a live `gemini-3.6-flash` call: on the shared
demo as it currently stands, 3 links drawn, 0 discarded as unsupported, 5 items
connected. Those counts are what that reconstruction supported, not a fixed
property of the demo — re-running can find a different number, and the panel
always reports how many were proposed against how many survived verification. When a corpus states no relationships the map says
so rather than inventing connections — that empty state is the feature working,
not failing.

One honest note: the map was intermittently empty earlier in the round. We raised
the model output ceiling and added detection for truncated replies, and it now
works on the deployed app — but the provider quota also reset at the same time, so
we cannot prove which change was responsible.

---

## FIELD: Anything else you improved after Round 2

**Capacity, raised 6.7×.** A single reconstruction request now carries 400,000
characters (~150 pages) instead of 60,000. Measured on a 183,000-character corpus:
one request instead of four, in 12 seconds, with every citation verified. It is
deliberately still well below the model's context ceiling, because larger requests
meet 503s and gateway timeouts on shared capacity.

**Two layers of caching, so nothing is computed twice.** Identical bytes give
identical text, so a file already parsed in one of your projects is not parsed
again in another (scoped to projects you already belong to). And a reconstruction
records a fingerprint of its inputs — every source's content, the model, the
prompt — so re-running with nothing changed keeps the existing draft, makes no AI
call, and preserves review decisions already made. "Re-run even if unchanged"
overrides it.

**One-click demo accounts on the sign-in screen.** Four accounts on one shared
synthetic project, credentials in plain view, a button each. These authenticate
through the ordinary `AuthService.authenticate` path with the published password —
a convenience, not a side door. It removes the sign-up form between a visitor and
the product, and makes improvement 2 something you can watch rather than read
about.

**Reliability of the model call.** Model reasoning disabled (`thinking_budget=0`):
on the demo corpus, thinking enabled returned a truncated 2.4 KB reply in 27.8 s;
disabled, a complete 12.5 KB reply in 11.3 s — reconstruction is structured
extraction with exact quoting, so the reasoning cost output and bought nothing.
Bounded retries (3 attempts, 2 s then 5 s) on availability failures only; a
rejected key or retired model fails immediately. A model fallback chain, with the
model that actually answered recorded on the reconstruction and named in the
report, so a handover never credits a model that did not write it.

**Interface.** A navigation rail and segmented filters replacing raw Streamlit
radio controls, a verification summary showing what evidence checking actually
did, and a workflow stepper on every project screen.

**Defect fixes found by testing** — the navigation bug, the placeholder-key false
claim, the viewer count leak, the dead-end instruction for viewers, the misleading
GitHub token error, and the stale-module-cache outage. All eight are described in
the Testing field.

**Documentation.** A README corrected where it had drifted from the code, verified
by cloning into a clean directory and following only what it says, and a 13-page
submission PDF generated by a script in the repository (`python
docs/build_submission_pdf.py`) so it can be regenerated rather than drifting again.

---

## FIELD: Product roadmap and future development plan

**Who it is for.** Today: a team handing a project to someone new, where the
person who holds the context is leaving. Next: the same team continuously, not
only at the moment of departure. The buyer is an engineering or delivery manager
who has been burned by a handover document nobody could trust.

**Ordered by what the current design is already shaped for.**

*Next — completing the current design*

1. **Session persistence across a reload** (small). A signed, expiring token in an
   HTTP-only cookie. Explicitly not a URL token, which would make a session
   shareable by copying the address bar. This is the most visible rough edge a
   user hits today.
2. **Durable hosting** (small, mostly configuration). Managed Postgres and object
   storage in place of SQLite on an ephemeral disk. Removes the persistence
   caveat and the sleeping-app behaviour in one move. Needed before anyone could
   put real project data in it.
3. **Near-duplicate detection** (medium). Shingling over extracted text to catch
   lightly edited revisions, which byte-exact hashing misses. Matters because real
   project folders are full of `spec_v2_final_FINAL.docx`.
4. **OCR for scanned documents** (medium). The work is not running Tesseract; it
   is that OCR text is lower confidence than embedded text, so it needs its own
   evidence tier rather than being silently mixed in.
5. **Slack and Teams ingestion** (medium), reusing the GitHub connector's shape:
   authorised, metadata-only, every claim quoted back to a permalink. GitHub
   proved the pattern; these are the same work against messier sources.

*Later — changes that alter what TRACE can honestly claim*

6. **Retrieval for corpora beyond ~150 pages** (large). Past the single-request
   envelope the model stops seeing everything, so "we checked all of it" stops
   being true. Retrieval cannot ship alone: it has to arrive with an interface
   that states what was and was not examined, or the central guarantee quietly
   weakens.
7. **Semantic evidence matching** (large). Exact matching marks a correct
   paraphrase unsupported, which fails safe. Any move to embeddings must keep a
   verbatim tier visibly distinct from a similarity tier, or the product becomes
   the thing it was built not to be.
8. **Continuous handover** (large). Re-running against a live repository on a
   schedule and surfacing what changed since the last approved handover, rather
   than regenerating it wholesale. This is the step from "a document you produce
   when someone leaves" to "a thing your team keeps", and it is where the product
   stops being a one-off tool.
9. **Multi-project organisations** (large). Shared membership, audit history
   across projects, and SSO in place of local accounts. Required before any
   organisation would adopt it beyond a single team.

**What it would take.** Items 1–2 are days and unblock real use. Items 3–5 are the
bulk of a second milestone and need no new architecture — the service boundaries
and the evidence pipeline already accommodate them, which is why ingesting GitHub
required no change to review, verification or export. Items 6–9 need design work
before code, because each one trades away part of the guarantee the product is
built on, and that trade has to be visible in the interface rather than buried in
a changelog.

**The through-line.** Every item above is judged against one question: does it
keep TRACE able to say exactly where a claim came from? Anything that blurs that —
inferred links, paraphrase matching, partial corpora — is only worth doing if the
interface stays honest about what changed.
