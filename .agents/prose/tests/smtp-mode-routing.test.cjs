"use strict";
const assert = require("node:assert/strict");
const { argumentsFrom, buildRender, deliveryConnector, briefReviewableProjection } = require("../src/evidence-reactor-daemon.cjs");
const { validateBrief, stableId } = require("../src/email-intel-brief-validator.cjs");
const base = ["node", "daemon", "--state", "/tmp/state", "--ledger", "/tmp/ledger.json", "--delivery-target", "recipient@example.test"];
const saved = { ...process.env };
try {
  process.env.OPENAI_API_KEY = "test"; process.env.PODSUM_PYTHON = "/usr/bin/python3";
  delete process.env.PODSUM_REACTOR_SMTP_ENABLED;
  assert.throws(() => argumentsFrom([...base, "--delivery-mode", "smtp"]), /SMTP/);
  process.env.PODSUM_REACTOR_SMTP_ENABLED = "true";
  process.env.PODSUM_EMAIL_SMTP_TO = "recipient@example.test";
  const smtp = argumentsFrom([...base, "--delivery-mode", "smtp"]);
  assert.equal(smtp.deliveryMode, "smtp"); assert.equal(smtp.deliveryTarget, "recipient@example.test");
  assert.throws(() => argumentsFrom([...base, "--delivery-mode", "smtp", "--delivery-target", "other@example.test"]), /匹配/);
  const file = argumentsFrom(["node", "daemon", "--state", "/tmp/state", "--ledger", "/tmp/ledger.json", "--delivery-mode", "file", "--delivery-outbox", "/tmp/outbox", "--delivery-target", "local-file"]);
  assert.equal(file.deliveryMode, "file");
  assert.deepEqual(deliveryConnector(smtp, "delivery:smtp"), { script: "smtp_delivery_action.py", args: ["recipient@example.test", "delivery:smtp"] }, "new SMTP actions route to the SMTP connector");
  assert.deepEqual(deliveryConnector(file, "delivery:file"), { script: "file_outbox_action.py", args: ["/tmp/outbox", "local-file", "delivery:file"] }, "file routing remains unchanged");

  const entryId = "email-item:A";
  const pack = { evidence_ledger: { material_fingerprint: "fingerprint-1", revision: 1, current_entries: [{ entry_id: entryId, redacted: false, refuted: false }] } };
  const claim = { text: "Launch evidence is available.", evidence_entry_ids: [entryId] };
  const confirmed = { object_type: "email_intel_brief", version: 1, brief_id: "email-intel-brief:primary", status: "confirmed", source: { material_fingerprint: "fingerprint-1", revision: 1 }, title: "Daily email intelligence", summary: "One grounded update.", claims: [{ claim_id: stableId("claim:", claim), ...claim }], coverage: { available_entry_ids: [entryId], cited_entry_ids: [entryId], uncited_entry_ids: [] }, gaps: [], decision: { last_action_id: "confirm-1", kind: "confirm_brief", actor: "podsum.local-owner", feedback: "" }, delivery: { status: "not_requested", delivery_id: null, attempt: 0, target: "recipient@example.test", last_action_id: null, outcome: null, error: null, receipt_ref: null } };
  const unknownDelivery = { status: "pending", delivery_id: "delivery:smtp", attempt: 1, target: "recipient@example.test", last_action_id: "send-1", outcome: "outcome_unknown", error: "outcome_unknown_pending_requires_owner_retry", receipt_ref: null };
  const states = { "workbench-action-gateway": { "state/workbench-action.json": Buffer.from(JSON.stringify({ evidence_action: null, brief_action: null, review_action: null, delivery_action: { action: { kind: "send_brief" }, delivery: unknownDelivery } })) }, "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M": { "state/email-intel-brief-via.json": Buffer.from(JSON.stringify(confirmed)), "state/email-intel-brief-reviewable.json": Buffer.from(JSON.stringify(briefReviewableProjection(confirmed))) } };
  const render = buildRender({ read: (node) => ({ files: states[node] ?? {} }) }, smtp);
  render({ node: "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M", wake: { source: "test" } }).then((result) => {
    const rendered = JSON.parse(Buffer.from(result.world_model["state/email-intel-brief-via.json"]).toString("utf8"));
    assert.deepEqual(rendered.delivery, unknownDelivery);
    assert.deepEqual(validateBrief(rendered, pack), rendered, "unknown SMTP replay is a schema-valid pending Brief VIA");
    console.log("SMTP mode routing, pending replay, and file behavior verified");
  }).catch((error) => { throw error; });
} finally { process.env = saved; }
