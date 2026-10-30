# SE201 AI-Assisted Merge Conflict Resolver

This tool uses an LLM to suggest resolutions for Git merge conflicts. The model runs through Ollama on the university GPU cluster. Your computer reaches it through an SSH tunnel, so you never download the model yourself.

```
VS Code (your computer)  --SSH tunnel-->  GPU compute node (Ollama + model)
http://localhost:<PORT>                   127.0.0.1:<PORT>
```

### 1. Create your `.env` file

The resolver reads its settings from a `.env` file in the project root. Copy the template:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

`.env` is git-ignored, so each team member keeps their own copy. You will fill in the port in step 3.

---

## Each working session

### 2. Start Ollama on the GPU cluster

1. SSH into the university cluster using the provided login instructions.
2. In the terminal, enter `mynotebook` and request a GPU.
3. Start a GPU-enabled Jupyter Notebook session using the cluster's instructions.
4. Open `01-ollama.ipynb` in Jupyter.
5. Run the notebook cells to start the Ollama server and check which models are available.

Note down these three values:

| What | How to find it |
|---|---|
| Cluster username | `whoami` (on origami) |
| Compute node | `squeue --me` (on origami), under `NODELIST(REASON)` |
| Ollama port | printed by `01-ollama.ipynb` |

**Keep the Jupyter session running.** If it stops, the model stops too.

The compute node and port can **change every session**, so check them each time.

### 3. Open the SSH tunnel and update `.env`

In VS Code, open a **new local terminal** (on your own computer, not on the cluster). Run the following, replacing the placeholders. `<PORT>` appears twice: the local port, then the cluster port.

```bash
ssh -J <USERNAME>@10.193.104.102 \
    -N \
    -L <PORT>:127.0.0.1:<PORT> \
    <USERNAME>@<COMPUTE_NODE>
```

After you enter your password, the command appears to hang. That means the tunnel is open. **Leave this terminal open**, and use a different terminal for everything else.

Now open `.env` and make sure the port and model match your session:

```env
OLLAMA_BASE_URL=http://localhost:<PORT>
OLLAMA_MODEL=gemma4:e2b
```

`OLLAMA_MODEL` must be one of the models the notebook listed.

### 4. Test the connection

In a **second** terminal, check that the tunnel works:

```bash
curl http://localhost:<PORT>/api/tags
```

You should see JSON listing the models on the cluster. If you get `Failed to connect`, the tunnel or the Jupyter session is down. Go back to steps 2–3.

### 5. Test the resolver and syntax repair

If the model's resolution fails the syntax check (leftover conflict markers or invalid Python), the resolver makes **one** repair attempt. It sends the model the failed resolution and the error message.

**Unit tests (no cluster needed).** These use a fake model:

```bash
python -m pytest tests/test_ollama_client.py -v
```

**Tests on cluster model.** These use the real model on cluster:

```bash
python -m tests.test_ollama_client
```

Both parts should end with `Syntax check: PASS`.

**CLI, normal conflicts.** Each conflict should pass without a repair:

```bash
python -m src.cli examples/sample_conflict.py
```

**CLI, conflicts with syntax errors.** Both branches contain errors, so a `Repair attempt` section showing the first error may appear before the final `Syntax check`:

```bash
python -m src.cli examples/repair_conflict.py
```

Check that the final `Syntax check` is `PASS` and that the resolution keeps both branches' changes. The CLI only prints suggestions and never modifies the file.

## Model evaluation using the selected smoke cases

Run the following commands from the project root. These steps reuse the ten smoke case IDs already saved in `evaluation/cases.json`. **Do not run `prepare` again.** The 200-case test partition is not run.

### 6. Check the evaluation files

Ensure these files are present:

```text
src/evaluation.py
src/resolver/ollama_client.py
tests/test_evaluation.py
evaluation/cases.json
data/model_inputs.jsonl
data/evaluation_references.jsonl
```

Download the two JSONL files from the team's Google Drive into `data/`. Use the dataset version used to create `cases.json`; the evaluator checks its checksum.

### 7. Activate your environment and run the unit tests

On macOS/Linux:

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

These unit tests check the evaluation logic and the resolver's repair step without calling Ollama. Continue once they pass.

### 8. Choose whether you need new predictions

### Rescore predictions that already exist

If `results/smoke_predictions.jsonl` already contains the predictions you want to evaluate, run only:

