from __future__ import annotations

import copy
from typing import Any

from email.providers import DeterministicLinkClassifier, LinkClassification, LinkContentClassifier
from email.schemas import EmailEvidencePack, EmailItem

DEFAULT_CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.5


def _legacy() -> Any:
    import podsum_email_summary

    return podsum_email_summary


def _pack_from_dict(scan: dict[str, Any]) -> EmailEvidencePack:
    return EmailEvidencePack.from_dict(scan)


def _pack_to_dict(pack: EmailEvidencePack | dict[str, Any]) -> dict[str, Any]:
    if isinstance(pack, EmailEvidencePack):
        return pack.to_dict()
    return copy.deepcopy(pack)


def message_item(uid: str, raw_message: bytes, policy: dict[str, Any]) -> EmailItem:
    item = _legacy().message_item(uid, raw_message, policy)
    return EmailItem.from_dict(item)


def message_items(raw_messages: list[tuple[str, bytes]], policy: dict[str, Any]) -> list[EmailItem]:
    return [message_item(uid, raw_message, policy) for uid, raw_message in raw_messages]


def normalize_evidence_pack(scan: dict[str, Any], policy: dict[str, Any]) -> EmailEvidencePack:
    normalized = _legacy().normalize_evidence_pack(copy.deepcopy(scan), policy)
    return _pack_from_dict(normalized)


def enrich_scan_links(
    pack: EmailEvidencePack | dict[str, Any],
    policy: dict[str, Any],
    fetcher: Any,
    topic_map: dict[str, Any] | None = None,
) -> EmailEvidencePack:
    enriched = _legacy().enrich_scan_links(_pack_to_dict(pack), policy, fetcher, topic_map)
    return _pack_from_dict(enriched)


def apply_topics(pack: EmailEvidencePack | dict[str, Any], topic_map: dict[str, Any]) -> EmailEvidencePack:
    with_topics = _legacy().apply_topics(_pack_to_dict(pack), topic_map)
    return _pack_from_dict(with_topics)


def classify_evidence_links(
    pack: EmailEvidencePack | dict[str, Any],
    policy: dict[str, Any],
    classifier: LinkContentClassifier,
    confidence_threshold: float,
) -> EmailEvidencePack:
    scan = _pack_to_dict(pack)
    for item in scan.get("items", []):
        if not isinstance(item, dict):
            continue
        links_by_url = _links_by_url(item)
        for evidence in item.get("evidence", []):
            if not isinstance(evidence, dict) or evidence.get("type") != "public_link":
                continue
            link = links_by_url.get(_normalize_url(str(evidence.get("url") or "")), {})
            result = classifier.classify(item, link, evidence, policy)
            _write_classification(evidence, result, confidence_threshold)
    return _pack_from_dict(scan)


def build_evidence_pack_with_classifier(
    scan: dict[str, Any],
    policy: dict[str, Any],
    topic_map: dict[str, Any],
    enrich_links: bool,
    fetcher: Any,
    classifier: LinkContentClassifier,
    confidence_threshold: float,
) -> EmailEvidencePack:
    pack = normalize_evidence_pack(scan, policy)
    if enrich_links:
        pack = enrich_scan_links(pack, policy, fetcher, topic_map)
    pack = classify_evidence_links(pack, policy, classifier, confidence_threshold)
    return apply_topics(pack, topic_map)


def build_evidence_pack(
    scan: dict[str, Any],
    policy: dict[str, Any],
    topic_map: dict[str, Any],
    enrich_links: bool,
    fetcher: Any,
) -> EmailEvidencePack:
    return build_evidence_pack_with_classifier(
        scan,
        policy,
        topic_map,
        enrich_links,
        fetcher,
        DeterministicLinkClassifier(),
        _classification_confidence_threshold(policy),
    )


def build_evidence_pack_from_messages(
    date: str,
    account: str,
    window: str,
    scan_limit: int,
    raw_count: int,
    raw_messages: list[tuple[str, bytes]],
    policy: dict[str, Any],
    topic_map: dict[str, Any],
    enrich_links: bool,
    fetcher: Any,
) -> EmailEvidencePack:
    items = [item.to_dict() for item in message_items(raw_messages, policy)]
    scan = {
        "object_type": "email_evidence_pack",
        "object_version": "0.1",
        "status": "ready_for_summary",
        "date": date,
        "account": account,
        "window": window,
        "scan_limit": scan_limit,
        "raw_count": raw_count,
        "possibly_truncated": raw_count >= scan_limit,
        "items": items,
    }
    return build_evidence_pack(scan, policy, topic_map, enrich_links, fetcher)


def build_evidence_pack_from_messages_with_classifier(
    date: str,
    account: str,
    window: str,
    scan_limit: int,
    raw_count: int,
    raw_messages: list[tuple[str, bytes]],
    policy: dict[str, Any],
    topic_map: dict[str, Any],
    enrich_links: bool,
    fetcher: Any,
    classifier: LinkContentClassifier,
    confidence_threshold: float,
) -> EmailEvidencePack:
    items = [item.to_dict() for item in message_items(raw_messages, policy)]
    scan = {
        "object_type": "email_evidence_pack",
        "object_version": "0.1",
        "status": "ready_for_summary",
        "date": date,
        "account": account,
        "window": window,
        "scan_limit": scan_limit,
        "raw_count": raw_count,
        "possibly_truncated": raw_count >= scan_limit,
        "items": items,
    }
    return build_evidence_pack_with_classifier(
        scan,
        policy,
        topic_map,
        enrich_links,
        fetcher,
        classifier,
        confidence_threshold,
    )


def _write_classification(
    evidence: dict[str, Any],
    result: LinkClassification,
    confidence_threshold: float,
) -> None:
    classification = result.classification
    decision_reason = result.decision_reason
    if result.confidence < confidence_threshold:
        classification = "unknown"
        decision_reason = f"low_confidence:{decision_reason}"
    evidence["classification"] = classification
    evidence["decision_source"] = result.decision_source
    evidence["decision_reason"] = decision_reason
    evidence["classifier_version"] = result.classifier_version
    evidence["confidence"] = result.confidence


def _links_by_url(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    links: dict[str, dict[str, Any]] = {}
    for link in item.get("links", []):
        if not isinstance(link, dict):
            continue
        url = _normalize_url(str(link.get("url") or ""))
        if url:
            links[url] = link
    return links


def _normalize_url(url: str) -> str:
    return _legacy().normalize_url(url)


def _classification_confidence_threshold(policy: dict[str, Any]) -> float:
    classification_policy = policy.get("classification", {})
    if isinstance(classification_policy, dict) and "confidence_threshold" in classification_policy:
        return float(classification_policy["confidence_threshold"])
    return DEFAULT_CLASSIFICATION_CONFIDENCE_THRESHOLD
