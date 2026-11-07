"""Run a Git merge, then review and apply resolutions for conflicted files."""

import argparse
import os
import subprocess
import sys

from src.cli import main as resolve_file


def find_conflicted_files(repository: str) -> list[str]:
    """Return repository-relative paths for files Git still marks unresolved."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U", "-z"],
        cwd=repository,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        error = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(error or "Could not ask Git for conflicted files.")

    # Git uses NUL separators so filenames containing spaces are handled safely.
    return [
        os.fsdecode(path)
        for path in result.stdout.split(b"\0")
        if path
    ]


def run_merge(branch: str, repository: str) -> int:
    """Merge a branch, then suggest resolutions for files left conflicted."""
    try:
        merge_result = subprocess.run(
            ["git", "merge", branch],
            cwd=repository,
            check=False,
        )
    except OSError as exc:
        print(f"Error: could not run Git: {exc}", file=sys.stderr)
        return 1

    try:
        conflicted_files = find_conflicted_files(repository)
    except (OSError, RuntimeError) as exc:
        print(f"Error: could not inspect merge conflicts: {exc}", file=sys.stderr)
        return 1

    if not conflicted_files:
        return merge_result.returncode

    print(f"\nFound {len(conflicted_files)} conflicted file(s).")
    suggestions_failed = False

    for relative_path in conflicted_files:
        file_path = os.path.abspath(os.path.join(repository, relative_path))
        print(f"\n===== File: {relative_path} =====")
        if resolve_file([file_path]) != 0:
            suggestions_failed = True

    print("\nCheck the resolved files, then stage them with git add.")
    print("The merge remains in progress until you finish resolving and commit.")

    # Preserve Git's nonzero status: conflicts mean the merge is not complete.
    if merge_result.returncode != 0 or suggestions_failed:
        return merge_result.returncode or 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a Git merge and suggest resolutions for conflicted files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser("merge", help="Merge a branch")
    merge_parser.add_argument("branch", help="Branch or commit to merge")
    merge_parser.add_argument(
        "--repo",
        default=".",
        help="Path to the local repository (default: current directory)",
    )

    args = parser.parse_args(argv)
    repository = os.path.abspath(args.repo)
    return run_merge(args.branch, repository)


if __name__ == "__main__":
    sys.exit(main())