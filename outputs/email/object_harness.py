from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

ObjectType = Literal[
    "email_topic_map",
    "email_evidence_policy",
    "email_evidence_pack",
    "email_intel_brief",
    "evidence_need_queue",
]
Scenario = Literal["current"]
LifecycleStatus = Literal["draft", "edited", "validated", "locked", "exported"]

OBJECT_TYPES: tuple[ObjectType, ...] = (
    "email_topic_map",
    "email_evidence_policy",
    "email_evidence_pack",
    "email_intel_brief",
    "evidence_need_queue",
)
CURRENT_SCENARIO: Scenario = "current"
SCENARIOS: tuple[Scenario, ...] = (CURRENT_SCENARIO,)
LIFECYCLE_STATUSES: tuple[LifecycleStatus, ...] = ("draft", "edited", "validated", "locked", "exported")
HARNESS_OBJECT_TYPE = "email_object_harness_session"
HARNESS_OBJECT_VERSION = 1
EVENT_OBJECT_TYPE = "email_object_harness_event"
RENDERER_CONTRACT = "email-workbench-object-renderer-v1"


@dataclass(frozen=True)
class ObjectEvent:
    event_id: str
    at: str
    actor: str
    event_type: str
    payload: dict[str, Any]
    before_version: str
    after_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_type": EVENT_OBJECT_TYPE,
            "event_id": self.event_id,
            "at": self.at,
            "actor": self.actor,
            "event_type": self.event_type,
            "payload": deep_copy(self.payload),
            "before_version": self.before_version,
            "after_version": self.after_version,
        }


@dataclass(frozen=True)
class ObjectHarnessSession:
    object_type: ObjectType
    scenario: Scenario
    lifecycle_status: LifecycleStatus
    current_object: dict[str, Any]
    event_log: tuple[dict[str, Any], ...]
    version_history: tuple[dict[str, Any], ...]
    risks: tuple[str, ...]
    missing_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_type": HARNESS_OBJECT_TYPE,
            "object_version": HARNESS_OBJECT_VERSION,
            "selected_object_type": self.object_type,
            "selected_fixture": self.scenario,
            "lifecycle_status": self.lifecycle_status,
            "current_object": deep_copy(self.current_object),
            "event_log": [deep_copy(event) for event in self.event_log],
            "version_history": [deep_copy(version) for version in self.version_history],
            "risks": list(self.risks),
            "missing_fields": list(self.missing_fields),
            "fixture_source": "workbench_artifact",
            "privacy_safe": False,
            "renderer": renderer_payload(self.object_type, self.current_object),
        }


def list_catalog() -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for object_type in OBJECT_TYPES:
        groups[object_type] = {
            "object_type": object_type,
            "scenarios": list(SCENARIOS),
            "fixtures": [],
            "current_source": {
                "object_type": object_type,
                "scenario": CURRENT_SCENARIO,
                "name": f"{object_type}:current",
                "fixture_source": "workbench_artifact",
                "privacy_safe": False,
                "description": "Loads the current formal Workbench artifact payload for this object.",
            },
        }
    return {
        "object_type": "email_object_fixture_catalog",
        "object_version": 1,
        "renderer_contract": RENDERER_CONTRACT,
        "object_types": list(OBJECT_TYPES),
        "scenarios": list(SCENARIOS),
        "groups": groups,
        "safe_defaults": {
            "reads_imap": False,
            "fetches_links": False,
            "calls_hermes": False,
            "sends": False,
            "launchd": False,
        },
    }


def new_session_from_object(
    object_type: str,
    object_data: dict[str, Any],
    risks: tuple[str, ...],
    missing_fields: tuple[str, ...],
) -> ObjectHarnessSession:
    validated_object_type = validate_object_type(object_type)
    current = deep_copy(object_data)
    version = object_version(current)
    return ObjectHarnessSession(
        object_type=validated_object_type,
        scenario=CURRENT_SCENARIO,
        lifecycle_status="draft",
        current_object=current,
        event_log=(),
        version_history=(version_record("load_current_workbench_artifact", version, current),),
        risks=risks,
        missing_fields=missing_fields,
    )


def session_from_dict(value: dict[str, Any]) -> ObjectHarnessSession:
    object_type = validate_object_type(str(value.get("selected_object_type") or ""))
    scenario = validate_scenario(str(value.get("selected_fixture") or ""))
    lifecycle_status = validate_lifecycle_status(str(value.get("lifecycle_status") or ""))
    current_object = value.get("current_object")
    if not isinstance(current_object, dict):
        raise ValueError("current_object must be an object")
    event_log = value.get("event_log")
    version_history = value.get("version_history")
    if not isinstance(event_log, list) or not isinstance(version_history, list):
        raise ValueError("event_log and version_history must be arrays")
    risks = value.get("risks") if isinstance(value.get("risks"), list) else []
    missing_fields = value.get("missing_fields") if isinstance(value.get("missing_fields"), list) else []
    return ObjectHarnessSession(
        object_type=object_type,
        scenario=scenario,
        lifecycle_status=lifecycle_status,
        current_object=deep_copy(current_object),
        event_log=tuple(deep_copy(item) for item in event_log if isinstance(item, dict)),
        version_history=tuple(deep_copy(item) for item in version_history if isinstance(item, dict)),
        risks=tuple(str(item) for item in risks),
        missing_fields=tuple(str(item) for item in missing_fields),
    )


