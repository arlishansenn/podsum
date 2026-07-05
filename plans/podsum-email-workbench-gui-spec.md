# Podsum Email Workbench GUI 规格

## Summary

Email Workbench 是 Podsum 的本地手动审核台。它读取已有 Email Summary artifact，把四个核心可视化工作对象呈现给用户，并通过质量门禁面板控制导出。

它不是新的后台 runner，不自动读 IMAP，不自动调用 Hermes，不自动发送。

## Access

启动命令：

```sh
/usr/bin/python3 outputs/podsum.py email-workbench \
  --root ~/Podcasts/AutoDownloads \
  --date 2026-07-05 \
  --host 127.0.0.1 \
  --port 8765
```

访问地址：

```text
http://127.0.0.1:8765
```

在 macmini 上运行时，通过 SSH tunnel 访问：

```sh
ssh -L 8765:127.0.0.1:8765 macmini
```

## Object Relationship

```mermaid
flowchart LR
  T["EmailTopicMap<br/>核心对象：跟踪话题"] --> E["EmailEvidencePack<br/>核心对象"]
  P["EmailEvidencePolicy<br/>核心对象：邮件证据策略"] --> E
  E --> B["EmailIntelBrief<br/>核心对象"]
  B --> R["ReviewChecklistPanel<br/>质量门禁面板"]
  R --> X["EPUB / Delivery<br/>人工批准后的动作"]
```

`EmailTopicMap` 和 `EmailEvidencePolicy` 都进入核心对象导航；`EmailPolicyPanel`
是 EvidencePolicy 的 GUI 编辑面板。`ReviewChecklistPanel` 是附属门禁面板。

## Layout

```text
+--------------------------------------------------------------------+
| Date / account / artifact status / local server mode               |
+----------------------+---------------------------------------------+
| Core object nav      | Main work area                              |
| - TopicMap           | - EmailTopicMap view                        |
| - EvidencePolicy     | - EmailPolicyPanel view                     |
| - EvidencePack       | - EvidencePack view                         |
| - IntelBrief         | - IntelBrief view                           |
+----------------------+----------------------+----------------------+
| Agent progress / commands                  | Checklist / Commands  |
+--------------------------------------------+----------------------+
```

第一版用单页 HTML/CSS/JS 实现。左侧导航四个核心对象；右侧只保留 ReviewChecklistPanel 和命令预览。

## EmailTopicMap View

输入文件：

```text
outputs/topic.md
```

展示内容：

- Tracked Topics：topic id、name、priority 和 summary focus。
- Priority：高优先级 topic 的数量和名称。
- Match Surface：关键词数量；匹配面包括 sender、subject、snippet、links 和 evidence excerpt。
- Default Behavior：未命中 topic 的邮件如何降级处理。
- Spec Editor：编辑 Markdown + fenced JSON；保存前必须校验 JSON。

用户操作：

- 编辑 Podsum 用户正在跟踪的话题、关键词和 summary focus。
- 保存 topic。保存失败时不覆盖原文件。
- 查看“修改 topic 不会自动重跑”的提示和后续 CLI 命令。

它控制 EvidencePack 的 `items[].topics` 和顶层 `topic_hits`，并控制 EmailIntelBrief
先按哪些主题展开。它不直接改写已有 scan JSON 或 summary Markdown。EmailIntelBrief
默认由 Podsum 本地 summary engine 生成；外部 summary engine 不接收完整 topic
JSON 作为独立输入。

## EmailEvidencePolicy View

输入文件：

```text
outputs/email_link_policy.md
```

GUI 面板名：

```text
EmailPolicyPanel
```

内部控制区：

- Type Rules：展示 email type、匹配条件和 summary focus。
- Link Strategy：展示哪些类型允许抓公开链接，以及每封/全局抓取上限。
- Snippet-only Types：展示只基于邮件摘要处理的类型，避免误以为有完整正文。
- Safety / Skip Rules：展示 tracking、unsubscribe、login、private URL 等跳过规则。
- Spec Editor：编辑 Markdown + fenced JSON；保存前必须校验 JSON。

用户操作：

- 编辑 email type 规则、`fetch_links`、skip patterns、limits。
- 保存 policy。保存失败时不覆盖原文件。
- 查看“修改 policy 不会自动重跑”的提示和后续 CLI 命令。

它控制未来 EvidencePack 如何生成，但不直接改写已有 scan JSON。

## EmailEvidencePack View

输入文件：

```text
EmailReports/email-scan-YYYY-MM-DD.json
```

展示内容：

