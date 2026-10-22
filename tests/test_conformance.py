"""Runs the shared fixtures from spec/fixtures (oloop-spec submodule) and guards the schema copy."""

import json
import shutil
from pathlib import Path

import pytest

from oloop import Store, StoreError

SPEC = Path(__file__).resolve().parents[1] / "spec"
FIXTURES = SPEC / "fixtures"
PKG_SCHEMAS = Path(__file__).resolve().parents[1] / "oloop" / "schemas"

if not (SPEC / "fixtures").is_dir():
    pytest.fail("spec/ is empty, run `git submodule update --init`", allow_module_level=True)


def _cases(kind):
    return sorted(p for p in (FIXTURES / kind).iterdir() if p.is_dir())


def _read_all(store):
    projects = [p.to_dict() for p in store.list_projects()]
    tasks = sorted((t.to_dict() for t in store.list_tasks()), key=lambda t: t["id"])
    return {"projects": projects, "tasks": tasks}


@pytest.mark.parametrize("case", _cases("valid"), ids=lambda p: p.name)
def test_valid_fixture(case):
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    assert _read_all(Store(case / "root", actor="test")) == expected


@pytest.mark.parametrize("case", _cases("invalid"), ids=lambda p: p.name)
def test_invalid_fixture(case):
    needle = (case / "expected_error.txt").read_text(encoding="utf-8").strip()
    with pytest.raises(StoreError, match=needle):
        _read_all(Store(case / "root", actor="test"))


def test_write_adds_format_version(tmp_path):
    root = tmp_path / "root"
    shutil.copytree(FIXTURES / "valid" / "basic" / "root", root)
    store = Store(root, actor="test")
    task = next(t for t in store.list_tasks() if t.title == "Crash on start")
    store.add_comment(task.id, "touched")
    text = (root / "example-project" / "tasks" / f"{task.id}.md").read_text(encoding="utf-8")
    assert text.startswith("---\nformatVersion: 1\n")


@pytest.mark.parametrize("name", ["project.schema.json", "task.schema.json"])
def test_package_schema_matches_spec(name):
    assert (PKG_SCHEMAS / name).read_bytes() == (SPEC / "schemas" / name).read_bytes(), (
        "run `python sync_schemas.py`"
    )


@pytest.mark.parametrize("name", ["due-date", "unknown-fields"])
def test_round_trip(name, tmp_path):
    """Lesen, schreiben, wieder lesen: dasselbe Ergebnis wie expected.json (inkl. dueDate
    und unbekannten Feldern, auch in Projekten und Kommentaren)."""
    root = tmp_path / "root"
    shutil.copytree(FIXTURES / "valid" / name / "root", root)
    expected = json.loads((FIXTURES / "valid" / name / "expected.json").read_text(encoding="utf-8"))
    store = Store(root, actor="test")

    for p in store.list_projects():
        store.edit_project(p.name, reference_host=p.referenceHost or "h")
    for t in store.list_tasks():
        # jede Schreibart: Edit ohne Änderung des Datums, Schließen, Wiederöffnen
        store.edit_task(t.id, title=t.title)
        store.close_task(t.id)
        store.reopen_task(t.id)
        if t.status == "closed":
            store.close_task(t.id)

    got = _read_all(store)
    # Nur die von uns gesetzten Felder dürfen abweichen
    for t_got, t_exp in zip(got["tasks"], expected["tasks"]):
        for key in ("updatedAt", "updatedBy"):
            t_got.pop(key), t_exp.pop(key)
    for p_got, p_exp in zip(got["projects"], expected["projects"]):
        p_got.pop("referenceHost", None), p_exp.pop("referenceHost", None)
    assert got == expected
