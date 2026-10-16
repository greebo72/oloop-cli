# HANDOVER — oloop (formerly "Lagebericht")

**As of:** 2026-10-16, v0.1.0 tagged and pushed (`greebo72/oloop`, `origin/main`). The SPEC is approved (2026-10-16). AP1, AP2'' and AP4'' are implemented and tested (30 tests). The CLI is installed globally via `uv tool install` and set up for Claude Code (see "Setup"). The project was renamed from "Lagebericht" to "oloop", and all documents are now in English.

## Reading order

1. `SPEC/SPEC.md` — start with **"Discarded predecessors"**: four architecture pivots, summarized briefly. Then purpose, non-goals, the ADR "File-based persistence, no service", and the "File format" section.
2. `SPEC/schemas/{project,task}.schema.json` — authoritative for the frontmatter structure. The package carries a copy so the wheel can validate; run `python sync_schemas.py` in `Implementation/` after changing a schema (a test catches drift). `SPEC/fixtures/` holds shared conformance data.
3. `SPEC/api-contract.yaml` — **obsolete** (HTTP/H2 architecture). Historical evidence only, still in German.
4. `PLAN.md` — AP1, AP2'' and AP4'' done. Open: **AP4d (MCP wrapper, optional, postponed)** and **AP6 (phase 2 desktop clients, own approval per client)**. Struck-through work packages are discarded.
5. `SPEC/app-brief.md` + `SPEC/build_bundle.py` — platform-neutral brief for building a client app (Android, iOS) with an AI app builder. `python SPEC/build_bundle.py` writes one upload file to `SPEC/dist/oloop-app-bundle.md` (not in git); replace the `{{TARGET PLATFORM}}` placeholder before use.
6. `Implementation/` — `oloop/store.py` (store), `oloop/cli.py` (CLI), `tests/`.

## Implementation status

- **Store** (`store.py`): projects, tasks, comments, main image (`set_image`), extra images (`add_attachment`), edit (title/description/labels), close/reopen, filters (`project`, `type`, `status`, `label`, `stale_days`), task ID prefix resolution (`resolve_task_id`, at least 4 characters). Atomic writes, backup `<project>/.backup/<file>.bak` (one generation), validation against the schemas on read and write. No delete.
- **Project fields** beyond the original spec: optional `referencePath` and `referenceHost`. `find_project_by_path` maps a directory to a project (longest matching path wins, host must match).
- **Task fields** beyond the original spec: `createdBy`, `updatedBy` (required; source: parameter, else `$OLOOP_ACTOR`, else OS user) and `labels` (list of unique strings, required, may be empty).
- **CLI:** all output, help and error messages are English. `project add/edit/list/find`, `task add/list/show/edit/close/reopen/image`, `comment add`. `--json` on `project list`, `project find`, `task list`, `task show`. `task image <id> <file>` sets the main image; `--embed` stores an extra image as `images/<task-id>-<n>.<ext>` and prints the Markdown link (`../images/...`, relative to the task file) for the description.
- **Release:** `uv build` in `Implementation/` produces `dist/` (not in git). Checked: the wheel installs in a fresh venv outside the workspace and the CLI runs.

## Setup (this machine)

- Installed via `uv tool install <wheel>`, command at `~/.local/bin/oloop`. After code changes, rebuild and run `uv tool install --force <wheel>`.
- **Data root:** `/Users/user/Google Drive/Meine Ablage/30_User/99_MD_NOTES` (Google Drive folder).
- `~/.zshrc`: `OLOOP_ROOT`. `~/.claude/settings.json` (backups: `*.bak-oloop`, `*.bak-repo`): `env.OLOOP_ROOT`, `env.OLOOP_ACTOR=claude`, `permissions.allow` `Bash(oloop *)`, trusted repo `greebo72/oloop`.
- `~/.claude/CLAUDE.md`: instructions on when and how Claude uses `oloop`, including `oloop project find`.
- Claude Code reads `env` and `CLAUDE.md` only at start. The old `LAGEBERICHT_*` variables still work as a fallback.

## Decisions

- **Rename Lagebericht → oloop** (short for "open loops"; PyPI, Homebrew and GitHub checked and free). Package, command and env variables are `oloop` / `OLOOP_ROOT` / `OLOOP_ACTOR`; the old `LAGEBERICHT_*` act as a fallback. The GitHub repo was renamed from `md_todo` to `oloop`. The local repo folder is still `lagebericht`. The tracker project `lagebericht` in the data root keeps its name (the mapping works through `referencePath`).
- `project find` maps the current directory to a project, so Claude can pick the right project when it creates a todo.
- No MCP wrapper for now: Claude Code calls the CLI directly via Bash (`--json`). MCP only makes sense if Claude Desktop or similar is to be connected without a shell.
- Several images per task via links in the description instead of `images: [..]` in the schema. Whether embedding gets simpler (for example `--embed` appends the link to the description itself) is for the user to decide after using the desktop app.
- Schemas now live in `SPEC/schemas/` (single source, package copy synced by script). `formatVersion` (currently 1) is written to every file; readers refuse newer versions. Conformance fixtures in `SPEC/fixtures/`. This prepares a later split into an `oloop-spec` repo, to be done when the first second client starts (then probably rename this repo to `oloop-cli`).
- A project's folder name is the slug of its name (not the UUID). The `project.id` schema description now says so.
- The backup lives per project (`<project>/.backup/`) because `project.md` has the same name in every project.

## Pitfalls

- **Google Drive as data root:** the SPEC rules out multi-device use (no lock, no conflict-copy detection). Usually fine on a single machine, but the sync can briefly produce old versions or conflict copies when many small writes happen. Do not use it from two machines at the same time.
- **No lock on simultaneous writes** — a deliberate gap (SPEC ADR). Mitigation: atomic writing, not preventing the race.
- **Existing task files without `createdBy`/`updatedBy`/`labels` are invalid** (required fields). There was no real data when this changed. If some exist, a migration is needed or the fields must become optional.
- **`task edit -d` replaces the whole description**, so embedded image links are lost unless they appear in the new text.
- A second main image with a different extension does not overwrite the first; the old file stays behind as an orphan (no delete).
- Predecessor prototype as a cloud artifact: `claude.ai/code/artifact/ad6da2b6-5b14-488b-ae53-9e59e11f8467` ("Lagebericht"). It is the template for the look of the phase 2 desktop app, not a data source.
- Historical: do not call H2 tools from PowerShell with `-password ""` (empty arguments are swallowed). There is no H2 in the project any more.

## Next steps

1. Use `oloop` for a while in real work. AP4d (MCP) is not needed for now.
2. AP6 (desktop clients): decided in SPEC "Desktop clients" (macOS SwiftUI together with iOS, Windows WPF provisional). Order: iOS/iPadOS (`oloop-ios`, brief and tasks exist), then macOS, then Windows. Include a "silent since" view based on `updatedAt`.
3. Code comments and docstrings are still German.
