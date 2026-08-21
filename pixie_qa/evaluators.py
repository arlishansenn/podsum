import hashlib
import json

from pixie import Evaluation, Evaluable, create_agent_evaluator
from pydantic import JsonValue

from pixie_qa.json_types import JsonObject


def _output(evaluable: Evaluable, name: str) -> JsonValue | None:
    return next((item.value for item in evaluable.eval_output if item.name == name), None)


def brief_contract(evaluable: Evaluable) -> Evaluation:
    """检查机械 contract、coverage、redaction、refutation 与 freshness。"""
    brief, evidence, update = _output(evaluable, "brief_via"), _output(evaluable, "evidence_via"), _output(evaluable, "update_receipt")
    if not isinstance(brief, dict) or not isinstance(evidence, dict):
        return Evaluation(score=0.0, reasoning="Missing real brief_via or evidence_via output.")
    brief_object: JsonObject = brief
    evidence_object: JsonObject = evidence
    ledger = evidence_object.get("evidence_ledger", {})
    if not isinstance(ledger, dict):
        return Evaluation(score=0.0, reasoning="Evidence VIA ledger is not a JSON object.")
    current_entries = ledger.get("current_entries", [])
    if not isinstance(current_entries, list):
        return Evaluation(score=0.0, reasoning="Evidence VIA current_entries is not a JSON list.")
    entries = {entry.get("entry_id"): entry for entry in current_entries if isinstance(entry, dict)}
    available = {key for key, value in entries.items() if isinstance(key, str) and not value.get("redacted") and value.get("kind") != "redaction_tombstone"}
    coverage = brief_object.get("coverage", {})
    if not isinstance(coverage, dict):
        return Evaluation(score=0.0, reasoning="Brief coverage is not a JSON object.")
    cited_values, uncited_values = coverage.get("cited_entry_ids", []), coverage.get("uncited_entry_ids", [])
    claims = brief_object.get("claims", [])
    if not isinstance(cited_values, list) or not isinstance(uncited_values, list) or not isinstance(claims, list):
        return Evaluation(score=0.0, reasoning="Brief coverage or claims has an invalid JSON shape.")
    cited, uncited = set(cited_values), set(uncited_values)
    citations = [eid for claim in claims if isinstance(claim, dict) for eid in claim.get("evidence_entry_ids", []) if isinstance(claim.get("evidence_entry_ids", []), list)]
    errors: list[str] = []
    source = brief_object.get("source", {})
    if not isinstance(source, dict) or brief_object.get("status") != "candidate" or source.get("material_fingerprint") != ledger.get("material_fingerprint") or source.get("revision") != ledger.get("revision"):
        errors.append("brief source/status is not current candidate")
    available_values = coverage.get("available_entry_ids", [])
    if not isinstance(available_values, list) or set(available_values) != available or cited | uncited != available or cited & uncited:
        errors.append("coverage is not a strict current-entry partition")
    if any(eid not in available for eid in citations):
        errors.append("citation references unavailable/redacted/tombstone evidence")
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"claim_id", "text", "evidence_entry_ids"}:
            errors.append("claim is not an exact kernel claim")
            continue
        text, ids = claim.get("text"), claim.get("evidence_entry_ids")
        expected_id = "claim:" + hashlib.sha256(_canonical({"text": text, "evidence_entry_ids": ids}).encode()).hexdigest()
        if not isinstance(text, str) or not isinstance(ids, list) or claim.get("claim_id") != expected_id:
            errors.append("proposal claim did not receive its deterministic kernel ID")
            continue
        if ids and all(isinstance(entries.get(eid), dict) and entries[eid].get("refuted") for eid in ids):
            errors.append("claim has only refuted support")
    delivery = brief_object.get("delivery")
    expected_delivery = {"status": "not_requested", "delivery_id": None, "attempt": 0, "last_action_id": None, "outcome": None, "error": None, "receipt_ref": None}
    if not isinstance(delivery, dict) or brief_object.get("decision") is not None or {key: delivery.get(key) for key in expected_delivery} != expected_delivery or not isinstance(delivery.get("target"), str) or not delivery["target"] or set(delivery) != {*expected_delivery, "target"}:
        errors.append("candidate decision/delivery is not the deterministic no-action state")
    if not isinstance(update, dict) or not {"performed", "kind", "action_id", "response", "brief_receipt"} <= set(update):
        errors.append("update receipt is missing required real-action fields")
    elif update.get("performed"):
        receipt = update.get("brief_receipt")
        fingerprints = receipt.get("fingerprints") if isinstance(receipt, dict) else None
        if source.get("revision") != ledger.get("revision") or source.get("material_fingerprint") != ledger.get("material_fingerprint"):
            errors.append("brief did not update after real action")
        if not isinstance(receipt, dict) or receipt.get("node") != "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M" or not isinstance(fingerprints, dict) or fingerprints.get("email_intel_brief_via") != brief_object.get("via_fingerprint"):
            errors.append("update receipt is not the real latest Brief ledger receipt")
    return Evaluation(score=0.0 if errors else 1.0, reasoning="; ".join(errors) if errors else "Exact VIA, coverage, safety, refutation, and freshness mechanics pass.")


