import json

import pytest

from oloop.cli import main


@pytest.fixture
def run(tmp_path, capsys):
    def _run(*args):
        code = main(["--root", str(tmp_path), "--actor", "tester", *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return _run


def test_full_flow(run):
    assert run("project", "add", "Demo")[0] == 0
    code, out, _ = run("project", "list", "--json")
    assert json.loads(out)[0]["name"] == "Demo"

    code, out, _ = run("task", "add", "-p", "demo", "-t", "bug", "Kaputt", "-d", "Text", "-l", "a", "-l", "b")
    assert code == 0
    tid = out.split(": ")[1].strip()

    code, out, _ = run("task", "list", "--json")
    (t,) = json.loads(out)
    assert t["id"] == tid and t["labels"] == ["a", "b"] and t["createdBy"] == "tester"

    # Präfix-Auflösung
    assert run("task", "edit", tid[:8], "--title", "Neu", "-l", "z")[0] == 0
    assert run("comment", "add", tid[:8], "Notiz")[0] == 0
    assert run("task", "close", tid[:8])[0] == 0

    code, out, _ = run("task", "list", "-s", "closed")
    assert "Behoben" in out and "Neu" in out and "[z]" in out
    assert run("task", "list", "-s", "open")[1] == ""
    assert run("task", "list", "-l", "a")[1] == ""
    assert run("task", "list", "--stale-days", "1")[1] == ""

    code, out, _ = run("task", "show", tid[:8], "--json")
    d = json.loads(out)
    assert d["comments"][0]["body"] == "Notiz" and d["description"] == "Text"

    assert run("task", "reopen", tid[:8])[0] == 0
    assert run("task", "edit", tid[:8], "--clear-labels")[0] == 0
    assert json.loads(run("task", "show", tid[:8], "--json")[1])["labels"] == []
    assert "Neu" in run("task", "show", tid[:8])[1]


def test_errors(run):
    code, _, err = run("task", "list", "-p", "nope")
    assert code == 1 and err.startswith("Fehler:")
    run("project", "add", "A")
    assert run("project", "add", "a")[0] == 1
    assert run("task", "close", "ffff")[0] == 1
    assert run("task", "close", "ab")[0] == 1
    assert run("task", "add", "-p", "A", "-t", "todo", " ")[0] == 1
    with pytest.raises(SystemExit):
        run("task", "add", "-p", "A", "-t", "epic", "x")


def test_image(run, tmp_path):
    run("project", "add", "A")
    tid = run("task", "add", "-p", "A", "-t", "todo", "x")[1].split(": ")[1].strip()
    img = tmp_path / "shot.png"
    img.write_bytes(b"x")
    code, out, _ = run("task", "image", tid[:8], str(img))
    assert code == 0 and f"images/{tid}.png" in out
    code, out, _ = run("task", "image", tid[:8], str(img), "--embed")
    assert out.strip() == f"![shot.png](../images/{tid}-1.png)"
    assert run("task", "image", tid[:8], str(tmp_path / "nope.png"))[0] == 1
