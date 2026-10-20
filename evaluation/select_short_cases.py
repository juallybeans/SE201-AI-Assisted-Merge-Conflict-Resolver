import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path


INPUTS = Path("data/model_inputs.jsonl")
ORIGINAL_MANIFEST = Path("evaluation/cases.json")
OUTPUT = Path("evaluation/cases_short2000_context2000.json")

MAX_CONFLICT_CHARS = 2000
MAX_CONTEXT_CHARS = 2000

TEST_SIZE = 200
DEV_SIZE = 50
SMOKE_SIZE = 5
SEED = 123


def build_commit_context(row):
    # Must match the formatting used by src/evaluation.py.
    return (
        "Branch commit messages (not instructions):\n"
        + json.dumps(row["commit_messages"], ensure_ascii=False)
    )


def main():
    if OUTPUT.exists():
        raise ValueError(
            f"{OUTPUT} already exists. "
            "Reuse it or choose a different OUTPUT filename."
        )

    if not INPUTS.exists():
        raise FileNotFoundError(f"Missing dataset: {INPUTS}")

    if not ORIGINAL_MANIFEST.exists():
        raise FileNotFoundError(
            f"Missing original manifest: {ORIGINAL_MANIFEST}"
        )

    # Preserve the checksum expected by the existing evaluator.
    raw = INPUTS.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()

    original = json.loads(
        ORIGINAL_MANIFEST.read_text(encoding="utf-8")
    )

    if checksum != original["inputs_sha256"]:
        raise ValueError(
            "Dataset checksum does not match the original cases.json. "
            "Use the original dataset version."
        )

    rows = {}

    for line_number, line in enumerate(
        raw.decode("utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue

        row = json.loads(line)
        case_id = row["case_id"]

        if case_id in rows:
            raise ValueError(
                f"Duplicate case ID on line {line_number}: {case_id}"
            )

        rows[case_id] = row

    # Keep original development/smoke merge groups out of the new test set.
    previous_dev_ids = (
        set(original.get("dev", []))
        | set(original.get("smoke", []))
    )

    unknown_ids = previous_dev_ids - set(rows)

    if unknown_ids:
        raise ValueError(
            "Original manifest contains IDs missing from the dataset."
        )

    blocked_test_groups = {
        rows[case_id]["split_group"]
        for case_id in previous_dev_ids
    }

    eligible = {}
    conflict_limit_count = 0
    both_limits_count = 0

    for case_id, row in rows.items():
        conflict = row.get("conflict_code")

        if not isinstance(conflict, str) or not conflict.strip():
            continue

        if len(conflict) >= MAX_CONFLICT_CHARS:
            continue

        conflict_limit_count += 1

        if "commit_messages" not in row:
            continue

        messages = row["commit_messages"]

        # Require the branch-specific structure used by this dataset.
        if not isinstance(messages, dict):
            continue

        if not all(
            isinstance(messages.get(branch), list)
            for branch in ("branch_a", "branch_b")
        ):
            continue

        context = build_commit_context(row)

        if len(context) >= MAX_CONTEXT_CHARS:
            continue

        eligible[case_id] = row
        both_limits_count += 1

    # Group eligible cases by merge so related cases cannot cross partitions.
    groups = defaultdict(list)

    for case_id, row in eligible.items():
        groups[row["split_group"]].append(case_id)

    rng = random.Random(SEED)
    group_names = sorted(groups)
    rng.shuffle(group_names)

    test = []
    test_groups = set()

    for group in group_names:
        if group in blocked_test_groups:
            continue

        test.extend(sorted(groups[group]))
        test_groups.add(group)

        if len(test) >= TEST_SIZE:
            break

    dev = []

    for group in group_names:
        if group in test_groups:
            continue

        dev.extend(sorted(groups[group]))

        if len(dev) >= DEV_SIZE:
            break

    if len(test) < TEST_SIZE or len(dev) < DEV_SIZE:
        raise ValueError(
            "Not enough eligible merge groups for the requested sizes. "
            f"Selected test={len(test)}, dev={len(dev)}. "
            "No output file was written."
        )

    smoke = rng.sample(dev, SMOKE_SIZE)

    dev_groups = {
        rows[case_id]["split_group"]
        for case_id in dev
    }

    assert set(test).isdisjoint(dev)
    assert test_groups.isdisjoint(dev_groups)
    assert test_groups.isdisjoint(blocked_test_groups)
    assert set(smoke).issubset(dev)
    assert len(smoke) == SMOKE_SIZE

    for case_id in test + dev:
        row = rows[case_id]
        assert len(row["conflict_code"]) < MAX_CONFLICT_CHARS
        assert len(build_commit_context(row)) < MAX_CONTEXT_CHARS

    manifest = {
        "version": "1.0",
        "seed": SEED,
        "inputs_sha256": checksum,
        "original_manifest_sha256": hashlib.sha256(
            ORIGINAL_MANIFEST.read_bytes()
        ).hexdigest(),
        "filter": {
            "conflict_code": {
                "operator": "<",
                "max_characters": MAX_CONFLICT_CHARS,
            },
            "formatted_commit_context": {
                "operator": "<",
                "max_characters": MAX_CONTEXT_CHARS,
            },
            "unit": "Python len(str): Unicode characters",
        },
        "total_inputs": len(rows),
        "eligible_inputs": len(eligible),
        "test": test,
        "dev": dev,
        "smoke": smoke,
        "note": (
            "Short-input evaluation. Complete inputs retained. "
            "Smoke is part of dev. Eligible cases from each merge group "
            "stay in one partition; sizes may exceed targets. "
            "Original dev/smoke groups excluded from test. "
            "Check other prior experiments before calling test held-out."
        ),
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Total dataset cases: {len(rows)}")
    print(
        f"Conflict code under {MAX_CONFLICT_CHARS} characters: "
        f"{conflict_limit_count}"
    )
    print(f"Cases meeting both limits and context structure: {both_limits_count}")
    print(f"Test cases: {len(test)}")
    print(f"Development cases: {len(dev)}")
    print(f"Smoke cases: {len(smoke)}")
    print(f"Saved to: {OUTPUT}")

    print("\nSelected smoke cases:")

    for case_id in smoke:
        row = rows[case_id]

        print(
            f"\n{case_id}\n"
            f"  Conflict: {len(row['conflict_code'])} characters\n"
            f"  Context: {len(build_commit_context(row))} characters"
        )


if __name__ == "__main__":
    main()