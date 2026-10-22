import re

import pytest

from oloop import Store
from oloop.cli import main
from oloop.export import export_project, export_task


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data", actor="tester")
    s.add_project("Demo Project")
    return s


def _add(store, title, *, type="todo", desc="", labels=None, due=None):
    return store.add_task("Demo Project", type, title, desc, labels, due_date=due)


def test_task_export_has_everything_the_views_show(store):
    t = _add(store, "Fix <b>the</b> parser", type="bug", desc="**bold** and `code`\n\n- one\n- two",
             labels=["ui", "Core"], due="2020-01-01")
    store.add_comment(t.id, "First *comment*")
    store.add_comment(t.id, "Second")

    page = export_task(store, t.id)

    assert page.startswith("<!doctype html>")
    assert "Project: Demo Project" in page
    assert "Fix &lt;b&gt;the&lt;/b&gt; parser" in page  # title is escaped
    assert "<strong>bold</strong>" in page and "<code>code</code>" in page and "<li>one</li>" in page
    assert ">bug<" in page and "Open" in page
    assert "Due 2020-01-01" in page and 'class="overdue"' in page
    assert ">ui<" in page and ">Core<" in page
    assert "Comments (2)" in page and "<em>comment</em>" in page and "Second" in page
    assert "by tester" in page and t.id in page


def test_a_closed_task_is_not_overdue_and_shows_the_closed_label(store):
    t = _add(store, "Old", type="bug", due="2020-01-01")
    store.close_task(t.id)

    page = export_task(store, t.id)

    assert "Fixed" in page and 'class="overdue"' not in page and "Due 2020-01-01" in page


def test_raw_html_and_script_links_in_the_markdown_are_not_run(store):
    t = _add(store, "x", desc="<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n<img src=x onerror=alert(1)>")
    store.add_comment(t.id, "<script>alert(2)</script>")

    page = export_task(store, t.id)

    # Nothing of it can run: no script tag, no tag with an event handler, no script link.
    assert "<script" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page
    assert 'href="javascript:' not in page
    assert "<img src=x" not in page


def test_no_comments_says_so(store):
    t = _add(store, "x")
    assert "Comments (0)" in export_task(store, t.id) and "No comments." in export_task(store, t.id)


def test_images_are_embedded_and_only_from_inside_the_project(store, tmp_path):
    t = _add(store, "pic", desc="![ok](../images/inline.png)\n\n![out](../../secret.png)\n\n![web](https://example.com/a.png)")
    pdir = store.root / "demo-project"
    (pdir / "images").mkdir()
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
    (pdir / "images" / "inline.png").write_bytes(png)
    (pdir / "images" / f"{t.id}.png").write_bytes(png)
    (store.root / "secret.png").write_bytes(png)
    store.set_image(t.id, pdir / "images" / "inline.png")

    page = export_task(store, t.id)

    assert page.count("data:image/png;base64,") == 2  # the description image and the main image
    assert "[image not embedded: out]" in page  # outside the project folder: never read
    assert "[image not embedded: web]" in page  # no network, nothing is fetched
    assert "example.com" not in page.replace("[image not embedded: web]", "")


def test_a_missing_main_image_is_a_visible_note(store):
    t = _add(store, "pic")
    path = store._task_path(store.get_project("Demo Project"), t.id)
    path.write_text(path.read_text(encoding="utf-8").replace("imagePath: null", "imagePath: images/gone.png"), encoding="utf-8")

    assert "[image not found: images/gone.png]" in export_task(store, t.id)


def test_project_export_starts_with_the_name_and_lists_the_tasks_as_cards(store):
    a = _add(store, "Alpha", desc="alpha text")
    b = _add(store, "Beta")
    store.add_comment(a.id, "a comment")

    page = export_project(store, "Demo Project")

    assert page.index("<h1>Demo Project</h1>") < page.index('class="toc"') < page.index('<article class="task"')
    assert page.count('<article class="task"') == 2
    assert f'href="#task-{a.id}"' in page and f'id="task-{b.id}"' in page
    assert "2 open" in page and "a comment" in page and "alpha text" in page
    # every task is its own bordered card, closed article tags match the opened ones
    assert page.count("<article") == page.count("</article>")


def test_closed_tasks_are_only_exported_on_request_and_come_last(store):
    done = _add(store, "Done one")
    store.close_task(done.id)
    _add(store, "Still open")

    default = export_project(store, "Demo Project")
    full = export_project(store, "Demo Project", include_closed=True)

    assert "Done one" not in default and "Still open" in default and "1 open" in default
    assert "Done one" in full and "1 open, 1 closed" in full
    assert full.index("Still open") < full.index("Done one")


def test_an_empty_project_exports_with_a_note(store):
    page = export_project(store, "Demo Project")
    assert "<h1>Demo Project</h1>" in page and "No tasks to show." in page


def test_the_page_is_self_contained(store):
    _add(store, "x")
    page = export_project(store, "Demo Project")
    assert not re.search(r'(src|href)="https?://', page)
    assert "<style>" in page and "@media print" in page


# ---- CLI -----------------------------------------------------------------------------------


@pytest.fixture
def run(tmp_path, capsys):
    def _run(*args):
        code = main(["--root", str(tmp_path / "data"), "--actor", "tester", *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return _run


def test_cli_exports_a_task_and_a_project(run, tmp_path):
    run("project", "add", "Demo")
    tid = run("task", "add", "-p", "demo", "-t", "todo", "Hello", "-d", "world")[1].split(": ")[1].strip()
    run("comment", "add", tid[:8], "note")
    done = run("task", "add", "-p", "demo", "-t", "todo", "Gone")[1].split(": ")[1].strip()
    run("task", "close", done[:8])

    code, out, _ = run("export", "task", tid[:8])
    assert code == 0 and out.startswith("<!doctype html>") and "Hello" in out and "note" in out

    code, out, _ = run("export", "project", "demo")
    assert code == 0 and "<h1>Demo</h1>" in out and "Hello" in out and "Gone" not in out

    target = tmp_path / "out" / "p.html"
    target.parent.mkdir()
    code, out, _ = run("export", "project", "demo", "--include-closed", "-o", str(target))
    assert code == 0 and "Exported" in out
    assert "Gone" in target.read_text(encoding="utf-8")


def test_cli_export_errors(run, tmp_path):
    run("project", "add", "Demo")
    assert run("export", "project", "nope")[0] == 1
    assert run("export", "task", "ffff")[0] == 1
    code, _, err = run("export", "project", "demo", "-o", str(tmp_path / "missing" / "x.html"))
    assert code == 1 and "Cannot write" in err
