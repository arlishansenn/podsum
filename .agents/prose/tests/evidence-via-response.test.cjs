"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { evidenceViaResponse } = require("../src/evidence-reactor-daemon.cjs");

const evidence = {
  object_type: "email_evidence_pack",
  object_version: "1",
  items: [],
  evidence_ledger: { revision: 7, material_fingerprint: "commit-fingerprint" },
};

function runtime(files, receipts) {
  return {
    reactor: {
      store: { read: () => ({ files }) },
      ledger: { all: () => receipts },
    },
  };
}

test("Evidence VIA body is exact backing plus current named receipt fingerprint", () => {
  const response = evidenceViaResponse(runtime(
    { "state/evidence-pack-via.json": Buffer.from(JSON.stringify(evidence)) },
    [{ node: "other", fingerprints: { evidence_pack_via: "wrong-node" } }, { node: "5ZPQV9NQVE4W4FR40F9XSCJ7TW", fingerprints: { evidence_pack_via: "current-evidence-receipt" } }],
  ));
  assert.deepEqual(response, { ...evidence, via_fingerprint: "current-evidence-receipt" });
  assert.equal(evidence.via_fingerprint, undefined, "fingerprint must not be written into world model backing");
});

test("Evidence VIA fails closed without backing or named receipt", () => {
  assert.throws(() => evidenceViaResponse(runtime({}, [{ node: "5ZPQV9NQVE4W4FR40F9XSCJ7TW", fingerprints: { evidence_pack_via: "receipt" } }])), /尚未 published/);
  assert.throws(() => evidenceViaResponse(runtime(
    { "state/evidence-pack-via.json": Buffer.from(JSON.stringify(evidence)) },
    [],
  )), /尚未有 receipt/);
});
