# oloop-cli

[oloop](https://github.com/greebo72/oloop-spec) collects the open items from all your projects in one place: todos, questions, decisions, features and bugs. It shows what is still open where, and what has been sitting untouched for weeks.

The data lives as Markdown files with YAML frontmatter in a plain folder. You and Claude Code work with it through a CLI. There is no server and no database. The files are not meant to be edited by hand.

## Installation

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```
uv tool install "git+ssh://git@github.com/greebo72/oloop-cli.git"
export OLOOP_ROOT=~/oloop-data
```

The repo is private, so the machine needs GitHub access (SSH key or `gh auth login`). To work on the code, clone it with `git clone --recurse-submodules` and run `uv run --extra dev pytest`.

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

See [oloop-spec](https://github.com/greebo72/oloop-spec) for the specification and [HANDOVER.md](HANDOVER.md) for the current state.
