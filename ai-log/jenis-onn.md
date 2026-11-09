# AI Use Log: Jenis Onn

## 2026-10-17 · Model evaluation script
- **Tool (model):** ChatGPT (GPT-6.1 Sol Light)
- **Files:** src/evaluation.py
- **Outcome:** Kept

### What I asked for
Help developing the model evaluation script for the AI-Assisted Merge Conflict Resolver, including evaluating saved model predictions against reference resolutions and producing evaluation results.

### How I checked it
I ran the unit tests in tests/test_evaluation.py, which passed. I also read through evaluation.py to confirm it scores predictions against the reference resolutions and writes the results as expected.

### What I kept or changed
I kept the AI-generated evaluation.py code without making any edits.

## 2026-10-17 · Unit tests for the evaluation script
- **Tool (model):** Claude (Sonnet 5.5)
- **Files:** tests/test_evaluation.py
- **Outcome:** Kept

### What I asked for
Help writing unit tests for evaluation.py to check that the evaluation logic behaves as expected.

### How I checked it
I ran the tests against evaluation.py and they passed. I reviewed the test cases and checked that their expected results match how the evaluation logic should behave. Used a different model as what was used to generate the actual scripts.

### What I kept or changed
I kept the AI-generated test_evaluation.py code without making any edits.

## 2026-10-20 · Selecting cases by character limit
- **Tool (model):** ChatGPT (GPT-6.1 Sol Light)
- **Files:** src/evaluation.py, evaluation/cases_short2000_context2000.json, evaluation/cases_short4000_context4000.json
- **Outcome:** Kept

### What I asked for
A way to pick evaluation cases whose conflict and context stay under a maximum character count.

### How I checked it
I looked at the cases it selected and checked their character counts against the dataset to confirm each one was under the limit.

### What I kept or changed
I kept the 4000 character version, and made a copy with a 2000 character limit for additional testing.

## 2026-10-21 · Diagnosing commit-context timeouts
- **Tool (model):** Claude Code (Claude Opus 5.5)
- **Files:** src/resolver/ollama_client.py
- **Outcome:** Kept

### What I asked for
Help working out why, with commit context included, the first 2 smoke cases passed and the last 3 timed out.

### How I checked it
Claude's first guess, that the model was generating without stopping, was wrong. My rerun showed cases 1 and 2 stopping on their own. A diagnostic script it wrote showed all cases passing when the reply was streamed. The cause was the non-streaming request going silent over the SSH tunnel to the cluster. After switching to streaming, I reran the smoke set and all 5 cases came back ok.

### What I kept or changed
I made the edits myself from Claude's line-by-line instructions. I kept the output-length cap and switched the request to streaming.

## 2026-10-23 · Fixing the 50-case dev run failures
- **Tool (model):** Claude Code (Claude Opus 5.5)
- **Files:** src/resolver/ollama_client.py, src/evaluation.py, 01-ollama.ipynb (cluster notebook)
- **Outcome:** Kept

### What I asked for
Help working out why the 50-case dev run kept failing partway through, and how to get all cases to run in one go.

### How I checked it
The saved results showed that after about 25 cases, every request waited the full timeout and got no reply, so the Ollama server had stopped answering. Claude found the cause in the notebook that starts Ollama: its output went to a pipe that nothing read, so Ollama froze once the pipe filled up. This was likely also behind the earlier commit-context timeouts. I also hit the cluster's idle auto-logout and some cases where the model returned a missing or non-text explanation. After the fixes, I reran the dev cases and checked the results file for errors.

### What I kept or changed
I changed the notebook to write Ollama's output to a log file. I added retries for failed cases, let a missing explanation or assumptions field pass instead of failing the case, and raised the output cap to 4096 tokens. Claude wrote the code changes; I reviewed them and applied them myself.


## 2026-10-30 · Relevance-filtered commit context (retrieved_context mode)
- **Tool (model):** Claude Code (Claude Opus 5.5)
- **Files:** src/commit_retrieval.py (new), src/evaluation.py
- **Outcome:** Kept

### What I asked for
A detailed how-to for : instead of sending every commit message to the model, keep only the ones related to the conflict, as a new evaluation mode to compare against conflict_only and commit_context.

### How I checked it
I applied the edits myself, ran the unit tests again, and ran the 50 dev cases with --mode retrieved_context, then scored them against the conflict_only and commit_context runs. [RESULT: e.g. all 50 cases ok]

### What I kept or changed
I kept the word-overlap scoring (messages ranked by how many words they share with the conflict code, merge and README-only messages dropped, top 3 per branch).

## 2026-10-30 · Unit tests for the new evaluation features and commit retrieval
- **Tool (model):** Claude (Sonnet 5.5)
- **Files:** tests/test_evaluation.py (updated), tests/test_commit_retrieval.py (new)
- **Outcome:** Kept