grounding_quality = create_agent_evaluator(
    name="GroundingQuality",
    criteria="Compare every material claim and summary assertion in brief_via with the sanitized official excerpts, subjects, and URLs in evidence_via. Score 1 only if all asserted facts are entailed or carefully qualified by those current sources; do not credit plausible external knowledge or invented details.",
)
source_coverage_quality = create_agent_evaluator(
    name="SourceCoverageQuality",
    criteria="Judge whether the candidate meaningfully represents the distinct current official evidence sources, including any cited/uncited rationale. It must not silently collapse an important source into an unrelated generalization.",
)
refutation_handling_quality = create_agent_evaluator(
    name="RefutationHandlingQuality",
    criteria="For scenarios whose evidence_via marks entries refuted, judge whether the brief avoids presenting the refuted proposition as settled and clearly qualifies uncertainty/conflict when it discusses it.",
)


def _canonical(value: JsonValue) -> str:
    if isinstance(value, list):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False, separators=(",", ":")) + ":" + _canonical(value[key]) for key in sorted(value)) + "}"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def review_contract(evaluable: Evaluable) -> Evaluation:
    """机械验证 proposal 的 kernel 绑定、冲突、权限边界与真实 receipt/cost。"""
    brief = _output(evaluable, "brief_via")
    evidence = _output(evaluable, "evidence_via")
    review = _output(evaluable, "review_via")
    # review_binding remains an observable compatibility field, but mechanical
    # correctness is derived from final published VIA/Evidence only.
    action = _output(evaluable, "review_action")
    rejection = _output(evaluable, "authority_rejection")
    receipts = _output(evaluable, "review_receipts")
    cost = _output(evaluable, "receipt_cost")
    if not all(isinstance(value, dict) for value in (brief, evidence, review, action, rejection, receipts, cost)):
        return Evaluation(score=0.0, reasoning="Missing real Evidence, Brief, Review, action, authority, receipt, or cost output.")
    brief_object: JsonObject = brief
    evidence_object: JsonObject = evidence
    review_object: JsonObject = review
    errors: list[str] = []
    review_brief = review_object.get("brief")
    reviews = review_object.get("reviews")
    conflicts = review_object.get("conflicts")
    if not isinstance(review_brief, dict) or review_brief.get("brief_id") != brief_object.get("brief_id") or not isinstance(review_brief.get("brief_fingerprint"), str):
        errors.append("Review collection is not bound to current Brief")
    if not isinstance(reviews, list):
        errors.append("Review collection reviews is invalid")
    else:
        current_fingerprint = review_brief.get("brief_fingerprint") if isinstance(review_brief, dict) else None
        agent = [value for value in reviews if isinstance(value, dict) and value.get("reviewer_id") == "agent:email-reviewer"]
        current_agent = [value for value in agent if value.get("brief_fingerprint") == current_fingerprint]
        historical_agent = [value for value in agent if value.get("brief_fingerprint") != current_fingerprint]
        human = [value for value in reviews if isinstance(value, dict) and value.get("kind") == "human" and value.get("status") == "submitted"]
        if len(current_agent) != 1 or not isinstance(current_agent[0].get("findings"), list) or not current_agent[0]["findings"]:
            errors.append("Agent Review schema, findings, or current fingerprint binding failed")
        elif not _valid_kernel_binding(brief_object, evidence_object, current_agent[0]):
            errors.append("proposal-to-final kernel binding, stable ID, or reviewable fingerprint failed")
        if any(value.get("status") not in {"superseded", "resolved"} for value in historical_agent):
            errors.append("historical Agent Reviews must be superseded or resolved")
        response = action.get("response")
        if not isinstance(response, dict) or action.get("opposite_verdict") == action.get("agent_verdict") or not human:
            errors.append("Gateway did not retain dynamic opposite human review")
    if not isinstance(conflicts, list) or not conflicts:
        errors.append("Opposing active verdicts are not visibly preserved as conflict")
    if rejection.get("performed") is True and (rejection.get("status") != 400 or not isinstance(rejection.get("response"), dict)):
        errors.append("Agent authority rejection did not return real Gateway 400")
    receipt_values = receipts.get("receipts")
    if not isinstance(receipt_values, list) or not any(isinstance(value, dict) and value.get("node") == "email-review-responsibility" for value in receipt_values):
        errors.append("Review receipt is missing")
    cost_value = cost.get("cost")
    if not isinstance(cost_value, dict):
        errors.append("Receipt cost is missing")
    if brief_object.get("status") != "candidate":
        errors.append("Rejected Agent confirm mutated Brief authority state")
    return Evaluation(score=0.0 if errors else 1.0, reasoning="; ".join(errors) if errors else "Proposal semantic fields and deterministic kernel binding, visible conflict, authority boundary, receipt, and cost mechanics pass.")


