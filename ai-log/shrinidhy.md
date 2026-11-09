# AI Use Log: S Shrinidhy

<!--
Copy this file to ai-log/<your-name>.md in your team repository
(for example ai-log/wei-ling.md). Edit only your own file.

Add one entry per task in which an AI tool produced or substantially
shaped work you submit. Add entries as you work, and commit each entry
together with the work it describes.

Outcome is one of: Kept, Changed, Rejected.
Delete the example entry below before your first commit.
-->

## 2026-10-16 · GitGoodBench Data Preparation
- **Tool (model):** ChatGPT (GPT-6)
- **Files:** model_inputs.jsonl, evaluation_references.jsonl 
- **Outcome:** Changed

### What I asked for
Assistance with automating the reconstruction of GitGoodBench merge conflict cases by retrieving the original GitHub repositories and extracting conflicting code, commit context and reference resolutions for model input and evaluation.

### How I checked it
I reviewed the extraction results, audit logs and output files to verify its success.

### What I kept or changed
I kept the extracted model inputs and reference resolutions and separated the two files for model testing and evaluation. 


## 2026-10-30 · Git ours/theirs/union baselines
- **Tool (model):** Cursor Grok 4.7
- **Files:** src/baselines.py, src/evaluation.py, tests/test_baselines.py, tests/test_evaluation.py, README.md
- **Outcome:** Kept

### What I asked for
Implement the git ours, theirs and union baselines from the proposal so each conflicted file can be resolved without the model and scored with the existing evaluation harness.

### How I checked it
I ran the unit tests, including a check that the resolutions match `git merge-file` on the same inputs. I also generated and scored the three baselines on the short 4000-character dev split.

### What I kept or changed:
I kept the baseline modes on `python -m src.evaluation generate` (`--mode ours`, `theirs` and `union`) and the tests that compare them with git. 

## 2026-11-07 · Test-split run of every mode and baseline
- **Tool (model):** Claude Sonnet 5.5
- **Files:** results/test_split/ (conflict_only, commit_context and retrieved_context runs, with their scores), results/test_split/baselines/ (ours, theirs, union and their scores)
- **Outcome:** Changed

### What I asked for
Help running the 200-case test split through every mode (conflict_only, commit_context, retrieved_context) and every baseline (ours, theirs, union). I also asked for the results to be scored with the same evaluation harness as the dev split.

### How I checked it
I checked that each of the six runs had all 200 cases in its score summary. I checked that all three evaluation modes used the same 200 test cases by comparing their case IDs and confirming that no cases were missing or duplicated. This helped ensure a fair comparison across the different approaches. The model run was interrupted around case 122 when the connection to the model server on the cluster dropped. I saved the partial results and backups, then re-ran so that every case had a result. I compared the model modes against the baselines instead of treating the model scores alone as the answer.

### What I kept or changed
I kept the final 200-case results for all six modes and baselines. I did not keep the partial and backup files from the interrupted run as results. I also found that scoring whole files makes the model modes look very weak (0-1% for exact match, against 25.5% for ours). Hence, because of this, we as a team later moved to scoring one conflict block at a time, where the model modes scored 10-13% exact match.





