import pytest

from src.resolver import ollama_client
from src.resolver.ollama_client import (
    OllamaResolverError,
    check_syntax,
    repair_resolution,
    resolve_conflict,
)


conflict = """<<<<<<< HEAD
def greet(name):
    return f"Hello, {name}!"
=======
def greet(name):
    return f"Hi, {name}!"
>>>>>>> feature/greeting
"""

context = """
Current branch commit: Add personalised greeting.
Incoming branch commit: Change greeting wording.
"""

VALID = 'def greet(name):\n    return f"Hello, {name}!"\n'
BROKEN = 'def greet(name)\n    return f"Hello, {name}!"\n'


def reply(resolution, explanation="merged"):
    return dict(resolution=resolution, explanation=explanation,
                assumptions="", done_reason="stop", eval_count=10)


@pytest.fixture
def fake_model(monkeypatch):
    """Replace the Ollama call with queued replies and record prompts."""
    prompts = []
    replies = []

    def fake_generate(prompt, timeout):
        prompts.append(prompt)
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ollama_client, "_generate", fake_generate)
    return prompts, replies


class TestCheckSyntax:
    def test_valid_code_passes(self):
        assert check_syntax(VALID) == ""

    def test_syntax_error_reports_line(self):
        assert check_syntax(BROKEN).startswith("SyntaxError on line 1")

    def test_leftover_markers_fail(self):
        assert "Conflict marker remains on line 1" in check_syntax(conflict)

    def test_indented_snippet_passes(self):
        assert check_syntax("    x = 1\n    y = 2\n") == ""


class TestRepair:
    def test_valid_first_resolution_skips_repair(self, fake_model):
        prompts, replies = fake_model
        replies.append(reply(VALID))
        result = resolve_conflict(conflict, context)
        assert len(prompts) == 1
        assert result["repair_attempted"] is False
        assert result["resolution"] == VALID
        assert result["syntax_error"] == ""

    def test_failed_syntax_triggers_one_repair(self, fake_model):
        prompts, replies = fake_model
        replies.extend([reply(BROKEN, "first"), reply(VALID, "fixed")])
        result = resolve_conflict(conflict, context)
        assert len(prompts) == 2
        assert result["repair_attempted"] is True
        assert result["resolution"] == VALID
        assert result["explanation"] == "fixed"
        assert result["syntax_error"] == ""
        assert result["initial_resolution"] == BROKEN
        assert result["initial_syntax_error"].startswith("SyntaxError")

    def test_repair_prompt_includes_failed_code_and_error(self, fake_model):
        prompts, replies = fake_model
        replies.extend([reply(BROKEN), reply(VALID)])
        resolve_conflict(conflict, context)
        repair_prompt = prompts[1]
        assert BROKEN in repair_prompt
        assert "SyntaxError on line 1" in repair_prompt
        assert conflict in repair_prompt
        assert "Change greeting wording" in repair_prompt

    def test_only_one_repair_when_repair_also_fails(self, fake_model):
        prompts, replies = fake_model
        replies.extend([reply(BROKEN), reply(BROKEN)])
        result = resolve_conflict(conflict, context)
        assert len(prompts) == 2
        assert result["repair_attempted"] is True
        assert result["syntax_error"].startswith("SyntaxError")

    def test_repair_request_error_keeps_first_resolution(self, fake_model):
        prompts, replies = fake_model
        replies.extend([reply(BROKEN), OllamaResolverError("timed out")])
        result = resolve_conflict(conflict, context)
        assert result["resolution"] == BROKEN
        assert result["repair_error"] == "timed out"

    def test_repair_can_be_disabled(self, fake_model):
        prompts, replies = fake_model
        replies.append(reply(BROKEN))
        result = resolve_conflict(conflict, context, repair=False)
        assert len(prompts) == 1
        assert result["repair_attempted"] is False
        assert result["syntax_error"].startswith("SyntaxError")

    def test_first_request_error_is_raised(self, fake_model):
        prompts, replies = fake_model
        replies.append(OllamaResolverError("down"))
        with pytest.raises(OllamaResolverError):
            resolve_conflict(conflict, context)


def print_result(result):
    print("=== Proposed resolution ===")
    print(result["resolution"])

    print("\n=== Explanation ===")
    print(result["explanation"])

    print("\n=== Assumptions and risks ===")
    print(result["assumptions"])

    print("\n=== Syntax check ===")
    print(f"FAIL: {result['syntax_error']}" if result.get("syntax_error")
          else "PASS")


def main():
    print("##### 1. Resolve the sample conflict #####\n")
    result = resolve_conflict(conflict=conflict, context=context)
    print_result(result)
    print(f"\nRepair attempted: {result['repair_attempted']}")
    if result["repair_attempted"]:
        print(f"First attempt failed with: {result['initial_syntax_error']}")

    # Models rarely fail on this small example, so also send a broken
    # resolution straight to the repair step to show it working.
    print("\n##### 2. Repair a deliberately broken resolution #####\n")
    print("Broken resolution:")
    print(BROKEN)
    error = check_syntax(BROKEN)
    print(f"Syntax check: FAIL: {error}\n")
    repaired = repair_resolution(conflict, context, BROKEN, error)
    repaired["syntax_error"] = check_syntax(repaired["resolution"])
    print_result(repaired)


if __name__ == "__main__":
    main()
