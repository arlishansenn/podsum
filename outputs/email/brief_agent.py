from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import need_store
from .schemas import EmailEvidencePack, EmailIntelBrief, EvidenceNeed, EvidenceNeedEvent, transition_need

BRIEF_AGENT_ACTOR = "EmailIntelBriefAgent"
DEFAULT_RECONCILE_MAX_CHECKS = 3


@dataclass(frozen=True)
class BriefComposition:
    email_intel_brief: EmailIntelBrief
    need_store: dict[str, Any]


def compose(pack: EmailEvidencePack, topic_map: dict[str, Any], needs: dict[str, Any]) -> EmailIntelBrief:
    composition = compose_with_need_store(pack, topic_map, needs, "", {}, "")
    return composition.email_intel_brief


def compose_with_need_store(
    pack: EmailEvidencePack,
    topic_map: dict[str, Any],
    needs: dict[str, Any],
    path: str,
    review: dict[str, Any],
    reason: str,
) -> BriefComposition:
    created_at = _now_stamp()
    scan = _scan_with_topic_map(pack, topic_map)
    reconciled_store = reconcile_need_store(pack, needs, created_at, DEFAULT_RECONCILE_MAX_CHECKS)
    emitted_needs = snippet_only_needs(pack, f"brief-{pack.date}", created_at)
    next_store = merge_need_store(reconciled_store, emitted_needs)
    markdown = _build_markdown(scan, reason, emitted_needs)
    brief = _brief_from_markdown(pack, path, markdown, review, emitted_needs)
    return BriefComposition(email_intel_brief=brief, need_store=next_store)


def compose_and_persist(
    pack: EmailEvidencePack,
    topic_map: dict[str, Any],
    artifact_dir: Path,
    path: str,
    review: dict[str, Any],
    reason: str,
) -> BriefComposition:
    existing_store = need_store.load_need_store(artifact_dir)
    composition = compose_with_need_store(pack, topic_map, existing_store, path, review, reason)
    need_store.save_need_store(artifact_dir, composition.need_store)
    return composition


def snippet_only_needs(pack: EmailEvidencePack, source_brief_id: str, created_at: str) -> list[EvidenceNeed]:
    needs: list[EvidenceNeed] = []
    for item in pack.items:
        risks = tuple(item.risks or ())
        if "snippet_only" not in risks:
            continue
        topic_id = _item_topic_id(item.topics)
        uid = item.uid
        needs.append(
            EvidenceNeed(
                need_id=_need_id(pack.date, uid, topic_id),
                status="open",
                urgency=_need_urgency(item.topics),
                topic_id=topic_id,
                source_brief_id=source_brief_id,
                claim_or_question=f"UID={uid} 是否有邮件摘要之外的可验证证据？",
                why_needed="当前 EvidencePack 将该邮件标记为 snippet_only。",
                known_source_refs=(f"email:{uid}",),
                needed_evidence=("public_link_or_full_body",),
                created_at=created_at,
                last_checked_at=created_at,
                resolved_by=(),
                response_policy="emit_need_reference_only",
                audit_trail=(),
            )
        )
    return needs


def reconcile_need_store(pack: EmailEvidencePack, store: dict[str, Any], checked_at: str, max_checks: int) -> dict[str, Any]:
    validated = need_store.validate_need_store(store)
    next_needs: list[dict[str, Any]] = []
    for item in validated["needs"]:
        existing_need = EvidenceNeed.from_dict(item)
        next_need = reconcile_need(pack, existing_need, checked_at, max_checks)
        next_needs.append(next_need.to_dict())
    return {
        "object_type": need_store.NEED_STORE_OBJECT_TYPE,
        "object_version": need_store.NEED_STORE_OBJECT_VERSION,
        "needs": next_needs,
    }


def reconcile_need(pack: EmailEvidencePack, existing_need: EvidenceNeed, checked_at: str, max_checks: int) -> EvidenceNeed:
    if existing_need.status not in ("open", "watching"):
        return existing_need
    if not _need_email_uids(existing_need):
        return transition_need(
            existing_need,
            EvidenceNeedEvent(
                event_type="block",
                at=checked_at,
                actor=BRIEF_AGENT_ACTOR,
                reason="该需求缺少可用的 email:<uid> 来源引用，无法用当前 EvidencePack 对账。",
                added_evidence_refs=(),
                resolved_by=(),
            ),
        )
    added_evidence_refs = current_pack_added_evidence_refs(pack, existing_need)
    if added_evidence_refs:
        return transition_need(
            existing_need,
            EvidenceNeedEvent(
                event_type="fulfill",
                at=checked_at,
                actor=BRIEF_AGENT_ACTOR,
                reason="当前 EvidencePack 已包含该邮件的可验证证据。",
                added_evidence_refs=added_evidence_refs,
                resolved_by=(f"pack-{pack.date}",),
            ),
        )
    event_type = "mark_stale" if reconcile_check_count(existing_need) + 1 >= max_checks else "watch"
    reason = "已达到本需求的后续扫描检查上限。" if event_type == "mark_stale" else "当前 EvidencePack 尚未补齐该需求，继续观察后续扫描。"
    return transition_need(
        existing_need,
        EvidenceNeedEvent(
            event_type=event_type,
            at=checked_at,
            actor=BRIEF_AGENT_ACTOR,
            reason=reason,
            added_evidence_refs=(),
            resolved_by=(),
        ),
    )


