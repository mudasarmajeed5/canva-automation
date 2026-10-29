"""LLM folder routing: GROQ → Gemini → OpenRouter, None → keyword rules.

Returns a validated ``{"folder_id", "path", "confidence", "reason"}`` or None.
The chosen folder_id must exist in the explored tree (anti-hallucination).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from config import Settings

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a Google Drive filing assistant. Choose the single best folder "
    "for the given file from the folder tree. Only use folder IDs that appear "
    "in the tree. Reply with JSON only: "
    '{"folder_id": "...", "path": "...", "confidence": 0.0, "reason": "..."} '
    "where confidence is between 0 and 1."
)


def route(
    tree: list[dict[str, Any]],
    file_name: str,
    account: str,
    task_type: str,
    settings: Settings,
) -> dict[str, Any] | None:
    """Ask GROQ, then Gemini, then OpenRouter. Returns a validated decision or None."""
    if not tree:
        return None
    user_prompt = _build_prompt(tree, file_name, account, task_type)

    decision = _call_groq(settings, user_prompt)
    if decision is None:
        decision = _call_gemini(settings, user_prompt)
    if decision is None:
        decision = _call_openrouter(settings, user_prompt)
    if decision is None:
        log.info("AI routing unavailable (all providers failed); keyword fallback")
        return None

    validated = _validate(decision, tree, settings)
    if validated is None:
        log.info(
            "AI routing rejected decision (confidence/id); keyword fallback: %s",
            json.dumps(decision)[:200],
        )
        return None
    log.info(
        "AI routing chose %s (confidence %.2f): %s",
        validated["path"],
        validated["confidence"],
        validated["reason"],
    )
    return validated


def _build_prompt(
    tree: list[dict[str, Any]], file_name: str, account: str, task_type: str
) -> str:
    lines = ["Folder tree (use only these IDs):"]
    for node in tree:
        indent = "  " * int(node.get("depth", 0))
        lines.append(f"{indent}- [{node['id']}] {node['path']}")
    lines.append("")
    lines.append(f"File name: {file_name}")
    lines.append(f"Business/Account: {account}")
    lines.append(f"Task type: {task_type}")
    lines.append("")
    lines.append(
        "Pick the most specific appropriate folder for this file. "
        "Return JSON only."
    )
    return "\n".join(lines)


def _call_groq(settings: Settings, user_prompt: str) -> dict[str, Any] | None:
    if not settings.groq_api_key:
        return None
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.routing_model_groq,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=settings.routing_timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _parse_json(content)
    except Exception as exc:
        log.warning("GROQ routing failed: %s", exc)
        return None


def _call_gemini(settings: Settings, user_prompt: str) -> dict[str, Any] | None:
    if not settings.gemini_api_key:
        return None
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.routing_model_gemini}:generateContent"
    )
    body = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
        },
    }
    for attempt in range(3):
        try:
            response = requests.post(
                url,
                params={"key": settings.gemini_api_key},
                json=body,
                timeout=settings.routing_timeout_seconds,
            )
            if response.status_code in (429, 503) and attempt < 2:
                continue
            response.raise_for_status()
            data = response.json()
            parts = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            text = "".join(str(part.get("text", "")) for part in parts)
            return _parse_json(text)
        except Exception as exc:
            log.warning("Gemini routing attempt %d failed: %s", attempt + 1, exc)
            if attempt >= 2:
                return None
    return None


def _call_openrouter(settings: Settings, user_prompt: str) -> dict[str, Any] | None:
    if not settings.openrouter_api_key:
        return None
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.routing_model_openrouter,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 500,
            },
            timeout=settings.routing_timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _parse_json(content)
    except Exception as exc:
        log.warning("OpenRouter routing failed: %s", exc)
        return None


def _parse_json(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content or "", re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def _validate(
    decision: dict[str, Any], tree: list[dict[str, Any]], settings: Settings
) -> dict[str, Any] | None:
    folder_id = str(decision.get("folder_id") or "").strip()
    if not folder_id:
        return None
    ids = {str(node["id"]) for node in tree}
    if folder_id not in ids:
        log.warning("AI returned folder_id not in tree: %s", folder_id)
        return None
    try:
        confidence = float(decision.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if confidence < settings.routing_min_confidence:
        return None
    match = next((n for n in tree if str(n["id"]) == folder_id), None)
    if match is None:
        return None
    return {
        "folder_id": folder_id,
        "path": str(match["path"]),
        "confidence": confidence,
        "reason": str(decision.get("reason") or ""),
    }
