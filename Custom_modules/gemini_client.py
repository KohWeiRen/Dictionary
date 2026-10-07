"""Gemini REST structured output using the project's existing httpx dependency."""
import os
import json
import logging
import re
import uuid
from typing import TypeVar
import httpx
from pydantic import BaseModel, ValidationError

# Verified against Google's current model and free-tier documentation.
DEFAULT_MODEL = "gemini-3.8-flash"
FREE_TIER_MODELS = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash")
MAX_MODEL_ATTEMPTS = 3
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
Result = TypeVar("Result", bound=BaseModel)
logger = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    """Safe Telegram message; logs contain matching diagnostic reference."""


class _AttemptFailure(AIUnavailable):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def model_sequence() -> tuple[str, ...]:
    primary = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    configured = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
    if configured.lower() == "none":
        return (primary,)
    fallbacks = [item.strip() for item in configured.split(",") if item.strip()] if configured else list(FREE_TIER_MODELS)
    if any(model not in FREE_TIER_MODELS for model in fallbacks):
        raise AIUnavailable("GEMINI_FALLBACK_MODELS must contain verified free-tier models: " + ", ".join(FREE_TIER_MODELS) + "; or use 'none'.")
    return tuple(dict.fromkeys([primary, *fallbacks]))[:MAX_MODEL_ATTEMPTS]


def _safe(value, limit=700) -> str:
    text = str(value)
    # Provider errors may echo configuration, including credentials.
    for name in ("GEMINI_API_KEY", "DATABASE_URL", "TELEGRAM_API_KEY", "WEBHOOK_SECRET"):
        secret = os.getenv(name, "")
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"AIza[\w-]{15,}", "[redacted]", text)
    text = re.sub(r"(?:postgres(?:ql)?://|https?://)\S+", "[url redacted]", text)
    text = re.sub(r"(?i)(?:api[_ -]?key|token|password)\s*[=:]\s*[^\s,;]+", "credential=[redacted]", text)
    return " ".join(text.split())[:limit]


def _provider_schema(schema: type[BaseModel]) -> dict:
    """Keep string length checks locally; express them as provider guidance.

    Gemini documents a JSON Schema subset without minLength/maxLength. Sending
    those constraints verbatim can cause rejection or leave them unenforced.
    """
    def convert(value):
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        # Property names are data, not schema keywords.
        result = {}
        for key, item in value.items():
            if key in ("minLength", "maxLength"):
                continue
            if key in ("properties", "$defs"):
                result[key] = {name: convert(child) for name, child in item.items()}
            else:
                result[key] = convert(item)
        if "maxLength" in value or "minLength" in value:
            limits = []
            if value.get("minLength"):
                limits.append(f"at least {value['minLength']}")
            if "maxLength" in value:
                limits.append(f"at most {value['maxLength']}")
            result["description"] = (result.get("description", "") + " Use " + " and ".join(limits) + " characters.").strip()
        return result
    return convert(schema.model_json_schema())


def generate_json(system: str, prompt: str | list[dict], schema: type[Result]) -> Result:
    sequence = model_sequence()
    reference = uuid.uuid4().hex[:8]
    last = None
    for attempt, model in enumerate(sequence, start=1):
        try:
            result = _generate_once(system, prompt, schema, model, reference, attempt)
            if attempt > 1:
                logger.warning("%s", json.dumps({"event": "gemini_fallback_success", "ref": reference,
                    "model": _safe(model, 100), "attempt": attempt}))
            return result
        except _AttemptFailure as error:
            if not error.retryable:
                raise
            last = error
    raise AIUnavailable(f"All {len(sequence)} configured Gemini model attempts failed. Last error: {last}") from None