def apply_event(session: ObjectHarnessSession, event_type: str, payload: dict[str, Any], actor: str) -> ObjectHarnessSession:
    if session.lifecycle_status == "locked" and event_type not in {"switch_state", "reset_history", "export_object"}:
        raise ValueError("locked harness object only allows switch_state, reset_history, or export_object")
    before = object_version(session.current_object)
    next_object = deep_copy(session.current_object)
    next_status = session.lifecycle_status
    next_risks = tuple(session.risks)
    next_missing = tuple(session.missing_fields)
    next_history = tuple(session.version_history)
    if event_type == "clear_data":
        next_object = clear_object_shape(session.object_type, session.current_object)
        next_status = "edited"
        next_risks = ("cleared_data",)
        next_missing = missing_fields_for(session.object_type, next_object)
    elif event_type == "switch_state":
        next_status = validate_lifecycle_status(str(payload.get("status") or ""))
    elif event_type == "save_version":
        next_history = (*next_history, version_record("manual_save", before, next_object))
    elif event_type == "reset_history":
        next_history = (version_record("reset_history", before, next_object),)
    elif event_type == "validate_object":
        next_status = "validated"
        next_missing = missing_fields_for(session.object_type, next_object)
    elif event_type == "export_object":
        next_status = "exported"
        next_history = (*next_history, version_record("export", before, next_object))
    elif event_type == "mark_important":
        next_object = mark_first_evidence_item(next_object)
        next_status = "edited"
    elif event_type in {"close_need", "stale_need"}:
        next_object = update_first_need(next_object, "closed" if event_type == "close_need" else "stale")
        next_status = "edited"
    elif event_type == "approve_brief":
        next_object["review"] = {**as_dict(next_object.get("review")), "brief_status": "approved"}
        next_status = "validated"
    elif event_type == "save_override":
        next_object["review"] = {**as_dict(next_object.get("review")), "brief_override_markdown": str(payload.get("markdown") or "")}
        next_status = "edited"
    elif event_type == "mock_evidence_agent":
        next_object = mock_evidence_agent(next_object)
        next_status = "edited"
    elif event_type == "mock_brief_agent":
        next_object = mock_brief_agent(next_object)
        next_status = "edited"
    else:
        raise ValueError("unsupported harness event")
    after = object_version(next_object)
    event = ObjectEvent(
        event_id=f"evt-{len(session.event_log) + 1}",
        at=now_stamp(),
        actor=actor,
        event_type=event_type,
        payload=deep_copy(payload),
        before_version=before,
        after_version=after,
    )
    if event_type not in {"save_version", "reset_history", "export_object"} and before != after:
        next_history = (*next_history, version_record(event_type, after, next_object))
    return ObjectHarnessSession(
        object_type=session.object_type,
        scenario=session.scenario,
        lifecycle_status=next_status,
        current_object=next_object,
        event_log=(*session.event_log, event.to_dict()),
        version_history=next_history,
        risks=next_risks,
        missing_fields=next_missing,
    )


def import_session_fixture(object_type: str, scenario: str, object_data: dict[str, Any]) -> ObjectHarnessSession:
    validated_object_type = validate_object_type(object_type)
    validate_scenario(scenario)
    validate_imported_object(validated_object_type, object_data)
    missing = missing_fields_for(validated_object_type, object_data)
    risks = ("imported_workbench_artifact",) if not missing else ("imported_workbench_artifact", "missing_fields")
    version = object_version(object_data)
    return ObjectHarnessSession(
        object_type=validated_object_type,
        scenario=CURRENT_SCENARIO,
        lifecycle_status="draft",
        current_object=deep_copy(object_data),
        event_log=(),
        version_history=(version_record("import_workbench_artifact", version, object_data),),
        risks=risks,
        missing_fields=missing,
    )


def export_session_fixture(session: ObjectHarnessSession) -> dict[str, Any]:
    return {
        "object_type": "email_object_harness_artifact_export",
        "object_version": 1,
        "selected_object_type": session.object_type,
        "scenario": session.scenario,
        "fixture": deep_copy(session.current_object),
        "version": object_version(session.current_object),
        "privacy_safe": False,
    }


def renderer_payload(object_type: str, object_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": RENDERER_CONTRACT,
        "object_type": object_type,
        "payload": deep_copy(object_data),
    }


