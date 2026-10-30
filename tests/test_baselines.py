"""Tests for the git ours/theirs/union baselines.

Run from the repository root:

    python -m pytest tests/test_baselines.py -v
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.baselines import resolve_baseline  # noqa: E402


CONFLICT = (
    'keep\n'
    '<<<<<<< HEAD\n'
    'ours = 1\n'
    '=======\n'
    'theirs = 2\n'
    '>>>>>>> incoming\n'
    'tail\n'
)


def test_ours_keeps_current_side_and_surrounding_code():
    assert resolve_baseline(CONFLICT, 'ours') == 'keep\nours = 1\ntail\n'


def test_theirs_keeps_incoming_side_and_surrounding_code():
    assert resolve_baseline(CONFLICT, 'theirs') == 'keep\ntheirs = 2\ntail\n'


def test_union_stacks_current_side_then_incoming_side():
    assert resolve_baseline(CONFLICT, 'union') == 'keep\nours = 1\ntheirs = 2\ntail\n'


def test_empty_current_side_is_a_real_resolution():
    code = '<<<<<<< HEAD\n=======\nkept = 1\n>>>>>>> incoming\n'
    assert resolve_baseline(code, 'ours') == ''
    assert resolve_baseline(code, 'theirs') == 'kept = 1\n'
    assert resolve_baseline(code, 'union') == 'kept = 1\n'


def test_multiple_hunks_are_resolved_independently():
    code = (
        '<<<<<<< HEAD\n'
        'a = 1\n'
        '=======\n'
        'a = 2\n'
        '>>>>>>> incoming\n'
        'middle\n'
        '<<<<<<< HEAD\n'
        'b = 1\n'
        '=======\n'
        'b = 2\n'
        '>>>>>>> incoming\n'
    )
    assert resolve_baseline(code, 'ours') == 'a = 1\nmiddle\nb = 1\n'
    assert resolve_baseline(code, 'union') == 'a = 1\na = 2\nmiddle\nb = 1\nb = 2\n'


def test_equals_banners_outside_a_hunk_stay_in_place():
    code = '===============\n' + CONFLICT + '===============\n'
    resolved = resolve_baseline(code, 'ours')
    assert resolved.startswith('===============\nkeep\n')
    assert resolved.endswith('tail\n===============\n')


def test_equals_banner_inside_the_incoming_side_is_kept():
    code = (
        '<<<<<<< HEAD\n'
        'ours\n'
        '=======\n'
        '===============\n'
        'theirs\n'
        '>>>>>>> incoming\n'
    )
    assert resolve_baseline(code, 'theirs') == '===============\ntheirs\n'


def test_diff3_base_section_is_not_kept():
    code = (
        '<<<<<<< HEAD\n'
        'ours\n'
        '||||||| base\n'
        'base\n'
        '=======\n'
        'theirs\n'
        '>>>>>>> incoming\n'
    )
    assert resolve_baseline(code, 'ours') == 'ours\n'
    assert resolve_baseline(code, 'theirs') == 'theirs\n'
    assert resolve_baseline(code, 'union') == 'ours\ntheirs\n'


def test_markers_already_inside_one_side_stay_there():
    code = (
        '<<<<<<< HEAD\n'
        'left\n'
        '<<<<<<< HEAD\n'
        'inner\n'
        '=======\n'
        'inner right\n'
        '>>>>>>> inner\n'
        '=======\n'
        'right\n'
        '>>>>>>> incoming\n'
    )
    assert resolve_baseline(code, 'ours') == (
        'left\n<<<<<<< HEAD\ninner\n=======\ninner right\n>>>>>>> inner\n'
    )
    assert resolve_baseline(code, 'theirs') == 'right\n'


def test_crlf_and_missing_final_newline_are_preserved():
    code = 'keep\r\n<<<<<<< HEAD\r\nours\r\n=======\r\ntheirs\r\n>>>>>>> incoming'
    assert resolve_baseline(code, 'ours') == 'keep\r\nours\r\n'
    assert resolve_baseline(code, 'theirs') == 'keep\r\ntheirs\r\n'


@pytest.mark.parametrize('code', [
    'x = 1\n',
    '<<<<<<< HEAD\nours\n',
    '<<<<<<< HEAD\n<<<<<<< HEAD\nours\n=======\n>>>>>>> inner\n>>>>>>> outer\n',
])
def test_unusable_conflicts_are_rejected(code):
    with pytest.raises(ValueError):
        resolve_baseline(code, 'ours')


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match='Unknown baseline'):
        resolve_baseline(CONFLICT, 'merge')


def _git_merge(tmp_path, ours, base, theirs, *flags):
    folder = tmp_path / 'git'
    folder.mkdir(parents=True)
    (folder / 'ours').write_bytes(ours.encode('utf-8'))
    (folder / 'base').write_bytes(base.encode('utf-8'))
    (folder / 'theirs').write_bytes(theirs.encode('utf-8'))
    command = [
        'git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
        'merge-file', '-p', '-q', '-L', 'HEAD', '-L', 'base', '-L', 'theirs',
        *flags,
        str(folder / 'ours'), str(folder / 'base'), str(folder / 'theirs'),
    ]
    completed = subprocess.run(command, capture_output=True)
    if completed.returncode < 0 or (completed.returncode > 0 and not completed.stdout):
        raise AssertionError(completed.stderr.decode('utf-8', errors='replace') or 'git merge-file failed')
    return completed.stdout.decode('utf-8')


@pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')
@pytest.mark.parametrize('favor', ['--ours', '--theirs', '--union'])
def test_resolution_matches_git_merge_file(tmp_path, favor):
    base = 'def f():\n    return 1\n\nmiddle = 0\n\ndef g():\n    return 1\n'
    ours = 'def f():\n    return 2\n\nmiddle = 0\n\ndef g():\n    value = 1\n    return value\n'
    theirs = 'def f():\n    return 3\n\nmiddle = 0\n\ndef g():\n    return 4\n'
    conflict = _git_merge(tmp_path, ours, base, theirs)
    expected = _git_merge(tmp_path / favor.strip('-'), ours, base, theirs, favor)
    assert '<<<<<<<' in conflict
    assert resolve_baseline(conflict, favor[2:]) == expected


@pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')
def test_diff3_conflict_matches_git_union(tmp_path):
    base = 'a\nb\nc\n'
    ours = 'a\nb_ours\nc\n'
    theirs = 'a\nb_theirs\nc\n'
    conflict = _git_merge(tmp_path, ours, base, theirs, '--diff3')
    expected = _git_merge(tmp_path / 'union', ours, base, theirs, '--union')
    assert '|||||||' in conflict
    assert resolve_baseline(conflict, 'union') == expected
