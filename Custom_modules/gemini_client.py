"""Gemini REST structured output using the project's existing httpx dependency."""
import os
from typing import TypeVar
import httpx
from pydantic import BaseModel, ValidationError

# Verified against Google's current model and free-tier documentation.
DEFAULT_MODEL = "gemini-3.8-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
Result = TypeVar("Result", bound=BaseModel)


class AIUnavailable(RuntimeError):
    """Safe to show in Telegram: never includes provider bodies or API keys."""


def generate_json(system: str, prompt: str | list[dict], schema: type[Result]) -> Result:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise AIUnavailable("Spanish AI isn't configured yet. Set GEMINI_API_KEY on Render and in GitHub Actions secrets, then retry /lesson.")
    from Custom_modules.learning_store import reserve_request
    reserve_request()
    body = {
        "model": os.getenv("GEMINI_MODEL") or DEFAULT_MODEL,
        "system_instruction": system,
        "input": prompt,
        "store": False,
        "generation_config": {"max_output_tokens": 8192},
        "response_format": {"type": "text", "mime_type": "application/json", "schema": schema.model_json_schema()},
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as client:
            response = client.post(ENDPOINT, headers={"x-goog-api-key": key}, json=body)
        if response.status_code == 429:
            raise AIUnavailable("Gemini's free quota is temporarily exhausted. Try later; saved lessons and pronunciation still work.")
        if response.status_code in (401, 403):
            raise AIUnavailable("Gemini rejected the API key or project access. Check GEMINI_API_KEY and the project in AI Studio.")
        if response.status_code == 404:
            raise AIUnavailable("The configured Gemini model is unavailable. Check GEMINI_MODEL against Google's model list.")
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "completed":
            raise AIUnavailable("Gemini couldn't complete this response. Please retry later.")
        parts = [part["text"] for step in result.get("steps", []) if step.get("type") == "model_output"
                 for part in step.get("content", []) if part.get("type") == "text" and isinstance(part.get("text"), str)]
        return schema.model_validate_json("".join(parts))
    except AIUnavailable:
        raise
    except (httpx.HTTPError, ValueError, ValidationError, TypeError, AttributeError, KeyError):
        raise AIUnavailable("Spanish AI is temporarily unavailable or returned an incomplete lesson. Try again later.") from None