def clear_object_shape(object_type: ObjectType, data: dict[str, Any]) -> dict[str, Any]:
    value = deep_copy(data)
    if object_type == "email_topic_map":
        value["topics"] = []
    elif object_type == "email_evidence_policy":
        if "limits" in value:
            value["limits"] = {key: 0 for key in as_dict(value.get("limits"))}
        if "link_budget" in value:
            value["link_budget"] = {"per_email": 0, "global": 0}
        value["skip_url_patterns"] = []
    elif object_type == "email_evidence_pack":
        value["raw_count"] = 0
        value["topic_hits"] = {}
        value["items"] = []
    elif object_type == "email_intel_brief":
        value["markdown"] = ""
        value["effective_markdown"] = ""
        value["source_index"] = []
        value["sections"] = []
        value["source_coverage"] = {"source_count": 0, "item_count": 0, "covered_count": 0, "missing_uids": [], "complete": False}
    else:
        value["needs"] = []
    return value


def missing_fields_for(object_type: ObjectType, data: dict[str, Any]) -> tuple[str, ...]:
    if object_type == "email_topic_map" and not data.get("topics"):
        return ("topics",)
    if object_type == "email_evidence_policy" and not (data.get("link_budget") or data.get("limits")):
        return ("link_budget",)
    if object_type == "email_evidence_pack":
        items = data.get("items") if isinstance(data.get("items"), list) else []
        if not items:
            return ("items",)
    if object_type == "email_intel_brief" and not data.get("source_index"):
        return ("source_index",)
    if object_type == "evidence_need_queue" and not data.get("needs"):
        return ("needs",)
    return ()


def mark_first_evidence_item(data: dict[str, Any]) -> dict[str, Any]:
    value = deep_copy(data)
    items = value.get("items") if isinstance(value.get("items"), list) else []
    if items and isinstance(items[0], dict):
        review = as_dict(items[0].get("_review"))
        review["important"] = True
        items[0]["_review"] = review
    return value


def update_first_need(data: dict[str, Any], status: str) -> dict[str, Any]:
    value = deep_copy(data)
    needs = value.get("needs") if isinstance(value.get("needs"), list) else []
    if needs and isinstance(needs[0], dict):
        needs[0]["status"] = status
        needs[0]["audit_trail"] = [*as_list(needs[0].get("audit_trail")), {"event_type": status, "actor": "ObjectHarness"}]
    return value


def mock_evidence_agent(data: dict[str, Any]) -> dict[str, Any]:
    value = deep_copy(data)
    items = value.get("items") if isinstance(value.get("items"), list) else []
    if items and isinstance(items[0], dict):
        flags = as_list(items[0].get("flags"))
        items[0]["flags"] = [*flags, "harness_mock_evidence_agent"] if "harness_mock_evidence_agent" not in flags else flags
        items[0]["_review"] = {**as_dict(items[0].get("_review")), "needs_link_review": True}
    else:
        value["harness_mock_evidence_agent"] = {"changed": False, "reason": "no current email item"}
    return value


def mock_brief_agent(data: dict[str, Any]) -> dict[str, Any]:
    value = deep_copy(data)
    markdown = str(value.get("effective_markdown") or value.get("markdown") or "")
    value["effective_markdown"] = markdown.rstrip() + "\n\n## Harness BriefAgent note\n- Current artifact was edited inside the local harness only.\n"
    value["markdown"] = value["effective_markdown"]
    return value


def validate_imported_object(object_type: ObjectType, object_data: dict[str, Any]) -> None:
    expected = {
        "email_topic_map": ("email_topic_map",),
        "email_evidence_policy": ("email_evidence_policy", "email_policy"),
        "email_evidence_pack": ("email_evidence_pack",),
        "email_intel_brief": ("email_intel_brief",),
        "evidence_need_queue": ("evidence_need_store", "email_need_store"),
    }[object_type]
    if object_data.get("object_type") not in expected:
        raise ValueError(f"imported artifact must have object_type one of {', '.join(expected)}")


def validate_object_type(value: str) -> ObjectType:
    if value not in OBJECT_TYPES:
        raise ValueError(f"unsupported harness object_type: {value}")
    return value  # type: ignore[return-value]


def validate_scenario(value: str) -> Scenario:
    if value not in SCENARIOS:
        raise ValueError(f"unsupported harness scenario: {value}")
    return value  # type: ignore[return-value]


def validate_lifecycle_status(value: str) -> LifecycleStatus:
    if value not in LIFECYCLE_STATUSES:
        raise ValueError(f"unsupported harness lifecycle status: {value}")
    return value  # type: ignore[return-value]


def object_version(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def version_record(reason: str, version: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "reason": reason,
        "version": version,
        "at": now_stamp(),
        "object": deep_copy(data),
    }


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def deep_copy(value: Any) -> Any:
    return copy.deepcopy(value)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
