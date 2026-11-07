"""Tests for review & apply in src/cli.py. 
    python -m pytest tests/test_cli.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import cli  # noqa: E402

CONFLICTED = """def greet():
    return 1


def total():
<<<<<<< HEAD
    return 2
=======
    return 3
>>>>>>> feature


def last():
<<<<<<< HEAD
    x = 'a'
=======
    x = 'b'
>>>>>>> feature
    return x
"""


@pytest.fixture
def conflicted(tmp_path, monkeypatch):
    path = tmp_path / "app.py"
    path.write_text(CONFLICTED, encoding="utf-8")

    def fake_resolve(conflict, context="", repair=True):
        # Keep the incoming side, without indentation, like the model often does.
        incoming = conflict.split("=======\n")[1].split(">>>>>>>")[0]
        return {"resolution": incoming.strip(), "explanation": "kept incoming",
                "assumptions": ""}

    monkeypatch.setattr(cli, "resolve_conflict", fake_resolve)
    monkeypatch.setattr(cli, "get_context", None)
    return path


def answer(monkeypatch, *replies):
    replies = iter(replies)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))


def test_accept_all_writes_file(conflicted, monkeypatch):
    answer(monkeypatch, "a", "a")
    assert cli.main([str(conflicted)]) == 0
    text = conflicted.read_text(encoding="utf-8")
    assert "<<<<<<<" not in text
    assert "    return 3\n" in text
    assert "    x = 'b'\n    return x\n" in text


def test_reject_all_leaves_file_unchanged(conflicted, monkeypatch):
    answer(monkeypatch, "r", "r")
    assert cli.main([str(conflicted)]) == 0
    assert conflicted.read_text(encoding="utf-8") == CONFLICTED


def test_accept_one_reject_one(conflicted, monkeypatch):
    answer(monkeypatch, "a", "r")
    cli.main([str(conflicted)])
    text = conflicted.read_text(encoding="utf-8")
    assert "    return 3\n" in text
    assert "<<<<<<< HEAD\n    x = 'a'\n" in text


def test_edit_uses_edited_text(conflicted, monkeypatch):
    answer(monkeypatch, "e", "a", "r")
    monkeypatch.setattr(cli, "edit_in_editor", lambda text, path: "    return 4\n")
    cli.main([str(conflicted)])
    assert "    return 4\n" in conflicted.read_text(encoding="utf-8")


def test_no_input_rejects(conflicted, monkeypatch):
    def no_input(_prompt):
        raise EOFError
    monkeypatch.setattr("builtins.input", no_input)
    cli.main([str(conflicted)])
    assert conflicted.read_text(encoding="utf-8") == CONFLICTED


def test_keeps_crlf_line_endings(conflicted, monkeypatch):
    conflicted.write_bytes(CONFLICTED.replace("\n", "\r\n").encode())
    answer(monkeypatch, "a", "a")
    cli.main([str(conflicted)])
    data = conflicted.read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
