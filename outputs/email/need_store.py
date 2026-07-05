from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from email.io import atomic_write_json
from email.schemas import EvidenceNeed

NEED_STORE_FILE = "email-needs.json"
NEED_STORE_OBJECT_TYPE = "email_need_store"
NEED_STORE_OBJECT_VERSION = "0.1"


def need_store_path(artifact_dir: Path) -> Path:
    return artifact_dir / NEED_STORE_FILE


def empty_need_store() -> dict[str, Any]:
    return {
        "object_type": NEED_STORE_OBJECT_TYPE,
        "object_version": NEED_STORE_OBJECT_VERSION,
        "needs": [],
    }


def load_need_store(artifact_dir: Path) -> dict[str, Any]:
    path = need_store_path(artifact_dir)
    if not path.exists():
        return empty_need_store()
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return validate_need_store(value)


def save_need_store(artifact_dir: Path, store: dict[str, Any]) -> None:
    validated = validate_need_store(store)
    atomic_write_json(need_store_path(artifact_dir), validated)


def validate_need_store(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("EvidenceNeedStore must be an object")
    object_type = value.get("object_type")
    if object_type != NEED_STORE_OBJECT_TYPE:
        raise ValueError("object_type must be email_need_store")
    object_version = value.get("object_version")
    if object_version != NEED_STORE_OBJECT_VERSION:
        raise ValueError("object_version must be 0.1")
    needs_value = value.get("needs")
    if not isinstance(needs_value, list):
        raise ValueError("needs must be a list")
    needs = [EvidenceNeed.from_dict(item).to_dict() for item in needs_value]
    need_ids = [item["need_id"] for item in needs]
    if len(need_ids) != len(set(need_ids)):
        raise ValueError("need_id must be unique")
    return {
        "object_type": NEED_STORE_OBJECT_TYPE,
        "object_version": NEED_STORE_OBJECT_VERSION,
        "needs": needs,
    }


def replace_need(store: dict[str, Any], need: EvidenceNeed) -> dict[str, Any]:
    validated = validate_need_store(store)
    next_needs = [item for item in validated["needs"] if item["need_id"] != need.need_id]
    next_needs.append(need.to_dict())
    return {
        "object_type": NEED_STORE_OBJECT_TYPE,
        "object_version": NEED_STORE_OBJECT_VERSION,
        "needs": next_needs,
    }
