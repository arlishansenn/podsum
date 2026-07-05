from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

EvidencePackStatus = Literal["ready_for_summary", "enriched"]
LinkEvidenceType = Literal["public_link"]
LinkEvidenceStatus = Literal["", "fetched", "failed", "skipped"]
LinkClassificationValue = Literal["content", "ad", "sponsor", "navigation", "tracking", "unknown"]
LinkDecisionSource = Literal["deterministic", "ai_classifier"]
IntelBriefSource = Literal["summary_file", "review_override"]


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _required(value: dict[str, Any], key: str) -> Any:
    if key not in value:
        raise ValueError(f"missing required field: {key}")
    return value[key]


def _string(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _int(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _float(value: Any, key: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _list(value: Any, key: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _dict_or_none(value: Any, key: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return dict(value)


def _literal(value: str, key: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(allowed)}")
    return value


def _optional(value: dict[str, Any], key: str) -> Any:
    return value[key] if key in value else None


def _put(output: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        output[key] = value


@dataclass(frozen=True)
class LinkEvidence:
    type: LinkEvidenceType
    source: str | None
    uid: str
    url: str
    final_url: str | None
    title: str | None
    excerpt: str | None
    status: LinkEvidenceStatus
    reason: str | None
    content_type: str | None
    anchor_text: str | None
    email_context: str | None
    source_content_type: str | None
    classification: LinkClassificationValue | None
    decision_source: LinkDecisionSource | None
    decision_reason: str | None
    classifier_version: str | None
    confidence: float | None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LinkEvidence:
        data = _mapping(value, "LinkEvidence")
        evidence_type = _literal(_string(_required(data, "type"), "type"), "type", get_args(LinkEvidenceType))
        status = _literal(_string(_required(data, "status"), "status"), "status", get_args(LinkEvidenceStatus))
        classification = _optional_literal(data, "classification", get_args(LinkClassificationValue))
        decision_source = _optional_literal(data, "decision_source", get_args(LinkDecisionSource))
        return cls(
            type=evidence_type,
            source=_optional_string(data, "source"),
            uid=_string(_required(data, "uid"), "uid"),
            url=_string(_required(data, "url"), "url"),
            final_url=_optional_string(data, "final_url"),
            title=_optional_string(data, "title"),
            excerpt=_optional_string(data, "excerpt"),
            status=status,
            reason=_optional_string(data, "reason"),
            content_type=_optional_string(data, "content_type"),
            anchor_text=_optional_string(data, "anchor_text"),
            email_context=_optional_string(data, "email_context"),
            source_content_type=_optional_string(data, "source_content_type"),
            classification=classification,
            decision_source=decision_source,
            decision_reason=_optional_string(data, "decision_reason"),
            classifier_version=_optional_string(data, "classifier_version"),
            confidence=_optional_float(data, "confidence"),
        )

    def to_dict(self) -> dict[str, Any]:
        output: dict[str, Any] = {"type": self.type}
        _put(output, "source", self.source)
        output["uid"] = self.uid
        output["url"] = self.url
        _put(output, "final_url", self.final_url)
        _put(output, "title", self.title)
        _put(output, "excerpt", self.excerpt)
        output["status"] = self.status
        _put(output, "reason", self.reason)
        _put(output, "content_type", self.content_type)
        _put(output, "anchor_text", self.anchor_text)
        _put(output, "email_context", self.email_context)
        _put(output, "source_content_type", self.source_content_type)
        _put(output, "classification", self.classification)
        _put(output, "decision_source", self.decision_source)
        _put(output, "decision_reason", self.decision_reason)
        _put(output, "classifier_version", self.classifier_version)
        _put(output, "confidence", self.confidence)
        return output


@dataclass(frozen=True)
class EmailItem:
    uid: str
    date: str | None
    from_: str
    subject: str
    snippet: str
    has_attachments: bool | None
    email_type: str | None
    links: list[dict[str, Any]] | None
    evidence: list[dict[str, Any] | LinkEvidence] | None
    risks: list[str] | None
    flags: list[str] | None
    attachment_count: int | None
    attachment_shapes: list[dict[str, Any]] | None
    body_part_count: int | None
    body_part_types: list[str] | None
    topics: list[dict[str, Any]] | None
    link_triage: dict[str, Any] | None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EmailItem:
        data = _mapping(value, "EmailItem")
        evidence = _optional_evidence_list(data, "evidence")
        return cls(
            uid=_string(_required(data, "uid"), "uid"),
            date=_optional_string(data, "date"),
            from_=_string(_required(data, "from"), "from"),
            subject=_string(_required(data, "subject"), "subject"),
            snippet=_string(_required(data, "snippet"), "snippet"),
            has_attachments=_optional_bool(data, "has_attachments"),
            email_type=_optional_string(data, "email_type"),
            links=_optional_dict_list(data, "links"),
            evidence=evidence,
            risks=_optional_string_list(data, "risks"),
            flags=_optional_string_list(data, "flags"),
            attachment_count=_optional_int(data, "attachment_count"),
            attachment_shapes=_optional_dict_list(data, "attachment_shapes"),
            body_part_count=_optional_int(data, "body_part_count"),
            body_part_types=_optional_string_list(data, "body_part_types"),
            topics=_optional_dict_list(data, "topics"),
            link_triage=_dict_or_none(_optional(data, "link_triage"), "link_triage"),
        )

    def to_dict(self) -> dict[str, Any]:
        output: dict[str, Any] = {"uid": self.uid}
        _put(output, "date", self.date)
        output["from"] = self.from_
        output["subject"] = self.subject
        output["snippet"] = self.snippet
        _put(output, "has_attachments", self.has_attachments)
        _put(output, "email_type", self.email_type)
        _put(output, "links", self.links)
        if self.evidence is not None:
            output["evidence"] = [item.to_dict() if isinstance(item, LinkEvidence) else item for item in self.evidence]
        _put(output, "risks", self.risks)
        _put(output, "flags", self.flags)
        _put(output, "attachment_count", self.attachment_count)
        _put(output, "attachment_shapes", self.attachment_shapes)
        _put(output, "body_part_count", self.body_part_count)
        _put(output, "body_part_types", self.body_part_types)
        _put(output, "topics", self.topics)
        _put(output, "link_triage", self.link_triage)
        return output


@dataclass(frozen=True)
class EmailEvidencePack:
    object_type: str
    object_version: str
    status: EvidencePackStatus
    date: str
    account: str
    window: str
    scan_limit: int
    raw_count: int
    possibly_truncated: bool
    items: list[EmailItem]
    topic_map: dict[str, Any] | None
    topic_hits: list[dict[str, Any]] | None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EmailEvidencePack:
        data = _mapping(value, "EmailEvidencePack")
        status = _literal(_string(_required(data, "status"), "status"), "status", get_args(EvidencePackStatus))
        items = [EmailItem.from_dict(item) for item in _list(_required(data, "items"), "items")]
        return cls(
            object_type=_string(_required(data, "object_type"), "object_type"),
            object_version=_string(_required(data, "object_version"), "object_version"),
            status=status,
            date=_string(_required(data, "date"), "date"),
            account=_string(_required(data, "account"), "account"),
            window=_string(_required(data, "window"), "window"),
            scan_limit=_int(_required(data, "scan_limit"), "scan_limit"),
            raw_count=_int(_required(data, "raw_count"), "raw_count"),
            possibly_truncated=_bool(_required(data, "possibly_truncated"), "possibly_truncated"),
            items=items,
            topic_map=_dict_or_none(_optional(data, "topic_map"), "topic_map"),
            topic_hits=_optional_dict_list(data, "topic_hits"),
        )

    def to_dict(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "object_type": self.object_type,
            "object_version": self.object_version,
            "status": self.status,
            "date": self.date,
            "account": self.account,
            "window": self.window,
            "scan_limit": self.scan_limit,
            "raw_count": self.raw_count,
            "possibly_truncated": self.possibly_truncated,
            "items": [item.to_dict() for item in self.items],
        }
        _put(output, "topic_map", self.topic_map)
        _put(output, "topic_hits", self.topic_hits)
        return output


@dataclass(frozen=True)
class EmailIntelBrief:
    object_type: str
    object_version: str
    missing: bool
    path: str
    markdown: str
    effective_markdown: str
    source: IntelBriefSource
    source_index: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    source_coverage: dict[str, Any]
    review: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EmailIntelBrief:
        data = _mapping(value, "EmailIntelBrief")
        source = _literal(_string(_required(data, "source"), "source"), "source", get_args(IntelBriefSource))
        return cls(
            object_type=_string(_required(data, "object_type"), "object_type"),
            object_version=_string(_required(data, "object_version"), "object_version"),
            missing=_bool(_required(data, "missing"), "missing"),
            path=_string(_required(data, "path"), "path"),
            markdown=_string(_required(data, "markdown"), "markdown"),
            effective_markdown=_string(_required(data, "effective_markdown"), "effective_markdown"),
            source=source,
            source_index=_dict_list(_required(data, "source_index"), "source_index"),
            sections=_dict_list(_required(data, "sections"), "sections"),
            source_coverage=_mapping(_required(data, "source_coverage"), "source_coverage"),
            review=_mapping(_required(data, "review"), "review"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_type": self.object_type,
            "object_version": self.object_version,
            "missing": self.missing,
            "path": self.path,
            "markdown": self.markdown,
            "effective_markdown": self.effective_markdown,
            "source": self.source,
            "source_index": self.source_index,
            "sections": self.sections,
            "source_coverage": self.source_coverage,
            "review": self.review,
        }


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    if key not in data:
        return None
    return _string(data[key], key)


def _optional_bool(data: dict[str, Any], key: str) -> bool | None:
    if key not in data:
        return None
    return _bool(data[key], key)


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    if key not in data:
        return None
    return _int(data[key], key)


def _optional_float(data: dict[str, Any], key: str) -> float | None:
    if key not in data:
        return None
    return _float(data[key], key)


def _optional_literal(data: dict[str, Any], key: str, allowed: tuple[str, ...]) -> str | None:
    if key not in data:
        return None
    return _literal(_string(data[key], key), key, allowed)


def _dict_list(value: Any, key: str) -> list[dict[str, Any]]:
    return [_mapping(item, key) for item in _list(value, key)]


def _optional_dict_list(data: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    if key not in data:
        return None
    return _dict_list(data[key], key)


def _optional_string_list(data: dict[str, Any], key: str) -> list[str] | None:
    if key not in data:
        return None
    return [_string(item, key) for item in _list(data[key], key)]


def _optional_evidence_list(data: dict[str, Any], key: str) -> list[dict[str, Any] | LinkEvidence] | None:
    if key not in data:
        return None
    values: list[dict[str, Any] | LinkEvidence] = []
    for item in _list(data[key], key):
        evidence = _mapping(item, key)
        if evidence.get("type") == "public_link":
            values.append(LinkEvidence.from_dict(evidence))
        else:
            values.append(dict(evidence))
    return values


def _tuple_strings(value: tuple[str, ...], key: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{key} must be a tuple")
    for item in value:
        _string(item, key)


EvidenceNeedStatus = Literal["open", "watching", "fulfilled_now", "blocked", "stale", "closed", "superseded"]
EvidenceNeedUrgency = Literal["low", "medium", "high"]
EvidenceNeedEventType = Literal["watch", "fulfill", "block", "mark_stale", "close", "supersede", "reopen"]


@dataclass(frozen=True)
class EvidenceNeedAuditEntry:
    at: str
    actor: str
    event_type: EvidenceNeedEventType
    old_status: EvidenceNeedStatus
    new_status: EvidenceNeedStatus
    reason: str
    added_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _literal(self.event_type, "event_type", get_args(EvidenceNeedEventType))
        _literal(self.old_status, "old_status", get_args(EvidenceNeedStatus))
        _literal(self.new_status, "new_status", get_args(EvidenceNeedStatus))
        _string(self.at, "at")
        _string(self.actor, "actor")
        _string(self.reason, "reason")
        _tuple_strings(self.added_evidence_refs, "added_evidence_refs")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceNeedAuditEntry:
        data = _mapping(value, "EvidenceNeedAuditEntry")
        event_type = _literal(_string(_required(data, "event_type"), "event_type"), "event_type", get_args(EvidenceNeedEventType))
        old_status = _literal(_string(_required(data, "old_status"), "old_status"), "old_status", get_args(EvidenceNeedStatus))
        new_status = _literal(_string(_required(data, "new_status"), "new_status"), "new_status", get_args(EvidenceNeedStatus))
        return cls(
            at=_string(_required(data, "at"), "at"),
            actor=_string(_required(data, "actor"), "actor"),
            event_type=event_type,
            old_status=old_status,
            new_status=new_status,
            reason=_string(_required(data, "reason"), "reason"),
            added_evidence_refs=tuple(_string(item, "added_evidence_refs") for item in _list(_required(data, "added_evidence_refs"), "added_evidence_refs")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "actor": self.actor,
            "event_type": self.event_type,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "reason": self.reason,
            "added_evidence_refs": list(self.added_evidence_refs),
        }


@dataclass(frozen=True)
class EvidenceNeedEvent:
    event_type: EvidenceNeedEventType
    at: str
    actor: str
    reason: str
    added_evidence_refs: tuple[str, ...]
    resolved_by: tuple[str, ...]

    def __post_init__(self) -> None:
        _literal(self.event_type, "event_type", get_args(EvidenceNeedEventType))
        _string(self.at, "at")
        _string(self.actor, "actor")
        _string(self.reason, "reason")
        _tuple_strings(self.added_evidence_refs, "added_evidence_refs")
        _tuple_strings(self.resolved_by, "resolved_by")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceNeedEvent:
        data = _mapping(value, "EvidenceNeedEvent")
        event_type = _literal(_string(_required(data, "event_type"), "event_type"), "event_type", get_args(EvidenceNeedEventType))
        return cls(
            event_type=event_type,
            at=_string(_required(data, "at"), "at"),
            actor=_string(_required(data, "actor"), "actor"),
            reason=_string(_required(data, "reason"), "reason"),
            added_evidence_refs=tuple(_string(item, "added_evidence_refs") for item in _list(_required(data, "added_evidence_refs"), "added_evidence_refs")),
            resolved_by=tuple(_string(item, "resolved_by") for item in _list(_required(data, "resolved_by"), "resolved_by")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "at": self.at,
            "actor": self.actor,
            "reason": self.reason,
            "added_evidence_refs": list(self.added_evidence_refs),
            "resolved_by": list(self.resolved_by),
        }


@dataclass(frozen=True)
class EvidenceNeed:
    need_id: str
    status: EvidenceNeedStatus
    urgency: EvidenceNeedUrgency
    topic_id: str
    source_brief_id: str
    claim_or_question: str
    why_needed: str
    known_source_refs: tuple[str, ...]
    needed_evidence: tuple[str, ...]
    created_at: str
    last_checked_at: str
    resolved_by: tuple[str, ...]
    response_policy: str
    audit_trail: tuple[EvidenceNeedAuditEntry, ...]

    def __post_init__(self) -> None:
        _string(self.need_id, "need_id")
        _literal(self.status, "status", get_args(EvidenceNeedStatus))
        _literal(self.urgency, "urgency", get_args(EvidenceNeedUrgency))
        _string(self.topic_id, "topic_id")
        _string(self.source_brief_id, "source_brief_id")
        _string(self.claim_or_question, "claim_or_question")
        _string(self.why_needed, "why_needed")
        _tuple_strings(self.known_source_refs, "known_source_refs")
        _tuple_strings(self.needed_evidence, "needed_evidence")
        _string(self.created_at, "created_at")
        _string(self.last_checked_at, "last_checked_at")
        _tuple_strings(self.resolved_by, "resolved_by")
        _string(self.response_policy, "response_policy")
        if not isinstance(self.audit_trail, tuple):
            raise ValueError("audit_trail must be a tuple")
        for item in self.audit_trail:
            if not isinstance(item, EvidenceNeedAuditEntry):
                raise ValueError("audit_trail must contain EvidenceNeedAuditEntry")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceNeed:
        data = _mapping(value, "EvidenceNeed")
        status = _literal(_string(_required(data, "status"), "status"), "status", get_args(EvidenceNeedStatus))
        urgency = _literal(_string(_required(data, "urgency"), "urgency"), "urgency", get_args(EvidenceNeedUrgency))
        return cls(
            need_id=_string(_required(data, "need_id"), "need_id"),
            status=status,
            urgency=urgency,
            topic_id=_string(_required(data, "topic_id"), "topic_id"),
            source_brief_id=_string(_required(data, "source_brief_id"), "source_brief_id"),
            claim_or_question=_string(_required(data, "claim_or_question"), "claim_or_question"),
            why_needed=_string(_required(data, "why_needed"), "why_needed"),
            known_source_refs=tuple(_string(item, "known_source_refs") for item in _list(_required(data, "known_source_refs"), "known_source_refs")),
            needed_evidence=tuple(_string(item, "needed_evidence") for item in _list(_required(data, "needed_evidence"), "needed_evidence")),
            created_at=_string(_required(data, "created_at"), "created_at"),
            last_checked_at=_string(_required(data, "last_checked_at"), "last_checked_at"),
            resolved_by=tuple(_string(item, "resolved_by") for item in _list(_required(data, "resolved_by"), "resolved_by")),
            response_policy=_string(_required(data, "response_policy"), "response_policy"),
            audit_trail=tuple(EvidenceNeedAuditEntry.from_dict(item) for item in _list(_required(data, "audit_trail"), "audit_trail")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "need_id": self.need_id,
            "status": self.status,
            "urgency": self.urgency,
            "topic_id": self.topic_id,
            "source_brief_id": self.source_brief_id,
            "claim_or_question": self.claim_or_question,
            "why_needed": self.why_needed,
            "known_source_refs": list(self.known_source_refs),
            "needed_evidence": list(self.needed_evidence),
            "created_at": self.created_at,
            "last_checked_at": self.last_checked_at,
            "resolved_by": list(self.resolved_by),
            "response_policy": self.response_policy,
            "audit_trail": [item.to_dict() for item in self.audit_trail],
        }


def transition_need(need: EvidenceNeed, event: EvidenceNeedEvent) -> EvidenceNeed:
    new_status = _event_status(need.status, event.event_type)
    if new_status == "fulfilled_now" and not event.added_evidence_refs:
        raise ValueError("fulfilled_now requires added_evidence_refs")
    audit_entry = EvidenceNeedAuditEntry(
        at=event.at,
        actor=event.actor,
        event_type=event.event_type,
        old_status=need.status,
        new_status=new_status,
        reason=event.reason,
        added_evidence_refs=tuple(event.added_evidence_refs),
    )
    resolved_by = tuple(event.resolved_by) if event.resolved_by else need.resolved_by
    if new_status == "fulfilled_now" and not resolved_by:
        resolved_by = tuple(event.added_evidence_refs)
    return EvidenceNeed(
        need_id=need.need_id,
        status=new_status,
        urgency=need.urgency,
        topic_id=need.topic_id,
        source_brief_id=need.source_brief_id,
        claim_or_question=need.claim_or_question,
        why_needed=need.why_needed,
        known_source_refs=need.known_source_refs,
        needed_evidence=need.needed_evidence,
        created_at=need.created_at,
        last_checked_at=event.at,
        resolved_by=resolved_by,
        response_policy=need.response_policy,
        audit_trail=(*need.audit_trail, audit_entry),
    )


def _event_status(status: EvidenceNeedStatus, event_type: EvidenceNeedEventType) -> EvidenceNeedStatus:
    transitions: dict[EvidenceNeedStatus, dict[EvidenceNeedEventType, EvidenceNeedStatus]] = {
        "open": {
            "watch": "watching",
            "fulfill": "fulfilled_now",
            "block": "blocked",
            "mark_stale": "stale",
            "close": "closed",
            "supersede": "superseded",
        },
        "watching": {
            "watch": "watching",
            "fulfill": "fulfilled_now",
            "block": "blocked",
            "mark_stale": "stale",
            "close": "closed",
            "supersede": "superseded",
        },
        "blocked": {
            "watch": "watching",
            "mark_stale": "stale",
            "close": "closed",
            "supersede": "superseded",
        },
        "stale": {
            "reopen": "open",
            "close": "closed",
            "supersede": "superseded",
        },
        "fulfilled_now": {
            "close": "closed",
        },
        "superseded": {},
        "closed": {},
    }
    new_status = transitions[status].get(event_type)
    if new_status is None:
        raise ValueError(f"illegal transition: {status} + {event_type}")
    return new_status
