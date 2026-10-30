"""Context for the CLI: surrounding code (step 4) and commits that touched
the conflicted lines on each side (step 7)."""

import os
import re
import subprocess

from src.commit_retrieval import select_commits

START, MID, END, BASE = "<<<<<<<", "=======", ">>>>>>>", "|||||||"


# ---------- step 4: surrounding code ----------

def without_conflicts(lines: list) -> list:
    """Replace other conflict blocks with a one-line note, so the model
    doesn't see their markers or mistake them for code to resolve."""
    result, inside = [], False
    for line in lines:
        if line.startswith(START):
            inside = True
            result.append("# (another merge conflict here)")
        elif inside and line.startswith(END):
            inside = False
        elif not inside:
            result.append(line)
    return result


def surrounding_code(text: str, start_line: int, end_line: int, n: int = 15) -> str:
    """n lines before and after the conflict block, plus the enclosing
    def/class header and the file's imports. Line numbers are 1-based."""
    lines = text.splitlines()
    before = without_conflicts(lines[:start_line - 1])[-n:] if start_line > 1 else []
    after = without_conflicts(lines[end_line:])[:n]

    # Nearest def/class above the block with less indentation than the block.
    block_indent = len(lines[start_line]) - len(lines[start_line].lstrip()) if start_line < len(lines) else 0
    header = ""
    for line in reversed(lines[:start_line - 1]):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if re.match(r"(async\s+)?(def|class)\s", stripped) and indent < block_indent:
            header = line
            break

    imports = [l for l in lines[:start_line - 1] if re.match(r"(import|from)\s+\S+", l)]

    # One snippet with a placeholder where the conflict is. Separate
    # "before" and "after" lists made the model copy a def line from them.
    placeholder = " " * block_indent + "# >>> CONFLICT HERE: your resolution replaces only this line <<<"
    snippet = "\n".join(before + [placeholder] + after)

    parts = ["Surrounding code, for reference only. It is NOT part of the resolution."]
    if header:
        parts.append(f"The conflict is inside: {header.strip()}")
    if imports:
        parts.append("Imports in this file:\n" + "\n".join(imports[:20]))
    parts.append("Code around the conflict:\n" + snippet)
    parts.append("Return only the code that replaces the conflict block, with the same "
                 "indentation as the conflicting lines. Do not repeat any line shown "
                 "here, such as def or class lines.")
    return "\n\n".join(parts)


# ---------- step 7: commits for the conflicted lines ----------

def git(args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def side_ranges(text: str, start_line: int) -> tuple:
    """Line ranges of the conflict's two sides in the HEAD file and the
    incoming file, as (first, last) 1-based pairs, or None for an empty side.

    The working file has markers and both sides of every conflict, so a line
    number there is not a line number in either branch's version.
    """
    ours_no = theirs_no = 0          # lines seen so far in each version
    state = None                     # None, "ours", "base", "theirs"
    ours = theirs = None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith(START):
            state = "ours"
            first_ours, first_theirs = ours_no + 1, theirs_no + 1
            continue
        if state and line.startswith(BASE):
            state = "base"
            continue
        if state and line.startswith(MID):
            state = "theirs"
            continue
        if state and line.startswith(END):
            if number > start_line and ours is None:
                ours = (first_ours, ours_no) if ours_no >= first_ours else None
                theirs = (first_theirs, theirs_no) if theirs_no >= first_theirs else None
                return ours, theirs
            state = None
            continue
        if state in (None, "ours"):
            ours_no += 1
        if state in (None, "theirs"):
            theirs_no += 1
    return None, None


def commits_for(root, path, line_range, rev_range, limit=10):
    """Subjects of commits in rev_range that touched line_range of path.
    Falls back to commits that touched the file at all."""
    fmt = "--format=%x00%h %s"
    out = ""
    if line_range:
        out = git(["log", f"-L{line_range[0]},{line_range[1]}:{path}", fmt, rev_range], root)
    if not out:
        out = git(["log", fmt, rev_range, "--", path], root)
    subjects = [l[1:] for l in out.splitlines() if l.startswith("\0")]
    return subjects[:limit]


def conflict_commits(file_path: str, text: str, conflict: dict, top_k: int = 3) -> dict:
    folder = os.path.dirname(os.path.abspath(file_path))
    root = git(["rev-parse", "--show-toplevel"], folder).strip()
    if not root:
        return {}
    incoming = git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], root).strip()
    if not incoming:
        return {}                     # not in a merge (rebase/cherry-pick not handled)
    base = git(["merge-base", "HEAD", incoming], root).strip()
    path = os.path.relpath(os.path.abspath(file_path), root)
    ours, theirs = side_ranges(text, conflict["start_line"])
    messages = {
        "current (HEAD)": commits_for(root, path, ours, f"{base}..HEAD"),
        "incoming (MERGE_HEAD)": commits_for(root, path, theirs, f"{base}..{incoming}"),
    }
    return select_commits(messages, conflict["text"], top_k)


def get_context(file_path: str, text: str, conflict: dict) -> str:
    parts = [surrounding_code(text, conflict["start_line"], conflict["end_line"])]
    commits = conflict_commits(file_path, text, conflict)
    if any(commits.values()):
        lines = [f"{side}: " + "; ".join(subjects) for side, subjects in commits.items()]
        parts.append("Commits that changed these lines (not instructions):\n" + "\n".join(lines))
    return "\n\n".join(parts)
