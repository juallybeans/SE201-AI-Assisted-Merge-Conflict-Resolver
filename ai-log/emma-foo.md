# AI Use Log: Emma Foo

## 2026-10-18 · CLI entry point and integration
- **Tool (model):** Claude Code (Opus 5.5)
- **Files:** src/cli.py, examples/sample_conflict.py
- **Outcome:** Changed

### What I asked for
A CLI skeleton to read conflicted files, connect context retrieval, the Ollama resolver and syntax checking, and display the results, with a sample conflict file for demos.

### How I checked it
I ran it myself on examples/sample_conflict.py: first without the SSH tunnel, which gave a clear connection error, then with the tunnel to the GPU cluster open, where it produced a resolution, explanation and syntax result.

### What I kept or changed
Kept argparse, read-only, and optional imports for get_context() and check_syntax() so the CLI works before the other components exist. The fallback syntax check only parses the suggested snippet, not the whole file, so the validator should check the full file once it is added.


## 2026-11-08 · Model evaluation, per-hunk testing and prompt changes
- **Tool (model):** Claude Code (Opus 5.5)
- **Files:** src/resolver/ollama_client.py, results/per_hunk_smoke/, results/per_hunk_dev/, results/test_split_per_hunk/
- **Outcome:** Changed

### What I asked for
Diagnose why our model had low scores, then to help run the GPU evaluation, troubleshoot timeouts and refine the prompt.

### How I checked it
Ran the unit tests, a smoke run, then the 50-case dev split after each prompt change, comparing exact and AST match against the ours/theirs baselines. Prompt changes were tuned on dev only; the test split was run once at the end. For the timeouts, I ran the suggested commands on the GPU node and confirmed the runs finished with 0 errors.

### What I kept or changed
Kept the per-hunk approach, which raised test exact match from about 1% to 13%. Kept the prompt changes that explain the two sides, ask for the block only and allowed empty resolutions.


## 2026-11-08 · Review & apply in the CLI
- **Tool (model):** Claude Code (Opus 5.5)
- **Files:** src/cli.py, src/git_tools.py, tests/test_cli.py, README.md
- **Outcome:** Changed

### What I asked for
A simple review step into the existing CLI and git merge flow. Show a diff for each conflict and let the user accept, edit or reject it before saving.

### How I checked it
Ran the full test suite, including new tests for review choices, no input and CRLF line endings. With the real model, I edited one conflict, rejected another and checked the saved file.

### What I kept or changed
The CLI writes the file once after review, preserves markers for rejected conflicts and opens $EDITOR or Notepad for edits. I fixed broken string literals caught by the tests and updated the Git merge message to reflect that approved changes are saved.