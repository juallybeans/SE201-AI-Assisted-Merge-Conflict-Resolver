"""Git ours, theirs and union baselines for a file that already contains conflict markers.

These match `git merge-file --ours`, `--theirs` and `--union`. Text outside a
conflict hunk is left as it is. Each hunk is replaced by the current side
(above =======), the incoming side (below =======), or both sides with the
current side first.

When a merge is replayed by checking out the first parent and merging the
second, the current side is `<<<<<<< HEAD` and the incoming side is the
second parent. Marker labels are not interpreted; only which side of the
hunk the lines are on matters.

Conflict markers that already sit inside one side, from an earlier bad merge,
stay in that side. A hunk that is nested or missing its separator is rejected
rather than guessed.
"""

BASELINE_MODES = ('ours', 'theirs', 'union')

RATIONALE = {
    'ours': 'Git merge-file --ours: kept the current side of each conflict.',
    'theirs': 'Git merge-file --theirs: kept the incoming side of each conflict.',
    'union': 'Git merge-file --union: kept both sides of each conflict, current side first.',
}


def _text(line):
    if line.endswith('\r\n'):
        return line[:-2]
    if line.endswith('\n') or line.endswith('\r'):
        return line[:-1]
    return line


def _marker_size(content, char, label_ok):
    """Length of a git marker line, or 0 when the line is ordinary text."""
    size = 0
    while size < len(content) and content[size] == char:
        size += 1
    if size < 7:
        return 0
    rest = content[size:]
    if rest == '':
        return size
    if label_ok and rest.startswith(' '):
        return size
    return 0


def _find_hunk(lines, start, size):
    """Return the end, separator and optional diff3 base line of one top-level hunk."""
    depth = 1
    separator = None
    base = None
    index = start + 1
    while index < len(lines):
        content = _text(lines[index])
        if _marker_size(content, '<', True) == size:
            depth += 1
        elif _marker_size(content, '>', True) == size:
            depth -= 1
            if depth == 0:
                if separator is None:
                    raise ValueError('Conflict is missing the ======= separator')
                return index, separator, base
        elif depth == 1 and separator is None and _marker_size(content, '=', False) == size:
            separator = index
        elif (depth == 1 and separator is None and base is None
              and _marker_size(content, '|', True) == size):
            base = index
        index += 1
    raise ValueError('Unclosed conflict marker')


def resolve_baseline(conflict_code, strategy):
    """Resolve every top-level conflict hunk with one git baseline strategy."""
    if strategy not in BASELINE_MODES:
        raise ValueError(f'Unknown baseline: {strategy}')
    if not isinstance(conflict_code, str):
        raise ValueError('conflict_code must be a string')

    lines = conflict_code.splitlines(keepends=True)
    resolved = []
    index = 0
    hunks = 0
    while index < len(lines):
        size = _marker_size(_text(lines[index]), '<', True)
        if size == 0:
            resolved.append(lines[index])
            index += 1
            continue
        end, separator, base = _find_hunk(lines, index, size)
        ours_end = base if base is not None else separator
        ours = lines[index + 1:ours_end]
        theirs = lines[separator + 1:end]
        if strategy == 'ours':
            chosen = ours
        elif strategy == 'theirs':
            chosen = theirs
        else:
            chosen = ours + theirs
        resolved.extend(chosen)
        hunks += 1
        index = end + 1
    if hunks == 0:
        raise ValueError('No conflict markers found')
    return ''.join(resolved)
