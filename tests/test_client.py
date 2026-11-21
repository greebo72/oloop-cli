"""Unit tests for oloop.client using httpx.MockTransport."""

import hashlib
import json
import uuid

import httpx
import pytest

from oloop.client import ServerApiError, ServerClient, ServerResponse


def test_server_response_json():
    headers = httpx.Headers({"etag": '"12345"'})
    resp = ServerResponse(200, b'{"foo": "bar"}', headers)
    assert resp.status == 200
    assert resp.etag == '"12345"'
    assert resp.json() == {"foo": "bar"}

    bad_resp = ServerResponse(200, b"not json", headers)
    with pytest.raises(ServerApiError, match="Server response is not valid JSON"):
        bad_resp.json()


def test_error_handling_and_properties():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/conflict":
            return httpx.Response(
                409,
                json={"error": {"code": "conflict", "message": "Document revision conflict"}},
            )
        if request.url.path == "/v1/rate-limited":
            return httpx.Response(
                429,
                headers={"retry-after": "5"},
                json={"error": {"code": "rate_limited", "message": "Too many requests"}},
            )
        if request.url.path == "/v1/forbidden":
            return httpx.Response(403, json={"error": {"code": "forbidden", "message": "Forbidden"}})
        if request.url.path == "/v1/unauthorized":
            return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "Unauthorized"}})
        if request.url.path == "/v1/not-found":
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "Not found"}})
        if request.url.path == "/v1/raw-error":
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", transport=transport)

    # 409 Conflict
    with pytest.raises(ServerApiError) as exc_info:
        client.request("GET", "v1/conflict")
    assert exc_info.value.is_conflict
    assert exc_info.value.code == "conflict"
    assert "Document revision conflict" in str(exc_info.value)

    # 429 Rate limited
    with pytest.raises(ServerApiError) as exc_info:
        client.request("GET", "v1/rate-limited")
    assert exc_info.value.status == 429
    assert exc_info.value.retry_after == 5.0

    # 403 Forbidden
    with pytest.raises(ServerApiError) as exc_info:
        client.request("GET", "v1/forbidden")
    assert exc_info.value.is_forbidden

    # 401 Unauthorized
    with pytest.raises(ServerApiError) as exc_info:
        client.request("GET", "v1/unauthorized")
    assert exc_info.value.is_unauthorized

    # 404 Not found
    with pytest.raises(ServerApiError) as exc_info:
        client.request("GET", "v1/not-found")
    assert exc_info.value.is_not_found

    # 502 Bad Gateway (non-JSON error body)
    with pytest.raises(ServerApiError) as exc_info:
        client.request("GET", "v1/raw-error")
    assert exc_info.value.status == 502
    assert "Server returned HTTP 502" in str(exc_info.value)


def test_bearer_token_and_set_token():
    last_auth = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal last_auth
        last_auth = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", token="initial_token", transport=transport)

    client.request("GET", "v1/test")
    assert last_auth == "Bearer initial_token"

    client.set_token("new_token")
    client.request("GET", "v1/test")
    assert last_auth == "Bearer new_token"

    client.set_token(None)
    client.request("GET", "v1/test")
    assert last_auth is None


