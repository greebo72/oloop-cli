"""CLI tests for agent commands and credentials routing."""

import json
import uuid
from pathlib import Path

import httpx
import pytest

from oloop.cli import main
from oloop.credentials import AgentCredentials, AgentOwner
from oloop.crypto import (
    b64url_encode,
    doc_aad,
    generate_keypair,
    seal_envelope,
    wrap_project_key,
)
from test_server_store import FakeServerTransport


@pytest.fixture
def agent_setup(tmp_path, monkeypatch):
    transport = FakeServerTransport()
    agent_pk, agent_sk = generate_keypair()
    owner_pk, owner_sk = generate_keypair()

    owner_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    project_key = b"01234567890123456789012345678901"

    # Setup project on server
    transport.projects[project_id] = {"id": project_id}
    wrapped = wrap_project_key(
        project_key,
        uuid.UUID(project_id),
        key_version=1,
        recipient_pk=agent_pk,
        sender_sk=owner_sk,
    )
    transport.project_keys[project_id] = [
        {"keyVersion": 1, "wrapped": b64url_encode(wrapped)}
    ]

    project_text = (
        f"---\nformatVersion: 1\nid: {project_id}\nname: Server Repo\n"
        "createdAt: 2026-11-20T10:00:00Z\n---\n"
    ).encode("utf-8")
    proj_envelope = seal_envelope(
        project_key,
        b"\x01" * 24,
        doc_aad("project", project_id, 1, 1),
        project_text,
    )
    transport.documents[project_id] = {
        "id": project_id,
        "kind": "project",
        "keyVersion": 1,
        "rev": 1,
        "data": b64url_encode(proj_envelope),
        "writtenBy": owner_id,
        "updatedAt": "2026-11-20T10:00:00Z",
    }

    cred_file = tmp_path / "credentials.json"
    creds = AgentCredentials(
        server="http://localhost:8000",
        agent_id=agent_id,
        token="tok_initial",
        public_key=agent_pk,
        secret_key=agent_sk,
        owner=AgentOwner(id=owner_id, public_key=owner_pk),
        expires_at="2026-12-31T00:00:00Z",
        key_number=1,
    )
    creds.save(cred_file)

    # Monkeypatch ServerClient to use transport
    from oloop.client import ServerClient
    orig_init = ServerClient.__init__

    def patched_init(self, server_url, token=None, timeout=15.0, transport=None):
        orig_init(self, server_url, token=token, timeout=timeout, transport=transport or FakeServerTransport_instance)

    FakeServerTransport_instance = transport
    monkeypatch.setattr(ServerClient, "__init__", patched_init)

    # Extra handlers for agent renew and keypair on the fake transport
    orig_handle = transport.handle_request

    def agent_handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/agent/renew":
            return httpx.Response(
                200, json={"token": "tok_renewed_123", "expiresAt": "2027-03-31T00:00:00Z"}
            )
        if request.method == "PUT" and request.url.path == "/v1/agent/keypair":
            data = json.loads(request.content)
            # Update server's registered project keys with the self-grants
            for k in data["keys"]:
                transport.project_keys[k["projectId"]] = [
                    {"keyVersion": k["keyVersion"], "wrapped": k["wrapped"]}
                ]
            return httpx.Response(
                200, json={"token": "tok_rotated_456", "expiresAt": "2027-04-15T00:00:00Z"}
            )
        return orig_handle(request)

    transport.handle_request = agent_handle

    return {
        "cred_file": cred_file,
        "transport": transport,
        "project_id": project_id,
    }


def test_cli_with_credentials(agent_setup, capsys):
    cred_file = str(agent_setup["cred_file"])

    # List projects via --credentials
    code = main(["--credentials", cred_file, "project", "list", "--json"])
    out, err = capsys.readouterr()
    assert code == 0
    projects = json.loads(out)
    assert len(projects) == 1
    assert projects[0]["name"] == "Server Repo"

    # Add task via --credentials
    code = main([
        "--credentials", cred_file,
        "task", "add",
        "-p", "Server Repo",
        "-t", "todo",
        "Agent created task",
    ])
    out, _ = capsys.readouterr()
    assert code == 0
    assert "Task created:" in out
    tid = out.split(": ")[1].strip()

    # List tasks via --credentials
    code = main(["--credentials", cred_file, "task", "list", "--json"])
    out, _ = capsys.readouterr()
    assert code == 0
    tasks = json.loads(out)
    assert len(tasks) == 1
    assert tasks[0]["id"] == tid
    assert tasks[0]["title"] == "Agent created task"


def test_agent_renew_token(agent_setup, capsys):
    cred_file = str(agent_setup["cred_file"])

    code = main(["--credentials", cred_file, "agent", "renew"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "Token renewed. Valid until: 2027-03-31T00:00:00Z" in out

    # Verify credentials file updated
    updated = AgentCredentials.load(cred_file)
    assert updated.token == "tok_renewed_123"
    assert updated.expires_at == "2027-03-31T00:00:00Z"


def test_agent_renew_keypair(agent_setup, capsys):
    cred_file = str(agent_setup["cred_file"])
    initial_creds = AgentCredentials.load(cred_file)

    code = main(["--credentials", cred_file, "agent", "renew", "--keypair"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "Key pair rotated and token renewed. Valid until: 2027-04-15T00:00:00Z" in out

    # Verify credentials file updated
    updated = AgentCredentials.load(cred_file)
    assert updated.token == "tok_rotated_456"
    assert updated.expires_at == "2027-04-15T00:00:00Z"
    assert updated.key_number == 2
    assert updated.public_key != initial_creds.public_key
    assert updated.secret_key != initial_creds.secret_key


def test_expiring_soon_warning(agent_setup, capsys):
    cred_file = agent_setup["cred_file"]
    creds = AgentCredentials.load(cred_file)
    # Set expiration to 5 days from now
    from datetime import datetime, timedelta, timezone
    exp = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = creds.with_token(creds.token, expires_at=exp)
    updated.save(cred_file)

    code = main(["--credentials", str(cred_file), "project", "list"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "Warning: Agent token expires on" in err
    assert "Run 'oloop agent renew' to extend it" in err


def test_invalid_credentials_file_fails(tmp_path, capsys):
    bad_file = tmp_path / "nonexistent.json"
    code = main(["--credentials", str(bad_file), "task", "list"])
    _, err = capsys.readouterr()
    assert code == 1
    assert "Credentials file unusable" in err