### What I asked for
Help updating tests/test_evaluation.py to cover the changes made to evaluation.py (retrieved_context mode, --top-k, retries, and the extra settings saved with each run), and writing unit tests for the new src/commit_retrieval.py, plus how to run them.

### How I checked it
I ran `python -m pytest tests -v` and all 200 tests passed (67 of them in test_commit_retrieval.py). I read through the new test cases and checked that the expected results match how retrieved_context mode, the retries and the commit selection should behave. Used a different model as what was used to generate the actual scripts.

### What I kept or changed
I kept the updated test_evaluation.py and the new test_commit_retrieval.py.

## 2026-10-30 · Surrounding code and commit context for the CLI
- **Tool (model):** Claude Code (Claude Opus 5.5)
- **Files:** src/context_retrieval.py (new), src/cli.py, src/resolver/ollama_client.py
- **Outcome:** Kept, after one round of fixes

### What I asked for
Surrounding code context and commit retrieval (cli) - for conflicted lines: give the CLI the code around each conflict, and the commit messages from each branch that changed the conflicted lines.

### How I checked it
I printed the context for examples/sample_conflict.py and checked that it showed the enclosing function and no conflict markers. Then I ran the CLI with the model on the cluster. The first run went wrong: one resolution copied a neighbouring function's def line, and a correct function body failed the repair step's syntax check with "'return' outside function". It was traced to two causes: the context listed "code before" and "code after" separately, and the repair check compiled function bodies on their own. After both fixes, both resolutions were function bodies only and passed the syntax check. I also made a small demo repo with a real merge conflict and checked that the right commit was listed for each side and that the initial commit was not. I also checked the dataset fields and found that conflict_code is already the whole file, so the evaluation needs no surrounding-code mode.

### What I kept or changed
I kept context_retrieval.py after fixes. The other changes were kept, manually inputting them into the files.

## 2026-10-31 · Unit tests for context_retrieval
- **Tool (model):** Claude (Sonnet 5.5)
- **Files:** tests/test_context_retrieval.py (new)
- **Outcome:** Kept

### What I asked for
Help writing unit tests for the new src/context_retrieval.py, which builds the context sent to the model: the surrounding code around a conflict, and the commits that touched the conflicted lines on each side of a merge.

### How I checked it
I ran `python -m pytest tests/test_context_retrieval.py -v` and all tests passed. I read through the new test cases and checked that the expected results match how the surrounding code, the line ranges for each side of a conflict, and the commit lookup should behave. The commit tests build small temporary git repos with a real merge conflict, so they exercise the actual git commands rather than mocks. The tests are written by a different model from the one that generated context_retrieval.py

### What I kept or changed
I kept the new test_context_retrieval.py.

## 2026-11-07 · Resolving one conflict block at a time (src/hunks.py)
- **Tool (model):** Claude Code (Claude Opus 5.5)
- **Files:** src/hunks.py (new)
- **Outcome:** Kept, with a change to fit()

### What I asked for
After the 200-case test run, I found the model lost or rewrote most of the code outside the conflicts, because it had to write the whole file back out. I asked for a way to send the model one conflict block at a time and copy the rest of the file unchanged, the same way the git baselines work.

### How I checked it
I read through hunks.py to check how it works. find_hunks() reuses the marker parsing from baselines.py, so both find the same conflict blocks. resolve_file() sends each block to the model with its surrounding code as context, then pastes the answers back into the file. fit() restores indentation that the model strips. The syntax repair checks the whole spliced file, then repairs the block the error points at. I ran the unit tests in tests/test_hunks.py, which check that a fake model always answering "ours" or "theirs" reproduces git's ours or theirs baseline byte for byte, and that the code outside the blocks is untouched.

### What I kept or changed
I kept find_hunks(), resolve_file() and the whole-file repair. I changed fit() so that it ignores the base section of diff3-style conflicts when working out the indentation, and copies the original indentation characters (so tabs stay tabs) instead of always using spaces.

## 2026-11-07 · Unit tests for hunks.py and the new evaluation modes
- **Tool (model):** Claude (Sonnet 5.5)
- **Files:** tests/test_hunks.py (new), tests/test_evaluation.py (updated)
- **Outcome:** Kept

### What I asked for
Help writing unit tests for the new src/hunks.py (splitting a file into conflict blocks, resolving each block separately, putting the answers back into the file and making one repair attempt on a syntax error), and updating tests/test_evaluation.py for the new evaluation.py. The new evaluation.py adds the git baselines (ours, theirs, union), the --per-hunk option, extra result fields for the repair attempt, and a code argument for build_context.

### How I checked it
I ran `python -m pytest tests/test_hunks.py -v` against my real baselines.py and it passed with 78 tests passed and 2 expected failures. I read through the new test cases and checked that the expected results match how block splitting, indentation, the repair attempt, the baselines and --per-hunk should behave. The tests are written by a different model from the one that generated hunks.py.

### What I kept or changed
I kept the new test_hunks.py and the updated test_evaluation.py.

