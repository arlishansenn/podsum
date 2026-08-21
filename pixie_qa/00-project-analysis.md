# Project Analysis

## What this software does

Podsum is a stateful local macOS pipeline for collecting podcast and email information and delivering readable summaries. Its email-evidence path normalizes email/link material into an append-only local ledger, projects a sanitized EvidencePack through an OpenProse Reactor, and uses a real LLM to maintain a review-only, cited EmailIntelBrief candidate. A successful run exposes a current brief whose claims can be traced to non-redacted evidence without confirming, locking, or sending anything.

## Target users and value proposition

A privacy-conscious Podsum operator uses the local workbench to review inbox-derived intelligence before taking any action. The evidence ledger and Reactor give that operator a current, auditable synthesis rather than an opaque free-form summary: source revisions, evidence IDs, redactions, and refutations remain explicit.

## Capability inventory

1. Evidence ingestion and projection: accept a kernel-validated evidence commit and publish a sanitized current EvidencePack.
2. Relation-aware evidence maintenance: represent support, refutation, qualification, and redaction/update events in the append-only ledger.
3. LLM brief synthesis: the input-driven `EmailIntelBrief Responsibility` uses the real configured provider to generate a candidate brief from the EvidencePack.
4. Deterministic safety validation: reject stale source revisions, unknown/redacted/tombstone citations, sole-refuted claims, unstable IDs, and incomplete coverage.
5. Read-only workbench delivery: expose the current VIA and action history for human review without confirm/lock/send semantics.
6. Review Agent quality: a real Agent produces a current-fingerprint-bound advisory review; deterministic merge preserves opposite human verdicts as visible conflicts and Gateway rejects Agent confirmation authority.

## Realistic input characteristics

The evaluated boundary receives controlled `EmailEvidencePack` commit envelopes (typically 2–20 current evidence entries; compact JSON from a few KB to roughly 100 KB), not raw IMAP credentials or production mailbox contents. Entries contain sanitized excerpts, stable email/link evidence IDs, source URLs, ledger revision/fingerprint, and relation state. Real cases mix multiple public announcements or advisories, may disagree, and can be revised by a redaction action; the resulting brief must cover every currently usable entry while never repeating removed content.

## Hard problems and failure modes

1. Grounding and source coverage: an LLM can make a plausible claim that omits available evidence or cites an entry that does not support its wording.
2. Conflicting/refuted evidence: a brief can present a contested statement as settled when all of its cited support has been refuted.
3. Redaction safety: historical content may be removed from the current pack; a model can leak it or retain it as a citation after the update.
4. Update freshness: an input-driven brief can retain an old ledger fingerprint/revision after a new evidence commit or action.
5. Structured contract compliance: a free-form agent response may fail to create stable claim IDs or the exact VIA/coverage structure required by the deterministic validator.
