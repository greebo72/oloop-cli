"""HTTP Client for the oloop server backend API.

Follows SERVER.md and AGENTS.md:
- Speaks to /v1 endpoints with Bearer token authentication.
- Implements agent endpoints (/v1/agent/renew, /v1/agent/keypair).
- Implements document feed, head cursor, and document CRUD (/v1/documents, /v1/head).
- Implements project membership & key handling (/v1/projects).
- Implements blob store (/v1/blobs).
- Implements auth & user agent administration (/v1/auth, /v1/agents, /v1/keys).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import httpx

from .credentials import validate_server_url
from .crypto import b64url_encode


class ServerApiError(Exception):
    """Raised when the server answers with an error (4xx, 5xx) or is unreachable."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after

    @property
    def is_conflict(self) -> bool:
        return self.status == 409

    @property
    def is_not_found(self) -> bool:
        return self.status == 404

    @property
    def is_unauthorized(self) -> bool:
        return self.status == 401

    @property
    def is_forbidden(self) -> bool:
        return self.status == 403

    @property
    def is_unreachable(self) -> bool:
        return self.status == 0


class ServerResponse:
    """Wrapper around an HTTP response."""

    def __init__(self, status: int, body: bytes, headers: httpx.Headers) -> None:
        self.status = status
        self.body = body
        self.headers = headers

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ServerApiError(
                self.status,
                "invalid_json",
                f"Server response is not valid JSON: {e}",
            ) from e


