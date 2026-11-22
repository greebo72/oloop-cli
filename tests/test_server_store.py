"""Unit tests for oloop.server_store backed by an in-memory mock server."""

import json
import uuid
from typing import Any

import httpx
import pytest

from oloop.client import ServerClient
from oloop.credentials import AgentCredentials, AgentOwner
from oloop.crypto import (
    b64url_decode,
    b64url_encode,
    doc_aad,
    generate_keypair,
    open_envelope,
    seal_envelope,
    wrap_project_key,
)
from oloop.server_store import ServerStore
from oloop.store import Conflict, NotFound, StoreError, ValidationError


class FakeServerTransport(httpx.BaseTransport):
    """In-memory mock server for testing ServerStore."""

    def __init__(self) -> None:
        self.cursor = 1
        self.projects: dict[str, dict[str, Any]] = {}
        self.project_keys: dict[str, list[dict[str, Any]]] = {}
        self.documents: dict[str, dict[str, Any]] = {}  # doc_id -> doc_dict
        self.blobs: dict[str, bytes] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path

        if method == "GET" and path == "/v1/head":
            return httpx.Response(200, json={"cursor": f"cur_{self.cursor}"})

        if method == "GET" and path == "/v1/projects":
            res = []
            for pid, pdata in self.projects.items():
                res.append({
                    "id": pid,
                    "role": "write",
                    "keys": self.project_keys.get(pid, []),
                })
            return httpx.Response(200, json=res)

        if method == "GET" and path == "/v1/documents":
            items = list(self.documents.values())
            return httpx.Response(200, json={"items": items, "nextCursor": None, "hasMore": False})

        if method == "PUT" and path.startswith("/v1/documents/"):
            doc_id = path.removeprefix("/v1/documents/")
            payload = json.loads(request.content)
            req_rev = payload["rev"]
            existing = self.documents.get(doc_id)

            if_none_match = request.headers.get("If-None-Match")
            if_match = request.headers.get("If-Match")

            if if_none_match == "*":
                if existing is not None:
                    return httpx.Response(409, json={"error": {"code": "conflict", "message": "Document exists"}})
            elif if_match:
                expected_rev = int(if_match.strip('"'))
                if existing is None or existing["rev"] != expected_rev:
                    return httpx.Response(409, json={"error": {"code": "conflict", "message": "Revision mismatch"}})

            self.cursor += 1
            doc_item = {
                "id": doc_id,
                "kind": payload["kind"],
                "keyVersion": payload["keyVersion"],
                "rev": req_rev,
                "data": payload["data"],
                "writtenBy": "test-principal",
                "updatedAt": "2026-11-21T20:00:00Z",
            }
            if "projectId" in payload and payload["projectId"]:
                doc_item["projectId"] = payload["projectId"]
            if "blobRef" in payload and payload["blobRef"]:
                doc_item["blobRef"] = payload["blobRef"]

            self.documents[doc_id] = doc_item

            if "key" in payload and payload["key"]:
                self.project_keys.setdefault(doc_id, []).append(payload["key"])
                self.projects[doc_id] = {"id": doc_id}

            return httpx.Response(200, json={"rev": req_rev, "cursor": f"cur_{self.cursor}"})

        if method == "PUT" and path.startswith("/v1/blobs/"):
            blob_id = path.removeprefix("/v1/blobs/")
            self.blobs[blob_id] = request.content
            return httpx.Response(201)

        if method == "GET" and path.startswith("/v1/blobs/"):
            blob_id = path.removeprefix("/v1/blobs/")
            if blob_id not in self.blobs:
                return httpx.Response(404, json={"error": {"code": "not_found", "message": "Blob not found"}})
            return httpx.Response(200, content=self.blobs[blob_id])

        return httpx.Response(404, json={"error": {"code": "not_found", "message": "Unknown path"}})


