"""
Command-line entry point for the AI-assisted merge conflict resolver.

Usage:
    python -m src.cli path/to/conflicted_file.py [--context "extra notes"]

For each conflict block in the file, the CLI prints the conflict,
the suggested resolution, the explanation, the assumptions and risks,
and a syntax check result. It then shows a diff and asks whether to
accept, edit or reject the resolution. Accepted and edited resolutions
are written to the file; rejected blocks keep their conflict markers.
"""

import argparse
import ast
import difflib
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap

from src.hunks import fit
from src.resolver.ollama_client import OllamaResolverError, resolve_conflict

# CLI falls back to simple defaults until they are implemented.
try:
    from src.context_retrieval import get_context
except ImportError:
    get_context = None

try:
    from src.syntax_check import check_syntax
except ImportError:
    check_syntax = None


CONFLICT_START = "<<<<<<<"
CONFLICT_END = ">>>>>>>"
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def find_conflicts(text: str) -> list[dict]:
    """
    Find Git conflict blocks in the file text.

    Returns a list of dictionaries containing:
    - start_line: 1-based line number of the <<<<<<< marker
    - end_line: 1-based line number of the >>>>>>> marker
    - text: the full conflict block, including markers
    """
    conflicts = []
    lines = text.splitlines(keepends=True)
    start = None

    for index, line in enumerate(lines):
        if line.startswith(CONFLICT_START):
            start = index
        elif line.startswith(CONFLICT_END) and start is not None:
            conflicts.append(
                {
                    "start_line": start + 1,
                    "end_line": index + 1,
                    "text": "".join(lines[start:index + 1]),
                }
            )
            start = None

    return conflicts


def default_syntax_check(code: str, file_path: str) -> tuple[bool, str]:
    """
    Basic syntax check used until the team's validator is available.

    Fails if conflict markers remain. For Python files, also parses
    the code with the built-in ast module.
    """
    for line in code.splitlines():
        if line.startswith(CONFLICT_MARKERS):
            return False, "Conflict markers remain in the resolution."

    if not file_path.endswith(".py"):
        return True, "No conflict markers remain (no parser for this file type)."

    try:
        ast.parse(textwrap.dedent(code))
    except SyntaxError as exc:
        return False, f"Python syntax error on line {exc.lineno}: {exc.msg}"

    return True, "Valid Python syntax."


def print_section(title: str, body: str) -> None:
    print(f"--- {title} ---")
    print(body.rstrip("\n") if body.strip() else "(none)")
    print()


def show_diff(conflict_text: str, resolution: str) -> None:
    """Print a unified diff from the conflict block to the resolution."""
    diff = difflib.unified_diff(
        conflict_text.splitlines(),
        resolution.splitlines(),
        "conflict",
        "resolution",
        lineterm="",
    )
    print("--- Diff ---")
    print("\n".join(diff))
    print()


def edit_in_editor(text: str, file_path: str) -> str:
    """Open text in $VISUAL/$EDITOR (notepad or nano by default) and
    return the edited text."""
    editor = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or ("notepad" if os.name == "nt" else "nano")
    )
    suffix = os.path.splitext(file_path)[1]
    with tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8"
    ) as handle:
        handle.write(text)
    try:
        subprocess.run(shlex.split(editor) + [handle.name], check=False)
        with open(handle.name, encoding="utf-8") as edited:
            text = edited.read()
    finally:
        os.remove(handle.name)
    return text if not text or text.endswith("\n") else text + "\n"


def review(conflict_text: str, resolution: str, file_path: str, validate):
    """
    Show the diff and ask the user to accept, edit or reject.

    Returns the text to put in place of the conflict block, or None
    when the user rejects it (or there is no input to read).
    """
    while True:
        show_diff(conflict_text, resolution)
        passed, message = validate(resolution, file_path)
        print_section("Syntax check", f"{'PASS' if passed else 'FAIL'}: {message}")
        try:
            choice = input("Apply? [a]ccept / [e]dit / [r]eject: ").strip().lower()
        except EOFError:
            return None
        if choice in ("a", "accept"):
            return resolution
        if choice in ("r", "reject"):
            return None
        if choice in ("e", "edit"):
            resolution = edit_in_editor(resolution, file_path)


def apply_resolutions(text: str, conflicts: list[dict], pieces: list) -> str:
    """Replace each conflict block with its piece. A None piece, or a
    conflict without a piece, keeps the original block."""
    lines = text.splitlines(keepends=True)
    out, previous = [], 0
    for conflict, piece in zip(conflicts, pieces):
        out.extend(lines[previous:conflict["start_line"] - 1])
        out.append(conflict["text"] if piece is None else piece)
        previous = conflict["end_line"]
    out.extend(lines[previous:])
    return "".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Suggest resolutions for Git merge conflicts using an LLM.",
    )
    parser.add_argument(
        "file",
        help="Path to a file containing Git conflict markers.",
    )
    parser.add_argument(
        "--context",
        default="",
        help="Extra context for the model, e.g. commit messages.",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as handle:
            text = handle.read()
            # Write the file back with the line endings it had.
            newline = handle.newlines if isinstance(handle.newlines, str) else "\n"
    except OSError as exc:
        print(f"Error: could not read '{args.file}': {exc}", file=sys.stderr)
        return 1

    conflicts = find_conflicts(text)
    if not conflicts:
        print(f"No merge conflicts found in '{args.file}'.")
        return 0

    total = len(conflicts)
    validate = check_syntax or default_syntax_check
    pieces = []
    status = 0

    for number, conflict in enumerate(conflicts, start=1):
        print(
            f"===== Conflict {number} of {total} "
            f"(lines {conflict['start_line']}-{conflict['end_line']}) ====="
        )
        print_section("Conflict", conflict["text"])

        context = args.context
        if get_context is not None:
            retrieved = get_context(args.file, text, conflict)
            context = "\n".join(part for part in (retrieved, context) if part)

        try:
            # The repair check parses Python, so only repair .py files.
            result = resolve_conflict(
                conflict["text"], context, repair=args.file.endswith(".py")
            )
        except OllamaResolverError as exc:
            # Stop asking the model, but keep what was already accepted.
            print(f"Error: {exc}", file=sys.stderr)
            status = 1
            break

        print_section("Suggested resolution", result["resolution"])
        print_section("Explanation", result["explanation"])
        print_section("Assumptions & risks", result["assumptions"])
        if result.get("repair_attempted"):
            note = f"First resolution failed: {result['initial_syntax_error']}"
            if result.get("repair_error"):
                note += f"\nRepair request failed: {result['repair_error']}"
            print_section("Repair attempt", note)

        # The model may drop the block's indentation; fit restores it.
        block_lines = conflict["text"].splitlines(keepends=True)
        resolution = fit(result["resolution"], block_lines)
        pieces.append(review(conflict["text"], resolution, args.file, validate))

    applied = sum(piece is not None for piece in pieces)
    if applied:
        try:
            with open(args.file, "w", encoding="utf-8", newline=newline) as handle:
                handle.write(apply_resolutions(text, conflicts, pieces))
        except OSError as exc:
            print(f"Error: could not write '{args.file}': {exc}", file=sys.stderr)
            return 1

    print(f"Applied {applied} of {total} resolution(s) to '{args.file}'.")
    if applied == total:
        print(f"All conflicts resolved. Check the file, then run: git add {args.file}")
    else:
        print("Unresolved conflicts keep their conflict markers.")
    return status


if __name__ == "__main__":
    sys.exit(main())
