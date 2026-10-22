"""HTML export of a task or a project: one self-contained file, readable without network.

The export carries what the interactive views show: metadata, description (Markdown rendered),
image and every comment. A project export starts with the project name and a list of its tasks,
then every task as its own bordered card, in the same layout as the single task export.

The CSS has a print part, so "Print to PDF" in a browser gives a clean PDF.

Safety: the files are user content that ends up in a page the browser opens. Raw HTML in the
Markdown is escaped, links with a script scheme are dropped by the Markdown parser, and every
other value is escaped. Images are embedded as data URIs, only from inside the project folder.
"""

from __future__ import annotations

import base64
import html
import mimetypes
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt

from .store import Project, Store, Task, parse_ts

CLOSED_LABEL = {
    "todo": "Done",
    "question": "Answered",
    "decision": "Decided",
    "feature": "Implemented",
    "bug": "Fixed",
}

_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _stamp(ts: str) -> str:
    """`2026-10-21T20:40:28Z` -> `2026-10-21 20:40 UTC`."""
    return parse_ts(ts).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _data_uri(path: Path) -> str | None:
    """The file as a data URI, or None when it is missing or no image."""
    if path.suffix.lower() not in _IMAGE_TYPES or not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


class _Images:
    """Resolves image references of one task to data URIs, never leaving the project folder."""

    def __init__(self, project_dir: Path, task_dir: Path):
        self.project_dir = project_dir.resolve()
        self.task_dir = task_dir.resolve()

    def inside(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve()
            resolved.relative_to(self.project_dir)
        except (OSError, ValueError):
            return None
        return resolved

    def from_project(self, relative: str) -> str | None:
        found = self.inside(self.project_dir / relative)
        return _data_uri(found) if found else None

    def from_task_file(self, src: str) -> str | None:
        if "://" in src or src.startswith(("data:", "/", "#")):
            return None
        found = self.inside(self.task_dir / unquote(src))
        return _data_uri(found) if found else None


def _markdown(images: _Images) -> MarkdownIt:
    # "commonmark" with html=False: raw HTML in the description is shown as text, not run.
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table").enable("strikethrough")
    default_image = md.renderer.rules.get("image")

    def image(tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet("src") or ""
        embedded = images.from_task_file(src)
        if embedded is None:
            # An image that cannot be embedded stays a visible note instead of a broken request.
            alt = token.content or src
            return f'<span class="missing-image">[image not embedded: {_esc(alt)}]</span>'
        token.attrSet("src", embedded)
        return default_image(tokens, idx, options, env)

    md.renderer.rules["image"] = image
    return md


def _task_article(store: Store, project: Project, task: Task, *, today: date) -> str:
    project_dir = store.root / project.slug
    images = _Images(project_dir, project_dir / "tasks")
    status_label = CLOSED_LABEL.get(task.type, "Closed") if task.status == "closed" else "Open"
    overdue = task.is_overdue(today)

    meta = [
        f'<span class="type type-{_esc(task.type)}">{_esc(task.type)}</span>',
        f'<span class="status status-{_esc(task.status)}">{_esc(status_label)}</span>',
    ]
    if task.dueDate:
        badge = ' <span class="overdue">overdue</span>' if overdue else ""
        meta.append(f'<span class="due">Due {_esc(task.dueDate)}{badge}</span>')
    labels = "".join(f'<span class="label">{_esc(label)}</span>' for label in task.labels)

    md = _markdown(images)
    parts = [
        f'<article class="task" id="task-{_esc(task.id)}">',
        f'<h2 class="task-title">{_esc(task.title)}</h2>',
        f'<p class="meta">{" ".join(meta)}</p>',
    ]
    if labels:
        parts.append(f'<p class="labels">{labels}</p>')
    if task.description.strip():
        parts.append(f'<div class="description">{md.render(task.description)}</div>')
    if task.imagePath:
        embedded = images.from_project(task.imagePath)
        if embedded:
            parts.append(f'<p class="image"><img src="{embedded}" alt="{_esc(task.title)}"></p>')
        else:
            parts.append(f'<p class="missing-image">[image not found: {_esc(task.imagePath)}]</p>')

    parts.append(
        '<p class="stamps">'
        f"Created {_esc(_stamp(task.createdAt))} by {_esc(task.createdBy)} · "
        f"Updated {_esc(_stamp(task.updatedAt))} by {_esc(task.updatedBy)} · "
        f'<span class="id">{_esc(task.id)}</span></p>'
    )

    parts.append(f'<section class="comments"><h3>Comments ({len(task.comments)})</h3>')
    if task.comments:
        for comment in task.comments:
            parts.append(
                '<div class="comment">'
                f'<p class="comment-time">{_esc(_stamp(comment.createdAt))}</p>'
                f'<div class="comment-body">{md.render(comment.body)}</div>'
                "</div>"
            )
    else:
        parts.append('<p class="none">No comments.</p>')
    parts.append("</section></article>")
    return "\n".join(parts)


_CSS = """
:root { color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6368; --line:#c9cdd4; --bg:#fff; --card:#fff;
        --chip:#eef0f4; --accent:#2f4bb0; --bad:#b3261e; --badbg:#fde7e5; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e3e3e8; --muted:#a9acb4; --line:#3b3e46; --bg:#121317; --card:#1a1c21;
          --chip:#262930; --accent:#9db1ff; --bad:#ffb4ab; --badbg:#3b2420; } }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1rem 4rem; background:var(--bg); color:var(--fg);
       font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width:52rem; margin:0 auto; }
h1 { font-size:2rem; margin:0 0 .25rem; }
.sub { color:var(--muted); margin:0 0 1.5rem; }
.toc { margin:0 0 2rem; padding:.75rem 1rem .75rem 2rem; border:1px solid var(--line); border-radius:10px; }
.toc li { margin:.15rem 0; } .toc a { color:var(--accent); text-decoration:none; }
.toc .closed { color:var(--muted); }
.task { border:1px solid var(--line); border-left:6px solid var(--accent); border-radius:10px;
        background:var(--card); padding:1rem 1.25rem 1.25rem; margin:0 0 2rem; }
.task-title { font-size:1.4rem; margin:0 0 .35rem; }
.meta, .labels, .stamps { margin:.25rem 0; }
.type, .status, .label, .due { display:inline-block; padding:.05rem .55rem; border-radius:.5rem;
        background:var(--chip); font-size:.85rem; margin:0 .25rem .25rem 0; }
.label { border:1px solid var(--line); background:none; }
.overdue { display:inline-block; padding:0 .45rem; border-radius:.4rem; background:var(--badbg); color:var(--bad);
           font-size:.8rem; }
.status-closed { color:var(--muted); }
.description, .comment-body { overflow-wrap:anywhere; }
.description img, .comment-body img, .image img { max-width:100%; height:auto; border-radius:6px; }
.description pre, .comment-body pre { overflow:auto; padding:.75rem; background:var(--chip); border-radius:6px; }
.description code, .comment-body code { background:var(--chip); padding:.05rem .25rem; border-radius:4px; }
.description pre code, .comment-body pre code { background:none; padding:0; }
.description table, .comment-body table { border-collapse:collapse; }
.description th, .description td, .comment-body th, .comment-body td { border:1px solid var(--line); padding:.25rem .5rem; }
.stamps { color:var(--muted); font-size:.85rem; border-top:1px solid var(--line); padding-top:.5rem; margin-top:1rem; }
.stamps .id { font-family:ui-monospace,Menlo,Consolas,monospace; }
.comments h3 { font-size:1rem; margin:1rem 0 .25rem; }
.comment { border-left:3px solid var(--line); padding:.1rem 0 .1rem .75rem; margin:.6rem 0; }
.comment-time { color:var(--muted); font-size:.8rem; margin:0; }
.comment-body p:first-child { margin-top:.1rem; } .comment-body p:last-child { margin-bottom:0; }
.none, .missing-image { color:var(--muted); font-style:italic; }
@media print {
  :root { color-scheme: light; --fg:#000; --muted:#444; --line:#999; --bg:#fff; --card:#fff; --chip:#eee;
          --accent:#2f4bb0; --bad:#8c1d18; --badbg:#fde7e5; }
  body { padding:0; font-size:11pt; } a { color:inherit; text-decoration:none; }
  .task { break-inside:avoid-page; page-break-inside:avoid; }
  .toc { break-after:page; }
}
""".strip()


def _page(title: str, body: str, generated: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>\n{_CSS}\n</style>\n</head>\n"
        f"<body>\n<main>\n{body}\n"
        f'<p class="sub">Exported {_esc(generated)} with oloop.</p>\n</main>\n</body>\n</html>\n'
    )


def export_task(store: Store, task_id: str, *, now: datetime | None = None, today: date | None = None) -> str:
    """One task as a complete HTML page. The project name stands above the task."""
    project, task = store.locate_task(task_id)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f'<p class="sub">Project: {_esc(project.name)}</p>\n'
        + _task_article(store, project, task, today=today or date.today())
    )
    return _page(f"{task.title} · {project.name}", body, stamp)


def export_project(
    store: Store,
    name: str,
    *,
    include_closed: bool = False,
    now: datetime | None = None,
    today: date | None = None,
) -> str:
    """A project as a complete HTML page: its name, a list of its tasks, then every task as a card.

    Open tasks come first, then closed ones (only with ``include_closed``). Inside each group the
    most recently changed task is first, like in ``task list``.
    """
    project = store.get_project(name)
    tasks = store.list_tasks(project.name)
    if not include_closed:
        tasks = [t for t in tasks if t.status == "open"]
    tasks.sort(key=lambda t: t.status == "closed")  # stable: keeps the newest-first order

    today = today or date.today()
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    open_count = sum(1 for t in tasks if t.status == "open")
    closed_count = len(tasks) - open_count
    counts = f"{open_count} open"
    if include_closed:
        counts += f", {closed_count} closed"
    head = [f"<h1>{_esc(project.name)}</h1>", f'<p class="sub">{counts}</p>']

    if tasks:
        items = "".join(
            f'<li class="{"closed" if t.status == "closed" else "open"}">'
            f'<a href="#task-{_esc(t.id)}">{_esc(t.title)}</a></li>'
            for t in tasks
        )
        head.append(f'<ol class="toc">{items}</ol>')
        cards = "\n".join(_task_article(store, project, t, today=today) for t in tasks)
    else:
        head.append('<p class="none">No tasks to show.</p>')
        cards = ""
    return _page(project.name, "\n".join(head) + "\n" + cards, stamp)
