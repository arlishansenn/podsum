"use strict";
// #32: exercise the production buildRender seam with its filesystem-store shape; no provider is invoked.
const assert = require("node:assert/strict");
const { briefNeedsAgent, briefReviewableProjection, buildRender, mergeReview } = require("../src/evidence-reactor-daemon.cjs");
const { stableId } = require("../src/email-intel-brief-validator.cjs");

const BRIEF = "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M";
const EVIDENCE = "5ZPQV9NQVE4W4FR40F9XSCJ7TW";
const ACTIONS = "workbench-action-gateway";
const entryId = "email-item:A";
const evidence = { evidence_ledger: { revision: 1, material_fingerprint: "fp-1", current_entries: [{ entry_id: entryId, redacted: false, refuted: false }] } };
const claim = { text: "Grounded launch update.", evidence_entry_ids: [entryId] };
const prior = { object_type: "email_intel_brief", version: 1, brief_id: "email-intel-brief:primary", status: "candidate", source: { revision: 1, material_fingerprint: "fp-1" }, title: "Daily brief", summary: "A grounded update.", claims: [{ claim_id: stableId("claim:", claim), ...claim }], coverage: { available_entry_ids: [entryId], cited_entry_ids: [entryId], uncited_entry_ids: [] }, gaps: [], decision: null, delivery: { status: "not_requested", delivery_id: null, attempt: 0, target: "local-file", last_action_id: null, outcome: null, error: null, receipt_ref: null } };
const bytes = (value) => Buffer.from(JSON.stringify(value));
const parseWorld = (value) => JSON.parse(Buffer.from(value).toString("utf8"));
const actions = (brief_action = null, review_action = null, delivery_action = null) => ({ evidence_action: null, brief_action, review_action, delivery_action });
const storeFor = (actionState = actions(), currentEvidence = evidence, currentBrief = prior) => ({ read(node) { const files = {}; if (node === EVIDENCE && currentEvidence) files["state/evidence-pack-via.json"] = bytes(currentEvidence); if (node === BRIEF && currentBrief) { files["state/email-intel-brief-via.json"] = bytes(currentBrief); files["state/email-intel-brief-reviewable.json"] = bytes(briefReviewableProjection(currentBrief)); } if (node === ACTIONS) files["state/workbench-action.json"] = bytes(actionState); return { files }; } });
const options = { contractsDir: __dirname, stateDir: "/tmp/podsum-brief-routing", skillPath: __filename, provider: {}, renderModel: "unused", maxTurns: 1, deliveryTarget: "local-file" };
const context = { node: BRIEF, wake: { source: "test" } };

(async () => {
  assert.equal(briefNeedsAgent(null, evidence, null, null), true, "cold named facet must route to Agent");
  assert.equal(briefNeedsAgent(prior, evidence, null, null), false, "same Evidence and explicit null baseline is a no-op");
  assert.equal(briefNeedsAgent(prior, evidence, null, null), false, "review-only actions never participate in Brief routing");
  assert.equal(briefNeedsAgent(prior, { ...evidence, evidence_ledger: { ...evidence.evidence_ledger, revision: 2 } }, null, null), true, "new Evidence revision must route to Agent");
  assert.equal(briefNeedsAgent(prior, evidence, { kind: "request_revision" }, null), true, "request_revision must route to Agent");
  assert.equal(briefNeedsAgent(prior, evidence, { kind: "confirm_brief" }, null), false, "confirm is deterministic");
  assert.equal(briefNeedsAgent(prior, evidence, null, { delivery: {} }), false, "delivery is deterministic");

  const reviewOnly = actions(null, { action: { kind: "submit_review", action_id: "review-1" } });
  const noOp = await buildRender(storeFor(reviewOnly), options)(context);
  assert.equal(noOp.cost.provider, "deterministic");
  assert.deepEqual(parseWorld(noOp.world_model["state/email-intel-brief-via.json"]), prior, "review-only wake must copy the complete prior Brief");
  assert.deepEqual(parseWorld(noOp.world_model["state/email-intel-brief-reviewable.json"]), briefReviewableProjection(prior), "reviewable backing must remain the validated projection");

  const confirmAction = { action_id: "confirm-1", kind: "confirm_brief", actor: "podsum.local-owner" };
  const confirmed = await buildRender(storeFor(actions({ action: confirmAction })), options)(context);
  assert.equal(parseWorld(confirmed.world_model["state/email-intel-brief-via.json"]).status, "confirmed", "confirm remains deterministic");
  const delivery = { ...prior.delivery, status: "succeeded", delivery_id: "delivery:1", attempt: 1, last_action_id: "send-1", outcome: "succeeded", receipt_ref: "receipt-1" };
  const delivered = await buildRender(storeFor(actions(null, null, { delivery }), evidence, { ...prior, status: "confirmed" }), options)(context);
  assert.deepEqual(parseWorld(delivered.world_model["state/email-intel-brief-via.json"]).delivery, delivery, "delivery remains deterministic");

  const submit = { action_id: "review-1", kind: "submit_review", actor: "podsum.local-owner", verdict: "approve", findings: ["grounded"] };
  const collection = mergeReview(null, prior, briefReviewableProjection(prior), submit);
  assert.equal(collection.brief.brief_fingerprint, collection.reviews[0].brief_fingerprint, "first submit_review must bind the current Brief fingerprint, not supersede it");
  const reviewRendered = await buildRender(storeFor(actions(null, { action: submit })), options)({ node: "email-review-responsibility", wake: { source: "test" } });
  const renderedCollection = parseWorld(reviewRendered.world_model["state/email-review-via.json"]);
  assert.equal(renderedCollection.brief.brief_fingerprint, renderedCollection.reviews[0].brief_fingerprint, "production Review render must retain the current fingerprint on first submit_review");
  assert.equal(renderedCollection.reviews[0].status, "submitted");
  console.log("#32 Brief cold baseline, semantic source guard, and deterministic routing verified");
})().catch((error) => { console.error(error); process.exitCode = 1; });
