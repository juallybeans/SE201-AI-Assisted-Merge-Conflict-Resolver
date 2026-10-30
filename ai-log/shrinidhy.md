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





