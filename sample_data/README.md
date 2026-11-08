# Synthetic demonstration data

Everything in this directory is **fictional**. It describes an invented project and no real
institution, team, or person. It exists so the TRACE workflow can be demonstrated and tested
without uploading anyone's real project records.

## CampusConnect

`campusconnect/` holds four source documents for a fictional campus event and
student-services portal:

| File | What it contributes |
|---|---|
| `project_brief.txt` | Purpose, scope, users, constraints |
| `meeting_notes.txt` | Decisions and the reasons given for them |
| `development_status.txt` | Completed and pending work |
| `known_issues.txt` | Blockers, dependencies, risks, open questions |

### What this data is designed to exercise

- **Every category** — the four files together evidence overview, decisions, completed and
  pending work, blockers, dependencies, risks, and open questions.
- **Evidence references** — each fact is stated in exactly one place, so a claim's citation
  can be checked by eye.
- **Honest absence** — no launch date and no accessibility-review owner is stated anywhere.
  A correct reconstruction says so instead of inventing one.
- **A deliberate contradiction** — `meeting_notes.txt` records an agreed event capacity of
  **500 attendees**, while `development_status.txt` records the implemented limit as
  **300 attendees**. A correct reconstruction flags this as conflicting and preserves both
  references rather than silently picking one.

### Using it

Either press **Load the CampusConnect demo** on the TRACE dashboard, or create a project and
upload these four files by hand to exercise the real upload and extraction path.