def _valid_kernel_binding(brief: JsonObject, evidence: JsonObject, agent: JsonObject) -> bool:
    """Derive proposal semantics and kernel identity from final VIA/Evidence only."""
    ledger = evidence.get("evidence_ledger")
    source = brief.get("source")
    if not isinstance(ledger, dict) or not isinstance(source, dict) or source.get("revision") != ledger.get("revision") or source.get("material_fingerprint") != ledger.get("material_fingerprint"):
        return False
    if agent.get("reviewer_id") != "agent:email-reviewer" or not isinstance(agent.get("verdict"), str) or not isinstance(agent.get("findings"), list) or not all(isinstance(finding, str) for finding in agent["findings"]):
        return False
    reviewable_keys = ("object_type", "version", "brief_id", "status", "source", "title", "summary", "claims", "coverage", "gaps", "decision")
    if any(key not in brief for key in reviewable_keys):
        return False
    reviewable = {key: brief[key] for key in reviewable_keys}
    fingerprint = hashlib.sha256(_canonical(reviewable).encode()).hexdigest()
    stable_id = "review:" + hashlib.sha256(_canonical({"reviewer_id": "agent:email-reviewer", "brief_id": brief.get("brief_id"), "brief_fingerprint": fingerprint}).encode()).hexdigest()
    return agent.get("brief_id") == brief.get("brief_id") and agent.get("brief_fingerprint") == fingerprint and agent.get("action_ref") == "agent:" + fingerprint and agent.get("review_id") == stable_id


review_quality = create_agent_evaluator(
    name="ReviewQuality",
    criteria="Judge only the real Agent review in review_via against current brief_via and evidence_via. Score 1 only when its findings name concrete Brief claims/evidence or omissions, are grounded in current public excerpts/URLs, and its approve/request_revision/abstain verdict is consistent with those findings. Do not reward generic praise, invented facts, or authority language that confirms, locks, or sends.",
)
