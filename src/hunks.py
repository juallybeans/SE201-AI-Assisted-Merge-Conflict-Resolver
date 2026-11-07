import textwrap

from src.baselines import _find_hunk, _marker_size, _text
from src.context_retrieval import surrounding_code


def find_hunks(conflict_code):
    """(start, end) 0-based line indexes of each top-level conflict block,
    from the <<<<<<< line to the >>>>>>> line."""
    lines = conflict_code.splitlines(keepends=True)
    hunks = []
    index = 0
    while index < len(lines):
        size = _marker_size(_text(lines[index]), '<', True)
        if size == 0:
            index += 1
            continue
        end, _, _ = _find_hunk(lines, index, size)
        hunks.append((index, end))
        index = end + 1
    if not hunks:
        raise ValueError('No conflict markers found')
    return hunks


def _indent(line):
    return len(line) - len(line.lstrip())


def fit(resolution, hunk_lines):
    """Make a block resolution fit back into the file: restore the
    indentation the model stripped and end it with a newline."""
    sides, in_base = [], False
    for line in hunk_lines[1:-1]:
        text = _text(line)
        if _marker_size(text, '|', True):
            in_base = True                      
        elif _marker_size(text, '=', False):
            in_base = False
        elif line.strip() and not in_base:
            sides.append(line)
    prefix = min((line[:_indent(line)] for line in sides), key=len, default='')
    resolution = textwrap.dedent(resolution.strip('\n'))
    if prefix:
        resolution = textwrap.indent(resolution, prefix)
    return resolution + '\n' if resolution else ''


def _splice(lines, hunks, pieces):
    """Rebuild the file with each hunk replaced by its piece. Also returns
    the 1-based (first, last) line range each piece ended up on."""
    out, ranges, previous = [], [], 0
    for (start, end), piece in zip(hunks, pieces):
        out.extend(lines[previous:start])
        first = sum(l.count('\n') for l in out) + 1
        out.append(piece)
        ranges.append((first, first + max(piece.count('\n'), 1) - 1))
        previous = end + 1
    out.extend(lines[previous:])
    return ''.join(out), ranges


def _file_error(code):
    try:
        compile(code, '<resolution>', 'exec', dont_inherit=True)
        return None
    except SyntaxError as exc:
        return exc
    except (ValueError, TypeError, RecursionError):
        return None


def resolve_file(client, conflict_code, context_for, timeout=300):
    """Resolve each conflict block of conflict_code with client and splice
    the answers back into the file.

    context_for(block_text) returns the commit context for one block ('' for
    conflict_only). Surrounding code is added here. Returns the same fields
    as client.resolve_conflict, for the whole file.
    """
    lines = conflict_code.splitlines(keepends=True)
    hunks = find_hunks(conflict_code)
    blocks, contexts, answers, pieces = [], [], [], []
    for start, end in hunks:
        block = ''.join(lines[start:end + 1])
        code = surrounding_code(conflict_code, start + 1, end + 1)
        context = '\n\n'.join(part for part in (context_for(block), code) if part)
        # Syntax is checked on the whole file below, not on the block alone,
        # since a block can be part of a statement (e.g. inside a list).
        answer = client.resolve_conflict(conflict=block, context=context, timeout=timeout, repair=False)
        if not isinstance(answer, dict) or not isinstance(answer.get('resolution'), str):
            raise ValueError('Client returned no string resolution')
        blocks.append(block)
        contexts.append(context)
        answers.append(answer)
        pieces.append(fit(answer['resolution'], lines[start:end + 1]))

    resolution, ranges = _splice(lines, hunks, pieces)
    result = dict(
        resolution=resolution,
        explanation='\n\n'.join(f"[Conflict {n}] {a.get('explanation', '')}" for n, a in enumerate(answers, 1)),
        assumptions='\n\n'.join(f"[Conflict {n}] {a.get('assumptions', '')}" for n, a in enumerate(answers, 1)),
        done_reason=answers[-1].get('done_reason'),
        eval_count=sum(a.get('eval_count') or 0 for a in answers),
        hunks=len(hunks),
        repair_attempted=False,
        initial_resolution=resolution,
        initial_syntax_error='',
        repair_error='',
    )

    # One repair attempt, on the block the whole-file syntax error points at.
    error = _file_error(resolution)
    if error is None:
        return result
    result['initial_syntax_error'] = f'SyntaxError on line {error.lineno}: {error.msg}'
    repair = getattr(client, 'repair_resolution', None)
    target = next((n for n, (first, last) in enumerate(ranges)
                   if error.lineno and first <= error.lineno <= last + 1), None)
    if repair is None or target is None:
        return result
    first = ranges[target][0]
    message = f'SyntaxError on line {error.lineno - first + 1} of your resolution: {error.msg}'
    result['repair_attempted'] = True
    try:
        repaired = repair(blocks[target], contexts[target], answers[target]['resolution'], message, timeout)
    except Exception as exc:
        result['repair_error'] = f'{type(exc).__name__}: {exc}'
        return result
    start, end = hunks[target]
    pieces[target] = fit(repaired['resolution'], lines[start:end + 1])
    result['resolution'], _ = _splice(lines, hunks, pieces)
    return result
