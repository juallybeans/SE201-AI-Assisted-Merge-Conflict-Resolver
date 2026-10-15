
import json
import os

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


class OllamaResolverError(RuntimeError):
    """Raised when Ollama cannot generate a resolution."""


def resolve_conflict(
    conflict: str,
    context: str = "",
    timeout: int = 300,
) -> dict:
    """
    Generate a proposed merge resolution.

    Args:
        conflict: The conflicting Git code, including markers.
        context: Optional surrounding code and branch-specific
                 commit messages.
        timeout: Maximum seconds to wait for Ollama.

    Returns:
        A dictionary containing:
        - resolution: proposed merged code
        - explanation: explanation of the changes
        - assumptions: assumptions or unresolved risks
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

Return valid JSON with exactly these fields:
{{
  "resolution": "The complete resolved code as a string",
  "explanation": "Explain what was retained, combined, or changed",
  "assumptions": "Describe uncertainties and possible risks"
}}

The resolution field must contain code only, without Markdown fences.
"""

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "keep_alive": "30m",
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()

        result = response.json()
        generated_text = result.get("response", "")

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

        for field in required_fields:
            if not isinstance(parsed.get(field), str):
                raise OllamaResolverError(
                    f"Model response has an invalid '{field}' field."
                )

        if not parsed["resolution"].strip():
            raise OllamaResolverError(
                "The proposed resolution is empty."
            )

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
