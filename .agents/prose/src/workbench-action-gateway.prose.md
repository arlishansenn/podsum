---
name: workbench-action-gateway
kind: gateway
---
# WorkbenchAction Gateway
### Goal
按 action responsibility 发布已验证的 named action facet，不让无关 action 唤醒无关责任。
### Continuity
- external-driven
### Receives
- `POST /trigger/workbench-action`：Python kernel 已验证的 action receipt；receipt 带精确 `facets` 数组，每次更新所有目标 facet，并保留其他 facet 的 prior truth。
### Maintains
backing `state/workbench-action.json` 是 `{evidence_action, brief_action, review_action, delivery_action}`；每一个值是 null 或该 facet 最近一次已验证 receipt。编译 canonicalizer 按独立 facet root 投影，绝不将整个 backing 作为任一 facet 的 truth。
#### evidence_action
Evidence relation/redaction action 的最近 receipt。
#### brief_action
Brief request_revision/confirm action 的最近 receipt。
#### review_action
Review submit_review，以及 request_revision/confirm action 的最近 receipt；后两者同时写入 brief_action 与 review_action。
#### delivery_action
仅 send_brief/retry_delivery 的已验证安全 outcome receipt。它只 feed Brief Responsibility，绝不 wake Evidence 或 Review。
### Emits
- 5ZPQV9NQVE4W4FR40F9XSCJ7TW
- 7R6QX8GZ3EW3S7PVJ9KQ6E2D4M
- email-review-responsibility
