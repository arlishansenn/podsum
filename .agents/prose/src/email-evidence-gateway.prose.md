---
name: email-evidence-gateway
kind: gateway
---
# EvidenceIngress Gateway
### Goal
发布已提交、无 payload 的 evidence ledger 投影。
### Continuity
- external-driven
### Receives
- `POST /trigger/email-evidence-gateway`：只接受 `{commit, projection, current_pack}`；不得含 payload store 或原文 payload。
### Maintains
唯一 backing `state/evidence-pack.json` 为完整 `{commit, projection, current_pack}`；编译 canonicalizer 的结构 truth 必须是 `{evidence_pack: <该 backing>}`，所有该 facet 的 material path 都以 `evidence_pack` 为根。
#### evidence_pack
唯一 facet 是该 canonical object。
### Emits
- 5ZPQV9NQVE4W4FR40F9XSCJ7TW
