# SPEC — oloop, formerly "Lagebericht" (local cross-project tracker)

**Status:** Draft (2026-10-16), not yet approved

**Revision:** 2026-10-16 — initial draft, after technical grounding (JVM/H2/OneDrive measured) and a user question (multi-device).
**Revision:** 2026-10-16 — architecture switch (user request): central local service instead of direct DB access from every client. Service language decided: Java/Spring Boot.
**Revision:** 2026-10-16 — full API contract added (`SPEC/api-contract.yaml`, OpenAPI 3.0.3).
**Revision:** 2026-10-16 — completeness audit: DB path, credentials and time format fixed, backup/recovery (copy on start) added, purpose and non-goals aligned with the service architecture.
**Revision:** 2026-10-16 — delete/edit decided (user): no delete anywhere (append-only, "mark as finished"), `PATCH /tasks/{id}` equivalent added for title/description.
**Revision:** 2026-10-16 — scope narrowed (user request): the server runs on one machine only. OneDrive sync ADR, lock file and conflict-copy check dropped without replacement. `StartupType` set to `Automatic`.
**Revision:** 2026-10-16 — **Architecture consolidation (4th pivot).** Storage moved from H2 to Markdown files with YAML frontmatter. The Java/Spring Boot service is dropped entirely: its only compelling reason (H2 speaks JDBC natively, no driver bridge risk) disappears with the storage change, since text files need no driver in any language. Phase 2 confirmed as a **desktop app** instead of a browser UI, so it needs no HTTP service either. The project is now pure Python with no JVM dependency. See "Discarded predecessors" below for a summary of all four pivots. `SPEC/api-contract.yaml` stays as historical evidence (its header marks it obsolete), replaced by `Implementation/oloop/schemas/*.schema.json`.
**Revision:** 2026-10-16 — task fields `createdBy`, `updatedBy`, `labels` added (user request).
**Revision:** 2026-10-16 — project fields `referencePath`, `referenceHost` added (optional), plus `project find`. Project renamed from "Lagebericht" to "oloop"; all documents translated to English.

## Discarded predecessors

Short version for readers starting cold: four real architecture pivots in this spec's history, each a simplification of the previous state.

1. **Python CLI with direct H2 access** (`jaydebeapi`/JPype bridge). Discarded: no native Python H2 driver exists, and every further client (MCP, UI) would have needed the same bridge.
2. **Java/Spring Boot service as the only JDBC instance**, with CLI/MCP/UI as HTTP clients. Solved problem 1 by leaving a single process that had to speak JDBC. Discarded with pivot 4: the driver problem that justified the service does not exist for text files.
3. **OneDrive sync protection + cross-device locking** (sync ADR, conflict-copy check, lock file, `StartupType=Manual`). Discarded: the server runs on one machine only (user decision), so the whole problem class does not exist.
4. **The HTTP service as such** (not just its Java implementation). Discarded: phase 2 becomes a desktop app (no browser backend needed), and text files have no central driver problem that would still justify a service. CLI, MCP wrapper and the later desktop app now read and write directly through a shared, documented file format.

The full reasoning behind the discarded decisions (arguments, alternatives weighed) lives in the revision lines above and in the session history this spec came from. Only the consequence is kept here, so the document stays readable.

## Purpose

The user works on many projects in parallel (different directories, some inactive for weeks). Open todos, unanswered questions, decisions made and key features get lost between sessions and projects, and there is no central overview of what is open where. oloop is the local tool for that: Markdown files with YAML frontmatter as storage (managed by the tool, never edited by hand), a Python module used directly by the CLI and the MCP wrapper (no network layer, no driver), and later a desktop app in the look of the Claude artifact shown earlier, reading and writing the same files.

Predecessor: Claude artifact prototype (`claude.ai/code/artifact/ad6da2b6-...`, 2026-10-16). It showed the data model and look as a trial and the user confirmed it ("looks very good already"). This project replaces the artifact's cloud DB with local files.

## Non-goals (this spec version)

- **Multi-user/auth** — single-user tool, no login, no role model.
- **Multiple devices** — the tool runs on one machine only (user decision). No sync protection, no cross-device locking, no conflict-copy detection.
- **UI** — phase 2, own chapter and revision. This spec covers file format, Python module and CLI. Phase 2 is confirmed as a desktop app (not browser); the toolkit choice stays open.
- **Network/service layer** — deliberately none. CLI, MCP wrapper and desktop app access the files directly (or a shared Python module if the desktop app is Python-based, otherwise re-implemented against the documented file format).
- **Direct editing of the files by the user** — explicit requirement. The Markdown files are an implementation detail of the tool, not a user interface.

## Measurements (2026-10-16, this machine)

- Python 3.13.3 available. Still relevant, the project is now pure Python.
- ~~JVM available: OpenJDK Corretto 11.0.32.1.~~ ~~H2 jar available locally (`~/.m2/repository/com/h2database/h2/2.2.224/h2-2.2.224.jar`).~~ ~~Account `SAMPLE\user` is a member of the local administrators group, so Windows service installation is possible.~~ **No longer relevant** — pivot 4 (no Java service, no JVM dependency in the project).
- ~~H2 embedded (MVStore) creates exactly one file and no extra or lock file when closed (verified by a test run in a OneDrive folder).~~ **No longer relevant** — no H2 any more, see pivots 1 and 4.

## Backup/recovery

Many small files instead of one binary file are more robust by nature: one damaged task does not kill the whole data set, unlike a corrupt `.mv.db`. **In scope for V1:** before every write, the Python module copies the affected file to `.backup/<filename>.bak` (one generation, no rotation scheme). This is cheap protection against the one remaining failure case (a bug in the write path, a crash mid-write) that atomic rename writes leave open in theory. More (versioning, several generations) is not part of this approval.

