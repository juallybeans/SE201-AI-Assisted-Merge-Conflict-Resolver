"""Tests for src/hunks.py.

Place at tests/test_hunks.py and run from the repository root:

    python -m pytest tests/test_hunks.py -v

Two tests are marked xfail: they describe known bugs in fit() (tab indentation
and diff3 base lines). They show as "xfailed" now and as "xpassed" once the bugs
are fixed; at that point delete the xfail marker.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import hunks as hk  # noqa: E402
from src.context_retrieval import surrounding_code  # noqa: E402

NOTE = '# (another merge conflict here)'


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def block(ours, theirs, base=None):
    """Text of one conflict block; sides are lists of lines (indentation included)."""
    text = '<<<<<<< HEAD\n' + ''.join(line + '\n' for line in ours)
    if base is not None:
        text += '||||||| base\n' + ''.join(line + '\n' for line in base)
    return text + '=======\n' + ''.join(line + '\n' for line in theirs) + '>>>>>>> feature\n'


def hunk_lines(ours, theirs, base=None):
    return block(ours, theirs, base).splitlines(keepends=True)


def no_context(_block):
    return ''


class FakeClient:
    """Stands in for the Ollama client; answers and repairs are consumed in order.

    An Exception instance in answers/repairs is raised instead of returned.
    """

    def __init__(self, answers, repairs=(), with_repair=True):
        self.answers = list(answers)
        self.repairs = list(repairs)
        self.calls = []
        self.repair_calls = []
        if with_repair:
            self.repair_resolution = self._repair

    def resolve_conflict(self, conflict, context, timeout, repair=True):
        self.calls.append(dict(conflict=conflict, context=context, timeout=timeout, repair=repair))
        answer = self.answers[len(self.calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _repair(self, block_text, context, resolution, message, timeout):
        self.repair_calls.append(dict(block=block_text, context=context, resolution=resolution,
                                      message=message, timeout=timeout))
        result = self.repairs[len(self.repair_calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result


BLOCK_1 = block(['x = 1'], ['x = 2'])
FILE_1 = 'import os\n' + BLOCK_1 + 'print(x)\n'          # block on lines 2-6 (1-based)

BLOCK_A = block(['b = 1'], ['b = 2'])
BLOCK_B = block(['d = 1'], ['d = 2'])
FILE_2 = 'a = 1\n' + BLOCK_A + 'c = 3\n' + BLOCK_B + 'e = 5\n'


# --------------------------------------------------------------------------
# find_hunks
# --------------------------------------------------------------------------

class TestFindHunks:
    def test_single_block(self):
        assert hk.find_hunks(FILE_1) == [(1, 5)]

    def test_block_at_start_and_end_of_file(self):
        assert hk.find_hunks(block(['a'], ['b'])) == [(0, 4)]

    def test_last_marker_without_trailing_newline(self):
        assert hk.find_hunks(block(['a'], ['b']).rstrip('\n')) == [(0, 4)]

    def test_multiple_blocks_in_order(self):
        text = 'a\n' + block(['b'], ['c']) + 'd\n' + block(['e', 'e2'], ['f']) + 'g\n'
        assert hk.find_hunks(text) == [(1, 5), (7, 12)]

    def test_diff3_block_runs_to_the_closing_marker(self):
        assert hk.find_hunks(block(['o'], ['t'], base=['b'])) == [(0, 6)]

    def test_empty_sides(self):
        assert hk.find_hunks(block([], [])) == [(0, 2)]

    def test_each_hunk_starts_and_ends_on_markers(self):
        text = FILE_2
        lines = text.splitlines(keepends=True)
        for start, end in hk.find_hunks(text):
            assert lines[start].startswith('<<<<<<<')
            assert lines[end].startswith('>>>>>>>')

    def test_hunks_do_not_overlap(self):
        spans = hk.find_hunks(FILE_2)
        assert all(a_end < b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))

    def test_crlf_line_endings(self):
        text = block(['a'], ['b']).replace('\n', '\r\n')
        assert hk.find_hunks('x\r\n' + text + 'y\r\n') == [(1, 5)]

    def test_nested_conflict_is_one_hunk_ending_at_the_outer_marker(self):
        text = ('<<<<<<< a\nx\n<<<<<<< b\ny\n=======\nz\n>>>>>>> b\n'
                '=======\nw\n>>>>>>> a\ntail\n')
        assert hk.find_hunks(text) == [(0, 9)]

    def test_block_missing_its_separator_raises(self):
        with pytest.raises(ValueError, match='separator'):
            hk.find_hunks('<<<<<<< HEAD\nx\n>>>>>>> b\n')

    def test_unclosed_block_raises(self):
        with pytest.raises(ValueError, match='Unclosed conflict marker'):
            hk.find_hunks('a\n<<<<<<< HEAD\nx\n=======\ny\n')

    @pytest.mark.parametrize('text', ['', 'x = 1\ny = 2\n'])
    def test_no_markers_raises(self, text):
        with pytest.raises(ValueError, match='No conflict markers found'):
            hk.find_hunks(text)

    @pytest.mark.parametrize('line', ['x = "<<<<<<<"\n', '<<<<<< HEAD\n', '    <<<<<<< HEAD\n',
                                      '<<<<<<<HEAD\n'])
    def test_lookalikes_are_not_hunks(self, line):
        with pytest.raises(ValueError, match='No conflict markers found'):
            hk.find_hunks('a = 1\n' + line + 'b = 2\n')


# --------------------------------------------------------------------------
# fit
# --------------------------------------------------------------------------

class TestFit:
    def test_top_level_gets_no_indent_and_a_newline(self):
        assert hk.fit('x = 3', hunk_lines(['x = 1'], ['x = 2'])) == 'x = 3\n'

    def test_restores_the_indentation_the_model_stripped(self):
        lines = hunk_lines(['    x = 1'], ['    x = 2'])
        assert hk.fit('x = 3', lines) == '    x = 3\n'

    def test_keeps_relative_indentation_of_multi_line_answers(self):
        lines = hunk_lines(['    x = 1'], ['    x = 2'])
        assert hk.fit('if x:\n    y = 1', lines) == '    if x:\n        y = 1\n'

    def test_already_indented_answer_is_not_indented_twice(self):
        lines = hunk_lines(['    x = 1'], ['    x = 2'])
        assert hk.fit('    x = 3', lines) == '    x = 3\n'

    def test_over_indented_answer_is_brought_back_to_the_block_indent(self):
        lines = hunk_lines(['    x = 1'], ['    x = 2'])
        assert hk.fit('        x = 3', lines) == '    x = 3\n'

    def test_uses_the_smaller_indent_of_the_two_sides(self):
        lines = hunk_lines(['        a'], ['    b'])
        assert hk.fit('x = 3', lines) == '    x = 3\n'

    def test_blank_lines_inside_the_sides_are_ignored_for_indent(self):
        lines = hunk_lines(['    a', '', '    b'], ['    c'])
        assert hk.fit('x = 3', lines) == '    x = 3\n'

    def test_both_sides_empty_means_no_indent(self):
        assert hk.fit('x = 3', hunk_lines([], [])) == 'x = 3\n'

    def test_diff3_marker_line_does_not_reset_indent_to_zero(self):
        lines = hunk_lines(['    a'], ['    b'], base=['    o'])
        assert hk.fit('x = 3', lines) == '    x = 3\n'

    def test_surrounding_blank_lines_are_stripped(self):
        assert hk.fit('\n\nx = 3\n\n', hunk_lines(['x = 1'], ['x = 2'])) == 'x = 3\n'

    def test_ends_with_exactly_one_newline(self):
        assert hk.fit('x = 3\n\n\n', hunk_lines(['x = 1'], ['x = 2'])) == 'x = 3\n'

    def test_blank_line_inside_the_answer_stays_empty(self):
        lines = hunk_lines(['    x'], ['    y'])
        assert hk.fit('a\n\nb', lines) == '    a\n\n    b\n'

    @pytest.mark.parametrize('resolution', ['', '   ', '\n\n'])
    def test_empty_answer_deletes_the_block(self, resolution):
        assert hk.fit(resolution, hunk_lines(['    x = 1'], ['    x = 2'])) == ''

    @pytest.mark.xfail(reason='fit() counts tab characters but indents with spaces, so a '
                              'tab-indented block gets one space per tab')
    def test_tab_indented_block_keeps_tabs(self):
        lines = hunk_lines(['\tx = 1'], ['\tx = 2'])
        assert hk.fit('x = 3', lines) == '\tx = 3\n'

    @pytest.mark.xfail(reason='lines in the diff3 base section are counted when working out '
                              'the indent, so a less-indented base shrinks the result')
    def test_diff3_base_lines_do_not_affect_indent(self):
        lines = hunk_lines(['        a'], ['        b'], base=['    old'])
        assert hk.fit('x = 3', lines) == '        x = 3\n'


# --------------------------------------------------------------------------
# _splice
# --------------------------------------------------------------------------

LINES_1 = FILE_1.splitlines(keepends=True)


class TestSplice:
    def test_replaces_the_block_and_reports_its_line_range(self):
        text, ranges = hk._splice(LINES_1, [(1, 5)], ['x = 3\n'])
        assert text == 'import os\nx = 3\nprint(x)\n'
        assert ranges == [(2, 2)]

    def test_multi_line_piece_range(self):
        text, ranges = hk._splice(LINES_1, [(1, 5)], ['x = 3\ny = 4\n'])
        assert text == 'import os\nx = 3\ny = 4\nprint(x)\n'
        assert ranges == [(2, 3)]

    def test_empty_piece_removes_the_block_but_reports_one_line(self):
        text, ranges = hk._splice(LINES_1, [(1, 5)], [''])
        assert text == 'import os\nprint(x)\n'
        assert ranges == [(2, 2)]  # deliberate: max(count, 1) keeps the range non-empty

    def test_second_range_shifts_by_the_first_pieces_length(self):
        lines = FILE_2.splitlines(keepends=True)
        hunks = hk.find_hunks(FILE_2)
        text, ranges = hk._splice(lines, hunks, ['X\nY\n', 'W\n'])
        assert text == 'a = 1\nX\nY\nc = 3\nW\ne = 5\n'
        assert ranges == [(2, 3), (5, 5)]

    def test_block_at_start_of_file(self):
        lines = block(['a'], ['b']).splitlines(keepends=True) + ['tail\n']
        text, ranges = hk._splice(lines, [(0, 4)], ['x\n'])
        assert text == 'x\ntail\n'
        assert ranges == [(1, 1)]

    def test_text_without_final_newline_is_kept_as_is(self):
        lines = (block(['a'], ['b']) + 'tail').splitlines(keepends=True)
        text, _ = hk._splice(lines, [(0, 4)], ['x\n'])
        assert text == 'x\ntail'

    def test_no_hunks_returns_the_file_unchanged(self):
        assert hk._splice(['a\n', 'b\n'], [], []) == ('a\nb\n', [])

    def test_does_not_modify_its_inputs(self):
        lines = list(LINES_1)
        hunks = [(1, 5)]
        hk._splice(lines, hunks, ['x = 3\n'])
        assert lines == LINES_1 and hunks == [(1, 5)]


# --------------------------------------------------------------------------
# _file_error
# --------------------------------------------------------------------------

class TestFileError:
    def test_valid_code_gives_none(self):
        assert hk._file_error('x = 1\n') is None

    def test_syntax_error_is_returned_with_its_line(self):
        error = hk._file_error('a = 1\nb = = 2\n')
        assert isinstance(error, SyntaxError) and error.lineno == 2

    def test_indentation_error_counts_as_a_syntax_error(self):
        error = hk._file_error('a = 1\nif a:\nb = 2\n')
        assert isinstance(error, SyntaxError) and error.lineno == 3

    def test_null_bytes_do_not_raise(self):
        result = hk._file_error('x = 1\0')
        assert result is None or isinstance(result, SyntaxError)

    def test_does_not_execute_code(self, tmp_path):
        marker = tmp_path / 'ran'
        hk._file_error(f'open({str(marker)!r}, "w").close()\n')
        assert not marker.exists()


# --------------------------------------------------------------------------
# resolve_file: building and sending each block
# --------------------------------------------------------------------------

class TestResolveFileRequests:
    def test_single_block_is_replaced_and_fields_are_filled(self):
        client = FakeClient([dict(resolution='x = 3', explanation='merged both', assumptions='none',
                                  done_reason='stop', eval_count=5)])
        result = hk.resolve_file(client, FILE_1, no_context)
        assert result['resolution'] == 'import os\nx = 3\nprint(x)\n'
        assert result['explanation'] == '[Conflict 1] merged both'
        assert result['assumptions'] == '[Conflict 1] none'
        assert result['done_reason'] == 'stop'
        assert result['eval_count'] == 5
        assert result['hunks'] == 1
        assert result['repair_attempted'] is False
        assert result['initial_resolution'] == result['resolution']
        assert result['initial_syntax_error'] == ''
        assert result['repair_error'] == ''

    def test_client_gets_only_the_block_with_the_timeout_and_no_repair(self):
        client = FakeClient([dict(resolution='x = 3')])
        hk.resolve_file(client, FILE_1, no_context, timeout=7)
        assert client.calls[0]['conflict'] == BLOCK_1
        assert client.calls[0]['timeout'] == 7
        assert client.calls[0]['repair'] is False

    def test_default_timeout_is_300(self):
        client = FakeClient([dict(resolution='x = 3')])
        hk.resolve_file(client, FILE_1, no_context)
        assert client.calls[0]['timeout'] == 300

    def test_context_for_gets_the_block_and_comes_before_surrounding_code(self):
        seen = []

        def commits(block_text):
            seen.append(block_text)
            return 'COMMITS HERE'

        client = FakeClient([dict(resolution='x = 3')])
        hk.resolve_file(client, FILE_1, commits)
        assert seen == [BLOCK_1]
        assert client.calls[0]['context'] == 'COMMITS HERE\n\n' + surrounding_code(FILE_1, 2, 6)

    def test_empty_commit_context_leaves_only_surrounding_code(self):
        client = FakeClient([dict(resolution='x = 3')])
        hk.resolve_file(client, FILE_1, no_context)
        assert client.calls[0]['context'] == surrounding_code(FILE_1, 2, 6)

    def test_indentation_is_restored_inside_a_function(self):
        text = 'def f():\n' + block(['    x = 1'], ['    x = 2']) + '    return x\n'
        client = FakeClient([dict(resolution='x = 3')])
        result = hk.resolve_file(client, text, no_context)
        assert result['resolution'] == 'def f():\n    x = 3\n    return x\n'

    def test_empty_answer_deletes_the_block(self):
        result = hk.resolve_file(FakeClient([dict(resolution='')]), FILE_1, no_context)
        assert result['resolution'] == 'import os\nprint(x)\n'

    def test_missing_explanation_and_counts_are_tolerated(self):
        result = hk.resolve_file(FakeClient([dict(resolution='x = 3')]), FILE_1, no_context)
        assert result['explanation'] == '[Conflict 1] '
        assert result['assumptions'] == '[Conflict 1] '
        assert result['done_reason'] is None
        assert result['eval_count'] == 0


class TestResolveFileMultipleBlocks:
    ANSWERS = [dict(resolution='b = 9', explanation='one', eval_count=3),
               dict(resolution='d = 9', explanation='two', eval_count=None, done_reason='length')]

    def test_every_block_is_resolved_in_order(self):
        client = FakeClient(self.ANSWERS)
        result = hk.resolve_file(client, FILE_2, no_context)
        assert result['resolution'] == 'a = 1\nb = 9\nc = 3\nd = 9\ne = 5\n'
        assert [c['conflict'] for c in client.calls] == [BLOCK_A, BLOCK_B]
        assert result['hunks'] == 2

    def test_explanations_and_assumptions_are_numbered(self):
        result = hk.resolve_file(FakeClient(self.ANSWERS), FILE_2, no_context)
        assert result['explanation'] == '[Conflict 1] one\n\n[Conflict 2] two'
        assert result['assumptions'] == '[Conflict 1] \n\n[Conflict 2] '

    def test_counts_are_summed_and_done_reason_is_the_last_one(self):
        result = hk.resolve_file(FakeClient(self.ANSWERS), FILE_2, no_context)
        assert result['eval_count'] == 3          # None counts as 0
        assert result['done_reason'] == 'length'

    def test_other_conflicts_are_hidden_from_each_blocks_context(self):
        client = FakeClient(self.ANSWERS)
        hk.resolve_file(client, FILE_2, no_context)
        for call in client.calls:
            assert NOTE in call['context']
            assert '<<<<<<<' not in call['context'] and '>>>>>>>' not in call['context']

    def test_block_in_the_middle_of_a_statement_is_checked_as_part_of_the_file(self):
        text = 'items = [\n' + block(["    'a',"], ["    'b',"]) + ']\n'
        client = FakeClient([dict(resolution="'a',\n'b',")])
        result = hk.resolve_file(client, text, no_context)
        assert result['resolution'] == "items = [\n    'a',\n    'b',\n]\n"
        assert result['repair_attempted'] is False
        assert client.repair_calls == []


class TestResolveFileErrors:
    @pytest.mark.parametrize('answer', ['just text', None, dict(explanation='x'),
                                        dict(resolution=None), dict(resolution=5)])
    def test_answer_without_a_string_resolution_raises(self, answer):
        with pytest.raises(ValueError, match='no string resolution'):
            hk.resolve_file(FakeClient([answer]), FILE_1, no_context)

    def test_no_markers_raises_before_calling_the_client(self):
        client = FakeClient([])
        with pytest.raises(ValueError, match='No conflict markers found'):
            hk.resolve_file(client, 'x = 1\n', no_context)
        assert client.calls == []

    def test_client_errors_propagate(self):
        with pytest.raises(RuntimeError, match='boom'):
            hk.resolve_file(FakeClient([RuntimeError('boom')]), FILE_1, no_context)

    def test_error_on_a_later_block_stops_the_run(self):
        client = FakeClient([dict(resolution='b = 9'), RuntimeError('boom')])
        with pytest.raises(RuntimeError):
            hk.resolve_file(client, FILE_2, no_context)
        assert len(client.calls) == 2


# --------------------------------------------------------------------------
# resolve_file: the single repair attempt
# --------------------------------------------------------------------------

FILE_R = 'a = 1\n' + BLOCK_1 + 'b = 2\n'   # the block is replaced by one line, which lands on line 2


class TestResolveFileRepair:
    def test_syntax_error_triggers_one_repair_of_that_block(self):
        client = FakeClient([dict(resolution='x = = 1', explanation='bad')],
                            repairs=[dict(resolution='x = 3')])
        result = hk.resolve_file(client, FILE_R, no_context)
        assert result['resolution'] == 'a = 1\nx = 3\nb = 2\n'
        assert result['initial_resolution'] == 'a = 1\nx = = 1\nb = 2\n'
        assert result['initial_syntax_error'].startswith('SyntaxError on line 2: ')
        assert result['repair_attempted'] is True
        assert result['repair_error'] == ''
        assert len(client.repair_calls) == 1

    def test_repair_gets_block_context_raw_answer_message_and_timeout(self):
        client = FakeClient([dict(resolution='x = = 1')], repairs=[dict(resolution='x = 3')])
        hk.resolve_file(client, FILE_R, no_context, timeout=9)
        call = client.repair_calls[0]
        assert call['block'] == BLOCK_1
        assert call['context'] == client.calls[0]['context']
        assert call['resolution'] == 'x = = 1'
        assert call['message'].startswith('SyntaxError on line 1 of your resolution: ')
        assert call['timeout'] == 9

    def test_error_line_is_reported_relative_to_the_resolution(self):
        client = FakeClient([dict(resolution='ok = 1\nbad = = 1')], repairs=[dict(resolution='ok = 1')])
        hk.resolve_file(client, FILE_R, no_context)
        assert client.repair_calls[0]['message'].startswith('SyntaxError on line 2 of your resolution: ')

    def test_error_on_the_line_after_the_block_still_counts_for_that_block(self):
        # 'if a:' with nothing indented after it is reported on the next line.
        client = FakeClient([dict(resolution='if a:')], repairs=[dict(resolution='x = 3')])
        result = hk.resolve_file(client, FILE_R, no_context)
        assert result['initial_syntax_error'].startswith('SyntaxError on line 3: ')
        assert result['repair_attempted'] is True
        assert client.repair_calls[0]['message'].startswith('SyntaxError on line 2 of your resolution: ')

    def test_only_the_broken_block_is_repaired(self):
        client = FakeClient([dict(resolution='b = 9'), dict(resolution='d = = 9')],
                            repairs=[dict(resolution='d = 9')])
        result = hk.resolve_file(client, FILE_2, no_context)
        assert result['resolution'] == 'a = 1\nb = 9\nc = 3\nd = 9\ne = 5\n'
        assert len(client.calls) == 2 and len(client.repair_calls) == 1
        assert client.repair_calls[0]['block'] == BLOCK_B
        assert client.repair_calls[0]['message'].startswith('SyntaxError on line 1 of your resolution: ')

    def test_repaired_answer_is_re_indented_like_any_other(self):
        text = 'def f():\n' + block(['    x = 1'], ['    x = 2']) + '    return x\n'
        client = FakeClient([dict(resolution='x = = 1')], repairs=[dict(resolution='x = 3')])
        result = hk.resolve_file(client, text, no_context)
        assert result['resolution'] == 'def f():\n    x = 3\n    return x\n'

    def test_valid_file_never_calls_repair(self):
        client = FakeClient([dict(resolution='x = 3')])
        result = hk.resolve_file(client, FILE_R, no_context)
        assert client.repair_calls == []
        assert result['repair_attempted'] is False

    def test_client_without_repair_support_returns_the_broken_file(self):
        client = FakeClient([dict(resolution='x = = 1')], with_repair=False)
        result = hk.resolve_file(client, FILE_R, no_context)
        assert result['resolution'] == 'a = 1\nx = = 1\nb = 2\n'
        assert result['initial_syntax_error'].startswith('SyntaxError on line 2: ')
        assert result['repair_attempted'] is False
        assert result['repair_error'] == ''

    def test_repair_failure_is_recorded_and_the_initial_file_is_returned(self):
        client = FakeClient([dict(resolution='x = = 1')], repairs=[RuntimeError('boom')])
        result = hk.resolve_file(client, FILE_R, no_context)
        assert result['repair_attempted'] is True
        assert result['repair_error'] == 'RuntimeError: boom'
        assert result['resolution'] == result['initial_resolution'] == 'a = 1\nx = = 1\nb = 2\n'

    def test_error_outside_every_block_is_recorded_but_not_repaired(self):
        text = 'def f(:\n' + BLOCK_1
        client = FakeClient([dict(resolution='x = 3')])
        result = hk.resolve_file(client, text, no_context)
        assert result['initial_syntax_error'].startswith('SyntaxError on line 1: ')
        assert result['repair_attempted'] is False
        assert client.repair_calls == []

    def test_a_repair_that_is_still_broken_is_returned_without_a_second_try(self):
        client = FakeClient([dict(resolution='x = = 1')], repairs=[dict(resolution='x = = 2')])
        result = hk.resolve_file(client, FILE_R, no_context)
        assert result['resolution'] == 'a = 1\nx = = 2\nb = 2\n'
        assert result['repair_attempted'] is True
        assert len(client.repair_calls) == 1