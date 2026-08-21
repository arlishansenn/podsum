---
name: email-review-responsibility
kind: responsibility
id: email-review-responsibility
---
# EmailReview Responsibility
### Goal
对每个 candidate Brief 维护独立 agent/human Review VIA，并保留 active verdict conflict；Review 不改变 Brief 共识。
### Requires
#### email_intel_brief_reviewable
- 当前 EmailIntelBrief 的 published structured reviewable facet，实际文件为 `state/email-intel-brief-reviewable.json`。其精确 JSON 模板是 `{object_type,version,brief_id,status,source,title,summary,claims,coverage,gaps,decision}`，即完整 Brief 的同名字段且没有 `delivery`；不得创建第二个文件、猜测路径或自行投影。订阅此 facet 使 candidate、revision、confirm 内容或 status 的变化自然唤醒 Review，而纯 delivery 变化不会唤醒它。Review Agent 从该文件读取被审阅对象；deterministic kernel 另从 `state/email-intel-brief-via.json` 读取完整严格 Brief 进行 merge。
#### review_action
- WorkbenchAction Gateway 的 review_action facet（submit_review/request_revision/confirm_brief）；不产生指向 Brief 的 subscription。
### Maintains
唯一 backing `state/email-review-via.json` 是 kernel commit 后的严格 collection facet，顶层恰好为 `object_type:"email_review_collection"`、`version:1`、`brief`、`reviews`、`conflicts`、`processed_action_ids`。`brief` 必须恰好是 `{brief_id,brief_fingerprint,status}`，且它精确绑定当前 Brief；fingerprint 是 `email_intel_brief_reviewable` 当前 canonical JSON 的 SHA-256 hex，不含 delivery。confirmed 时 collection 保留该被审阅 candidate reviewable fingerprint 供 resolve，绝不是 confirmed JSON hash。每个 Review 必须恰好有 stable `review_id`、`brief_id`、`brief_fingerprint`、`reviewer_id`、`kind:"agent"|"human"`、`status:"draft"|"submitted"|"resolved"|"superseded"`、`verdict:"approve"|"request_revision"|"abstain"`、非空具体 `findings`、`action_ref`。

新 candidate 的 Agent render 只在同一路径写 transient proposal，且 JSON 必须恰好为 `{verdict,findings}`：verdict 是 `approve|request_revision|abstain`，findings 是非空具体审阅发现，绝不是 `pending` 占位。proposal 从不直接 commit。runtime kernel 从当前实际 reviewable backing 计算 fingerprint，并唯一构造 agent Review：`reviewer_id:"agent:email-reviewer"`、`kind:"agent"`、`status:"submitted"`、`action_ref:"agent:" + brief_fingerprint`，以及仅由 `{reviewer_id,brief_id,brief_fingerprint}` canonical hash 导出的 stable `review_id`；findings 的随机措辞不得改变 ID。Agent 不得提供或覆盖 ID、actor、status、brief binding、history、conflicts 或 processed actions。kernel 随后与 prior 做 deterministic merge、验证完整 collection，并只发布这个最终 backing。

request_revision 先由 Brief 生成新 candidate，再为该新 candidate 调用真实 Review Agent；旧 brief reviews superseded。submit_review 和 confirm 均只走 deterministic merge：前者原样加入，后者使目标 candidate reviews resolved，绝不调用 Review Agent；不同 active verdict 必须在 conflicts 可见。confirmed Brief 不产生新 agent review。`processed_action_ids` 是 material，用于 restart 幂等。
#### email_review_via
唯一可见 EmailReview collection facet。
### Continuity
- input-driven
### Skills
- skill:open-prose
### Shape
- `self`: 对新 candidate 做 advisory review；human/confirm merge 不调用模型。
- `prohibited`: 不确认、锁定、发送、授权 redaction，且不写 Brief。
