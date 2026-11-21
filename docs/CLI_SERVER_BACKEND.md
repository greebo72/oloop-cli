# Server‑Backend CLI How‑To

This short guide shows how to use **oloop‑cli** with the new encrypted server backend (`ServerStore`).

---

## Prerequisites

- `oloop-cli` installed (see the **Installation** section of the main README).
- Access to a running oloop server that supports the encrypted protocol.
- An **Agent Credentials** JSON file (or the `$OLOOP_AGENT_CREDENTIALS` environment variable) that contains:
  ```json
  {
    "server": "https://my‑oloop‑server.example.com",
    "agentId": "my‑agent",
    "token": "<jwt-token>",
    "publicKey": "<base64url‑public>",
    "secretKey": "<base64url‑secret>",
    "owner": "my‑user",
    "expiresAt": "2027-01-01T00:00:00Z",
    "keyNumber": 1
  }
  ```
- Python 3.11+ and the virtual‑env used for development.

---

## Activating Server Mode

Tell the CLI where to find the credentials. You can do this **once per command** with the `--credentials` flag or **globally** via the environment variable.

```bash
# Per‑command
oloop --credentials /path/to/agent_credentials.json task list

# Or set the env var (recommended for interactive use)
export OLOOP_AGENT_CREDENTIALS=/path/to/agent_credentials.json
oloop task list   # now talks to the server automatically
```

When a credentials file is present, `oloop` automatically selects the `ServerStore` backend.

---

## Typical Workflow

Below is a minimal end‑to‑end example that works against the server backend.

```bash
# Create a new project on the server
oloop project add "My Server Project"

# Add a task to that project
oloop task add -p "My Server Project" -t todo "Review API design" -l dev

# List open tasks (server‑side data)
oloop task list -s open

# Show a single task (replace <id> with the real ID)
oloop task show <id>

# Attach an image to a task
oloop task image <id> path/to/screenshot.png
```

All commands read/write to the remote store; no local files are created.

---

## Agent Token Management

### Renew the token

```bash
oloop agent renew
```
The command contacts the server, obtains a fresh JWT and prints the new expiry date. If the token is close to expiry (within 14 days) the CLI already prints a warning.

### Rotate the key‑pair

If you suspect a key compromise or simply want a fresh key pair:

```bash
oloop agent renew --keypair
```
A new public/secret key‑pair is generated, uploaded to the server, and the credentials file is overwritten with the new keys and token.

---

## Credentials File Format

| Field          | Description |
|----------------|-------------|
| `server`       | Base URL of the oloop server |
| `agentId`      | Identifier of the CLI agent |
| `token`        | JWT used for authentication |
| `publicKey`    | Base64‑url encoded public key |
| `secretKey`    | Base64‑url encoded secret key (private) |
| `owner`        | User that owns the agent |
| `expiresAt`    | ISO‑8601 timestamp of token expiry (optional, added by CLI) |
| `keyNumber`    | Incrementing counter for key‑pair rotations |

The CLI reads this JSON via `AgentCredentials.from_path`. It will refuse a file that is missing any required field.

---

## Troubleshooting

- **Invalid credentials file** – Ensure all required keys are present and the JSON is well‑formed.
- **Server connectivity errors** – Verify the `server` URL, network reachability, and that the server’s TLS certificate is trusted.
- **Token expiry warning** – Run `oloop agent renew` before the warning window closes.
- **Concurrent edit conflicts** – The server returns a `409 Conflict`. Re‑run the command; the CLI will fetch the latest state automatically.

---

## Quick Reference

```bash
# Enable server mode (once per session)
export OLOOP_AGENT_CREDENTIALS=~/my‑agent‑creds.json

# Core commands
oloop project add "Demo"
oloop task add -p Demo -t todo "Write docs"
oloop task list
oloop agent renew            # token
oloop agent renew --keypair  # rotate keys
```

Feel free to explore `oloop --help` for the full command list.
