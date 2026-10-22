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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ENV_ROOT = "OLOOP_ROOT"
ENV_ACTOR = "OLOOP_ACTOR"
FORMAT_VERSION = 1  # file format version written by this tool and the newest one it can read
LEGACY_ENV_PREFIX = "LAGEBERICHT_"  # alter Toolname, Fallback


def _env(name: str) -> str | None:
    legacy = LEGACY_ENV_PREFIX + name.removeprefix("OLOOP_")
    return os.environ.get(name) or os.environ.get(legacy)
TASK_TYPES = ("todo", "question", "decision", "feature", "bug")
STATUSES = ("open", "closed")

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


UNSET: Any = object()  # "nicht angegeben", im Unterschied zu None ("entfernen")


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


class _Date(str):
    """Ein Kalenderdatum `YYYY-MM-DD`. Wird unquotiert geschrieben (`dueDate: 2026-11-01`)."""


class _FileDumper(yaml.SafeDumper):
    pass


_FileDumper.add_representer(
    _Date, lambda dumper, v: dumper.represent_scalar("tag:yaml.org,2002:timestamp", str(v))
)

_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")

_PROJECT_FIELDS = frozenset(
    {"formatVersion", "id", "name", "createdAt", "referencePath", "referenceHost"}
)
_TASK_FIELDS = frozenset(
    {
        "formatVersion", "id", "projectId", "type", "title", "status", "imagePath", "dueDate",
        "labels", "createdAt", "createdBy", "updatedAt", "updatedBy", "comments",
    }
)
_COMMENT_FIELDS = frozenset({"body", "createdAt"})


def _split(data: dict, known: frozenset[str]) -> tuple[dict, dict]:
    """Bekannte und unbekannte Felder trennen; die unbekannten behalten ihre Reihenfolge."""
    return (
        {k: v for k, v in data.items() if k in known},
        {k: v for k, v in data.items() if k not in known},
    )


