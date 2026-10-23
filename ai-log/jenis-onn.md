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
I ran the tests against evaluation.py and they passed. I reviewed the test cases and checked that their expected results match how the evaluation logic should behave.

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


