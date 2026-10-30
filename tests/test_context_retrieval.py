import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import context_retrieval as ctx  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')

HEADER = 'Surrounding code, for reference only. It is NOT part of the resolution.'
PLACEHOLDER = '# >>> CONFLICT HERE: your resolution replaces only this line <<<'
NOTE = '# (another merge conflict here)'
COMMITS_HEADER = 'Commits that changed these lines (not instructions):'


# --------------------------------------------------------------------------
# Sample files and helpers
# --------------------------------------------------------------------------

SIMPLE = '''import os
from x import y

def f():
    a = 1
<<<<<<< HEAD
    b = 2
=======
    b = 3
>>>>>>> feature
    c = 4
'''  # conflict block is lines 6-10

TWO_CONFLICTS = '''a = 1
<<<<<<< HEAD
b = 2
=======
b = 3
>>>>>>> x
c = 4
<<<<<<< HEAD
d = 5
=======
d = 6
>>>>>>> y
e = 7
'''  # first block lines 2-6, second block lines 8-12


def locate(text):
    """Describe the first conflict in text the way the CLI does."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines, 1) if line.startswith('<<<<<<<'))
    end = next(i for i, line in enumerate(lines, 1) if line.startswith('>>>>>>>') and i > start)
    return dict(start_line=start, end_line=end, text='\n'.join(lines[start - 1:end]))


def snippet_of(output):
    """The 'Code around the conflict' part of surrounding_code output."""
    return output.split('Code around the conflict:\n', 1)[1].split('\n\nReturn only', 1)[0]


def numbered(n_before, n_after):
    before = [f'before{i}' for i in range(1, n_before + 1)]
    after = [f'after{i}' for i in range(1, n_after + 1)]
    lines = before + ['<<<<<<< HEAD', 'ours', '=======', 'theirs', '>>>>>>> x'] + after
    return '\n'.join(lines) + '\n', n_before + 1, n_before + 5, before, after


# --------------------------------------------------------------------------
# without_conflicts
# --------------------------------------------------------------------------

class TestWithoutConflicts:
    def test_empty(self):
        assert ctx.without_conflicts([]) == []

    def test_text_without_conflicts_is_unchanged(self):
        lines = ['a = 1', 'b = 2']
        assert ctx.without_conflicts(lines) == lines

    def test_block_replaced_by_one_note(self):
        lines = ['a', '<<<<<<< HEAD', 'ours', '=======', 'theirs', '>>>>>>> x', 'b']
        assert ctx.without_conflicts(lines) == ['a', NOTE, 'b']

    def test_diff3_base_section_is_dropped_too(self):
        lines = ['a', '<<<<<<< HEAD', 'ours', '||||||| base', 'old', '=======', 'theirs', '>>>>>>> x', 'b']
        assert ctx.without_conflicts(lines) == ['a', NOTE, 'b']

    def test_each_block_gets_its_own_note(self):
        result = ctx.without_conflicts(TWO_CONFLICTS.splitlines())
        assert result == ['a = 1', NOTE, 'c = 4', NOTE, 'e = 7']

    def test_unterminated_block_drops_the_rest(self):
        assert ctx.without_conflicts(['a', '<<<<<<< HEAD', 'b', 'c']) == ['a', NOTE]

    def test_does_not_modify_input(self):
        lines = ['a', '<<<<<<< HEAD', 'x', '>>>>>>> y']
        ctx.without_conflicts(lines)
        assert lines == ['a', '<<<<<<< HEAD', 'x', '>>>>>>> y']


# --------------------------------------------------------------------------
# surrounding_code
# --------------------------------------------------------------------------

class TestSurroundingCode:
    def test_overall_structure(self):
        out = ctx.surrounding_code(SIMPLE, 6, 10)
        assert out.startswith(HEADER)
        assert 'The conflict is inside: def f():' in out
        assert 'Imports in this file:\nimport os\nfrom x import y' in out
        assert out.rstrip().endswith('such as def or class lines.')
        assert 'Return only the code that replaces the conflict block' in out

    def test_snippet_has_placeholder_between_before_and_after(self):
        expected = ('import os\nfrom x import y\n\ndef f():\n    a = 1\n'
                    f'    {PLACEHOLDER}\n    c = 4')
        assert snippet_of(ctx.surrounding_code(SIMPLE, 6, 10)) == expected

    def test_placeholder_uses_the_conflict_indentation(self):
        assert f'    {PLACEHOLDER}' in ctx.surrounding_code(SIMPLE, 6, 10)

    def test_conflict_lines_and_markers_are_not_shown(self):
        out = ctx.surrounding_code(SIMPLE, 6, 10)
        for text in ('b = 2', 'b = 3', '<<<<<<<', '=======', '>>>>>>>'):
            assert text not in out

    def test_n_limits_lines_on_each_side(self):
        text, start, end, before, after = numbered(30, 30)
        lines = snippet_of(ctx.surrounding_code(text, start, end, n=5)).splitlines()
        assert lines == before[-5:] + [PLACEHOLDER] + after[:5]

    def test_default_is_fifteen_lines_each_side(self):
        text, start, end, _, _ = numbered(30, 30)
        assert len(snippet_of(ctx.surrounding_code(text, start, end)).splitlines()) == 15 + 1 + 15

    def test_fewer_lines_available_than_n(self):
        text, start, end, before, after = numbered(2, 1)
        lines = snippet_of(ctx.surrounding_code(text, start, end)).splitlines()
        assert lines == before + [PLACEHOLDER] + after

    def test_conflict_on_first_line_has_nothing_before(self):
        text = '<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> x\nafter\n'
        assert snippet_of(ctx.surrounding_code(text, 1, 5)) == f'{PLACEHOLDER}\nafter'

    def test_conflict_at_end_of_file_has_nothing_after(self):
        text = 'before\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> x\n'
        assert snippet_of(ctx.surrounding_code(text, 2, 6)) == f'before\n{PLACEHOLDER}'

    def test_top_level_conflict_has_no_header_or_imports_sections(self):
        text, start, end, _, _ = numbered(3, 3)
        out = ctx.surrounding_code(text, start, end)
        assert 'The conflict is inside' not in out
        assert 'Imports in this file' not in out

    def test_other_conflicts_are_replaced_by_a_note(self):
        out = ctx.surrounding_code(TWO_CONFLICTS, 8, 12)
        assert snippet_of(out) == f'a = 1\n{NOTE}\nc = 4\n{PLACEHOLDER}\ne = 7'
        for text in ('b = 2', 'b = 3', 'd = 5', '<<<<<<<', '>>>>>>>'):
            assert text not in out

    def test_other_conflict_after_the_block_is_replaced_too(self):
        out = ctx.surrounding_code(TWO_CONFLICTS, 2, 6)
        assert snippet_of(out) == f'a = 1\n{PLACEHOLDER}\nc = 4\n{NOTE}\ne = 7'

    # --- enclosing def/class header ---

    def test_header_is_nearest_enclosing_method_not_class_or_sibling(self):
        text = ('class C:\n    def a(self):\n        pass\n\n    def b(self):\n'
                '<<<<<<< HEAD\n        x = 1\n=======\n        x = 2\n>>>>>>> f\n        return x\n')
        assert 'The conflict is inside: def b(self):' in ctx.surrounding_code(text, 6, 10)

    def test_class_level_conflict_picks_the_class(self):
        text = ('class C:\n    def a(self):\n        pass\n'
                '<<<<<<< HEAD\n    x = 1\n=======\n    x = 2\n>>>>>>> f\n')
        assert 'The conflict is inside: class C:' in ctx.surrounding_code(text, 4, 8)

    def test_async_def_is_recognised(self):
        text = 'async def fetch():\n    a = 1\n<<<<<<< HEAD\n    b = 1\n=======\n    b = 2\n>>>>>>> f\n'
        assert 'The conflict is inside: async def fetch():' in ctx.surrounding_code(text, 3, 7)

    def test_names_that_merely_start_with_def_are_not_headers(self):
        text = 'define = 1\n<<<<<<< HEAD\n    x = 1\n=======\n    x = 2\n>>>>>>> f\n'
        assert 'The conflict is inside' not in ctx.surrounding_code(text, 2, 6)

    def test_def_below_the_conflict_is_not_a_header(self):
        text = '<<<<<<< HEAD\n    x = 1\n=======\n    x = 2\n>>>>>>> f\ndef later():\n    pass\n'
        assert 'The conflict is inside' not in ctx.surrounding_code(text, 1, 5)

    # --- imports ---

    def test_only_imports_above_the_conflict_are_listed(self):
        text = 'import os\n<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> f\nimport json\n'
        out = ctx.surrounding_code(text, 2, 6)
        assert 'Imports in this file:\nimport os\n\n' in out
        assert 'import json' not in out.split('Code around the conflict:')[0]

    def test_indented_imports_are_not_listed(self):
        text = 'def f():\n    import json\n<<<<<<< HEAD\n    x = 1\n=======\n    x = 2\n>>>>>>> f\n'
        assert 'Imports in this file' not in ctx.surrounding_code(text, 3, 7)

    def test_at_most_twenty_imports(self):
        imports = [f'import mod{i}' for i in range(25)]
        text = '\n'.join(imports) + '\n<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> f\n'
        out = ctx.surrounding_code(text, 26, 30)
        section = out.split('Imports in this file:\n', 1)[1].split('\n\nCode around', 1)[0]
        assert section.splitlines() == imports[:20]

    def test_from_imports_are_listed(self):
        text = 'from pathlib import Path\n<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> f\n'
        assert 'from pathlib import Path' in ctx.surrounding_code(text, 2, 6)


# --------------------------------------------------------------------------
# git helper
# --------------------------------------------------------------------------

@requires_git
class TestGit:
    def test_returns_stdout(self, tmp_path):
        assert ctx.git(['--version'], tmp_path).startswith('git version')

    def test_failure_returns_empty_string(self, tmp_path):
        assert ctx.git(['rev-parse', '--verify', 'no-such-ref'], tmp_path) == ''


# --------------------------------------------------------------------------
# side_ranges
# --------------------------------------------------------------------------

class TestSideRanges:
    def test_no_conflict(self):
        assert ctx.side_ranges('a\nb\nc\n', 1) == (None, None)

    def test_single_conflict(self):
        text = 'l1\n<<<<<<< HEAD\nours1\nours2\n=======\ntheirs1\n>>>>>>> b\nl8\n'
        assert ctx.side_ranges(text, 2) == ((2, 3), (2, 2))

    def test_empty_ours_side(self):
        text = 'l1\n<<<<<<< HEAD\n=======\ntheirs1\ntheirs2\n>>>>>>> b\n'
        assert ctx.side_ranges(text, 2) == (None, (2, 3))

    def test_empty_theirs_side(self):
        text = 'l1\n<<<<<<< HEAD\nours1\n=======\n>>>>>>> b\n'
        assert ctx.side_ranges(text, 2) == ((2, 2), None)

    def test_both_sides_empty(self):
        assert ctx.side_ranges('l1\n<<<<<<< HEAD\n=======\n>>>>>>> b\n', 2) == (None, None)

    def test_diff3_base_lines_count_for_neither_side(self):
        text = 'l1\n<<<<<<< HEAD\nours1\n||||||| base\nold1\nold2\n=======\ntheirs1\n>>>>>>> b\nl10\n'
        assert ctx.side_ranges(text, 2) == ((2, 2), (2, 2))

    # First block: ours has 1 line, theirs has 2. Second block: 1 line each.
    UNEVEN = ('a\n<<<<<<< HEAD\no1\n=======\nt1\nt2\n>>>>>>> x\nb\n'
              '<<<<<<< HEAD\no2\n=======\nt3\n>>>>>>> y\n')

    def test_first_conflict_selected_by_start_line(self):
        assert ctx.side_ranges(self.UNEVEN, 2) == ((2, 2), (2, 3))

    def test_second_conflict_counts_lines_of_the_first_in_each_version(self):
        # HEAD version is a, o1, b, o2   -> o2 is line 4.
        # Incoming version is a, t1, t2, b, t3 -> t3 is line 5.
        assert ctx.side_ranges(self.UNEVEN, 9) == ((4, 4), (5, 5))

    def test_equal_sized_blocks_give_equal_ranges(self):
        assert ctx.side_ranges(TWO_CONFLICTS, 2) == ((2, 2), (2, 2))
        assert ctx.side_ranges(TWO_CONFLICTS, 8) == ((4, 4), (4, 4))

    def test_start_line_past_every_conflict(self):
        assert ctx.side_ranges(TWO_CONFLICTS, 13) == (None, None)


# --------------------------------------------------------------------------
# Real git repositories
# --------------------------------------------------------------------------

BASE_APP = ('def compute():\n    value = 1\n    pad_a = 0\n    pad_b = 0\n'
            '    pad_c = 0\n    other = 10\n    return value + other\n')


def git_run(cwd, *args, check=True):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    return subprocess.run(
        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
         '-c', 'commit.gpgsign=false', *args],
        cwd=cwd, check=check, capture_output=True, text=True, env=env)


def commit_all(repo, message):
    git_run(repo, 'add', '-A')
    git_run(repo, 'commit', '-q', '-m', message)


def make_repo(tmp_path, head_values=(('2', 'raise value to two'),), merge=True):
    """A repo where main and feature both changed `value` (line 2 of app.py).

    main: one commit per entry in head_values, then 'bump other to twenty'
    (line 6, which merges cleanly). feature: 'set value to three'.
    Returns (repo, app.py path). With merge=True, `git merge feature` is left
    stopped on the conflict.
    """
    repo = tmp_path / 'repo'
    repo.mkdir()
    app = repo / 'app.py'
    git_run(repo, 'init', '-q')
    git_run(repo, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    app.write_text(BASE_APP)
    commit_all(repo, 'initial app')
    git_run(repo, 'checkout', '-q', '-b', 'feature')
    app.write_text(BASE_APP.replace('value = 1', 'value = 3'))
    commit_all(repo, 'set value to three')
    git_run(repo, 'checkout', '-q', 'main')
    for value, message in head_values:
        app.write_text(re.sub(r'value = \d+', f'value = {value}', app.read_text(), count=1))
        commit_all(repo, message)
    app.write_text(app.read_text().replace('other = 10', 'other = 20'))
    commit_all(repo, 'bump other to twenty')
    if merge:
        git_run(repo, 'merge', '--no-edit', '-q', 'feature', check=False)
    return repo, app


def merge_base(repo):
    return git_run(repo, 'merge-base', 'HEAD', 'MERGE_HEAD').stdout.strip()


@requires_git
class TestRealMergeFixture:
    """Sanity checks that the fixture really produces the conflict the tests rely on."""

    def test_merge_leaves_markers_on_line_two(self, tmp_path):
        _, app = make_repo(tmp_path)
        text = app.read_text()
        assert locate(text)['start_line'] == 2
        assert 'value = 2' in text and 'value = 3' in text and 'other = 20' in text

    def test_side_ranges_on_real_file(self, tmp_path):
        _, app = make_repo(tmp_path)
        assert ctx.side_ranges(app.read_text(), 2) == ((2, 2), (2, 2))


# --------------------------------------------------------------------------
# commits_for
# --------------------------------------------------------------------------

@requires_git
class TestCommitsFor:
    def test_line_range_keeps_only_commits_that_touched_those_lines(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        subjects = ctx.commits_for(str(repo), 'app.py', (2, 2), f'{merge_base(repo)}..HEAD')
        assert len(subjects) == 1
        assert re.fullmatch(r'[0-9a-f]+ raise value to two', subjects[0])

    def test_incoming_side(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        subjects = ctx.commits_for(str(repo), 'app.py', (2, 2), f'{merge_base(repo)}..MERGE_HEAD')
        assert [s.split(' ', 1)[1] for s in subjects] == ['set value to three']

    def test_without_line_range_uses_every_commit_touching_the_file(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        subjects = ctx.commits_for(str(repo), 'app.py', None, f'{merge_base(repo)}..HEAD')
        assert [s.split(' ', 1)[1] for s in subjects] == ['bump other to twenty', 'raise value to two']

    def test_line_range_with_no_commits_falls_back_to_the_file(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        subjects = ctx.commits_for(str(repo), 'app.py', (3, 3), f'{merge_base(repo)}..HEAD')
        assert [s.split(' ', 1)[1] for s in subjects] == ['bump other to twenty', 'raise value to two']

    def test_limit(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        subjects = ctx.commits_for(str(repo), 'app.py', None, f'{merge_base(repo)}..HEAD', limit=1)
        assert [s.split(' ', 1)[1] for s in subjects] == ['bump other to twenty']

    def test_empty_revision_range(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        base = merge_base(repo)
        assert ctx.commits_for(str(repo), 'app.py', (2, 2), f'{base}..{base}') == []

    def test_outside_a_repository(self, tmp_path):
        assert ctx.commits_for(str(tmp_path), 'app.py', (1, 1), 'HEAD~1..HEAD') == []


# --------------------------------------------------------------------------
# conflict_commits
# --------------------------------------------------------------------------

@requires_git
class TestConflictCommits:
    def test_commits_grouped_by_side(self, tmp_path):
        repo, app = make_repo(tmp_path)
        text = app.read_text()
        result = ctx.conflict_commits(str(app), text, locate(text))
        assert list(result) == ['current (HEAD)', 'incoming (MERGE_HEAD)']
        assert [s.split(' ', 1)[1] for s in result['current (HEAD)']] == ['raise value to two']
        assert [s.split(' ', 1)[1] for s in result['incoming (MERGE_HEAD)']] == ['set value to three']

    def test_unrelated_commits_are_left_out(self, tmp_path):
        _, app = make_repo(tmp_path)
        text = app.read_text()
        result = ctx.conflict_commits(str(app), text, locate(text))
        assert not any('bump other' in s for subjects in result.values() for s in subjects)

    def test_top_k_limits_each_side(self, tmp_path):
        _, app = make_repo(tmp_path, head_values=(('2', 'raise value to two'),
                                                  ('4', 'raise value to four')))
        text = app.read_text()
        conflict = locate(text)
        both = ctx.conflict_commits(str(app), text, conflict)
        one = ctx.conflict_commits(str(app), text, conflict, top_k=1)
        assert len(both['current (HEAD)']) == 2
        assert len(one['current (HEAD)']) == 1
        assert 'raise value to four' in one['current (HEAD)'][0]  # newest wins the tie

    def test_not_in_a_git_repository(self, tmp_path):
        path = tmp_path / 'plain.py'
        path.write_text(SIMPLE)
        assert ctx.conflict_commits(str(path), SIMPLE, locate(SIMPLE)) == {}

    def test_repository_without_a_merge_in_progress(self, tmp_path):
        _, app = make_repo(tmp_path, merge=False)
        text = SIMPLE  # no merge, so the text is irrelevant
        assert ctx.conflict_commits(str(app), text, locate(text)) == {}

    def test_works_through_a_symlinked_folder(self, tmp_path):
        repo, app = make_repo(tmp_path)
        link = tmp_path / 'link'
        link.symlink_to(repo, target_is_directory=True)
        text = app.read_text()
        conflict = locate(text)
        through_link = ctx.conflict_commits(str(link / 'app.py'), text, conflict)
        assert through_link == ctx.conflict_commits(str(app), text, conflict)
        assert all(through_link.values())  # both sides found commits

    def test_works_with_a_relative_path(self, tmp_path, monkeypatch):
        repo, app = make_repo(tmp_path)
        text = app.read_text()
        monkeypatch.chdir(repo)
        result = ctx.conflict_commits('app.py', text, locate(text))
        assert all(result.values())


# --------------------------------------------------------------------------
# get_context
# --------------------------------------------------------------------------

class TestGetContextFormatting:
    """Uses a stub for conflict_commits, so git is not needed."""

    def test_outside_a_merge_only_surrounding_code(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ctx, 'conflict_commits', lambda *a, **k: {})
        conflict = locate(SIMPLE)
        out = ctx.get_context(str(tmp_path / 'f.py'), SIMPLE, conflict)
        assert out == ctx.surrounding_code(SIMPLE, conflict['start_line'], conflict['end_line'])

    def test_sides_with_no_commits_add_no_section(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ctx, 'conflict_commits', lambda *a, **k: {'a': [], 'b': []})
        out = ctx.get_context(str(tmp_path / 'f.py'), SIMPLE, locate(SIMPLE))
        assert COMMITS_HEADER not in out

    def test_commit_section_format(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ctx, 'conflict_commits',
                            lambda *a, **k: {'current (HEAD)': ['abc fix one', 'def fix two'],
                                             'incoming (MERGE_HEAD)': []})
        conflict = locate(SIMPLE)
        out = ctx.get_context(str(tmp_path / 'f.py'), SIMPLE, conflict)
        surrounding = ctx.surrounding_code(SIMPLE, conflict['start_line'], conflict['end_line'])
        assert out == (f'{surrounding}\n\n{COMMITS_HEADER}\n'
                       'current (HEAD): abc fix one; def fix two\n'
                       'incoming (MERGE_HEAD): ')

    def test_passes_file_text_and_conflict_through(self, monkeypatch, tmp_path):
        seen = {}

        def fake(file_path, text, conflict, top_k=3):
            seen.update(file_path=file_path, text=text, conflict=conflict)
            return {}

        monkeypatch.setattr(ctx, 'conflict_commits', fake)
        conflict = locate(SIMPLE)
        ctx.get_context('some/file.py', SIMPLE, conflict)
        assert seen == dict(file_path='some/file.py', text=SIMPLE, conflict=conflict)


@requires_git
class TestGetContextWithGit:
    def test_includes_commits_from_both_sides(self, tmp_path):
        _, app = make_repo(tmp_path)
        text = app.read_text()
        out = ctx.get_context(str(app), text, locate(text))
        assert out.startswith(HEADER)
        assert COMMITS_HEADER in out
        commits = out.split(COMMITS_HEADER + '\n', 1)[1].splitlines()
        assert commits[0].startswith('current (HEAD): ') and 'raise value to two' in commits[0]
        assert commits[1].startswith('incoming (MERGE_HEAD): ') and 'set value to three' in commits[1]
        assert 'bump other' not in out

    def test_plain_file_gets_surrounding_code_only(self, tmp_path):
        path = tmp_path / 'plain.py'
        path.write_text(SIMPLE)
        out = ctx.get_context(str(path), SIMPLE, locate(SIMPLE))
        assert out.startswith(HEADER)
        assert COMMITS_HEADER not in out