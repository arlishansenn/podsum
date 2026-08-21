"use strict";

const crypto = require("node:crypto");
function fail(message) { throw new Error(`EmailIntelBrief VIA 不合法：${message}`); }
function object(value, name) { if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${name} 必须是对象`); return value; }
function string(value, name) { if (typeof value !== "string" || !value.trim()) fail(`${name} 必须是非空字符串`); return value; }
function integer(value, name) { if (!Number.isSafeInteger(value) || value < 0) fail(`${name} 必须是非负整数`); return value; }
function exact(value, keys, name) { if (Object.keys(value).length !== keys.length || keys.some((key) => !(key in value))) fail(`${name} 字段不精确`); }
function canonical(value) { if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`; if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`; return JSON.stringify(value); }
function stableId(prefix, value) { return `${prefix}${crypto.createHash("sha256").update(canonical(value)).digest("hex")}`; }
function evidenceIndex(pack) {
  const ledger = object(pack.evidence_ledger, "evidence_pack.evidence_ledger");
  const entries = Array.isArray(ledger.current_entries) ? ledger.current_entries : fail("current_entries 必须是数组");
  const map = new Map();
  for (const entryValue of entries) { const entry = object(entryValue, "EvidenceEntry"); const id = string(entry.entry_id, "EvidenceEntry.entry_id"); if (map.has(id)) fail("current_entries 有重复 EvidenceEntry ID"); map.set(id, { redacted: entry.redacted === true, refuted: entry.refuted === true, tombstone: entry.kind === "redaction_tombstone" }); }
  return { map, fingerprint: string(ledger.material_fingerprint, "evidence material_fingerprint"), revision: integer(ledger.revision, "evidence revision") };
}
function availableEvidenceIds(evidence) { return [...evidence.map].filter(([, entry]) => !entry.redacted && !entry.tombstone).map(([id]) => id); }
function validateBriefProposal(value, pack) {
  const proposal = object(value, "brief proposal");
  exact(proposal, ["title", "summary", "claims", "gaps"], "brief proposal");
  string(proposal.title, "proposal.title"); string(proposal.summary, "proposal.summary");
  const claims = Array.isArray(proposal.claims) ? proposal.claims : fail("proposal.claims 必须是数组");
  const evidence = evidenceIndex(pack); const available = new Set(availableEvidenceIds(evidence)); if (available.size && !claims.length) fail("存在可用 EvidenceEntry 时 proposal.claims 不得为空"); const identities = new Set();
  for (const claimValue of claims) {
    const claim = object(claimValue, "proposal.claim"); exact(claim, ["text", "evidence_entry_ids"], "proposal.claim");
    const text = string(claim.text, "proposal.claim.text"); const ids = Array.isArray(claim.evidence_entry_ids) ? claim.evidence_entry_ids.map((id) => string(id, "proposal.evidence_entry_id")) : fail("proposal.claim.evidence_entry_ids 必须是数组");
    if (!ids.length) fail("proposal.claim 缺少 evidence_entry_ids"); if (new Set(ids).size !== ids.length) fail("proposal.claim.evidence_entry_ids 不得有重复 ID");
    if (ids.some((id) => !available.has(id))) fail("proposal.claim 只能引用 current 未 redacted 非 tombstone EvidenceEntry");
    const identity = canonical({ text, evidence_entry_ids: ids }); if (identities.has(identity)) fail("proposal.claims 不得有重复 claim"); identities.add(identity);
  }
  const gaps = Array.isArray(proposal.gaps) ? proposal.gaps : fail("proposal.gaps 必须是数组"); gaps.forEach((gap) => string(gap, "proposal.gap"));
  return proposal;
}
function buildBriefFromProposal(proposalValue, pack, action, deliveryTarget) {
  if (action !== null && object(action, "action").kind !== "request_revision") fail("proposal kernel 只接受 request_revision action");
  const proposal = validateBriefProposal(proposalValue, pack); const evidence = evidenceIndex(pack); const available = availableEvidenceIds(evidence);
  const cited = new Set(proposal.claims.flatMap((claim) => claim.evidence_entry_ids));
  const brief = {
    object_type: "email_intel_brief", version: 1, brief_id: "email-intel-brief:primary", status: "candidate",
    source: { material_fingerprint: evidence.fingerprint, revision: evidence.revision }, title: proposal.title, summary: proposal.summary,
    claims: proposal.claims.map((claim) => ({ claim_id: stableId("claim:", { text: claim.text, evidence_entry_ids: claim.evidence_entry_ids }), text: claim.text, evidence_entry_ids: claim.evidence_entry_ids })),
    coverage: { available_entry_ids: available, cited_entry_ids: available.filter((id) => cited.has(id)), uncited_entry_ids: available.filter((id) => !cited.has(id)) },
    gaps: proposal.gaps, decision: action === null ? null : { last_action_id: string(action.action_id, "action.action_id"), kind: "request_revision", actor: string(action.actor, "action.actor"), feedback: typeof action.feedback === "string" ? action.feedback : fail("action.feedback 必须是字符串") },
    delivery: { status: "not_requested", delivery_id: null, attempt: 0, target: string(deliveryTarget, "delivery target"), last_action_id: null, outcome: null, error: null, receipt_ref: null },
  };
  return validateBrief(brief, pack);
}
function validateBrief(value, pack) {
  const brief = object(value, "brief");
  exact(brief, ["object_type", "version", "brief_id", "status", "source", "title", "summary", "claims", "coverage", "gaps", "decision", "delivery"], "brief");
  if (brief.object_type !== "email_intel_brief" || brief.version !== 1) fail("object_type/version 不匹配");
  if (brief.brief_id !== "email-intel-brief:primary") fail("brief_id 必须是稳定 primary identity");
  if (!["draft", "candidate", "confirmed", "locked"].includes(brief.status)) fail("status 不合法");
  if (brief.decision !== null) { const decision = object(brief.decision, "decision"); exact(decision, ["last_action_id", "kind", "actor", "feedback"], "decision"); string(decision.last_action_id, "decision.last_action_id"); if (!["request_revision", "confirm_brief"].includes(decision.kind)) fail("decision.kind 不合法"); string(decision.actor, "decision.actor"); if (typeof decision.feedback !== "string") fail("decision.feedback 必须是字符串"); }
  const delivery = object(brief.delivery, "delivery"); exact(delivery, ["status", "delivery_id", "attempt", "target", "last_action_id", "outcome", "error", "receipt_ref"], "delivery"); if (!["not_requested", "pending", "succeeded", "failed"].includes(delivery.status)) fail("delivery.status 不合法"); integer(delivery.attempt, "delivery.attempt"); string(delivery.target, "delivery.target"); for (const key of ["delivery_id", "last_action_id", "outcome", "error", "receipt_ref"]) if (delivery[key] !== null && typeof delivery[key] !== "string") fail(`delivery.${key} 必须是 string 或 null`); if (delivery.status === "not_requested" && (delivery.delivery_id !== null || delivery.attempt !== 0 || delivery.last_action_id !== null || delivery.outcome !== null || delivery.error !== null || delivery.receipt_ref !== null)) fail("not_requested delivery 必须为空初始状态"); if (delivery.status === "succeeded" && (!delivery.delivery_id || !delivery.last_action_id || !delivery.outcome || !delivery.receipt_ref || delivery.error !== null)) fail("succeeded delivery 字段不完整"); if (delivery.status === "failed" && (!delivery.delivery_id || !delivery.last_action_id || !delivery.outcome || typeof delivery.error !== "string" || delivery.receipt_ref !== null)) fail("failed delivery 字段不完整");
  const source = object(brief.source, "source"); exact(source, ["material_fingerprint", "revision"], "source");
  const evidence = evidenceIndex(pack);
  if (source.material_fingerprint !== evidence.fingerprint || source.revision !== evidence.revision) fail("source fingerprint/revision 已过期");
  string(brief.title, "title"); string(brief.summary, "summary");
  const claims = Array.isArray(brief.claims) ? brief.claims : fail("claims 必须是数组"); const claimIds = new Set();
  for (const claimValue of claims) { const claim = object(claimValue, "claim"); exact(claim, ["claim_id", "text", "evidence_entry_ids"], "claim"); const ids = Array.isArray(claim.evidence_entry_ids) ? claim.evidence_entry_ids : fail("claim.evidence_entry_ids 必须是数组"); if (!ids.length) fail("material claim 缺少 evidence_entry_ids"); if (new Set(ids).size !== ids.length) fail("claim.evidence_entry_ids 不得有重复 ID"); const expected = stableId("claim:", { text: string(claim.text, "claim.text"), evidence_entry_ids: ids }); if (claim.claim_id !== expected || claimIds.has(claim.claim_id)) fail("claim_id 必须稳定且唯一"); claimIds.add(claim.claim_id); const supports = ids.map((id) => evidence.map.get(string(id, "evidence_entry_id"))); if (supports.some((entry) => entry === undefined)) fail("claim 引用未知 EvidenceEntry"); if (supports.some((entry) => entry.redacted || entry.tombstone)) fail("claim 引用 redacted 或 tombstone EvidenceEntry"); if (supports.every((entry) => entry.refuted)) fail("claim 只有 refuted support"); }
  const coverage = object(brief.coverage, "coverage"); exact(coverage, ["available_entry_ids", "cited_entry_ids", "uncited_entry_ids"], "coverage"); for (const key of ["available_entry_ids", "cited_entry_ids", "uncited_entry_ids"]) if (!Array.isArray(coverage[key])) fail(`coverage.${key} 必须是数组`); const coverageIds = (key) => { const ids = coverage[key].map((id) => string(id, `coverage.${key} ID`)); if (new Set(ids).size !== ids.length) fail(`coverage.${key} 不得有重复 ID`); return new Set(ids); }; const available = coverageIds("available_entry_ids"); const cited = coverageIds("cited_entry_ids"); const uncited = coverageIds("uncited_entry_ids"); const expectedAvailable = new Set(availableEvidenceIds(evidence)); if (available.size !== expectedAvailable.size || [...available].some((id) => !expectedAvailable.has(id))) fail("coverage.available_entry_ids 必须等于全部未 redacted current EvidenceEntry"); if ([...cited].some((id) => !available.has(id)) || [...uncited].some((id) => !available.has(id)) || [...cited].some((id) => uncited.has(id)) || [...available].some((id) => !cited.has(id) && !uncited.has(id))) fail("coverage.cited_entry_ids 与 uncited_entry_ids 必须严格分割 available_entry_ids"); if (expectedAvailable.size && !claims.length) fail("存在可用 EvidenceEntry 时 claims 不得为空"); if (!Array.isArray(brief.gaps) || brief.gaps.some((gap) => typeof gap !== "string" || !gap.trim())) fail("gaps 必须是非空字符串数组"); return brief;
}
module.exports = { availableEvidenceIds, buildBriefFromProposal, canonical, stableId, validateBrief, validateBriefProposal };
