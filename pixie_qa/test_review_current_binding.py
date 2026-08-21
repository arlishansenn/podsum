import hashlib
import unittest

from pixie.eval.evaluable import NamedData
from pixie import Evaluable

from pixie_qa.evaluators import _canonical, review_contract
from pixie_qa.run_app import _ReactorRunnable


CURRENT_FINGERPRINT = "current-fingerprint"


def _agent(fingerprint: str, status: str, review_id: str) -> dict[str, object]:
    return {
        "reviewer_id": "agent:email-reviewer",
        "review_id": review_id,
        "action_ref": "agent:" + fingerprint,
        "brief_id": "brief:primary",
        "brief_fingerprint": fingerprint,
        "status": status,
        "verdict": "approve",
        "findings": ["Concrete current finding."],
    }


def _collection(reviews: list[dict[str, object]]) -> dict[str, object]:
    return {"brief": {"brief_id": "brief:primary", "brief_fingerprint": CURRENT_FINGERPRINT}, "reviews": reviews}


def _contract_evaluable(historical_status: str) -> Evaluable:
    brief = {
        "object_type": "email_intel_brief", "version": 1, "brief_id": "brief:primary", "status": "candidate",
        "source": {"revision": 1, "material_fingerprint": "material"}, "title": "Title", "summary": "Summary",
        "claims": [], "coverage": {}, "gaps": [], "decision": None,
    }
    fingerprint = hashlib.sha256(_canonical(brief).encode()).hexdigest()
    current = _agent(fingerprint, "submitted", "review:current")
    current["brief_fingerprint"] = fingerprint
    current["action_ref"] = "agent:" + fingerprint
    current["review_id"] = "review:" + hashlib.sha256(_canonical({"reviewer_id": "agent:email-reviewer", "brief_id": "brief:primary", "brief_fingerprint": fingerprint}).encode()).hexdigest()
    historical = _agent("old-fingerprint", historical_status, "review:old")
    review = {
        "brief": {"brief_id": "brief:primary", "brief_fingerprint": fingerprint},
        "reviews": [historical, current, {"kind": "human", "status": "submitted"}],
        "conflicts": [{"review_ids": [current["review_id"], "review:human"]}],
    }
    values = {
        "brief_via": brief,
        "evidence_via": {"evidence_ledger": {"revision": 1, "material_fingerprint": "material"}},
        "review_via": review,
        "review_action": {"agent_verdict": "approve", "opposite_verdict": "request_revision", "response": {}},
        "authority_rejection": {"performed": False},
        "review_receipts": {"receipts": [{"node": "email-review-responsibility"}]},
        "receipt_cost": {"cost": {}},
    }
    return Evaluable(eval_input=[NamedData(name="scenario", value={})], eval_output=[NamedData(name=name, value=value) for name, value in values.items()])


class CurrentReviewBindingTest(unittest.TestCase):
    def test_binding_uses_current_review_after_historical_review(self) -> None:
        historical = _agent("old-fingerprint", "superseded", "review:old")
        current = _agent(CURRENT_FINGERPRINT, "submitted", "review:current")
        binding = _ReactorRunnable._review_binding(_collection([historical, current]))
        self.assertEqual(binding["kernel_binding"]["review_id"], "review:current")

    def test_binding_rejects_missing_or_multiple_current_reviews(self) -> None:
        historical = _agent("old-fingerprint", "superseded", "review:old")
        with self.assertRaisesRegex(ValueError, "exactly one current Agent Review"):
            _ReactorRunnable._review_binding(_collection([historical]))
        with self.assertRaisesRegex(ValueError, "exactly one current Agent Review"):
            _ReactorRunnable._review_binding(_collection([_agent(CURRENT_FINGERPRINT, "submitted", "review:one"), _agent(CURRENT_FINGERPRINT, "submitted", "review:two")]))

    def test_contract_allows_resolved_or_superseded_history_and_rejects_active_history(self) -> None:
        for status in ("resolved", "superseded"):
            with self.subTest(status=status):
                self.assertEqual(review_contract(_contract_evaluable(status)).score, 1.0)
        failed = review_contract(_contract_evaluable("submitted"))
        self.assertEqual(failed.score, 0.0)
        self.assertIn("historical Agent Reviews must be superseded or resolved", failed.reasoning)


if __name__ == "__main__":
    unittest.main()