def test_agent_endpoints():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer tok_agent"
        if request.url.path == "/v1/agent/renew":
            return httpx.Response(200, json={"token": "tok_renewed", "expiresAt": "2026-12-01T00:00:00Z"})
        if request.url.path == "/v1/agent/keypair":
            data = json.loads(request.content)
            assert data["publicKey"] == "pk_test"
            assert data["statement"] == "stmt_test"
            assert len(data["keys"]) == 1
            return httpx.Response(200, json={"token": "tok_rotated", "expiresAt": "2026-12-01T00:00:00Z"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", token="tok_agent", transport=transport)

    # Renew
    res_renew = client.agent_renew()
    assert res_renew["token"] == "tok_renewed"
    assert client.token == "tok_renewed"

    # Rotate
    client.set_token("tok_agent")
    res_rot = client.agent_rotate_keypair(
        public_key="pk_test",
        statement="stmt_test",
        keys=[{"projectId": str(uuid.uuid4()), "keyVersion": 1, "wrapped": "wrap_test"}],
    )
    assert res_rot["token"] == "tok_rotated"
    assert client.token == "tok_rotated"


def test_user_agent_endpoints():
    agent_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/agents":
            return httpx.Response(201, json={"agentId": agent_id, "token": "tok_123"})
        if request.method == "GET" and request.url.path == "/v1/agents":
            return httpx.Response(200, json=[{"id": agent_id, "name": "claude"}])
        if request.method == "POST" and request.url.path == f"/v1/agents/{agent_id}/revoke":
            return httpx.Response(204)
        if request.method == "POST" and request.url.path == f"/v1/agents/{agent_id}/token":
            return httpx.Response(200, json={"token": "fresh_token"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", token="user_tok", transport=transport)

    reg = client.register_agent("claude", b"pk_32_bytes_12345678901234567890", b"reg_data")
    assert reg["agentId"] == agent_id

    agents = client.list_agents()
    assert len(agents) == 1
    assert agents[0]["name"] == "claude"

    client.revoke_agent(agent_id)
    tok_info = client.issue_agent_token(agent_id)
    assert tok_info["token"] == "fresh_token"


def test_projects_endpoints():
    proj_id = str(uuid.uuid4())
    principal_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/projects":
            return httpx.Response(200, json=[{"id": proj_id, "role": "owner"}])
        if request.method == "PUT" and request.url.path == f"/v1/projects/{proj_id}/members/{principal_id}":
            data = json.loads(request.content)
            assert data["role"] == "write"
            assert data["key"]["keyVersion"] == 1
            return httpx.Response(200)
        if request.method == "POST" and request.url.path == f"/v1/projects/{proj_id}/revoke":
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", token="tok", transport=transport)

    projects = client.get_projects()
    assert projects[0]["id"] == proj_id

    client.add_project_member(proj_id, principal_id, role="write", key_version=1, wrapped_key=b"wrap_key")
    client.revoke_project_member(proj_id, principal_id)


def test_documents_endpoints():
    doc_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/head":
            return httpx.Response(200, json={"cursor": "cur_123"})
        if request.method == "GET" and request.url.path == "/v1/documents":
            assert request.url.params.get("cursor") == "cur_123"
            return httpx.Response(200, json={"items": [{"id": doc_id}], "nextCursor": "cur_124", "hasMore": False})
        if request.method == "GET" and request.url.path == f"/v1/documents/{doc_id}":
            return httpx.Response(200, json={"id": doc_id, "rev": 1})
        if request.method == "PUT" and request.url.path == f"/v1/documents/{doc_id}":
            if request.headers.get("If-None-Match") == "*":
                return httpx.Response(201, json={"rev": 1, "cursor": "c1"})
            if request.headers.get("If-Match") == '"1"':
                return httpx.Response(200, json={"rev": 2, "cursor": "c2"})
            return httpx.Response(412)
        if request.method == "GET" and request.url.path == f"/v1/documents/{doc_id}/revisions":
            return httpx.Response(200, json=[{"rev": 1}])
        if request.method == "GET" and request.url.path == f"/v1/documents/{doc_id}/revisions/1":
            return httpx.Response(200, json={"rev": 1})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", token="tok", transport=transport)

    assert client.get_head() == "cur_123"

    feed = client.get_documents(cursor="cur_123")
    assert feed["nextCursor"] == "cur_124"

    doc = client.get_document(doc_id)
    assert doc["rev"] == 1

    # Put new
    p1 = client.put_document(doc_id, kind="project", key_version=1, rev=1, data=b"data")
    assert p1["rev"] == 1

    # Put update
    p2 = client.put_document(doc_id, kind="project", key_version=1, rev=2, data=b"data", base_rev=1)
    assert p2["rev"] == 2

    revs = client.get_document_revisions(doc_id)
    assert len(revs) == 1
    r1 = client.get_document_revision(doc_id, 1)
    assert r1["rev"] == 1


def test_blobs_endpoints():
    blob_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    content = b"fake-encrypted-envelope-bytes"
    expected_hash = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path == f"/v1/blobs/{blob_id}":
            assert request.headers.get("X-Content-SHA256") == expected_hash
            assert request.headers.get("X-Oloop-Task") == task_id
            assert request.content == content
            return httpx.Response(201)
        if request.method == "GET" and request.url.path == f"/v1/blobs/{blob_id}":
            return httpx.Response(200, content=content)
        if request.method == "HEAD" and request.url.path == f"/v1/blobs/{blob_id}":
            return httpx.Response(200)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", token="tok", transport=transport)

    client.put_blob(blob_id, task_id, content)
    downloaded = client.get_blob(blob_id)
    assert downloaded == content
    assert client.head_blob(blob_id) is True
    assert client.head_blob(str(uuid.uuid4())) is False


def test_auth_and_keys():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/login":
            return httpx.Response(
                200,
                json={"userId": "u1", "accessToken": "tok_access", "refreshToken": "tok_refresh"},
            )
        if request.url.path == "/v1/auth/refresh":
            return httpx.Response(
                200,
                json={"userId": "u1", "accessToken": "tok_access_2", "refreshToken": "tok_refresh_2"},
            )
        if request.url.path == "/v1/auth/logout":
            return httpx.Response(204)
        if request.url.path == "/v1/keys":
            if request.method == "GET":
                return httpx.Response(200, json={"v": 1, "kdf": "argon2id"})
            if request.method == "PUT":
                return httpx.Response(200)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = ServerClient("http://localhost:8000", transport=transport)

    login_res = client.login("alice", "pass", "desktop")
    assert login_res["accessToken"] == "tok_access"
    assert client.token == "tok_access"

    refresh_res = client.refresh("tok_refresh")
    assert refresh_res["accessToken"] == "tok_access_2"
    assert client.token == "tok_access_2"

    keys = client.get_keys()
    assert keys["v"] == 1
    client.put_keys({"v": 1})

    client.logout()
    assert client.token is None
