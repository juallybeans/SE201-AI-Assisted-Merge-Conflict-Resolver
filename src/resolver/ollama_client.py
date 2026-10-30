
import json
import os
import re
import textwrap

import requests
from dotenv import load_dotenv


load_dotenv()

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen2.5-coder:7b",
)

CONFLICT_MARKERS = re.compile(
    r"^(?:<{7,}(?: .*)?|={7,}|>{7,}(?: .*)?|\|{7,}(?: .*)?)$",
    re.MULTILINE,
)

RESPONSE_REQUIREMENTS = """
Return valid JSON with exactly these fields:
{
  "resolution": "The complete resolved code as a string",
  "explanation": "Explain what was retained, combined, or changed",
  "assumptions": "Describe uncertainties and possible risks"
}

The resolution field must contain code only, without Markdown fences.
"""


class OllamaResolverError(RuntimeError):
    """Raised when Ollama cannot generate a resolution."""


def check_syntax(code: str) -> str:
    """
    Check a proposed resolution for leftover conflict markers and
    Python syntax errors.

    Returns an empty string when the code passes, otherwise a short
    description of the first problem found.
    """
    match = CONFLICT_MARKERS.search(code)
    if match:
        line = code.count("\n", 0, match.start()) + 1
        return f"Conflict marker remains on line {line}: {match.group(0)}"

        code = textwrap.dedent(code)
    try:
        compile(code, "<resolution>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        # A conflict inside a function body is resolved without its def
        # line, so return/yield/await are valid there. Check it inside one.
        wrapped = "async def _resolution():\n" + textwrap.indent(code, "    ")
        try:
            compile(wrapped, "<resolution>", "exec", dont_inherit=True)
        except SyntaxError:
            return f"SyntaxError on line {exc.lineno}: {exc.msg}"
    except (ValueError, TypeError, RecursionError) as exc:
        return f"{type(exc).__name__}: {exc}"

    return ""


def _generate(prompt: str, timeout: int) -> dict:
    """Send one prompt to Ollama and return the parsed JSON reply."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": {
            "type": "object",
            "properties": {
                "resolution": {"type": "string"},
                "explanation": {"type": "string"},
                "assumptions": {"type": "string"},
            },
            "required": ["resolution", "explanation", "assumptions"],
        },
        "stream": True,
        "keep_alive": "2h",
        # Cap output so a model stuck repeating itself in JSON mode
        # cannot generate until the client times out.
        "options": {
            "num_predict": 4096,
            "num_ctx": 8192,
            "temperature": 0,
            "seed": 0,
        },
    }

    try:
        chunks = []
        result = {}
        with requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            stream=True,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                result = json.loads(line)
                chunks.append(result.get("response", ""))
                if result.get("done"):
                    break

        generated_text = "".join(chunks)

        if not result.get("done"):
            raise OllamaResolverError(
                "Ollama stream ended before the reply was complete."
            )
        if result.get("done_reason") == "length":
            raise OllamaResolverError(
                "Reply hit the num_predict cap before finishing."
            )

        if not generated_text.strip():
            raise OllamaResolverError(
                "Ollama returned an empty response."
            )

        parsed = json.loads(generated_text)

        required_fields = (
            "resolution",
            "explanation",
            "assumptions",
        )

        # Small models sometimes return a list or object for the
        # free-text fields; keep it as text rather than failing.
        for field in ("explanation", "assumptions"):
            value = parsed.get(field)
            if isinstance(value, list):
                parsed[field] = "\n".join(str(item) for item in value)
            elif isinstance(value, dict):
                parsed[field] = json.dumps(value, ensure_ascii=False)
            elif value is None:
                parsed[field] = ""
            elif not isinstance(value, str):
                parsed[field] = str(value)

        for field in required_fields:
            if not isinstance(parsed.get(field), str):
                raise OllamaResolverError(
                    f"Model response has an invalid '{field}' field."
                )

        if not parsed["resolution"].strip():
            raise OllamaResolverError(
                "The proposed resolution is empty."
            )

        # "length" means generation stopped at the num_predict cap.
        parsed["done_reason"] = result.get("done_reason", "")
        parsed["eval_count"] = result.get("eval_count")

        return parsed

    except requests.RequestException as exc:
        raise OllamaResolverError(
            f"Could not connect to Ollama at "
            f"{OLLAMA_BASE_URL}: {exc}"
        ) from exc
    except (ValueError, TypeError) as exc:
        raise OllamaResolverError(
            f"Could not parse Ollama's JSON response: {exc}"
        ) from exc


def repair_resolution(
    conflict: str,
    context: str,
    previous_resolution: str,
    syntax_error: str,
    timeout: int = 300,
) -> dict:
    """
    Ask the model to fix a resolution that failed the syntax check.

    The prompt includes the original conflict, the failed resolution
    and the error message so the model can correct only what is broken.
    Returns the same fields as resolve_conflict's model reply.
    """
    prompt = f"""
You are an expert software engineer assisting with Git merges.

Your previous resolution of this merge conflict failed the syntax
check. Fix the error and return a corrected resolution.

Requirements:
1. Fix the reported error and any other syntax problems.
2. Keep the merge decisions of the previous resolution unless they
   caused the error; do not discard either side's changes.
3. Remove all Git conflict markers from the proposed code.
4. Do not invent requirements unsupported by the context.

Repository context and relevant commit messages:
{context or "(No additional context provided)"}

Original conflicting code:
{conflict}

Previous resolution (failed the syntax check):
{previous_resolution}

Syntax check error:
{syntax_error}
{RESPONSE_REQUIREMENTS}"""

    return _generate(prompt, timeout)


def resolve_conflict(
    conflict: str,
    context: str = "",
    timeout: int = 300,
    repair: bool = True,
) -> dict:
    """
    Generate a proposed merge resolution.

    If the first resolution fails the syntax check, one repair attempt
    is made using the error as feedback.

    Args:
        conflict: The conflicting Git code, including markers.
        context: Optional surrounding code and branch-specific
                 commit messages.
        timeout: Maximum seconds to wait for each Ollama request.
        repair: Whether to make one repair attempt when the first
                resolution fails the syntax check.

    Returns:
        A dictionary containing:
        - resolution: proposed merged code (repaired, if a repair
          attempt produced one)
        - explanation: explanation of the changes
        - assumptions: assumptions or unresolved risks
        - syntax_error: error for the returned resolution, or ""
        - repair_attempted: whether a repair request was sent
        - initial_resolution: the first resolution, before repair
        - initial_syntax_error: error for the first resolution, or ""
        - repair_error: why the repair request failed, or ""
    """
    if not conflict.strip():
        raise ValueError("Conflict content cannot be empty.")

    prompt = f"""
You are an expert software engineer assisting with Git merges.

Resolve the supplied merge conflict conservatively.

Requirements:
1. Understand both the current and incoming changes.
2. Preserve the intended functionality of both sides when
   they are compatible.
3. Do not invent requirements unsupported by the context.
4. Remove all Git conflict markers from the proposed code.
5. If the changes are incompatible or the intent is unclear,
   explain the uncertainty instead of silently discarding a change.
6. Do not claim that the resolution passes tests unless tests
   have actually been run.

Repository context and relevant commit messages:
{context or "(No additional context provided)"}

Conflicting code:
{conflict}
{RESPONSE_REQUIREMENTS}"""

    result = _generate(prompt, timeout)
    error = check_syntax(result["resolution"])
    result.update(
        syntax_error=error,
        repair_attempted=False,
        initial_resolution=result["resolution"],
        initial_syntax_error=error,
        repair_error="",
    )

    if not error or not repair:
        return result

    result["repair_attempted"] = True
    try:
        repaired = repair_resolution(
            conflict, context, result["resolution"], error, timeout,
        )
    except OllamaResolverError as exc:
        # Keep the first resolution rather than losing the case.
        result["repair_error"] = str(exc)
        return result

    result.update(
        resolution=repaired["resolution"],
        explanation=repaired["explanation"],
        assumptions=repaired["assumptions"],
        done_reason=repaired["done_reason"],
        eval_count=repaired["eval_count"],
        syntax_error=check_syntax(repaired["resolution"]),
    )
    return result
