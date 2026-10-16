# oloop

oloop collects the open items from all your projects in one place: todos, questions, decisions, features and bugs. It shows what is still open where, and what has been sitting untouched for weeks.

The data lives as Markdown files with YAML frontmatter in a plain folder. You and Claude Code work with it through a CLI. There is no server and no database. The files are not meant to be edited by hand.

## Installation

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```
cd Implementation
uv build
uv tool install dist/*.whl
export OLOOP_ROOT=~/oloop-data
```

## Usage

```
oloop project add myproject --reference-path ~/dev/myproject
oloop task add -p myproject -t todo "Check backups" -l infra
oloop task list -s open --stale-days 14
oloop task close <id>
oloop project find          # project for the current directory
```

Task types are `todo`, `question`, `decision`, `feature` and `bug`. Comments, labels and images attach directly to a task. `list` and `show` take `--json` for machine-readable output. `oloop --help` lists the remaining commands.

## Limits

oloop is built for one person on one machine, without locking or sync protection. Tasks are closed, never deleted. A desktop app is planned for later.

See [SPEC/SPEC.md](SPEC/SPEC.md) for details and [HANDOVER.md](HANDOVER.md) for the current state.
