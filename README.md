<p align="center">
  <img src="assets/logo.png" alt="oloop logo" width="160" />
</p>

<h1 align="center">oloop-cli</h1>

<p align="center">
  Cross-project task and context tracker for humans and AI agents.
</p>

---

oloop-cli is the command-line client of [oloop](https://github.com/greebo72/oloop-spec). It collects open items across all your projects in one central place: todos, questions, decisions, features, and bugs. It shows what is open where and surfaces items that have been sitting untouched for weeks.

The data lives as Markdown files with YAML frontmatter in a plain folder. You and AI agents (such as Claude Code) interact with it through the CLI. There is no server and no database. The files are not meant to be edited by hand.

## Background & Motivation

Working in IT presales involves juggling dozens of parallel customer inquiries, RFPs, and ongoing projects simultaneously. Modern LLM tools (specifically Claude) offer tremendous leverage when analyzing, drafting, and processing requests. However, what was missing was a unified, cross-project task tracking system that works equally well for autonomous AI agents and humans alike.

Key design principles:
- **Zero Server Setup:** Completely serverless. No server installation, no daemon, no database maintenance.
- **Cross-Platform & Portable:** Consistent behavior across developer workstations and mobile devices.
- **Seamless Cloud Sync:** Data is stored in plain files, making directory synchronization straightforward. Currently used with **Google Drive** automatic synchronization, running smoothly across Claude on macOS, Windows, and Android. Tested and confirmed to work just as reliably with self-hosted **Nextcloud** sync.
- **Dual Audience:** Clean terminal UI and human-friendly workflows, coupled with structured JSON output (`--json`) for AI agents.

## Native Apps & Ecosystem

oloop is built as an ecosystem with native clients across platforms:

- **Android:** Available (Native app)
- **iOS / iPadOS / macOS:** In active development
- **Windows:** In active development
- **CLI (`oloop-cli`):** Available (macOS, Linux, Windows)

See [oloop-spec](https://github.com/greebo72/oloop-spec) for architectural specifications (the spec currently is held as a private project, might change in the future) and schema definitions.

## Installation

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install "git+ssh://git@github.com/greebo72/oloop-cli.git"
export OLOOP_ROOT=~/oloop-data          # Windows: setx OLOOP_ROOT "C:\path\to\oloop-data"
```

The repo is private, so the machine needs GitHub access (SSH key or `gh auth login`). To work on the code, clone it with `git clone --recurse-submodules` and run `uv run --extra dev pytest`.

## Usage

```bash
oloop project add myproject --reference-path ~/dev/myproject
oloop task add -p myproject -t todo "Check backups" -l infra
oloop task list -s open --stale-days 14
oloop task close <id>
oloop project find          # project for the current directory
```

Task types are `todo`, `question`, `decision`, `feature`, and `bug`. Comments, labels, and images attach directly to a task. `list` and `show` support `--json` for machine-readable output. Run `oloop --help` to explore all commands.

## Architecture & Constraints

oloop is designed for a single user across their devices, without heavy database locking. Tasks are closed rather than deleted. 

See [oloop-spec](https://github.com/greebo72/oloop-spec) for specifications and [HANDOVER.md](HANDOVER.md) for current implementation status.
