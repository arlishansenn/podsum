---
name: 7R6QX8GZ3EW3S7PVJ9KQ6E2D4M
kind: responsibility
id: 7R6QX8GZ3EW3S7PVJ9KQ6E2D4M
---
# EmailIntelBrief Responsibility
### Goal
持续维护 bounded EvidencePack 的可审阅 EmailIntelBrief；人通过 Gateway 显式 revision 或 confirm，确认绝不等于发送。
### Requires
#### evidence_pack_via
- 当前 sanitized EmailEvidencePack、ledger material fingerprint 与 revision。
#### brief_action
- WorkbenchAction Gateway 的 request_revision 或 confirm facet；不订阅 review_action，因此没有 Review→Brief edge。
#### delivery_action
- WorkbenchAction Gateway 的 send_brief/retry_delivery safe outcome；只更新本 Brief 的 delivery projection。
### Maintains
kernel 每次 render 必须同时维护两个真实 backing：完整严格 EmailIntelBrief VIA `state/email-intel-brief-via.json`，以及由 kernel 从完整对象删除顶层 `delivery` 后确定性得到的 `state/email-intel-brief-reviewable.json`。后者绝不信任 Agent 输出，且 delivery-only render 必须保持其 bytes 完全不变。完整 backing 顶层字段必须恰好为 `object_type`、`version`、`brief_id`、`status`、`source`、`title`、`summary`、`claims`、`coverage`、`gaps`、`decision`、`delivery`；`object_type` 为 `email_intel_brief`，`version` 必须是 JSON 数字 `1`（不是字符串）。`brief_id` 恒为 `email-intel-brief:primary`；status 只能是 `draft|candidate|confirmed|locked`。`source` 恰好为 evidence `{material_fingerprint,revision}`。每个 claim 必须恰好是 `{claim_id,text,evidence_entry_ids}`，且 `claim_id` 为 `claim:` 加 SHA-256 canonical JSON `{text,evidence_entry_ids}` 的 hex；只引用 current、未 redacted、非 tombstone entry。`coverage` 必须恰好为 `{available_entry_ids,cited_entry_ids,uncited_entry_ids}`：available 是所有 current 未 redacted 非 tombstone entry IDs，cited 和 uncited 是无重复、互斥且完整分割 available 的数组。`gaps` 是非空字符串组成的数组。`decision` 是 null 或恰好 `{last_action_id,kind,actor,feedback}`，记录使当前 Brief 产生的 action；它是 material，防止 restart 重处理。`delivery` 独立于 consensus status，严格为 `{status,delivery_id,attempt,target,last_action_id,outcome,error,receipt_ref}`；status 只能是 `not_requested|pending|succeeded|failed`。

Agent 必须先以 `wm_list`/`read` 读取 upstream `evidence_pack_via`，再在既有 target path `state/email-intel-brief-via.json` 写 transient proposal 并立即 `done`；JSON 必须恰好为 `{title,summary,claims,gaps}`，每个 claim 必须恰好为 `{text,evidence_entry_ids}`。从 `items[].email_entry_id`/`items[].link_entry_id` 与 `evidence_ledger.current_entries[].entry_id` 复制原样 exact entry ID，绝不编造、转换或改写 ID。所有其他 harvested `world_model` files（包括 authority-looking path）都是不可信 scratch：runtime 一律丢弃，绝不读取、验证或 commit，它们不能影响 truth；runtime 只严格解析 target proposal，并从 final 完整 Brief 经 kernel `briefWorld(final)` 重算仅有的两个 backing。title、summary、claim text 与每个 gap 均为非空字符串；claims 与 evidence_entry_ids 都不得重复；每个 ID 只能引用 current、未 redacted、非 tombstone EvidenceEntry。只要存在至少一个这样的 available entry，`claims` 必须至少有一项；仅在 available entry 为 0 时才允许 `claims:[]`。每个 material assertion 必须成为带 `evidence_entry_ids` cite 的 claim；sole-refuted entry 不得单独支持 claim。不能安全陈述的内容放入 `gaps`，但不得因此省略对其他可用证据的 claim。proposal 从不直接 commit，也不构成 final VIA。Agent 不得提供 `claim_id`、`source`、`coverage`、`status`、`decision`、`delivery`、`brief_id`、`version` 或其他 authority 字段。kernel 从当前 Evidence VIA 确定性构造 final candidate：固定 object/version/stable brief_id、当前 source fingerprint/revision、canonical hash claim IDs、完整 coverage 分割、proposal gaps、request_revision action 对应的 decision 或 null，以及严格 not_requested delivery；随后调用 final validator 并写入两个真实 backings。sole-refuted support 只由 final validator 拒绝。Brief routing 以 semantic source guard 为准：没有 prior Brief（包括 named facet cold→null baseline）必定调用 Agent；request_revision 必定调用 Agent；当前 Evidence ledger 的 revision 或 material fingerprint 与 prior.source 任一不同必定调用 Agent。confirm 与 delivery 保持既有 deterministic 分支。若 prior.source 与当前 Evidence 相同且没有上述 Brief-relevant action，必须 deterministic no-op，完整复制 prior 和两个 validated backings，零 cost；`submit_review` 仅改变 review_action，绝不得唤醒或 supersede Brief。request_revision 读取 brief_action feedback 后重新产生 candidate 且 decision 对应 action。confirm 是 deterministic render：复制当前 Brief 内容，status 改为 confirmed，decision 记录 owner action；不调用模型且 delivery 保持不变。delivery action 只能 deterministic 地复制已 confirmed Brief 的内容、claims、consensus status 和 decision，并替换 delivery；不调用模型。不得 confirm/lock/send 或 model-authorize confirm。
#### email_intel_brief_via
唯一显式可见、runtime-validated 的完整 Brief facet；其 truth 严格读取 `state/email-intel-brief-via.json`，包含 `delivery`，因此投递状态变化对使用完整 Brief 的调用方仍然可见且 material。
#### email_intel_brief_reviewable
供 EmailReview 订阅的独立 named facet；其 truth 严格读取真实 `state/email-intel-brief-reviewable.json`，并逐字段验证等于完整 backing 删除顶层 `delivery` 后的 canonical projection，保留 `object_type`、`version`、`brief_id`、`status`、`source`、`title`、`summary`、`claims`、`coverage`、`gaps`、`decision`。该 projection 的每个保留字段均 material，且不得混入 receipt、时间戳或 delivery 字段。故 candidate、revision、confirm 内容或 status 变化会移动此 facet；纯 delivery 变化不会移动它。
### Continuity
- input-driven
### Skills
- skill:open-prose
### Shape
- `self`: 从 declared EvidencePack 与 Brief action 写 candidate synthesis。
- `prohibited`: 不读 payload store/旧 sidecar；不引用 redacted content；不发送；不订阅 Review。