@pytest.fixture
def mock_environment():
    transport = FakeServerTransport()
    agent_pk, agent_sk = generate_keypair()
    owner_pk, owner_sk = generate_keypair()

    owner_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    project_key = b"01234567890123456789012345678901"

    # Setup initial project on server
    transport.projects[project_id] = {"id": project_id}
    wrapped_for_agent = wrap_project_key(
        project_key,
        uuid.UUID(project_id),
        key_version=1,
        recipient_pk=agent_pk,
        sender_sk=owner_sk,
    )
    transport.project_keys[project_id] = [
        {"keyVersion": 1, "wrapped": b64url_encode(wrapped_for_agent)}
    ]

    # Project document on server
    project_text = (
        f"---\nformatVersion: 1\nid: {project_id}\nname: Backend Project\n"
        "createdAt: 2026-11-20T10:00:00Z\n---\n"
    ).encode("utf-8")
    proj_nonce = b"\x01" * 24
    proj_envelope = seal_envelope(
        project_key,
        proj_nonce,
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

    credentials = AgentCredentials(
        server="http://localhost:8000",
        agent_id=agent_id,
        token="tok_agent",
        public_key=agent_pk,
        secret_key=agent_sk,
        owner=AgentOwner(id=owner_id, public_key=owner_pk),
    )

    client = ServerClient("http://localhost:8000", token="tok_agent", transport=transport)
    store = ServerStore.from_credentials(credentials, client=client, actor="claude")

    return {
        "transport": transport,
        "store": store,
        "project_id": project_id,
        "project_key": project_key,
    }


def test_initial_load_and_projects(mock_environment):
    store = mock_environment["store"]
    projects = store.list_projects()
    assert len(projects) == 1
    p = projects[0]
    assert p.name == "Backend Project"
    assert p.id == mock_environment["project_id"]

    # get_project by name or id
    assert store.get_project("Backend Project").id == p.id
    assert store.get_project("backend project").id == p.id
    assert store.get_project(p.id).id == p.id

    with pytest.raises(NotFound):
        store.get_project("Nonexistent")


def test_add_and_get_tasks(mock_environment):
    store = mock_environment["store"]
    task = store.add_task(
        project_name="Backend Project",
        type="todo",
        title="Implement encryption",
        description="Must follow SERVER.md",
        labels=["security", "core"],
        due_date="2026-12-01",
    )

    assert task.title == "Implement encryption"
    assert task.type == "todo"
    assert task.status == "open"
    assert task.dueDate == "2026-12-01"
    assert task.labels == ["security", "core"]
    assert task.createdBy == "claude"
    assert task.projectName == "Backend Project"

    # get_task
    fetched = store.get_task(task.id)
    assert fetched == task

    # resolve_task_id
    full_id = store.resolve_task_id(task.id[:6])
    assert full_id == task.id


def test_list_tasks_filters(mock_environment):
    store = mock_environment["store"]
    t1 = store.add_task(
        "Backend Project", "todo", "Task 1", labels=["backend"], due_date="2026-12-01"
    )
    t2 = store.add_task(
        "Backend Project", "bug", "Task 2", labels=["frontend"], due_date="2026-11-15"
    )

    # Filter by type
    assert [t.id for t in store.list_tasks(type="bug")] == [t2.id]

    # Filter by label
    assert [t.id for t in store.list_tasks(label="backend")] == [t1.id]

    # Filter by due date
    assert len(store.list_tasks(has_due=True)) == 2
    assert [t.id for t in store.list_tasks(due_before="2026-11-20")] == [t2.id]


def test_list_tasks_created_by(mock_environment):
    store = mock_environment["store"]
    store.actor = "claude"
    t1 = store.add_task("Backend Project", "todo", "Task 1")
    store.actor = "Claude"
    t2 = store.add_task("Backend Project", "bug", "Task 2")
    store.actor = "bot"
    t3 = store.add_task("Backend Project", "todo", "Task 3")
    ids = lambda ts: {t.id for t in ts}

    assert ids(store.list_tasks(created_by="claude")) == {t1.id, t2.id}
    assert ids(store.list_tasks(created_by=["bot"])) == {t3.id}
    assert store.list_tasks(created_by="cla") == []
    assert ids(store.list_tasks(created_by=["claude", "bot"])) == {t1.id, t2.id, t3.id}
    assert ids(store.list_tasks(created_by="claude", type="bug")) == {t2.id}


def test_edit_task_close_and_reopen(mock_environment):
    store = mock_environment["store"]
    t = store.add_task("Backend Project", "todo", "Task to edit")

    edited = store.edit_task(t.id, title="Edited title", labels=["new_label"])
    assert edited.title == "Edited title"
    assert edited.labels == ["new_label"]

    closed = store.close_task(t.id)
    assert closed.status == "closed"

    reopened = store.reopen_task(t.id)
    assert reopened.status == "open"

    commented = store.add_comment(t.id, "First comment")
    assert len(commented.comments) == 1
    assert commented.comments[0].body == "First comment"


def test_concurrency_conflict(mock_environment):
    store = mock_environment["store"]
    transport = mock_environment["transport"]

    t = store.add_task("Backend Project", "todo", "Task concurrency")

    # Simulate another device modifying the task on the server (bumps rev)
    transport.documents[t.id]["rev"] = 99

    with pytest.raises(Conflict, match="modified concurrently"):
        store.edit_task(t.id, title="Conflict edit")


def test_agent_cannot_create_project(mock_environment):
    store = mock_environment["store"]
    with pytest.raises(StoreError, match="Agents cannot create projects"):
        store.add_project("New Project")


def test_image_blob_upload_and_download(mock_environment, tmp_path):
    store = mock_environment["store"]
    task = store.add_task("Backend Project", "todo", "Task with image")

    # 1x1 transparent PNG
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    img_file = tmp_path / "test.png"
    img_file.write_bytes(png_bytes)

    updated = store.set_image(task.id, img_file)
    assert updated.imagePath is not None
    assert updated.imagePath.startswith("blob:")

    downloaded_bytes = store.get_image_bytes(task.id)
    assert downloaded_bytes == png_bytes


def test_actor_order_from_credentials(monkeypatch):
    import uuid
    from dataclasses import replace

    from oloop.credentials import AgentCredentials, AgentOwner
    from oloop.crypto import generate_keypair

    pk, sk = generate_keypair()
    owner_pk, _ = generate_keypair()
    creds = AgentCredentials(
        server="https://oloop.example.com",
        agent_id=str(uuid.uuid4()),
        token="agent_tok_1234567890abcdef",
        public_key=pk,
        secret_key=sk,
        owner=AgentOwner(id=str(uuid.uuid4()), public_key=owner_pk),
        name="claude",
    )
    monkeypatch.delenv("OLOOP_ACTOR", raising=False)
    assert ServerStore.from_credentials(creds).actor == "claude"
    monkeypatch.setenv("OLOOP_ACTOR", "bot")
    assert ServerStore.from_credentials(creds).actor == "bot"
    assert ServerStore.from_credentials(creds, actor="x").actor == "x"
    monkeypatch.delenv("OLOOP_ACTOR")
    monkeypatch.setattr("getpass.getuser", lambda: "osuser")
    assert ServerStore.from_credentials(replace(creds, name=None)).actor == "osuser"