def _generate_once(system, prompt, schema, model, reference, attempt):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise AIUnavailable("Spanish AI isn't configured yet. Set GEMINI_API_KEY on Render and in GitHub Actions secrets, then retry /lesson.")
    from Custom_modules.learning_store import reserve_request
    reserve_request()
    def fail(category, message, retryable=False, **details):
        record = {"event": "gemini_failure", "ref": reference, "model": _safe(model, 100), "attempt": attempt, "category": category}
        record.update(details)
        logger.error("%s", json.dumps(record, ensure_ascii=False))
        raise _AttemptFailure(f"{message} [Gemini: {category}; ref {reference}]", retryable) from None

    body = {
        "model": model,
        "system_instruction": system,
        "input": prompt,
        "store": False,
        "generation_config": {"max_output_tokens": 8192},
        "response_format": {"type": "text", "mime_type": "application/json", "schema": _provider_schema(schema)},
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as client:
            response = client.post(ENDPOINT, headers={"x-goog-api-key": key}, json=body)
    except httpx.TimeoutException as error:
        fail("timeout", "Gemini timed out before returning a lesson. Try again later.", retryable=True, error_type=type(error).__name__)
    except httpx.HTTPError as error:
        fail("network", "The bot couldn't connect to Gemini. Try again later.", error_type=type(error).__name__)

    if response.is_error:
        try:
            payload = response.json()
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            error = error if isinstance(error, dict) else {}
        except ValueError:
            error = {}
        status = response.status_code
        provider_status = _safe(error.get("status", "unspecified"), 80)
        reason = _safe(error.get("message", "No provider explanation returned."))
        details = {"http_status": status, "provider_status": provider_status, "reason": reason}
        if status == 429:
            category, message = "quota", "Gemini's rate or quota limit was reached. Try later; saved lessons still work."
        elif status in (401, 403):
            category, message = "auth", "Gemini rejected the API key or project access. Check the key in AI Studio."
        elif status == 404:
            category, message = "model", f"Gemini couldn't find the configured model ({_safe(model, 100)}) or API endpoint."
        elif status in (400, 422):
            category, message = "request", "Gemini rejected the request."
        else:
            category, message = "provider", "Gemini returned a service error. Try again later."
        fail(category, f"{message} HTTP {status} / {provider_status}: {reason[:250]}",
             retryable=status in (404, 500, 502, 503, 504), **details)

    try:
        result = response.json()
    except ValueError:
        fail("response_json", "Gemini returned a response that wasn't valid JSON.", retryable=True, http_status=response.status_code)
    if not isinstance(result, dict):
        fail("response_shape", "Gemini returned an unexpected response format.", retryable=True, http_status=response.status_code)
    status = result.get("status")
    if status != "completed":
        safe_status = _safe(status or "missing", 80)
        fail("incomplete", f"Gemini didn't finish the response (status: {safe_status}). Try later.",
             retryable=status == "incomplete", interaction_status=safe_status)

    parts = []
    steps = result.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            content = step.get("content")
            if isinstance(content, list):
                parts.extend(part["text"] for part in content if isinstance(part, dict)
                             and part.get("type") == "text" and isinstance(part.get("text"), str))
    # Older Interactions deployments used outputs instead of steps.
    if not parts and isinstance(result.get("outputs"), list):
        parts = [part["text"] for part in result["outputs"] if isinstance(part, dict)
                 and part.get("type") == "text" and isinstance(part.get("text"), str)]
    if not parts:
        fail("empty_output", "Gemini finished but returned no lesson text.", retryable=True, interaction_status="completed")
    try:
        return schema.model_validate_json("".join(parts))
    except ValidationError as error:
        fields = [{"field": _safe(".".join(str(item) for item in entry["loc"]) or "lesson", 100),
                   "type": _safe(entry["type"], 80)}
                  for entry in error.errors(include_input=False, include_context=False, include_url=False)[:8]]
        category = "output_json" if any(item["type"] == "json_invalid" for item in fields) else "validation"
        summary = "; ".join(f"{item['field']} ({item['type']})" for item in fields[:3])
        fail(category, f"Gemini returned an invalid lesson: {summary}. Try /lesson again.", retryable=True, fields=fields)
