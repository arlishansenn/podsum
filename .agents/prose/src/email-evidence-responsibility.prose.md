---
name: 5ZPQV9NQVE4W4FR40F9XSCJ7TW
kind: responsibility
id: 5ZPQV9NQVE4W4FR40F9XSCJ7TW
---
# Evidence Responsibility
### Goal
从 evidence ingress 与 Evidence Action Gateway truth 选择 revision 最新的 sanitized evidence projection。
### Requires
#### evidence_pack
- EvidenceIngress Gateway 的已发布 `{commit, projection, current_pack}`。
#### evidence_action
- WorkbenchAction Gateway 的 relation/redaction facet；不订阅 Brief 或 Review action。
### Maintains
唯一 backing `state/evidence-pack-via.json` 是 flat EmailEvidencePack：`{...current_pack, evidence_ledger: projection}`；编译 canonicalizer 的结构 truth 必须是 `{evidence_pack_via: <backing>}`。确定性读取两个 upstream published truths，严格验证并按 projection/commit revision 选择最高；同 revision 不同 material fingerprint 必须 fail closed。任一 Gateway 尚未 published 时可使用另一方；不得读取 payload store。
#### evidence_pack_via
唯一可见 facet 是该 flat canonical object。
### Continuity
- input-driven
