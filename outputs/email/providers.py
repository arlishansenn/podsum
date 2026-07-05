from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

LinkClassificationValue = Literal["content", "ad", "sponsor", "navigation", "tracking", "unknown"]
LinkDecisionSource = Literal["deterministic", "ai_classifier"]


@dataclass(frozen=True)
class LinkClassification:
    classification: LinkClassificationValue
    decision_source: LinkDecisionSource
    decision_reason: str
    classifier_version: str
    confidence: float


class LinkContentClassifier(Protocol):
    def classify(
        self,
        item: dict[str, Any],
        link: dict[str, Any],
        evidence: dict[str, Any],
        policy: dict[str, Any],
    ) -> LinkClassification:
        raise NotImplementedError


class DeterministicLinkClassifier:
    def classify(
        self,
        item: dict[str, Any],
        link: dict[str, Any],
        evidence: dict[str, Any],
        policy: dict[str, Any],
    ) -> LinkClassification:
        legacy = _legacy()
        url = str(evidence.get("url") or link.get("url") or "")
        reason = legacy.skip_reason_for_url(url, policy)
        if reason:
            return _classification(_classification_for_skip_reason(reason), reason, 1.0)
        text = " ".join(
            [
                str(link.get("anchor_text") or evidence.get("anchor_text") or ""),
                str(link.get("context") or evidence.get("email_context") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("excerpt") or ""),
                url,
            ]
        ).lower()
        if _contains_any(text, ("sponsor", "sponsored", "赞助", "推广")):
            return _classification("sponsor", "matched_sponsor_text", 1.0)
        if _contains_any(text, ("sale", "discount", "promo", "coupon", "优惠", "促销")):
            return _classification("ad", "matched_ad_text", 1.0)
        if _contains_any(text, ("login", "signin", "account", "privacy", "terms", "calendar")):
            return _classification("navigation", "matched_navigation_text", 1.0)
        if evidence.get("status") == "fetched":
            return _classification("content", "fetched_public_link", 1.0)
        return _classification("content", "eligible_public_link", 0.8)


class FakeLinkClassifier:
    def __init__(self, classifications: dict[str, LinkClassification]) -> None:
        self._classifications = dict(classifications)

    def classify(
        self,
        item: dict[str, Any],
        link: dict[str, Any],
        evidence: dict[str, Any],
        policy: dict[str, Any],
    ) -> LinkClassification:
        url = str(evidence.get("url") or link.get("url") or "")
        if url in self._classifications:
            return self._classifications[url]
        return LinkClassification(
            classification="unknown",
            decision_source="ai_classifier",
            decision_reason="fake_classifier_missing",
            classifier_version="fake",
            confidence=0.0,
        )


def _legacy() -> Any:
    import podsum_email_summary

    return podsum_email_summary


def _classification(
    classification: LinkClassificationValue,
    decision_reason: str,
    confidence: float,
) -> LinkClassification:
    return LinkClassification(
        classification=classification,
        decision_source="deterministic",
        decision_reason=decision_reason,
        classifier_version="deterministic-v1",
        confidence=confidence,
    )


def _classification_for_skip_reason(reason: str) -> LinkClassificationValue:
    lowered = reason.lower()
    if _contains_any(lowered, ("track", "unsubscribe", "optout", "pixel")):
        return "tracking"
    if _contains_any(lowered, ("login", "signin", "calendar", "attachment", "download")):
        return "navigation"
    return "unknown"


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)
