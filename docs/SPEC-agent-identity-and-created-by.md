# SPEC: Agent name as actor, and finding your own tasks

**Status:** implemented (2026-11-22; drafted 2026-11-21). Two small CLI features and one change of the credentials file. The protocol side (the `name` field, the actor order) is written down in `oloop-spec/AGENTS.md`, "The credentials file" and "Who wrote what"; this document says what the CLI does.

## Why

An agent that works through the CLI cannot tell afterwards which tasks it created:

- `oloop task list` has no filter for the creator.
- `createdBy` is free text. In server mode it comes from `--actor`, `$OLOOP_ACTOR` or the operating-system user name (`oloop/server_store.py`), never from the agent. Two agents under one OS user write the same text, and `AGENTS.md` already promises that "an agent's default is its name", which the CLI cannot keep because the credentials file has no name.

## Feature 1: the name in the credentials file, and the actor from it

### File

`AgentCredentials` gets an optional `name`:

```json
{
  "server": "https://oloop.example.com",
  "agentId": "<UUID>",
  "name": "claude",
  "token": "<token>",
  "publicKey": "<base64url, 32 bytes>",
  "secretKey": "<base64url, 32 bytes>",
  "owner": { "id": "<user UUID>", "publicKey": "<base64url, 32 bytes>" }
}
```

- `name` is a string of 1 to 100 characters. It is not trimmed or normalised by the CLI: it is shown and written as stored.
- `name` is **optional on read**, so files written before this field stay valid. An empty string, or a value that is not a string, is `CredentialsError("name must be a string of 1 to 100 characters")`.
- `name` is **written by the client that creates the agent** (the desktop app, see `oloop-desktop/docs/SPEC-agent-management.md`). The CLI itself never makes up a name.
- `name` is a label, not an identity. It is not sent to the server, not compared with anything, and not used to find the agent. `agentId` stays the identity.
- `AgentCredentials` stores it as a field (`name: str | None = None`) and writes it in `to_dict` right after `agentId` when it is set. Today an unknown field is kept in `extra`; a file that carries `name` must not end up with it twice, so `name` joins the set of known fields in `from_dict`.
- `with_token` and `with_keypair` keep the name, so `oloop agent renew` and `oloop agent renew --keypair` do not lose it.

### Actor order

`ServerStore` and `Store` receive the actor as today. The order that decides it, for every command that writes `createdBy` or `updatedBy`:

1. `--actor`
2. `$OLOOP_ACTOR`
3. `name` of the credentials file (only when the CLI runs with credentials)
4. the operating-system user name

In `get_store` (`oloop/cli.py`) the server branch passes `actor=args.actor` and nothing else today. It has to resolve steps 2 and 3 itself, or hand `creds.name` to `ServerStore.from_credentials` as the fallback that comes after `$OLOOP_ACTOR` and before `getpass.getuser()`. The folder backend has no credentials and keeps steps 1, 2 and 4.

### What it does not change

- Existing tasks keep their `createdBy`. Nothing is rewritten.
- `createdBy` stays free text (`SPEC.md`). An agent can still pass `--actor` and write anything. This feature makes the default right; it does not make the value trustworthy. `writtenBy` is the only evidence (`AGENTS.md`).

### Acceptance

1. A file with `name: "claude"` and no `--actor`, no `$OLOOP_ACTOR`: a new task has `createdBy: claude`, and a comment or edit sets `updatedBy: claude`.
2. With `$OLOOP_ACTOR=bot`, the same command writes `bot`. With `--actor x` it writes `x`.
3. A file without `name`, no `--actor`, no `$OLOOP_ACTOR`: the OS user name, as today.
4. `oloop agent renew` and `oloop agent renew --keypair` write a file that still has the same `name`.
5. `name` of 101 characters, `""`, and `5` are refused with the error above. A missing `name` is accepted.
6. A file with `name` loads without a duplicate key: `to_dict()` has `name` once.

## Feature 2: `--created-by` and `--mine` for `task list`

### Behaviour

```
oloop task list --created-by TEXT
oloop task list --mine
```

- `--created-by TEXT`: only tasks whose `createdBy` equals `TEXT`, **ignoring case** (as `--label` does). Exact match, not a substring. May be given more than once; a task matches if it matches at least one (like `--label`).
- `--mine`: shorthand for `--created-by <the actor>`, where the actor is the one the CLI resolved by the order above. It does not prompt and does not read any other source. When it is given together with `--created-by`, both values count.
- Both combine with every other filter (`--project`, `--type`, `--status`, `--stale-days`, `--label`, the due filters) with *and*. The sort option is unchanged.
- Both are options of `task list` only. Other commands that list (`project list`, exports) are not touched in this round.
- The output stays as it is, with and without `--json`. `createdBy` is already part of the JSON.

An empty result is an empty list, exit code 0, as for any other filter.

### Where the filter runs

In `Store.list_tasks` and `ServerStore.list_tasks`, next to the label filter, so both backends behave the same and a later GUI can reuse it. The signature gets one optional keyword, `created_by: list[str] | None = None`; `cmd_task_list` builds it from the two options. A `--mine` with an empty actor is not possible, because the last step of the order always yields a name.

### What it is not

- It is **not proof** that this agent wrote the task. A task counts as "mine" when its free-text `createdBy` matches. It is wrong when another member wrote the same text, or when the agent used a different `--actor` for some tasks. The help text of both options says so in one line: "createdBy is free text, not proof."
- It does not look at `writtenBy`. A filter on `writtenBy` would be evidence, but it names the last writer and not the creator, and the server may drop the first revision (`SERVER.md`, "History"). See "Open points".
- An agent only sees the projects that were granted to it. `--mine` finds tasks there and nowhere else.

### Acceptance

1. Three tasks with `createdBy` `claude`, `Claude` and `bot`: `--created-by claude` returns the first two, `--created-by bot` the third, `--created-by cla` returns none.
2. `--created-by claude --created-by bot` returns all three.
3. With a credentials file with `name: "claude"`: `--mine` equals `--created-by claude`. With `$OLOOP_ACTOR=bot` set: `--mine` equals `--created-by bot`. With `--actor x`: `--mine` equals `--created-by x`.
4. `--mine` and `-s open -p P` together return only the open tasks of project `P` that match.
5. The folder backend and the server backend return the same result for the same data.
6. `--json` output of a filtered list is a JSON array of the same task objects as today.
7. `--help` shows both options and the "free text, not proof" note.

## Tests

In `tests/`, in the style of `test_cli_agent.py`:

- `AgentCredentials`: `name` read, written, kept through `with_token` and `with_keypair`, refused when it is not 1 to 100 characters, accepted when missing, not duplicated in `to_dict` (feature 1, points 4 to 6).
- The actor order with a fake store: the four sources, each winning once (feature 1, points 1 to 3).
- `list_tasks(created_by=...)` on both stores: case, exact match, several values, combination with the other filters (feature 2, points 1, 2, 4, 5).
- `--mine` against `--created-by` with the three actor sources (feature 2, point 3).

## Open points

- **A filter on evidence.** `--written-by-me` could filter on `writtenBy` of the current revision and mean "last changed by me". It needs the server to hand `writtenBy` to the client in a list call and a decision on whether the CLI may show a principal's id or name. Left out on purpose.
- **A creator the server can vouch for.** It would need the server to keep the first revision's `writtenBy` for the life of the document, which `SERVER.md` does not promise. A change of the server spec, not of the CLI.
- **`oloop agent whoami`.** It would print name, id, owner and token expiry from the file and is now easy because the name is there. Not part of this spec.
