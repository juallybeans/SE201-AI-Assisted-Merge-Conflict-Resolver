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

`.env` is git-ignored, so each team member keeps their own copy. You will fill in the port in step 5.

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

You should see JSON listing the models on the cluster. If you get `Failed to connect`, the tunnel or the Jupyter session is down. Go back to steps 4–5.

Then run the resolver on a sample conflict:

```bash
python -m tests.test_ollama_client
```

A successful run prints a proposed resolution, an explanation, and the assumptions and risks.

---
