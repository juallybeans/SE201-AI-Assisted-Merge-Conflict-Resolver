# AI Use Log: Lua Yi Ning

<!--
Copy this file to ai-log/<your-name>.md in your team repository
(for example ai-log/wei-ling.md). Edit only your own file.

Add one entry per task in which an AI tool produced or substantially
shaped work you submit. Add entries as you work, and commit each entry
together with the work it describes.

Outcome is one of: Kept, Changed, Rejected.
Delete the example entry below before your first commit.
-->

## 2026-10-15 · Model integration
- **Tool (model):** Claude Code (Opus 5.5)
- **Files:** src/resolver/ollama_client.py, tests/test_ollama_client.py
- **Outcome:** Changed

### What I asked for
A skeleton for the model integration: retrieving the relevant info and feeding it to model to generate a proposed merge resolution.

### How I checked it
Ran the test file, which passed by showing an output from the model. Reviewed the logic by hand to ensure work is correctly done.

### What I kept or changed
Kept the structure of folders. 