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

### 5. Test the CLI

You test the CLI by creating a small practice repository next to this project, making a merge conflict in it, and resolving the conflict with the tool. The commands below are for Windows.

#### Set up a practice repository

From the project root, create the repository in a sibling folder:

```bash
cd ..
mkdir demo-playground
cd demo-playground
git init -b main
notepad shop.py
```

Paste this **base version** into `shop.py`, then save and close Notepad:

```python
def add(a, b):
    return a - b


def helper_one():
    pass


def greet(name):
    return "Hello " + name


def helper_two():
    pass


def total(prices):
    return sum(prices)
```

Commit it, then create an `incoming` branch:

```bash
git add shop.py
git commit -m "Base version"
git switch -c incoming
notepad shop.py
```

Replace everything with this **incoming version**, then save and close:

```python
def add(a, b):
    return a + b


def helper_one():
    pass


def greet(name):
    return f"Hi, {name}!"


def helper_two():
    pass


def total(prices):
    return 0  # temporary stub
```

Commit it and switch back to `main`:

```bash
git commit -am "Incoming: fix add, friendlier greeting, stub total"
git switch main
notepad shop.py
```

Replace everything with this **main version**, then save and close:

```python
def add(a, b):
    return a - b  # TODO: check this


def helper_one():
    pass


def greet(name):
    return "Hello, " + name.title()


def helper_two():
    pass


def total(prices):
    return sum(p for p in prices if p > 0)
```

Commit it and go back to the project:

```bash
git commit -am "Main: comment on add, title-case name, ignore negative prices"
cd ..\SE201-AI-Assisted-Merge-Conflict-Resolver
```

Make sure the SSH tunnel is open and `.env` has the right `OLLAMA_BASE_URL` (steps 3–4).

#### Run the merge with the tool

```bash
py -3 -m src.git_tools merge incoming --repo ..\demo-playground
```

Git finds three conflicts in `shop.py` (`add`, `greet` and `total`). For each one, the tool shows the model's suggestion, its explanation and risks, a diff and a syntax check, then asks:

```text
Apply? [a]ccept / [e]dit / [r]eject:
```

Try each option once:

1. **`add`: type `a`** (accept). The suggestion should keep incoming's bug fix (`a + b`).
2. **`greet`: type `e`** (edit). In Notepad, change the line to the following (keep the 4 spaces of indentation), then save and close:
   ```python
       return f"Hi, {name.title()}!"
   ```
   The tool shows the new diff and checks the syntax again. Type `a` to accept it.
3. **`total`: type `r`** (reject). The conflict markers stay in the file.

The tool should print `Applied 2 of 3`. Check the file:

```bash
type ..\demo-playground\shop.py
```

`add` and `greet` are resolved, `total` still has its conflict markers, and the code outside the conflicts is unchanged. Nothing has been committed yet.

#### Resolve the remaining conflict

Run the CLI on the file directly. It only asks about `total`:

```bash
py -3 -m src.cli ..\demo-playground\shop.py
```

Type `e`, change the line to `    return sum(p for p in prices if p > 0)`, save and close, then type `a`.

#### Finish the merge

```bash
cd ..\demo-playground
type shop.py
git add shop.py
git commit -m "Merge incoming"
git log --oneline --graph
```

#### Reset to test again

If you stopped before finishing the merge:

```bash
cd ..\demo-playground
git merge --abort
cd ..\SE201-AI-Assisted-Merge-Conflict-Resolver
```

If you committed the merge:

```bash
cd ..\demo-playground
git reset --hard HEAD~1
cd ..\SE201-AI-Assisted-Merge-Conflict-Resolver
```

This undoes the merge commit. Only run it inside `demo-playground`, since it permanently deletes any uncommitted changes in that folder.
