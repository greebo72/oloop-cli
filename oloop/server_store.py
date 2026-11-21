"""Server-backed Store for oloop (spec/SERVER.md and spec/AGENTS.md).

Drop-in replacement for oloop.store.Store that reads and writes encrypted
documents and blobs on an oloop server instead of local Markdown files.

Works seamlessly for both:
- Agents holding AgentCredentials (AGENTS.md)
- User sessions holding an unlocked keypair
"""
from __future__ import annotations

import getpass
import os
import socket
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .client import ServerApiError, ServerClient
from .credentials import AgentCredentials
from .crypto import (
    CryptoFailure,
    b64url_decode,
    b64url_encode,
    blob_aad,
    doc_aad,
    open_envelope,
    seal_envelope,
    unwrap_project_key,
    wrap_project_key,
)
from .store import (
    FORMAT_VERSION,
    UNSET,
    Comment,
    Conflict,
    NotFound,
    Project,
    StoreError,
    Task,
    ValidationError,
    _clean_labels,
    _COMMENT_FIELDS,
    _PROJECT_FIELDS,
    _project_validator,
    _parse,
    _render,
    _split,
    _TASK_FIELDS,
    _task_validator,
    _validate,
    check_due_date,
    now_utc,
    parse_ts,
    slugify,
    today,
)

