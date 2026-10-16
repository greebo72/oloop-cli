"""Dateibasierter Store: Markdown + YAML-Frontmatter (SPEC „Dateiformat").

Layout:
    <root>/<project-slug>/project.md
    <root>/<project-slug>/tasks/<task-id>.md
    <root>/<project-slug>/images/<task-id>.<ext>
    <root>/<project-slug>/.backup/<datei>.bak   (eine Generation)

Kein Delete, kein Lock (SPEC-ADR): Schreiben ist atomar (Temp-Datei + os.replace),
gleichzeitige Schreiber sind last-writer-wins.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import socket
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ENV_ROOT = "LAGEBERICHT_ROOT"
ENV_ACTOR = "LAGEBERICHT_ACTOR"
TASK_TYPES = ("todo", "question", "decision", "feature", "bug")
STATUSES = ("open", "closed")

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class StoreError(Exception):
    pass


class NotFound(StoreError):
    pass


class Conflict(StoreError):
    pass


class ValidationError(StoreError):
    pass


class _StringTimestampLoader(yaml.SafeLoader):
    """Timestamps bleiben Strings (ISO-8601 mit Z), kein datetime-Roundtrip."""


_StringTimestampLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


_project_validator = _validator("project.schema.json")
_task_validator = _validator("task.schema.json")


def _validate(validator: Draft202012Validator, data: dict, where: str) -> None:
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        msg = "; ".join(
            f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors
        )
        raise ValidationError(f"{where}: {msg}")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.casefold())
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        raise ValidationError(f"Projektname ergibt keinen gültigen Slug: {name!r}")
    return s


def _clean_labels(labels: list[str] | None) -> list[str]:
    """Trimmen, Leere verwerfen, Duplikate entfernen (Reihenfolge bleibt)."""
    return list(dict.fromkeys(l.strip() for l in labels or [] if l.strip()))


def _parse(text: str, where: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValidationError(f"{where}: kein Frontmatter")
    data = yaml.load(m.group(1), Loader=_StringTimestampLoader)
    if not isinstance(data, dict):
        raise ValidationError(f"{where}: Frontmatter ist kein Mapping")
    return data, m.group(2).strip("\n")


def _render(data: dict, body: str = "") -> str:
    head = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{head}---\n{body.strip(chr(10))}\n" if body.strip() else f"---\n{head}---\n"


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_with_backup(path: Path, text: str, backup_dir: Path) -> None:
    if path.exists():
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(path, backup_dir / f"{path.name}.bak")
    _atomic_write(path, text)


@dataclass
class Comment:
    body: str
    createdAt: str


@dataclass
class Project:
    id: str
    name: str
    createdAt: str
    slug: str = ""
    referencePath: str | None = None
    referenceHost: str | None = None

    def frontmatter(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "name": self.name, "createdAt": self.createdAt}
        if self.referencePath:
            d["referencePath"] = self.referencePath
        if self.referenceHost:
            d["referenceHost"] = self.referenceHost
        return d

    to_dict = frontmatter


@dataclass
class Task:
    id: str
    projectId: str
    type: str
    title: str
    status: str
    createdAt: str
    createdBy: str
    updatedAt: str
    updatedBy: str
    labels: list[str] = field(default_factory=list)
    description: str = ""
    imagePath: str | None = None
    comments: list[Comment] = field(default_factory=list)
    projectName: str = ""

    def frontmatter(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "projectId": self.projectId,
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "imagePath": self.imagePath,
            "labels": list(self.labels),
            "createdAt": self.createdAt,
            "createdBy": self.createdBy,
            "updatedAt": self.updatedAt,
            "updatedBy": self.updatedBy,
            "comments": [{"body": c.body, "createdAt": c.createdAt} for c in self.comments],
        }

    def to_dict(self) -> dict[str, Any]:
        d = self.frontmatter()
        d["description"] = self.description
        d["projectName"] = self.projectName
        return d


class Store:
    def __init__(
        self, root: str | os.PathLike[str] | None = None, actor: str | None = None
    ):
        self.actor = actor or os.environ.get(ENV_ACTOR) or getpass.getuser()
        root = root or os.environ.get(ENV_ROOT)
        if not root:
            raise StoreError(f"Daten-Root nicht gesetzt (Parameter oder ${ENV_ROOT})")
        self.root = Path(root).expanduser()

    # ---- Projekte -------------------------------------------------------

    def _project_dir(self, slug: str) -> Path:
        return self.root / slug

    def _load_project(self, slug: str) -> Project:
        path = self._project_dir(slug) / "project.md"
        try:
            data, _ = _parse(path.read_text(encoding="utf-8"), str(path))
        except FileNotFoundError:
            raise NotFound(f"Projekt-Ordner ohne project.md: {slug}") from None
        _validate(_project_validator, data, str(path))
        return Project(slug=slug, **data)

    def list_projects(self) -> list[Project]:
        if not self.root.is_dir():
            return []
        out = [
            self._load_project(p.name)
            for p in sorted(self.root.iterdir())
            if p.is_dir() and (p / "project.md").exists()
        ]
        return sorted(out, key=lambda p: p.name.casefold())

    def get_project(self, name: str) -> Project:
        key = name.casefold()
        for p in self.list_projects():
            if p.name.casefold() == key:
                return p
        raise NotFound(f"Projekt nicht gefunden: {name!r}")

    def add_project(
        self,
        name: str,
        reference_path: str | None = None,
        reference_host: str | None = None,
    ) -> Project:
        name = name.strip()
        slug = slugify(name)
        if any(p.name.casefold() == name.casefold() for p in self.list_projects()):
            raise Conflict(f"Projekt existiert bereits: {name!r}")
        pdir = self._project_dir(slug)
        if pdir.exists():
            raise Conflict(f"Slug-Kollision: Ordner {slug!r} existiert bereits")
        project = Project(id=str(uuid.uuid4()), name=name, createdAt=now_utc(), slug=slug)
        self._apply_reference(project, reference_path, reference_host)
        data = project.frontmatter()
        _validate(_project_validator, data, "project")
        (pdir / "tasks").mkdir(parents=True)
        _atomic_write(pdir / "project.md", _render(data))
        return project

    @staticmethod
    def _apply_reference(
        project: Project, path: str | None, host: str | None
    ) -> None:
        if path is not None:
            project.referencePath = path.strip() or None
        if host is not None:
            project.referenceHost = host.strip() or None
        elif path and not project.referenceHost:
            project.referenceHost = socket.gethostname()

    def edit_project(
        self,
        name: str,
        reference_path: str | None = None,
        reference_host: str | None = None,
    ) -> Project:
        """Setzt referencePath/referenceHost; leerer String entfernt das Feld."""
        project = self.get_project(name)
        self._apply_reference(project, reference_path, reference_host)
        data = project.frontmatter()
        _validate(_project_validator, data, "project")
        pdir = self._project_dir(project.slug)
        _write_with_backup(pdir / "project.md", _render(data), pdir / ".backup")
        return project

    # ---- Tasks ----------------------------------------------------------

    def _task_path(self, project: Project, task_id: str) -> Path:
        return self._project_dir(project.slug) / "tasks" / f"{task_id}.md"

    def _read_task(self, path: Path, project: Project) -> Task:
        data, body = _parse(path.read_text(encoding="utf-8"), str(path))
        _validate(_task_validator, data, str(path))
        if data["id"] != path.stem:
            raise ValidationError(f"{path}: id {data['id']} != Dateiname")
        if data["projectId"] != project.id:
            raise ValidationError(f"{path}: projectId passt nicht zu {project.name!r}")
        comments = [Comment(**c) for c in data.pop("comments", [])]
        return Task(description=body, comments=comments, projectName=project.name, **data)

    def _write_task(self, project: Project, task: Task) -> None:
        _validate(_task_validator, task.frontmatter(), f"task {task.id}")
        pdir = self._project_dir(project.slug)
        _write_with_backup(
            self._task_path(project, task.id),
            _render(task.frontmatter(), task.description),
            pdir / ".backup",
        )

    def _find_task(self, task_id: str) -> tuple[Project, Task]:
        for project in self.list_projects():
            path = self._task_path(project, task_id)
            if path.exists():
                return project, self._read_task(path, project)
        raise NotFound(f"Task nicht gefunden: {task_id}")

    def resolve_task_id(self, prefix: str) -> str:
        """Eindeutiges ID-Präfix (min. 4 Zeichen) -> volle UUID."""
        prefix = prefix.strip().lower()
        if len(prefix) < 4:
            raise ValidationError("Task-ID-Präfix: mindestens 4 Zeichen")
        hits = [
            p.stem
            for project in self.list_projects()
            for p in (self._project_dir(project.slug) / "tasks").glob(f"{prefix}*.md")
        ]
        if not hits:
            raise NotFound(f"Task nicht gefunden: {prefix}")
        if len(hits) > 1:
            raise Conflict(f"Task-ID-Präfix mehrdeutig: {prefix} ({len(hits)} Treffer)")
        return hits[0]

    def get_task(self, task_id: str) -> Task:
        return self._find_task(task_id)[1]

    def add_task(
        self,
        project_name: str,
        type: str,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> Task:
        project = self.get_project(project_name)
        ts = now_utc()
        task = Task(
            id=str(uuid.uuid4()),
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
            projectName=project.name,
        )
        self._write_task(project, task)
        return task

    def list_tasks(
        self,
        project_name: str | None = None,
        type: str | None = None,
        status: str | None = None,
        stale_days: float | None = None,
        label: str | None = None,
        now: datetime | None = None,
    ) -> list[Task]:
        projects = [self.get_project(project_name)] if project_name else self.list_projects()
        cutoff = None
        if stale_days is not None:
            cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=stale_days)
        out: list[Task] = []
        for project in projects:
            tdir = self._project_dir(project.slug) / "tasks"
            if not tdir.is_dir():
                continue
            for path in tdir.glob("*.md"):
                t = self._read_task(path, project)
                if type and t.type != type:
                    continue
                if status and t.status != status:
                    continue
                if label and label not in t.labels:
                    continue
                if cutoff and parse_ts(t.updatedAt) > cutoff:
                    continue
                out.append(t)
        return sorted(out, key=lambda t: t.updatedAt, reverse=True)

    def _update(self, task_id: str, mutate) -> Task:
        project, task = self._find_task(task_id)
        mutate(task)
        task.updatedAt = now_utc()
        task.updatedBy = self.actor
        self._write_task(project, task)
        return task

    def edit_task(
        self,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
    ) -> Task:
        if title is None and description is None and labels is None:
            raise ValidationError("edit ohne Änderung (title/description/labels)")

        def mutate(t: Task) -> None:
            if title is not None:
                t.title = title.strip()
            if description is not None:
                t.description = description
            if labels is not None:
                t.labels = _clean_labels(labels)

        return self._update(task_id, mutate)

    def close_task(self, task_id: str) -> Task:
        return self._update(task_id, lambda t: setattr(t, "status", "closed"))

    def reopen_task(self, task_id: str) -> Task:
        return self._update(task_id, lambda t: setattr(t, "status", "open"))

    def add_comment(self, task_id: str, body: str) -> Task:
        body = body.strip()
        if not body:
            raise ValidationError("Kommentar leer")
        return self._update(
            task_id, lambda t: t.comments.append(Comment(body=body, createdAt=now_utc()))
        )

    def set_image(self, task_id: str, source: str | os.PathLike[str]) -> Task:
        src = Path(source)
        if not src.is_file():
            raise NotFound(f"Bilddatei nicht gefunden: {src}")
        project, _ = self._find_task(task_id)
        rel = f"images/{task_id}{src.suffix.lower()}"
        dest = self._project_dir(project.slug) / rel
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(src, dest)

        def mutate(t: Task) -> None:
            t.imagePath = rel

        return self._update(task_id, mutate)

    def add_attachment(self, task_id: str, source: str | os.PathLike[str]) -> tuple[Task, str]:
        """Zusätzliches Bild als `images/<task-id>-<n>.<ext>` ablegen (für Links in der
        Description). Liefert (Task, Pfad relativ zur Task-Datei, z. B. `../images/...`)."""
        src = Path(source)
        if not src.is_file():
            raise NotFound(f"Bilddatei nicht gefunden: {src}")
        project, _ = self._find_task(task_id)
        images = self._project_dir(project.slug) / "images"
        images.mkdir(exist_ok=True)
        n = 1
        while True:
            dest = images / f"{task_id}-{n}{src.suffix.lower()}"
            try:
                with open(dest, "xb") as out, open(src, "rb") as inp:
                    shutil.copyfileobj(inp, out)
                break
            except FileExistsError:
                n += 1
        return self._update(task_id, lambda t: None), f"../images/{dest.name}"