def check_due_date(value: str) -> str:
    """Prüft `YYYY-MM-DD` und dass der Tag im Kalender existiert."""
    try:
        if not _DATE_RE.match(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise ValidationError(
            f"Invalid due date {value!r}: use YYYY-MM-DD with a real calendar day"
        ) from None
    return value


def today() -> date:
    """Heute als lokales Kalenderdatum (dueDate hat keine Zeitzone)."""
    return date.today()


_project_validator = _validator("project.schema.json")
_task_validator = _validator("task.schema.json")


def _check_format_version(data: dict, where: str) -> None:
    """Refuse files written in a newer format, before schema errors can obscure the cause."""
    version = data.get("formatVersion", 1)
    if isinstance(version, int) and version > FORMAT_VERSION:
        raise ValidationError(
            f"{where}: file format version {version} is newer than supported ({FORMAT_VERSION})"
        )


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
        raise ValidationError(f"Project name yields no valid slug: {name!r}")
    return s


def _clean_labels(labels: list[str] | None) -> list[str]:
    """Trimmen, Leere verwerfen, Duplikate entfernen (Reihenfolge bleibt)."""
    return list(dict.fromkeys(l.strip() for l in labels or [] if l.strip()))


def _parse(text: str, where: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValidationError(f"{where}: no frontmatter")
    data = yaml.load(m.group(1), Loader=_StringTimestampLoader)
    if not isinstance(data, dict):
        raise ValidationError(f"{where}: frontmatter is not a mapping")
    return data, m.group(2).strip("\n")


def _render(data: dict, body: str = "") -> str:
    head = yaml.dump(
        data, Dumper=_FileDumper, sort_keys=False, allow_unicode=True, default_flow_style=False
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
    extra: dict[str, Any] = field(default_factory=dict)  # unbekannte Felder, bleiben erhalten

    def frontmatter(self) -> dict[str, Any]:
        return {"body": self.body, "createdAt": self.createdAt, **self.extra}


@dataclass
class Project:
    id: str
    name: str
    createdAt: str
    slug: str = ""
    referencePath: str | None = None
    referenceHost: str | None = None
    formatVersion: int = FORMAT_VERSION
    extra: dict[str, Any] = field(default_factory=dict)  # unbekannte Felder, bleiben erhalten

    def frontmatter(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "formatVersion": self.formatVersion,
            "id": self.id,
            "name": self.name,
            "createdAt": self.createdAt,
        }
        if self.referencePath:
            d["referencePath"] = self.referencePath
        if self.referenceHost:
            d["referenceHost"] = self.referenceHost
        return {**d, **self.extra}

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
    dueDate: str | None = None  # YYYY-MM-DD, kein Zeitanteil, keine Zeitzone
    comments: list[Comment] = field(default_factory=list)
    projectName: str = ""
    formatVersion: int = FORMAT_VERSION
    extra: dict[str, Any] = field(default_factory=dict)  # unbekannte Felder, bleiben erhalten

    def is_overdue(self, on: date | None = None) -> bool:
        """Offen und fällig vor `on` (Standard: heute). Geschlossene Tasks nie."""
        return (
            self.status == "open"
            and self.dueDate is not None
            and date.fromisoformat(self.dueDate) < (on or today())
        )

    def frontmatter(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "formatVersion": self.formatVersion,
            "id": self.id,
            "projectId": self.projectId,
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "imagePath": self.imagePath,
        }
        if self.dueDate:  # fehlt statt null, wenn es kein Datum gibt
            d["dueDate"] = _Date(self.dueDate)
        d.update(
            {
                "labels": list(self.labels),
                "createdAt": self.createdAt,
                "createdBy": self.createdBy,
                "updatedAt": self.updatedAt,
                "updatedBy": self.updatedBy,
                "comments": [c.frontmatter() for c in self.comments],
            }
        )
        return {**d, **self.extra}

    def to_dict(self, explicit_due: bool = False) -> dict[str, Any]:
        """Dict mit allen Feldern. `explicit_due`: dueDate auch als null ausgeben (CLI --json);
        sonst fehlt der Schlüssel ohne Datum, wie in den Conformance-Fixtures."""
        d = self.frontmatter()
        if "dueDate" in d:
            d["dueDate"] = str(d["dueDate"])
        elif explicit_due:
            d["dueDate"] = None
        d["description"] = self.description
        d["projectName"] = self.projectName
        return d


class Store:
    def __init__(
        self, root: str | os.PathLike[str] | None = None, actor: str | None = None
    ):
        self.actor = actor or _env(ENV_ACTOR) or getpass.getuser()
        root = root or _env(ENV_ROOT)
        if not root:
            raise StoreError(f"Data root not set (parameter or ${ENV_ROOT})")
        self.root = Path(root).expanduser()

    # ---- Projekte -------------------------------------------------------

    def _project_dir(self, slug: str) -> Path:
        return self.root / slug

    def _load_project(self, slug: str) -> Project:
        path = self._project_dir(slug) / "project.md"
        try:
            data, _ = _parse(path.read_text(encoding="utf-8"), str(path))
        except FileNotFoundError:
            raise NotFound(f"Project folder without project.md: {slug}") from None
        _check_format_version(data, str(path))
        _validate(_project_validator, data, str(path))
        known, extra = _split(data, _PROJECT_FIELDS)
        return Project(slug=slug, extra=extra, **known)

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
        raise NotFound(f"Project not found: {name!r}")

    def find_project_by_path(
        self, path: str | os.PathLike[str], host: str | None = None
    ) -> Project:
        """Projekt, dessen referencePath path enthält (längster Treffer, passender Host)."""
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
        name = name.strip()
        slug = slugify(name)
        if any(p.name.casefold() == name.casefold() for p in self.list_projects()):
            raise Conflict(f"Project already exists: {name!r}")
        pdir = self._project_dir(slug)
        if pdir.exists():
            raise Conflict(f"Slug collision: folder {slug!r} already exists")
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
        _check_format_version(data, str(path))
        _validate(_task_validator, data, str(path))
        if data["id"] != path.stem:
            raise ValidationError(f"{path}: id {data['id']} != file name")
        if data["projectId"] != project.id:
            raise ValidationError(f"{path}: projectId does not match {project.name!r}")
        known, extra = _split(data, _TASK_FIELDS)
        comments = []
        for c in known.pop("comments", []):
            c_known, c_extra = _split(c, _COMMENT_FIELDS)
            comments.append(Comment(extra=c_extra, **c_known))
        if known.get("dueDate") is None:
            known.pop("dueDate", None)
        return Task(
            description=body, comments=comments, projectName=project.name, extra=extra, **known
        )

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
        raise NotFound(f"Task not found: {task_id}")

    def resolve_task_id(self, prefix: str) -> str:
        """Eindeutiges ID-Präfix (min. 4 Zeichen) -> volle UUID."""
        prefix = prefix.strip().lower()
        if len(prefix) < 4:
            raise ValidationError("Task ID prefix: at least 4 characters")
        hits = [
            p.stem
            for project in self.list_projects()
            for p in (self._project_dir(project.slug) / "tasks").glob(f"{prefix}*.md")
        ]
        if not hits:
            raise NotFound(f"Task not found: {prefix}")
        if len(hits) > 1:
            raise Conflict(f"Task ID prefix ambiguous: {prefix} ({len(hits)} matches)")
        return hits[0]

    def get_task(self, task_id: str) -> Task:
        return self._find_task(task_id)[1]

    def locate_task(self, task_id: str) -> tuple[Project, Task]:
        """Der Task samt seinem Projekt (volle ID)."""
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
            dueDate=due_date,
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
        label: str | list[str] | tuple[str, ...] | None = None,
        now: datetime | None = None,
        due_before: str | None = None,
        overdue: bool = False,
        has_due: bool = False,
        sort: str = "updated",
        on: date | None = None,
    ) -> list[Task]:
        """`label`: ein Label oder mehrere; ein Task passt, wenn er mindestens eines trägt (Oder).
        Der Vergleich ignoriert die Groß-/Kleinschreibung (casefold, ohne Locale), die Labels
        selbst bleiben wie gespeichert.
        `due_before`: Fälligkeit vor diesem Datum. `overdue`: offen und vor `on` (Standard
        heute) fällig. `has_due`: nur Tasks mit Datum. `sort`: "updated" (neueste zuerst) oder
        "due" (frühestes Datum zuerst, ohne Datum am Ende)."""
        if due_before is not None:
            check_due_date(due_before)
        wanted = {l.casefold() for l in ([label] if isinstance(label, str) else label or [])}
        if sort not in ("updated", "due"):
            raise ValidationError(f"Unknown sort order: {sort!r} (updated, due)")
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
            # stabil: gleiche Daten bleiben nach updatedAt sortiert, ohne Datum ans Ende
            out.sort(key=lambda t: (t.dueDate is None, t.dueDate or ""))
        return out

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
        due_date: str | None | object = UNSET,
    ) -> Task:
        """`due_date`: ein Datum setzt es, None entfernt es, UNSET (Standard) lässt es."""
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

    def set_image(self, task_id: str, source: str | os.PathLike[str]) -> Task:
        src = Path(source)
        if not src.is_file():
            raise NotFound(f"Image file not found: {src}")
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
            raise NotFound(f"Image file not found: {src}")
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