```bash
python -m src.evaluation score --split smoke --predictions results/smoke_predictions.jsonl --out-dir results/smoke_scores
```

This does not contact Ollama or change the saved predictions. It replaces `per_case.csv` and `summary.json` in the output folder. The same predictions and scoring rules should produce the same scores.

The current prediction schema uses a `resolution` field. Old predictions with `initial_resolution`/`final_resolution` require the compatibility fallback in `score()` or conversion before use. No fallback is needed for new outputs.

If you only need to rescore saved predictions, skip generation and go to step 10 to inspect the results.

### Generate predictions for the five smoke cases

Keep the Jupyter session and SSH tunnel running, and confirm that `.env` matches your current session.

For a first run, when the output file does not exist:

```bash
python -m src.evaluation generate --split smoke --mode conflict_only --output results/smoke_predictions.jsonl
```

This sends each selected conflicted file to the existing Ollama client and saves the resolution, rationale, assumptions, status and runtime. Reference answers are not sent to the model.

Generation includes the syntax repair attempt (see step 5). Each prediction row also records `repair_attempted`, `initial_resolution`, `initial_syntax_error` and `repair_error`. Scoring uses the final `resolution`. To measure the effect of repair, count the rows with `repair_attempted: true` and compare how many of their `initial_resolution` and `resolution` values compile.

Generation processes cases sequentially and prints a status after each finishes. With the current non-streaming client, there can be several minutes without terminal output. The default per-request timeout is 300 seconds.

If the prediction file already exists and you want a fresh run on the same cases, use a new filename:

```bash
python -m src.evaluation generate --split smoke --mode conflict_only --output results/smoke_predictions_rerun.jsonl
```

Use another new filename if that file also exists.

### 9. Score the predictions

For the first-run prediction file:

```bash
python -m src.evaluation score --split smoke --predictions results/smoke_predictions.jsonl --out-dir results/smoke_scores
```

If you used the rerun filename instead:

```bash
python -m src.evaluation score --split smoke --predictions results/smoke_predictions_rerun.jsonl --out-dir results/smoke_scores_rerun
```

Scoring uses the reference answers locally and does not require Ollama. Running it again replaces `per_case.csv` and `summary.json` in the specified output folder.

### 10. Inspect the results

| Output | Contents |
|---|---|
| `per_case.csv` | Each case's status, exact match, AST match, compilation success, conflict-marker check and compilation diagnostics |
| `summary.json` | Aggregate scores, difficulty breakdown, latency and checksums |
| Prediction JSONL | Generated resolutions, rationale, assumptions, timing and errors |
| Prediction `.meta.json` | Settings used for reproducibility and resume checks |

Errors and missing predictions count as failures in the overall score denominator. Check error messages before interpreting low scores as resolution-quality failures.

Compilation does not execute the generated code or prove behavioral correctness. The ten-case smoke run checks the pipeline; it is not a final performance estimate.

### 11. Resume an interrupted generation, if needed

Use the same options and output path as the interrupted run, adding `--resume`. For example:

```bash
python -m src.evaluation generate --split smoke --mode conflict_only --output results/smoke_predictions.jsonl --resume
```

Keep the accompanying `.meta.json` file. Resume requires matching settings and skips every saved case, including errors. It does not retry failed cases. Use a new output filename for a fresh run.

The settings include a checksum of `ollama_client.py`. You cannot resume a run started before the repair step was added; start a new output file instead.

### 12. Run the 50 dev cases

Run both modes on the 50-case `dev` split of the short-input manifest, then score each one. Use the same `--manifest` and `--split` for `generate` and `score`.

```bash
python -m src.evaluation generate --manifest evaluation/cases_short4000_context4000.json --split dev --mode conflict_only --timeout 120 --output results/short4000_dev_conflict_only.jsonl
python -m src.evaluation score --manifest evaluation/cases_short4000_context4000.json --split dev --predictions results/short4000_dev_conflict_only.jsonl --out-dir results/short4000_dev_conflict_only_scores

python -m src.evaluation generate --manifest evaluation/cases_short4000_context4000.json --split dev --mode commit_context --timeout 120 --output results/short4000_dev_commit_context.jsonl
python -m src.evaluation score --manifest evaluation/cases_short4000_context4000.json --split dev --predictions results/short4000_dev_commit_context.jsonl --out-dir results/short4000_dev_commit_context_scores
```

Compare the two `summary.json` files to see whether commit context changes the scores.

---
