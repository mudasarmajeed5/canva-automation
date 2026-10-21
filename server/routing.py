"""Data-driven Google Drive folder routing."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_CONFIG = Path(__file__).with_name("routing.yaml")


def _normalized(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value.casefold())


def load_config() -> dict:
    with _CONFIG.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def account_keyword(account: str) -> str:
    keyword = load_config().get("accounts", {}).get(account)
    if not keyword:
        raise KeyError(f"no Drive routing rule for account {account!r}")
    return _normalized(keyword)


def content_hint(file_name: str, task_type: str) -> str:
    config = load_config()
    lowered = file_name.casefold()
    for rule in config.get("file_rules", []):
        if any(keyword.casefold() in lowered for keyword in rule.get("keywords", [])):
            return _normalized(str(rule["hint"]))
    fallback = config.get("task_fallbacks", {}).get(task_type)
    return _normalized(str(fallback)) if fallback else ""


def matches(name: str, keyword: str) -> bool:
    return _normalized(keyword) in _normalized(name)