- scan metadata：date、account、window、scan_limit、raw_count、possibly_truncated。
- Evidence Health：strong/usable/weak/failed/skipped。
- 分布：email_type、risks、attachments、links/evidence。
- 邮件列表：UID、From、Subject、Date、email_type、risk badges。
- 邮件详情：Base Evidence、Link Decision、Link Context、Link Evidence、Risks。
- topic 命中：来自 `EmailTopicMap` 的 topic badges、matched keywords 和 summary focus。
- evidence 至少包含 `type=email_snippet` 的邮件自身证据；`type=public_link`
  是链接补全后的增强证据。
- EvidencePack 0.1 只保存 MIME body 形态、附件形态、snippet 和链接上下文；
  不把完整 raw 邮件正文作为默认 artifact 保存。

用户操作：

- 标记 `ignore`。
- 标记 `important`。
- 写入 `type_override`。
- 标记 `needs_link_review`。
- 从 Brief 来源索引跳转并高亮对应 UID。

这些操作只保存到：

```text
EmailReports/email-review-YYYY-MM-DD.json
```

不覆盖原始 scan JSON。

## EmailIntelBrief View

输入文件：

```text
EmailReports/email-summary-YYYY-MM-DD.md
```

展示内容：

- EmailIntelBrief 对象版本、来源类型和当前 review 状态。
- Markdown 渲染视图。
- Brief section 摘要：section 数量、来源覆盖率、来源索引条数、coverage 是否完整。
- 原始 Markdown 文本区。
- 来源索引解析结果。
- 当前 brief 状态：`draft`、`needs_revision`、`approved`。

用户操作：

- 编辑 brief override。
- 保存 override 到 review sidecar。
- 标记 `needs_revision`。
- 标记 `approved`。
- 点击 UID 回到 EvidencePack 对应邮件。

第一版不覆盖原始 summary Markdown；人工编辑内容只写入 sidecar 的 `brief_override_markdown`。

EvidencePack 到 EmailIntelBrief 的 0.1 生成规则：

- dry-run 和默认本地 summary engine 都生成可审核 draft，而不是只写失败信息。
- draft 必须包含 key takeaway、跟踪话题、需要处理、值得知道、可以忽略、如果只记三件事和来源索引。
- draft 必须先按 `EmailTopicMap` 展开 topic 命中邮件；没有命中 topic 但仍需处理的邮件再进入后续分类。
- snippet-only、link skipped、truncated window 等证据边界必须进入 brief。
- Workbench 计算 source coverage；缺 UID 来源或缺 topic 展开会使 checklist 或审核状态不可批准。

## ReviewChecklistPanel

输入：

- normalized `EmailEvidencePack`
- summary Markdown 或 sidecar brief override
- review sidecar

检查项：

- has_key_takeaway
- has_topic_expansion
- has_source_index
- has_uid_trace
- has_truncated_warning
- uses_link_evidence_when_available
- marks_snippet_only_claims
- ready_to_send

只有 checklist passed 且 brief 状态为 `approved` 时，才显示 EPUB/Delivery 的人工下一步命令。

## APIs

- `GET /api/context`：date、root、artifact paths、exists、mtime、server mode。
- `GET /api/topics`：topic Markdown 和解析后的 JSON。
- `POST /api/topics`：保存 topic Markdown；无效 JSON 时拒绝。
- `GET /api/evidence-pack`：normalized scan JSON，合并 review 标注。
- `GET /api/intel-brief`：summary Markdown、brief override、source index。
- `GET /api/policy`：policy Markdown 和解析后的 JSON。
- `POST /api/policy`：保存 policy Markdown；无效 JSON 时拒绝。
- `GET /api/checklist`：质量门禁结果。
- `POST /api/review`：保存 review sidecar 局部更新。
- `GET /api/commands`：返回可复制 CLI 命令。

## Failure States

- scan 缺失：显示 missing 状态和生成 scan 的 CLI 命令。
- summary 缺失：显示 missing 状态和基于 scan 生成 summary 的 CLI 命令。
- topic 解析失败：显示错误，不影响 EvidencePack 浏览，保存时拒绝覆盖。
- policy 解析失败：显示错误，不影响 EvidencePack 浏览，保存时拒绝覆盖。
- checklist 失败：显示失败项，禁止显示发送批准状态。
- path 越界：返回 404 或 403，不读取任意文件。

## Safety Rules

- 默认只绑定 `127.0.0.1`。
- 不提供 IMAP API。
- 不提供 Hermes API。
- 不提供 send API。
- 不提供 launchd API。
- 不修改邮箱状态。
- 不覆盖 scan JSON。
- 不默认覆盖 summary Markdown。
- policy 保存使用临时文件加原子替换。
