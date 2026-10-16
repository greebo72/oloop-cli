# PLAN — oloop (formerly "Lagebericht")

**Revision:** 2026-10-16 — initial, after the SPEC draft
**Revision:** 2026-10-16 — architecture switch to a service (Java/Spring Boot) instead of a Python CLI with direct access. ~~AP3/AP4 (jaydebeapi CLI)~~ replaced by AP3'/AP4'.
**Revision:** 2026-10-16 — scope narrowed to one machine (user request): ~~AP5~~ (conflict-copy check) dropped without replacement, AP3b simplified (`StartupType=Automatic`, no lock file any more).
**Revision:** 2026-10-16 — **architecture consolidation (4th pivot):** file-based persistence instead of H2, no HTTP service any more. ~~AP3'/AP3b/AP4'~~ (Spring Boot service, Windows service lifecycle, service endpoints) dropped without replacement. See SPEC.md "Discarded predecessors". Replaced by AP2''/AP4'' below.

The order is dependency order, not necessarily chronological.

## Work packages

- [ ] **AP1 — Project setup**
  Directory structure (`SPEC/`, `Implementation/`), `git init`, `.gitignore` (venv, `__pycache__`, `*.mv.db`, `*.jar`; the last two are leftovers from the H2 era, harmless, kept in case the history is ever needed). Depends on: nothing. *(Structure/git done)*

- ~~[ ] **AP2 — Schema + DDL**~~ (H2 DDL) — dropped, see AP2'' below.
- ~~[ ] **AP3 — Python CLI skeleton** (jaydebeapi)~~ — dropped.
- ~~[ ] **AP4 — CLI commands** (direct access via jaydebeapi)~~ — dropped, see AP4'' below.
- ~~[ ] **AP3' — Spring Boot service skeleton**~~ — dropped, no service any more.
- ~~[ ] **AP3b — Service lifecycle (Windows service)**~~ — dropped, no resident process any more.
- ~~[ ] **AP4' — Service endpoints**~~ — dropped, no HTTP any more. The endpoint list lives on as CLI subcommands in AP4'' (the functional operations stay the same).
- ~~[ ] **AP4c — CLI/MCP as HTTP client**~~ — dropped, CLI/MCP now call a Python module directly instead of HTTP.

- [x] **AP2'' — File format module**
  Python package (`oloop/store.py` or similar): read/write frontmatter (`python-frontmatter` or a minimal implementation of our own), atomic writing (temp file + `os.replace`), UUID assignment, project/task resolution (name → folder, ID → file), backup copy before writing (SPEC "Backup/recovery"). Validates against `Implementation/oloop/schemas/*.schema.json`. Depends on: AP1, SPEC approved.

- [x] **AP4'' — CLI commands**
  `project add/list/edit/find`, `task add/list/show/edit/close/reopen/image`, `comment add`. Functionally identical to the former HTTP endpoints (SPEC "File format" / the discarded `api-contract.yaml` as reference for the filter parameters: project, type, status, stale days). `--json` on read commands. Directly against AP2'', no network layer. Depends on: AP2''.

- [ ] **AP4d — MCP wrapper** *(optional, can run in parallel or later)*
  Thin layer importing the same Python module as the CLI (AP2''). Depends on: AP2''.

- [ ] **AP6 — Phase 2: desktop app** *(separate spec chapter, own approval)*
  Toolkit decision (SPEC ADR observation: Electron probably too heavy for "small and fast", Tauri/Wails/native GUI as candidates), then implementation in the look of the artifact prototype, reading and writing the same files (directly or via AP2'', depending on the toolkit language). Depends on: AP4'', some real use via the CLI.

## Risk table

| Risk | Impact | Status |
|---|---|---|
| ~~OneDrive syncs `.mv.db` mid-write~~ | ~~DB corruption~~ | **Void** — scope narrowed to one machine |
| ~~Conflict-copy naming pattern unknown~~ | ~~AP5 false negative~~ | **Void** — AP5 dropped |
| ~~`jaydebeapi`/JPype installation is platform dependent~~ | ~~CLI does not start without setup~~ | **Void** — no Java/H2 in the project any more |
| ~~Service installation needs an elevated step (UAC)~~ | ~~No automatable step~~ | **Void** — no service/Windows service any more |
| ~~Single `.mv.db` file grows with image BLOBs~~ | ~~File gets large/slow~~ | **Void** — never BLOBs, files next to files now |
| No lock on simultaneous writes (for example CLI + future desktop app at the same moment) | Last writer wins at task level, the losing write is lost | Reduced by atomic writing (no broken files), the race itself accepted on purpose (SPEC ADR): single-user tool, rarely truly simultaneous |
| Arbitrary file damage (bug, crash mid-write) | Loss of a single task file, not of the whole data set | Reduced by a backup copy before writing (SPEC "Backup/recovery") and by damage being structurally limited to one file |