## ADR — file-based persistence, no service

**Context:** The user asked what switching from H2 to text files would mean (the UI should stay the same, files never edited directly), then whether a separate HTTP service still makes sense given that.

**Decision:** No service. Markdown + YAML frontmatter files, managed by a shared Python module that the CLI and MCP wrapper import directly.

**Why the service goes away, not just its Java implementation:** The dominant reason for the service was to concentrate the JDBC driver problem in one instance (see predecessor 2 above). Text files have no such problem in any language. The second reason (a browser cannot speak JDBC) would only have mattered if phase 2 became a browser UI. The user confirmed phase 2 as a desktop app instead ("small and fast"), which can read local files directly, so no backend is needed. The third reason (bundling access code) is much weaker for file I/O (parsing and writing frontmatter) than for connection pooling and JDBC. There is little worth hiding behind an interface.

**What this gives up, accepted on purpose:** Without a central service there is no built-in serialization of writes. Mitigation: atomic writing (temp file in the same directory + `os.replace`) prevents broken or half-written files. A real race between two *simultaneous* writes to the same task (for example the CLI and a future desktop app at the same moment) remains a residual risk: last writer wins, no lock. Accepted for a single-user tool that is rarely used truly concurrently, and documented here rather than left open silently.

**Observation for phase 2, not a decision now:** Electron contradicts the stated "small and fast" (it bundles Chromium, high memory use). Lighter alternatives that could reuse the HTML/CSS/JS look of the artifact prototype: Tauri (Rust) or Wails (Go) with the system WebView instead of bundled Chromium; alternatively a native GUI (for example .NET/WPF, since the target is Windows only). Own spec chapter, own approval, only noted here.

## File format

```
<data-root>/                       -- path configurable (CLI config/ENV, no hardcoding)
  <project-slug>/
    project.md                     -- frontmatter: id (UUID), name, createdAt,
                                       optional referencePath, referenceHost
    tasks/
      <task-id>.md                 -- file name = UUID, stable over the task's lifetime
                                       (changing the title does NOT rename the file, so
                                       references and comments stay stable)
    images/
      <task-id>.<ext>              -- optional image per task, next to the task file
```

Frontmatter per task (YAML header, `python-frontmatter` compatible):

```yaml
id: <uuid>
projectId: <uuid>
type: todo|question|decision|feature|bug
title: "..."
status: open|closed
imagePath: null | "images/<task-id>.<ext>"
createdAt: 2026-10-16T12:00:00Z
createdBy: user
updatedAt: 2026-10-16T12:00:00Z
updatedBy: user
labels: [backend, urgent]
comments:
  - body: "..."
    createdAt: 2026-10-16T12:05:00Z
```

Markdown body = `description`. **Authoritative for the exact structure:** `Implementation/oloop/schemas/project.schema.json` and `Implementation/oloop/schemas/task.schema.json` (JSON Schema, taking over the role `api-contract.yaml` had before; the schema files win on conflict).

**Relative `image_path`, file instead of BLOB:** unchanged from the predecessors, and now the natural solution for file-based persistence anyway.

**`createdBy`/`updatedBy`/`labels`:** the actor is a free-text string (source: parameter, else `$OLOOP_ACTOR`, else OS user name). `updatedBy` is set together with `updatedAt` on every change, `createdBy` never. `labels` is a list of unique strings, replaceable via edit.

**`referencePath`/`referenceHost` (project, optional):** working directory (for example a repo) a project belongs to, and the machine name it applies to. `oloop project find` maps a directory to a project (longest matching path wins, host must match). If a path is given without a host, the current machine name is stored.

**One `status` field, no enum per type:** the label in the presentation layer varies per type (todo → "Done", question → "Answered", decision → "Decided", feature → "Implemented", bug → "Fixed"). The data model stays `open`/`closed`.

**`updatedAt` is a required field, not an extra:** the tool's central purpose is to see what happened last after one or two quiet weeks. CLI (and later UI) filter and sort by it.

**No delete, anywhere.** User decision: "Mark as finished instead of delete is the way." Task, comment and project are append-only. `close`/`reopen` (a status flip in the frontmatter) is the only completion mechanism, and no file ever disappears.

**Edit allowed, but limited.** `title`/`description` of a task can be corrected afterwards (overwrites the frontmatter field or the Markdown body, `updatedAt` is updated, the file name stays stable). Project name and task type are NOT editable in V1.

**ID scheme:** UUID4 per entity (no auto-increment without a DB). File name = ID.

**Timestamps:** UTC, ISO 8601 with `Z`. Localization happens on the display side (CLI/UI), not in the file format. Relevant for the stale-days filter in the CLI.

## Phase 2

Desktop app (user decision, "small and fast"), look and structure like the artifact shown (sidebar project filter, type tabs/stat tiles, cards with status toggle), plus "silent since" as a new view (follows from `updatedAt`). Toolkit TBD, see the ADR observation above (Electron probably too heavy for "small and fast", Tauri/Wails/native GUI as candidates). Own spec chapter, own approval, only after approval and use of this version.

## Open points

No known approval blockers. Remaining, to be clarified before the respective work packages:

- **Concrete data root path.** The spec only fixes that it is configurable (CLI config/ENV, no hardcoding); the actual value comes from the user before the first run.
- CLI framework (Python, `click` vs. `argparse`), an implementation detail.
- MCP wrapper: imports the same Python module and can be built in parallel to the CLI or later.
- Phase 2 toolkit (see above), own chapter, not part of this approval.
- Git remote for this project (local only vs. backup remote). Not asked; `git init` is enough for the commit references required by CLAUDE.md, a remote can follow later.