def current_pack_added_evidence_refs(pack: EmailEvidencePack, existing_need: EvidenceNeed) -> tuple[str, ...]:
    refs: list[str] = []
    wanted_uids = _need_email_uids(existing_need)
    for item in pack.items:
        if item.uid not in wanted_uids:
            continue
        risks = tuple(item.risks or ())
        if "snippet_only" not in risks:
            refs.append(f"email:{item.uid}")
        for index, evidence in enumerate(item.evidence or (), start=1):
            evidence_dict = evidence.to_dict() if hasattr(evidence, "to_dict") else evidence
            if not isinstance(evidence_dict, dict):
                continue
            if evidence_dict.get("type") == "public_link" and evidence_dict.get("status") == "fetched":
                refs.append(f"link:{item.uid}:{index}")
    return tuple(dict.fromkeys(refs))


def reconcile_check_count(existing_need: EvidenceNeed) -> int:
    return sum(1 for item in existing_need.audit_trail if item.event_type in ("watch", "mark_stale"))


def merge_need_store(store: dict[str, Any], emitted_needs: list[EvidenceNeed]) -> dict[str, Any]:
    next_store = need_store.validate_need_store(store)
    existing_need_ids = {item["need_id"] for item in next_store["needs"]}
    for emitted_need in emitted_needs:
        if emitted_need.need_id in existing_need_ids:
            continue
        next_store = need_store.replace_need(next_store, emitted_need)
        existing_need_ids.add(emitted_need.need_id)
    return next_store


def _need_email_uids(existing_need: EvidenceNeed) -> set[str]:
    uids: set[str] = set()
    for source_ref in existing_need.known_source_refs:
        if source_ref.startswith("email:"):
            uid = source_ref.removeprefix("email:")
            if uid:
                uids.add(uid)
    return uids


def _scan_with_topic_map(pack: EmailEvidencePack, topic_map: dict[str, Any]) -> dict[str, Any]:
    scan = pack.to_dict()
    if topic_map:
        scan["topic_map"] = topic_map
    return scan


def _build_markdown(scan: dict[str, Any], reason: str, emitted_needs: list[EvidenceNeed]) -> str:
    import podsum_email_summary as email_summary

    return email_summary.build_intel_brief_draft(scan, reason)


def _brief_from_markdown(
    pack: EmailEvidencePack,
    path: str,
    markdown: str,
    review: dict[str, Any],
    emitted_needs: list[EvidenceNeed],
) -> EmailIntelBrief:
    import podsum_email_workbench as email_workbench

    need_ids = [emitted_need.need_id for emitted_need in emitted_needs]
    source_coverage = email_workbench.brief_source_coverage(markdown, pack.to_dict())
    source_coverage["need_ids"] = need_ids
    return EmailIntelBrief(
        object_type="email_intel_brief",
        object_version="0.1",
        missing=False,
        path=path,
        markdown=markdown,
        effective_markdown=markdown,
        source="summary_file",
        source_index=email_workbench.parse_source_index(markdown),
        sections=email_workbench.parse_markdown_sections(markdown),
        source_coverage=source_coverage,
        review=review,
    )


def _replace_vague_need_text(markdown: str, emitted_needs: list[EvidenceNeed]) -> str:
    if not emitted_needs:
        return markdown
    reference = "证据需求见文末「证据需求」"
    text = markdown.replace("仅基于邮件摘要或待外部验证", f"仅基于邮件摘要；{reference}")
    text = text.replace("；待外部验证", f"；{reference}")
    return text.replace("待外部验证", reference)


def _append_need_references(markdown: str, emitted_needs: list[EvidenceNeed]) -> str:
    if not emitted_needs:
        return markdown
    lines = [markdown.rstrip(), "", "## 证据需求", ""]
    for emitted_need in emitted_needs:
        lines.append(f"- need_id={emitted_need.need_id}")
    return "\n".join(lines).rstrip() + "\n"


def _item_topic_id(topics: list[dict[str, Any]] | None) -> str:
    if topics:
        topic_id = str(topics[0].get("id") or "")
        if topic_id:
            return topic_id
    return "unmapped"


def _need_urgency(topics: list[dict[str, Any]] | None) -> str:
    if topics and str(topics[0].get("priority") or "") == "high":
        return "high"
    return "medium"


def _need_id(date: str, uid: str, topic_id: str) -> str:
    safe_uid = "".join(ch if ch.isalnum() else "-" for ch in uid).strip("-") or "unknown"
    safe_topic = "".join(ch if ch.isalnum() else "-" for ch in topic_id).strip("-") or "unmapped"
    return f"need-{date}-{safe_topic}-{safe_uid}-snippet-only"


def _now_stamp() -> str:
    import podsum_email_summary as email_summary

    return email_summary.now_stamp()
