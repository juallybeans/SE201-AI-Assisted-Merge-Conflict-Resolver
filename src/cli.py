"""
Command-line entry point for the AI-assisted merge conflict resolver.

Usage:
    python -m src.cli path/to/conflicted_file.py [--context "extra notes"]

For each conflict block in the file, the CLI prints the conflict,
the suggested resolution, the explanation, the assumptions and risks,
and a syntax check result. It never modifies the input file.
"""

import argparse
import ast
import sys
import textwrap

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
    except OSError as exc:
        print(f"Error: could not read '{args.file}': {exc}", file=sys.stderr)
        return 1

    conflicts = find_conflicts(text)
    if not conflicts:
        print(f"No merge conflicts found in '{args.file}'.")
        return 0

    total = len(conflicts)
    validate = check_syntax or default_syntax_check

    for number, conflict in enumerate(conflicts, start=1):
        print(
            f"===== Conflict {number} of {total} "
            f"(lines {conflict['start_line']}-{conflict['end_line']}) ====="
        )
        print_section("Conflict", conflict["text"])

        context = args.context
        if get_context is not None:
            retrieved = get_context(args.file, conflict["text"])
            context = "\n".join(part for part in (retrieved, context) if part)

        try:
            # The repair check parses Python, so only repair .py files.
            result = resolve_conflict(
                conflict["text"], context, repair=args.file.endswith(".py")
            )
        except OllamaResolverError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        print_section("Suggested resolution", result["resolution"])
        print_section("Explanation", result["explanation"])
        print_section("Assumptions & risks", result["assumptions"])
        if result.get("repair_attempted"):
            note = f"First resolution failed: {result['initial_syntax_error']}"
            if result.get("repair_error"):
                note += f"\nRepair request failed: {result['repair_error']}"
            print_section("Repair attempt", note)

        passed, message = validate(result["resolution"], args.file)
        status = "PASS" if passed else "FAIL"
        print_section("Syntax check", f"{status}: {message}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
