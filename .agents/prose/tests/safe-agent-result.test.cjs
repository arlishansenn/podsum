"use strict";
const assert = require("node:assert/strict");
const { safeAgentResult } = require("../src/evidence-reactor-daemon.cjs");

const secret = "SENTINEL-AGENT-SECRET";
const result = safeAgentResult({
  world_model: { "state/candidate.json": Buffer.from("{}") },
  semantic_diff: { summary: secret, title: secret, claims: [secret], findings: [secret], snippet: secret, excerpt: secret },
});
assert.deepEqual(result.semantic_diff, { summary: "validated candidate committed", notes: [] });
assert.equal(JSON.stringify(result).includes(secret), false);
