"""CLI (AP4''): thin layer over oloop.store."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .store import STATUSES, TASK_TYPES, UNSET, Store, StoreError, parse_ts

# Display label per type for status=closed (SPEC "One status field")
CLOSED_LABEL = {
    "todo": "Done",
    "question": "Answered",
    "decision": "Decided",
    "feature": "Implemented",
    "bug": "Fixed",
}


def _dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _age(ts: str) -> str:
    days = (datetime.now(timezone.utc) - parse_ts(ts)).days
    return "today" if days < 1 else f"{days}d"


def _line(t) -> str:
    status = CLOSED_LABEL[t.type] if t.status == "closed" else "open"
    labels = f" [{', '.join(t.labels)}]" if t.labels else ""
    due = ""
    if t.dueDate:
        due = f" due {t.dueDate}" + (" OVERDUE" if t.is_overdue() else "")
    return (
        f"{t.id[:8]}  {t.type:<8} {status:<11} {t.projectName}  "
        f"{_age(t.updatedAt):>6}  {t.title}{labels}{due}"
    )


def _task_id(store: Store, args) -> str:
    return store.resolve_task_id(args.id)


def cmd_project_add(store, args):
    p = store.add_project(args.name, args.reference_path, args.host)
    print(f"Project created: {p.name} ({p.id})")


def cmd_project_edit(store, args):
    p = store.edit_project(args.name, args.reference_path, args.host)
    print(f"Project updated: {p.name}")


def cmd_project_find(store, args):
    p = store.find_project_by_path(args.path, args.host)
    if args.json:
        return _dump(p.to_dict())
    print(p.name)


def cmd_project_list(store, args):
    projects = store.list_projects()
    if args.json:
        return _dump([p.to_dict() for p in projects])
    for p in projects:
        ref = f"  {p.referenceHost or '?'}:{p.referencePath}" if p.referencePath else ""
        print(f"{p.name}{ref}")


def cmd_task_add(store, args):
    t = store.add_task(
        args.project, args.type, args.title, args.description, args.label, args.due
    )
    print(f"Task created: {t.id}")


def cmd_task_list(store, args):
    tasks = store.list_tasks(
        args.project, args.type, args.status, args.stale_days, args.label,
        due_before=args.due_before, overdue=args.overdue, has_due=args.has_due, sort=args.sort,
    )
    if args.json:
        return _dump([t.to_dict(explicit_due=True) for t in tasks])
    for t in tasks:
        print(_line(t))


def cmd_task_show(store, args):
    t = store.get_task(_task_id(store, args))
    if args.json:
        return _dump(t.to_dict(explicit_due=True))
    print(_line(t))
    print(f"id: {t.id}")
    if t.dueDate:
        print(f"due: {t.dueDate}" + (" (overdue)" if t.is_overdue() else ""))
    print(f"created {t.createdAt} by {t.createdBy}; updated {t.updatedAt} by {t.updatedBy}")
    if t.imagePath:
        print(f"image: {t.imagePath}")
    if t.description:
        print(f"\n{t.description}")
    for c in t.comments:
        print(f"\n[{c.createdAt}] {c.body}")


def cmd_task_edit(store, args):
    labels = [] if args.clear_labels else args.label
    due = None if args.clear_due else args.due if args.due is not None else UNSET
    t = store.edit_task(_task_id(store, args), args.title, args.description, labels, due)
    print(f"Task updated: {t.id}")


def cmd_task_close(store, args):
    print(f"Task closed: {store.close_task(_task_id(store, args)).id}")


def cmd_task_reopen(store, args):
    print(f"Task reopened: {store.reopen_task(_task_id(store, args)).id}")


def cmd_task_image(store, args):
    tid = _task_id(store, args)
    if args.embed:
        _, link = store.add_attachment(tid, args.file)
        print(f"![{args.file.rsplit('/', 1)[-1]}]({link})")
    else:
        t = store.set_image(tid, args.file)
        print(f"Main image set: {t.imagePath}")


def cmd_comment_add(store, args):
    t = store.add_comment(_task_id(store, args), args.body)
    print(f"Comment added: {t.id}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="oloop")
    ap.add_argument("--root", help="data root (default: $OLOOP_ROOT)")
    ap.add_argument("--actor", help="actor for createdBy/updatedBy (default: $OLOOP_ACTOR)")
    sub = ap.add_subparsers(dest="group", required=True)

    def leaf(parent, name, fn, json_flag=False):
        p = parent.add_parser(name)
        p.set_defaults(fn=fn)
        if json_flag:
            p.add_argument("--json", action="store_true")
        return p

    proj = sub.add_parser("project").add_subparsers(dest="cmd", required=True)
    for cmd, fn in (("add", cmd_project_add), ("edit", cmd_project_edit)):
        p = leaf(proj, cmd, fn)
        p.add_argument("name")
        p.add_argument("--reference-path", help="working directory; empty string removes it")
        p.add_argument("--host", help="machine name; with a path, defaults to this machine")
    leaf(proj, "list", cmd_project_list, True)
    p = leaf(proj, "find", cmd_project_find, True)
    p.add_argument("--path", default=".", help="default: current directory")
    p.add_argument("--host", help="default: this machine")

    task = sub.add_parser("task").add_subparsers(dest="cmd", required=True)
    p = leaf(task, "add", cmd_task_add)
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--type", "-t", required=True, choices=TASK_TYPES)
    p.add_argument("title")
    p.add_argument("--description", "-d", default="")
    p.add_argument("--label", "-l", action="append", default=[])
    p.add_argument("--due", metavar="YYYY-MM-DD", help="due date (calendar day, no time zone)")

    p = leaf(task, "list", cmd_task_list, True)
    p.add_argument("--project", "-p")
    p.add_argument("--type", "-t", choices=TASK_TYPES)
    p.add_argument("--status", "-s", choices=STATUSES)
    p.add_argument("--stale-days", type=float, help="only tasks unchanged for N days")
    p.add_argument("--label", "-l")
    p.add_argument("--due-before", metavar="YYYY-MM-DD",
                   help="only tasks with a due date before this day")
    p.add_argument("--overdue", action="store_true",
                   help="only open tasks whose due date has passed")
    p.add_argument("--has-due", action="store_true", help="only tasks with a due date")
    p.add_argument("--sort", choices=("updated", "due"), default="updated",
                   help="updated: newest first (default); due: earliest due date first, "
                        "tasks without a date last")

    leaf(task, "show", cmd_task_show, True).add_argument("id")

    p = leaf(task, "edit", cmd_task_edit)
    p.add_argument("id")
    p.add_argument("--title")
    p.add_argument("--description", "-d")
    p.add_argument("--label", "-l", action="append", default=None,
                   help="replaces all labels (repeatable)")
    p.add_argument("--clear-labels", action="store_true")
    due = p.add_mutually_exclusive_group()
    due.add_argument("--due", metavar="YYYY-MM-DD", help="set the due date")
    due.add_argument("--clear-due", action="store_true", help="remove the due date")

    p = leaf(task, "image", cmd_task_image)
    p.add_argument("id")
    p.add_argument("file")
    p.add_argument("--embed", action="store_true",
                   help="store as an extra image and print a Markdown link for the description")

    leaf(task, "close", cmd_task_close).add_argument("id")
    leaf(task, "reopen", cmd_task_reopen).add_argument("id")

    com = sub.add_parser("comment").add_subparsers(dest="cmd", required=True)
    p = leaf(com, "add", cmd_comment_add)
    p.add_argument("id")
    p.add_argument("body")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(Store(args.root, args.actor), args)
    except StoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