class ServerClient:
    """HTTP client for an oloop server."""

    def __init__(
        self,
        server_url: str,
        token: str | None = None,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.server_url = validate_server_url(server_url)
        self._token = token
        self._client = httpx.Client(
            base_url=self.server_url,
            timeout=timeout,
            transport=transport,
        )

    @property
    def token(self) -> str | None:
        return self._token

    def set_token(self, token: str | None) -> None:
        self._token = token

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ServerClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ---------------- Core Request ----------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json_data: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ServerResponse:
        """Send an HTTP request with authentication and error parsing."""
        req_headers = dict(headers or {})
        if self._token and "Authorization" not in req_headers:
            req_headers["Authorization"] = f"Bearer {self._token}"

        clean_path = path.lstrip("/")

        try:
            resp = self._client.request(
                method,
                clean_path,
                json=json_data,
                content=content,
                headers=req_headers,
                params=params,
            )
        except httpx.TimeoutException as e:
            raise ServerApiError(0, "timeout", f"Server did not answer in time: {e}") from e
        except httpx.RequestError as e:
            raise ServerApiError(0, "unreachable", f"Server cannot be reached: {e}") from e

        if resp.status_code >= 400:
            code = ""
            message = ""
            try:
                err_data = resp.json().get("error", {})
                code = err_data.get("code", "")
                message = err_data.get("message", "")
            except Exception:
                pass

            if not message:
                message = f"Server returned HTTP {resp.status_code}"

            retry_after: float | None = None
            if "retry-after" in resp.headers:
                try:
                    retry_after = float(resp.headers["retry-after"])
                except ValueError:
                    pass

            raise ServerApiError(resp.status_code, code, message, retry_after)

        return ServerResponse(resp.status_code, resp.content, resp.headers)

    # ---------------- Agent Self-Management (AGENTS.md) ----------------

    def agent_renew(self) -> dict[str, Any]:
        """Renew current agent token (POST /v1/agent/renew)."""
        res = self.request("POST", "v1/agent/renew")
        data = res.json()
        if "token" in data:
            self.set_token(data["token"])
        return data

    def agent_rotate_keypair(
        self,
        public_key: bytes | str,
        statement: bytes | str,
        keys: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Rotate agent key pair (PUT /v1/agent/keypair)."""
        pk_str = b64url_encode(public_key) if isinstance(public_key, bytes) else public_key
        stmt_str = b64url_encode(statement) if isinstance(statement, bytes) else statement

        keys_payload = []
        for k in keys:
            wrapped = k["wrapped"]
            w_str = b64url_encode(wrapped) if isinstance(wrapped, bytes) else wrapped
            keys_payload.append({
                "projectId": str(k["projectId"]),
                "keyVersion": k["keyVersion"],
                "wrapped": w_str,
            })

        payload = {
            "publicKey": pk_str,
            "statement": stmt_str,
            "keys": keys_payload,
        }
        res = self.request("PUT", "v1/agent/keypair", json_data=payload)
        data = res.json()
        if "token" in data:
            self.set_token(data["token"])
        return data

    # ---------------- Agent Administration (User) ----------------

    def register_agent(
        self,
        name: str,
        public_key: bytes | str,
        registration: bytes | str,
        expires_at: str | None = None,
        renew_until: str | None = None,
    ) -> dict[str, Any]:
        """Register a new agent (POST /v1/agents)."""
        pk_str = b64url_encode(public_key) if isinstance(public_key, bytes) else public_key
        reg_str = b64url_encode(registration) if isinstance(registration, bytes) else registration

        payload: dict[str, Any] = {
            "name": name,
            "publicKey": pk_str,
            "registration": reg_str,
        }
        if expires_at:
            payload["expiresAt"] = expires_at
        if renew_until:
            payload["renewUntil"] = renew_until

        return self.request("POST", "v1/agents", json_data=payload).json()

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents created by the user (GET /v1/agents)."""
        return self.request("GET", "v1/agents").json()

    def revoke_agent(self, agent_id: str | uuid.UUID) -> None:
        """Revoke an agent (POST /v1/agents/{id}/revoke)."""
        self.request("POST", f"v1/agents/{agent_id}/revoke")

    def issue_agent_token(self, agent_id: str | uuid.UUID) -> dict[str, Any]:
        """Issue a fresh token for an agent (POST /v1/agents/{id}/token)."""
        return self.request("POST", f"v1/agents/{agent_id}/token").json()

    # ---------------- Projects (SERVER.md) ----------------

    def get_projects(self) -> list[dict[str, Any]]:
        """List projects and their keys available to the caller (GET /v1/projects)."""
        return self.request("GET", "v1/projects").json()

    def add_project_member(
        self,
        project_id: str | uuid.UUID,
        principal_id: str | uuid.UUID,
        role: str,
        key_version: int,
        wrapped_key: bytes | str,
    ) -> None:
        """Add or update a project member (PUT /v1/projects/{id}/members/{principalId})."""
        w_str = b64url_encode(wrapped_key) if isinstance(wrapped_key, bytes) else wrapped_key
        payload = {
            "role": role,
            "key": {
                "keyVersion": key_version,
                "wrapped": w_str,
            },
        }
        self.request("PUT", f"v1/projects/{project_id}/members/{principal_id}", json_data=payload)

    def revoke_project_member(
        self,
        project_id: str | uuid.UUID,
        principal_id: str | uuid.UUID,
        new_key: dict[str, Any] | None = None,
    ) -> None:
        """Revoke a project member (POST /v1/projects/{id}/revoke)."""
        payload: dict[str, Any] = {"principalId": str(principal_id)}
        if new_key:
            payload["newKey"] = new_key
        self.request("POST", f"v1/projects/{project_id}/revoke", json_data=payload)

    # ---------------- Documents (SERVER.md) ----------------

    def get_head(self) -> str:
        """Get latest change cursor (GET /v1/head)."""
        return self.request("GET", "v1/head").json()["cursor"]

    def get_documents(
        self,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get document feed (GET /v1/documents)."""
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self.request("GET", "v1/documents", params=params).json()

    def get_document(self, doc_id: str | uuid.UUID) -> dict[str, Any]:
        """Get single document (GET /v1/documents/{id})."""
        return self.request("GET", f"v1/documents/{doc_id}").json()

    def put_document(
        self,
        doc_id: str | uuid.UUID,
        kind: str,
        key_version: int,
        rev: int,
        data: bytes | str,
        project_id: str | uuid.UUID | None = None,
        blob_ref: str | uuid.UUID | None = None,
        base_rev: int | None = None,
        key: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update a document (PUT /v1/documents/{id})."""
        headers: dict[str, str] = {}
        if base_rev is None or rev == 1:
            headers["If-None-Match"] = "*"
        else:
            headers["If-Match"] = f'"{base_rev}"'

        data_str = b64url_encode(data) if isinstance(data, bytes) else data

        payload: dict[str, Any] = {
            "kind": kind,
            "keyVersion": key_version,
            "rev": rev,
            "data": data_str,
        }
        if project_id:
            payload["projectId"] = str(project_id)
        if blob_ref:
            payload["blobRef"] = str(blob_ref)
        if key:
            payload["key"] = key

        return self.request(
            "PUT",
            f"v1/documents/{doc_id}",
            json_data=payload,
            headers=headers,
        ).json()

    def get_document_revisions(self, doc_id: str | uuid.UUID) -> list[dict[str, Any]]:
        """Get revision history for a document (GET /v1/documents/{id}/revisions)."""
        return self.request("GET", f"v1/documents/{doc_id}/revisions").json()

    def get_document_revision(self, doc_id: str | uuid.UUID, rev: int) -> dict[str, Any]:
        """Get specific revision for a document (GET /v1/documents/{id}/revisions/{rev})."""
        return self.request("GET", f"v1/documents/{doc_id}/revisions/{rev}").json()

    # ---------------- Blobs (Task Images) ----------------

    def put_blob(
        self,
        blob_id: str | uuid.UUID,
        task_id: str | uuid.UUID,
        envelope_bytes: bytes,
    ) -> None:
        """Upload encrypted image blob (PUT /v1/blobs/{id})."""
        sha256_hex = hashlib.sha256(envelope_bytes).hexdigest()
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Content-SHA256": sha256_hex,
            "X-Oloop-Task": str(task_id),
        }
        self.request("PUT", f"v1/blobs/{blob_id}", content=envelope_bytes, headers=headers)

    def get_blob(self, blob_id: str | uuid.UUID) -> bytes:
        """Download encrypted image blob (GET /v1/blobs/{id})."""
        res = self.request("GET", f"v1/blobs/{blob_id}")
        return res.body

    def head_blob(self, blob_id: str | uuid.UUID) -> bool:
        """Check if blob exists (HEAD /v1/blobs/{id})."""
        try:
            self.request("HEAD", f"v1/blobs/{blob_id}")
            return True
        except ServerApiError as e:
            if e.is_not_found:
                return False
            raise

    # ---------------- Auth & Keys (User Sessions) ----------------

    def login(self, username: str, password: str, device_name: str) -> dict[str, Any]:
        """Sign in with username/password (POST /v1/auth/login)."""
        payload = {
            "username": username,
            "password": password,
            "deviceName": device_name,
        }
        data = self.request("POST", "v1/auth/login", json_data=payload).json()
        if "accessToken" in data:
            self.set_token(data["accessToken"])
        return data

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Rotate refresh token & get new access token (POST /v1/auth/refresh)."""
        payload = {"refreshToken": refresh_token}
        data = self.request("POST", "v1/auth/refresh", json_data=payload).json()
        if "accessToken" in data:
            self.set_token(data["accessToken"])
        return data

    def logout(self) -> None:
        """Revoke device session (POST /v1/auth/logout)."""
        try:
            self.request("POST", "v1/auth/logout")
        finally:
            self.set_token(None)

    def get_keys(self) -> dict[str, Any]:
        """Get user key bundle (GET /v1/keys)."""
        return self.request("GET", "v1/keys").json()

    def put_keys(self, bundle: dict[str, Any]) -> None:
        """Store user key bundle (PUT /v1/keys)."""
        self.request("PUT", "v1/keys", json_data=bundle)
