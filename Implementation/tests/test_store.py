from datetime import datetime, timedelta, timezone

import pytest

from oloop import Conflict, NotFound, Store, ValidationError
from oloop.store import ENV_ROOT, StoreError, slugify


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


def test_root_required(monkeypatch):
    monkeypatch.delenv(ENV_ROOT, raising=False)
    monkeypatch.delenv("LAGEBERICHT_ROOT", raising=False)
    with pytest.raises(StoreError):
        Store()


def test_root_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_ROOT, str(tmp_path))
    assert Store().root == tmp_path


def test_slugify():
    assert slugify("Mein Projekt Über") == "mein-projekt-uber"
    assert slugify("Straße") == "strasse"
    with pytest.raises(ValidationError):
        slugify("???")


def test_project_add_list_conflict(store):
    p = store.add_project("Alpha Proj")
    assert (store.root / "alpha-proj" / "project.md").exists()
    assert store.get_project("alpha proj").id == p.id
    assert [x.name for x in store.list_projects()] == ["Alpha Proj"]
    with pytest.raises(Conflict):
        store.add_project("ALPHA PROJ")
    with pytest.raises(Conflict):
        store.add_project("alpha-proj")  # gleicher Slug, anderer Name
    with pytest.raises(NotFound):
        store.get_project("nope")


def test_legacy_env_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("OLOOP_ROOT", raising=False)
    monkeypatch.delenv("OLOOP_ACTOR", raising=False)
    monkeypatch.delenv("LAGEBERICHT_ACTOR", raising=False)
    monkeypatch.setenv("LAGEBERICHT_ROOT", str(tmp_path))
    monkeypatch.setenv("LAGEBERICHT_ACTOR", "old")
    s = Store()
    assert s.root == tmp_path and s.actor == "old"
    monkeypatch.setenv("OLOOP_ACTOR", "new")
    assert Store().actor == "new"


def test_project_reference(store):
    p = store.add_project("Ref", reference_path="/work/ref", reference_host="mac1")
    assert store.get_project("Ref").referencePath == "/work/ref"
    assert store.get_project("Ref").referenceHost == "mac1"
    store.add_project("Plain")
    assert "referencePath" not in store.get_project("Plain").to_dict()
    store.edit_project("Plain", reference_path="/work/plain")
    assert store.get_project("Plain").referenceHost  # Default: dieser Rechner
    store.edit_project("Ref", reference_path="")
    assert store.get_project("Ref").referencePath is None
    assert p.id == store.get_project("Ref").id


def test_project_find_by_path(store, tmp_path):
    outer, inner = tmp_path / "a", tmp_path / "a" / "b"
    (inner / "sub").mkdir(parents=True)
    store.add_project("Outer", reference_path=str(outer), reference_host="h")
    store.add_project("Inner", reference_path=str(inner), reference_host="h")
    assert store.find_project_by_path(inner / "sub", "h").name == "Inner"
    assert store.find_project_by_path(outer, "h").name == "Outer"
    with pytest.raises(NotFound):
        store.find_project_by_path(outer, "other-host")
    with pytest.raises(NotFound):
        store.find_project_by_path(tmp_path, "h")


def test_task_roundtrip(store):
    store.add_project("A")
    t = store.add_task("A", "todo", "Titel: mit # Sonderzeichen", "Beschreibung\n\nzwei Absätze äöü")
    got = store.get_task(t.id)
    assert got.title == "Titel: mit # Sonderzeichen"
    assert got.description == "Beschreibung\n\nzwei Absätze äöü"
    assert got.status == "open" and got.imagePath is None and got.comments == []
    assert got.createdAt.endswith("Z") and isinstance(got.createdAt, str)


def test_empty_description_and_dashes_in_body(store):
    store.add_project("A")
    t = store.add_task("A", "bug", "x")
    assert store.get_task(t.id).description == ""
    store.edit_task(t.id, description="a\n---\nb")
    assert store.get_task(t.id).description == "a\n---\nb"


def test_invalid_type_and_title(store):
    store.add_project("A")
    with pytest.raises(ValidationError):
        store.add_task("A", "epic", "x")
    with pytest.raises(ValidationError):
        store.add_task("A", "todo", "   ")
    assert list((store.root / "a" / "tasks").iterdir()) == []