SUPPORTED_IMAGE_TYPES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def _detect_image_type(data: bytes) -> str:
    """Validate image bytes match PNG, JPEG, GIF, or WEBP (SERVER.md)."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    for magic, ext in SUPPORTED_IMAGE_TYPES.items():
        if data.startswith(magic):
            return ext
    raise ValidationError("Unsupported image format: must be PNG, JPEG, GIF, or WEBP")


class ServerStore:
    """Store implementation backed by an oloop server."""

    def __init__(
        self,
        client: ServerClient,
        secret_key: bytes,
        public_key: bytes,
        owner_public_key: bytes | None = None,
        actor: str | None = None,
        is_agent: bool = False,
    ) -> None:
        self.client = client
        self.secret_key = secret_key
        self.public_key = public_key
        self.owner_public_key = owner_public_key
        self.is_agent = is_agent
        self.actor = actor or os.environ.get("OLOOP_ACTOR") or getpass.getuser()

        # Cached state
        self._project_keys: dict[str, dict[int, bytes]] = {}  # pid -> {keyVersion -> key}
        self._projects: dict[str, Project] = {}  # pid -> Project
        self._tasks: dict[str, Task] = {}  # tid -> Task
        self._revisions: dict[str, int] = {}  # doc_id -> rev
        self._known_head: str | None = None
        self._loaded = False

    @classmethod
    def from_credentials(
        cls,
        credentials: AgentCredentials,
        client: ServerClient | None = None,
        actor: str | None = None,
    ) -> ServerStore:
        """Create a ServerStore from AgentCredentials."""
        cli = client or ServerClient(credentials.server, token=credentials.token)
        # Actor order: --actor, $OLOOP_ACTOR, name from the credentials, OS user (in __init__)
        actor = actor or os.environ.get("OLOOP_ACTOR") or credentials.name
        return cls(
            client=cli,
            secret_key=credentials.secret_key,
            public_key=credentials.public_key,
            owner_public_key=credentials.owner.public_key,
            actor=actor,
            is_agent=True,
        )

    # ---------------- Synchronisation & Loading ----------------

    def refresh(self) -> None:
        """Load or refresh all projects, keys, and documents from server."""
        self._known_head = self.client.get_head()
        self._load_project_keys()
        self._load_documents()
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()

    def _load_project_keys(self) -> None:
        """Load and unwrap project keys from GET /v1/projects."""
        projects_data = self.client.get_projects()
        keys_by_project: dict[str, dict[int, bytes]] = {}

        for p in projects_data:
            pid = str(p["id"])
            p_uuid = uuid.UUID(pid)
            keys_by_project[pid] = {}

            for entry in p.get("keys", []):
                kv = entry["keyVersion"]
                wrapped_str = entry["wrapped"]
                wrapped_bytes = b64url_decode(wrapped_str)

                # Determine who the sender is
                # For an agent: granted by owner (owner_public_key), or self-granted (own public_key)
                sender_candidates: list[bytes] = []
                if self.owner_public_key:
                    sender_candidates.append(self.owner_public_key)
                sender_candidates.append(self.public_key)

                unwrapped_key: bytes | None = None
                for sender_pk in sender_candidates:
                    try:
                        unwrapped_key = unwrap_project_key(
                            wrapped_bytes,
                            recipient_sk=self.secret_key,
                            sender_pk=sender_pk,
                            expected_project_id=p_uuid,
                            expected_key_version=kv,
                        )
                        break
                    except (CryptoFailure, ValueError):
                        continue

                if unwrapped_key is not None:
                    keys_by_project[pid][kv] = unwrapped_key

        self._project_keys = keys_by_project

    def _load_documents(self) -> None:
        """Load and decrypt all documents from the feed."""
        cursor: str | None = None
        has_more = True
        projects: dict[str, Project] = {}
        raw_tasks: list[tuple[str, int, int, str, bytes]] = []  # (tid, kv, rev, pid, data)

        while has_more:
            page = self.client.get_documents(cursor=cursor, limit=200)
            items = page.get("items", [])
            for item in items:
                doc_id = str(item["id"])
                kind = item["kind"]
                kv = item["keyVersion"]
                rev = item["rev"]
                data_bytes = b64url_decode(item["data"])
                pid = doc_id if kind == "project" else str(item["projectId"])

                project_keys = self._project_keys.get(pid, {})
                proj_key = project_keys.get(kv)
                if not proj_key:
                    # Key for this version is not available
                    continue

                aad_bytes = doc_aad(kind, doc_id, kv, rev)
                try:
                    payload = open_envelope(proj_key, data_bytes, aad_bytes)
                except CryptoFailure:
                    continue

                self._revisions[doc_id] = rev

                if kind == "project":
                    try:
                        data, _ = _parse(payload.decode("utf-8"), doc_id)
                        _validate(_project_validator, data, doc_id)
                        known, extra = _split(data, _PROJECT_FIELDS)
                        slug = slugify(known["name"])
                        projects[doc_id] = Project(slug=slug, extra=extra, **known)
                    except (ValidationError, Exception):
                        continue
                else:
                    raw_tasks.append((doc_id, kv, rev, pid, payload))

            cursor = page.get("nextCursor")
            has_more = page.get("hasMore", False)

        tasks: dict[str, Task] = {}
        for doc_id, kv, rev, pid, payload in raw_tasks:
            project = projects.get(pid)
            if not project:
                continue
            try:
                data, body = _parse(payload.decode("utf-8"), doc_id)
                _validate(_task_validator, data, doc_id)
                known, extra = _split(data, _TASK_FIELDS)
                comments = []
                for c in known.pop("comments", []):
                    c_known, c_extra = _split(c, _COMMENT_FIELDS)
                    comments.append(Comment(extra=c_extra, **c_known))
                if known.get("dueDate") is None:
                    known.pop("dueDate", None)
                task = Task(
                    description=body,
                    comments=comments,
                    projectName=project.name,
                    extra=extra,
                    **known,
                )
                tasks[doc_id] = task
            except (ValidationError, Exception):
                continue

        self._projects = projects
        self._tasks = tasks

    def _get_latest_project_key(self, project_id: str) -> tuple[int, bytes]:
        versions = self._project_keys.get(project_id, {})
        if not versions:
            raise StoreError(f"No encryption key available for project {project_id}")
        latest_kv = max(versions.keys())
        return latest_kv, versions[latest_kv]

    # ---------------- Projects ----------------

    def list_projects(self) -> list[Project]:
        self._ensure_loaded()
        return sorted(self._projects.values(), key=lambda p: p.name.casefold())

    def get_project(self, name_or_id: str) -> Project:
        self._ensure_loaded()
        key = name_or_id.casefold()
        for p in self._projects.values():
            if p.name.casefold() == key or p.id == name_or_id:
                return p
        raise NotFound(f"Project not found: {name_or_id!r}")

    def find_project_by_path(
        self, path: str | os.PathLike[str], host: str | None = None
    ) -> Project:
        """Find project containing path (longest match)."""
        target = Path(path).expanduser().resolve()
        host = host or socket.gethostname()
        best: tuple[int, Project] | None = None
        for p in self.list_projects():
            if not p.referencePath or p.referenceHost not in (None, host):
                continue
            ref = Path(p.referencePath).expanduser().resolve()
            if target == ref or ref in target.parents:
                if best is None or len(ref.parts) > best[0]:
                    best = (len(ref.parts), p)
        if best is None:
            raise NotFound(f"No project for path {str(target)!r} on {host!r}")
        return best[1]

    def add_project(
        self,
        name: str,
        reference_path: str | None = None,
        reference_host: str | None = None,
    ) -> Project:
        if self.is_agent:
            raise StoreError("Agents cannot create projects (AGENTS.md)")

        self._ensure_loaded()
        name = name.strip()
        if any(p.name.casefold() == name.casefold() for p in self._projects.values()):
            raise Conflict(f"Project already exists: {name!r}")

        project_id = str(uuid.uuid4())
        slug = slugify(name)
        project = Project(id=project_id, name=name, createdAt=now_utc(), slug=slug)
        if reference_path is not None:
            project.referencePath = reference_path.strip() or None
        if reference_host is not None:
            project.referenceHost = reference_host.strip() or None
        elif reference_path and not project.referenceHost:
            project.referenceHost = socket.gethostname()

        data = project.frontmatter()
        _validate(_project_validator, data, "project")
        payload = _render(data).encode("utf-8")

        # Generate new 32-byte project key
        new_project_key = os.urandom(32)
        wrapped_key = wrap_project_key(
            new_project_key,
            uuid.UUID(project_id),
            key_version=1,
            recipient_pk=self.public_key,
            sender_sk=self.secret_key,
        )

        nonce = os.urandom(24)
        aad_bytes = doc_aad("project", project_id, 1, 1)
        envelope = seal_envelope(new_project_key, nonce, aad_bytes, payload)

        key_field = {
            "keyVersion": 1,
            "wrapped": b64url_encode(wrapped_key),
        }

        try:
            res = self.client.put_document(
                doc_id=project_id,
                kind="project",
                key_version=1,
                rev=1,
                data=envelope,
                key=key_field,
            )
        except ServerApiError as e:
            if e.is_forbidden:
                raise StoreError("Only a user can create projects on the server") from e
            raise StoreError(f"Server rejected project creation: {e}") from e

        self._project_keys[project_id] = {1: new_project_key}
        self._projects[project_id] = project
        self._revisions[project_id] = res.get("rev", 1)
        return project

    def edit_project(
        self,
        name: str,
        reference_path: str | None = None,
        reference_host: str | None = None,
    ) -> Project:
        self._ensure_loaded()
        project = self.get_project(name)

        if reference_path is not None:
            project.referencePath = reference_path.strip() or None
        if reference_host is not None:
            project.referenceHost = reference_host.strip() or None
        elif reference_path and not project.referenceHost:
            project.referenceHost = socket.gethostname()

        data = project.frontmatter()
        _validate(_project_validator, data, "project")
        payload = _render(data).encode("utf-8")

        kv, proj_key = self._get_latest_project_key(project.id)
        base_rev = self._revisions.get(project.id, 1)
        new_rev = base_rev + 1

        nonce = os.urandom(24)
        aad_bytes = doc_aad("project", project.id, kv, new_rev)
        envelope = seal_envelope(proj_key, nonce, aad_bytes, payload)

        try:
            res = self.client.put_document(
                doc_id=project.id,
                kind="project",
                key_version=kv,
                rev=new_rev,
                data=envelope,
                base_rev=base_rev,
            )
            self._revisions[project.id] = res.get("rev", new_rev)
        except ServerApiError as e:
            if e.is_conflict:
                raise Conflict(f"Project {name!r} was modified concurrently on the server") from e
            raise StoreError(f"Failed to edit project: {e}") from e

        return project

    # ---------------- Tasks ----------------

    def _find_task(self, task_id: str) -> tuple[Project, Task]:
        self._ensure_loaded()
        task = self._tasks.get(task_id)
        if not task:
            raise NotFound(f"Task not found: {task_id}")
        project = self._projects.get(task.projectId)
        if not project:
            raise NotFound(f"Project for task {task_id} not found")
        return project, task

    def resolve_task_id(self, prefix: str) -> str:
        self._ensure_loaded()
        prefix = prefix.strip().lower()
        if len(prefix) < 4:
            raise ValidationError("Task ID prefix: at least 4 characters")
        hits = [tid for tid in self._tasks.keys() if tid.lower().startswith(prefix)]
        if not hits:
            raise NotFound(f"Task not found: {prefix}")
        if len(hits) > 1:
            raise Conflict(f"Task ID prefix ambiguous: {prefix} ({len(hits)} matches)")
        return hits[0]

    def get_task(self, task_id: str) -> Task:
        return self._find_task(task_id)[1]

    def locate_task(self, task_id: str) -> tuple[Project, Task]:
        return self._find_task(task_id)

    def add_task(
        self,
        project_name: str,
        type: str,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
        due_date: str | None = None,
    ) -> Task:
        if due_date is not None:
            check_due_date(due_date)
        project = self.get_project(project_name)
        task_id = str(uuid.uuid4())
        ts = now_utc()

        task = Task(
            id=task_id,
            projectId=project.id,
            type=type,
            title=title.strip(),
            status="open",
            createdAt=ts,
            createdBy=self.actor,
            updatedAt=ts,
            updatedBy=self.actor,
            labels=_clean_labels(labels),
            description=description,
            dueDate=due_date,
            projectName=project.name,
        )

        _validate(_task_validator, task.frontmatter(), f"task {task.id}")
        payload = _render(task.frontmatter(), task.description).encode("utf-8")

        kv, proj_key = self._get_latest_project_key(project.id)
        nonce = os.urandom(24)
        aad_bytes = doc_aad("task", task_id, kv, 1)
        envelope = seal_envelope(proj_key, nonce, aad_bytes, payload)

        try:
            res = self.client.put_document(
                doc_id=task_id,
                kind="task",
                key_version=kv,
                rev=1,
                data=envelope,
                project_id=project.id,
                base_rev=None,
            )
            self._revisions[task_id] = res.get("rev", 1)
        except ServerApiError as e:
            raise StoreError(f"Failed to add task: {e}") from e

        self._tasks[task_id] = task
        return task

    def list_tasks(
        self,
        project_name: str | None = None,
        type: str | None = None,
        status: str | None = None,
        stale_days: float | None = None,
        label: str | list[str] | tuple[str, ...] | None = None,
        now: datetime | None = None,
        due_before: str | None = None,
        overdue: bool = False,
        has_due: bool = False,
        sort: str = "updated",
        on: date | None = None,
    ) -> list[Task]:
        self._ensure_loaded()
        if due_before is not None:
            check_due_date(due_before)
        wanted = {l.casefold() for l in ([label] if isinstance(label, str) else label or [])}
        if sort not in ("updated", "due"):
            raise ValidationError(f"Unknown sort order: {sort!r} (updated, due)")

        target_pid = self.get_project(project_name).id if project_name else None
        cutoff = None
        if stale_days is not None:
            cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=stale_days)

        out: list[Task] = []
        for t in self._tasks.values():
            if target_pid and t.projectId != target_pid:
                continue
            if type and t.type != type:
                continue
            if status and t.status != status:
                continue
            if wanted and not wanted & {l.casefold() for l in t.labels}:
                continue
            if cutoff and parse_ts(t.updatedAt) > cutoff:
                continue
            if has_due and not t.dueDate:
                continue
            if due_before and not (t.dueDate and t.dueDate < due_before):
                continue
            if overdue and not t.is_overdue(on):
                continue
            out.append(t)

        out.sort(key=lambda t: t.updatedAt, reverse=True)
        if sort == "due":
            out.sort(key=lambda t: (t.dueDate is None, t.dueDate or ""))
        return out

    def _update(self, task_id: str, mutate) -> Task:
        project, task = self._find_task(task_id)
        mutate(task)
        task.updatedAt = now_utc()
        task.updatedBy = self.actor

        _validate(_task_validator, task.frontmatter(), f"task {task.id}")
        payload = _render(task.frontmatter(), task.description).encode("utf-8")

        kv, proj_key = self._get_latest_project_key(project.id)
        base_rev = self._revisions.get(task.id, 1)
        new_rev = base_rev + 1

        nonce = os.urandom(24)
        aad_bytes = doc_aad("task", task.id, kv, new_rev)
        envelope = seal_envelope(proj_key, nonce, aad_bytes, payload)

        blob_ref = None
        if task.imagePath and task.imagePath.startswith("blob:"):
            blob_ref = task.imagePath.removeprefix("blob:")

        try:
            res = self.client.put_document(
                doc_id=task.id,
                kind="task",
                key_version=kv,
                rev=new_rev,
                data=envelope,
                project_id=project.id,
                blob_ref=blob_ref,
                base_rev=base_rev,
            )
            self._revisions[task.id] = res.get("rev", new_rev)
        except ServerApiError as e:
            if e.is_conflict:
                raise Conflict(f"Task {task.id} was modified concurrently on the server") from e
            raise StoreError(f"Failed to update task: {e}") from e

        return task

    def edit_task(
        self,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        due_date: str | None | object = UNSET,
    ) -> Task:
        if title is None and description is None and labels is None and due_date is UNSET:
            raise ValidationError("edit without change (title/description/labels/due date)")
        if isinstance(due_date, str):
            check_due_date(due_date)

        def mutate(t: Task) -> None:
            if title is not None:
                t.title = title.strip()
            if description is not None:
                t.description = description
            if labels is not None:
                t.labels = _clean_labels(labels)
            if due_date is not UNSET:
                t.dueDate = due_date

        return self._update(task_id, mutate)

    def close_task(self, task_id: str) -> Task:
        return self._update(task_id, lambda t: setattr(t, "status", "closed"))

    def reopen_task(self, task_id: str) -> Task:
        return self._update(task_id, lambda t: setattr(t, "status", "open"))

    def add_comment(self, task_id: str, body: str) -> Task:
        body = body.strip()
        if not body:
            raise ValidationError("Comment is empty")
        return self._update(
            task_id, lambda t: t.comments.append(Comment(body=body, createdAt=now_utc()))
        )

    # ---------------- Blobs & Task Images ----------------

    def set_image(self, task_id: str, source: str | os.PathLike[str]) -> Task:
        """Upload encrypted image blob and attach it to task (SERVER.md)."""
        src = Path(source)
        if not src.is_file():
            raise NotFound(f"Image file not found: {src}")

        image_bytes = src.read_bytes()
        _detect_image_type(image_bytes)

        project, task = self._find_task(task_id)
        kv, proj_key = self._get_latest_project_key(project.id)

        blob_id = str(uuid.uuid4())
        nonce = os.urandom(24)
        aad_bytes = blob_aad(blob_id, task_id, kv)
        envelope = seal_envelope(proj_key, nonce, aad_bytes, image_bytes)

        try:
            self.client.put_blob(blob_id, task_id, envelope)
        except ServerApiError as e:
            raise StoreError(f"Failed to upload image blob: {e}") from e

        def mutate(t: Task) -> None:
            t.imagePath = f"blob:{blob_id}"

        return self._update(task_id, mutate)

    def get_image_bytes(self, task_id: str) -> bytes:
        """Download and decrypt the image blob of a task."""
        project, task = self._find_task(task_id)
        if not task.imagePath or not task.imagePath.startswith("blob:"):
            raise NotFound(f"Task {task_id} has no server image")

        blob_id = task.imagePath.removeprefix("blob:")
        envelope = self.client.get_blob(blob_id)

        versions = self._project_keys.get(project.id, {})
        # Try keys, newest first (SERVER.md, "Images")
        for kv in sorted(versions.keys(), reverse=True):
            proj_key = versions[kv]
            aad_bytes = blob_aad(blob_id, task_id, kv)
            try:
                return open_envelope(proj_key, envelope, aad_bytes)
            except CryptoFailure:
                continue

        raise StoreError(f"Cannot decrypt image blob {blob_id}: no matching key version")