def test_close_reopen_edit_comment_bump_updated(store):
    store.add_project("A")
    t = store.add_task("A", "question", "q")
    path = store.root / "a" / "tasks" / f"{t.id}.md"
    store.close_task(t.id)
    assert store.get_task(t.id).status == "closed"
    store.reopen_task(t.id)
    assert store.get_task(t.id).status == "open"
    e = store.edit_task(t.id, title="neu")
    assert e.title == "neu" and path.exists()  # Dateiname stabil
    c = store.add_comment(t.id, "hallo")
    assert [x.body for x in store.get_task(t.id).comments] == ["hallo"]
    assert c.updatedAt >= t.updatedAt
    with pytest.raises(ValidationError):
        store.add_comment(t.id, "  ")
    with pytest.raises(ValidationError):
        store.edit_task(t.id)
    with pytest.raises(NotFound):
        store.close_task("00000000-0000-4000-8000-000000000000")


def test_backup_and_no_tmp_left(store):
    store.add_project("A")
    t = store.add_task("A", "todo", "v1")
    pdir = store.root / "a"
    assert not (pdir / ".backup").exists()  # erstes Schreiben: nichts zu sichern
    store.edit_task(t.id, title="v2")
    bak = pdir / ".backup" / f"{t.id}.md.bak"
    assert "title: v1" in bak.read_text(encoding="utf-8")
    store.edit_task(t.id, title="v3")
    assert "title: v2" in bak.read_text(encoding="utf-8")  # eine Generation
    assert not list((pdir / "tasks").glob(".*.tmp"))


def test_list_filters(store):
    store.add_project("A")
    store.add_project("B")
    a1 = store.add_task("A", "todo", "a1")
    a2 = store.add_task("A", "bug", "a2")
    b1 = store.add_task("B", "todo", "b1")
    store.close_task(a1.id)
    ids = lambda ts: {t.id for t in ts}
    assert ids(store.list_tasks()) == {a1.id, a2.id, b1.id}
    assert ids(store.list_tasks(project_name="a")) == {a1.id, a2.id}
    assert ids(store.list_tasks(type="todo")) == {a1.id, b1.id}
    assert ids(store.list_tasks(status="open")) == {a2.id, b1.id}
    future = datetime.now(timezone.utc) + timedelta(days=10)
    assert ids(store.list_tasks(stale_days=7, now=future)) == {a1.id, a2.id, b1.id}
    assert store.list_tasks(stale_days=7) == []
    assert store.list_tasks(project_name="A", type="bug")[0].projectName == "A"


def test_corrupt_file_detected(store):
    store.add_project("A")
    t = store.add_task("A", "todo", "x")
    path = store.root / "a" / "tasks" / f"{t.id}.md"
    path.write_text("kaputt", encoding="utf-8")
    with pytest.raises(ValidationError):
        store.get_task(t.id)


def test_set_image(store, tmp_path):
    store.add_project("A")
    t = store.add_task("A", "todo", "x")
    img = tmp_path / "shot.PNG"
    img.write_bytes(b"png")
    got = store.set_image(t.id, img)
    assert got.imagePath == f"images/{t.id}.png"
    assert (store.root / "a" / got.imagePath).read_bytes() == b"png"
    with pytest.raises(NotFound):
        store.set_image(t.id, tmp_path / "missing.png")


def test_actor_and_labels(tmp_path, monkeypatch):
    monkeypatch.delenv("OLOOP_ACTOR", raising=False)
    monkeypatch.delenv("LAGEBERICHT_ACTOR", raising=False)
    s = Store(tmp_path, actor="user")
    s.add_project("A")
    t = s.add_task("A", "todo", "x", labels=["backend", " urgent ", "backend", ""])
    got = s.get_task(t.id)
    assert got.labels == ["backend", "urgent"]
    assert got.createdBy == got.updatedBy == "user"
    other = Store(tmp_path, actor="claude")
    e = other.edit_task(t.id, labels=["x"])
    assert e.labels == ["x"] and e.createdBy == "user" and e.updatedBy == "claude"
    assert other.add_comment(t.id, "c").updatedBy == "claude"
    assert other.edit_task(t.id, labels=[]).labels == []


def test_actor_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OLOOP_ACTOR", "envuser")
    assert Store(tmp_path).actor == "envuser"


def test_add_attachment(store, tmp_path):
    store.add_project("A")
    t = store.add_task("A", "todo", "x")
    img = tmp_path / "a.jpg"
    img.write_bytes(b"1")
    t1, l1 = store.add_attachment(t.id, img)
    _, l2 = store.add_attachment(t.id, img)
    assert l1 == f"../images/{t.id}-1.jpg" and l2 == f"../images/{t.id}-2.jpg"
    assert (store.root / "a" / "tasks" / l2).resolve().read_bytes() == b"1"
    assert t1.imagePath is None  # Hauptbild unberührt
    with pytest.raises(NotFound):
        store.add_attachment(t.id, tmp_path / "nope.jpg")
