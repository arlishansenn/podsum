# Podsum 邮件摘要能力重构计划

## 摘要

把 macmini OpenClaw 原有 Gmail/IMAP 邮件情报任务，迁移为 Podsum 的正式
Email Summary 能力。

目标是让 Podsum 支持第二类输入源：Email。Podcast 和 Email 分别采集、
分别建模，但复用 Markdown、EPUB、Hermes 摘要和投递能力。生产环境仍保持
单一 `com.local.podsum` runner，邮件摘要先手动验证，再决定是否接入定时
任务。

验收测试采用更真实的策略：允许从真实 Gmail 抽取少量样本，但只提交脱敏后
的 `.eml` fixture，不提交真实邮件内容或账号信息。

## 关键改动

- 新增 Email Summary 流程：
  - IMAP/Gmail 扫描最近 N 天邮件。
  - 生成 `EmailReports/email-scan-YYYY-MM-DD.json`。
  - Hermes 生成 `email-summary-YYYY-MM-DD.md`。
  - 转 EPUB 并通过现有 Podsum delivery 发送。
- 新增 CLI：
  - `podsum.py email-summary`
  - `podsum.py run-once --email-summary`
- 新增离线验收输入：
  - `--scan-file`：直接使用结构化 scan JSON。
  - `--eml-dir`：读取脱敏 `.eml` fixture 生成 scan，再走摘要流程。
  - 默认优先级：`--scan-file` > `--eml-dir` > 真实 IMAP。
  - 真实 IMAP/Gmail 读取必须额外带 `--allow-imap-read` 或
    `--email-allow-imap-read`，否则命令只提醒，不访问邮箱。
- 凭据来源：
  - 优先环境变量。
  - 兼容 `~/.openclaw/.env`。
  - 不提交账号、密码、app password 或旧脚本敏感信息。

## 真实 Fixture 策略

- 允许做一次真实 Gmail 抽样，但 raw 邮件只能作为本地临时数据。
- 抽样默认值：
  - 最近 7 天。
  - 最多 20 封。
  - INBOX。
- 生成 fixture 时必须脱敏：
  - 替换 From/To/Cc/Bcc 邮箱和显示名。
  - 替换 Message-ID、thread id、邮件地址、手机号、真实姓名。
  - Subject 改成语义占位标题，例如 `Fixture Newsletter 001`。
  - 正文只保留 MIME 结构、纯文本/HTML 类型、链接形态和 snippet 长度特征；
    内容改写为合成文本。
  - 附件只保留 `has_attachments` 形态，不保存附件内容。
- raw `.eml` 生成脱敏 fixture 后立即删除或不落盘。
- 脱敏 `.eml` fixture 提交前必须人工检查一次，确认没有真实邮箱、姓名、
  公司敏感内容。

## 提交拆分

1. 新增中文重构计划文档，锁定产品边界和测试策略。
2. 更新文档，定义 Email Summary、Email Scan、EmailReports 输出目录。
3. 新增邮件摘要 Hermes prompt，要求 UID/from/subject/date 溯源。
4. 新增 fixture-only email 模块，先支持 `--scan-file` 和 dry-run。
5. 增加 `--eml-dir`，从脱敏 `.eml` fixture 生成 scan JSON。
6. 增加真实 Gmail fixture capture/redaction 工具，仅用于本地生成脱敏测试资产。
7. 接入 Hermes 摘要，失败时生成 fallback report 并保留来源索引。
8. 接入 EPUB 生成和 delivery，投递文案必须是 email-specific。
9. 增加真实 IMAP scan，读取最近 N 天邮件并生成 scan JSON。
10. 暴露 `podsum.py email-summary` 子命令。
11. 增加 `run-once --email-summary`，默认不启用。
12. 更新 README 和 macmini 运维说明。
13. 跑完整测试，确认 podcast 现有流程不受影响。
14. 经确认后再同步到 macmini 做 fixture、dry-run、真实 IMAP 手动验证。
15. 手动验证通过后，另行决定是否修改 `com.local.podsum.plist` 加入
    `--email-summary`。

## 测试计划

- `email-summary --scan-file ... --dry-run --no-send` 能生成 scan 和 summary。
- `email-summary --eml-dir ... --dry-run --no-send` 能从脱敏 `.eml` 生成 scan
  和 summary。
- 脱敏 `.eml` fixtures 覆盖：
  - newsletter HTML 邮件。
  - Google Alerts 类中文主题邮件。
  - 普通个人邮件。
  - 带附件邮件。
  - multipart text/plain + text/html 邮件。
  - 编码 subject/from header。
  - 空结果或触达 limit 的 scan JSON。
- dry-run 不访问真实 IMAP、Hermes 或投递目标。
- fake Hermes 验证摘要和发送路径。
- 缺少 IMAP 凭据时给出明确错误，不发送半成品。
- 不加 `--email-summary` 时，`run-once` 行为与现在一致。
- 完整运行 `/usr/bin/python3 -m unittest discover -s tests -v`。

## 明确不做

- 不迁移 OpenClaw cron 系统本身。
- 不新增 Gmail 专用 LaunchAgent。
- 不迁移 Product Hunt、Weather Report、weekly/monthly intel。
- 不做完整邮件客户端。
- 默认不保存完整真实邮件正文。
- 不修改邮箱状态，不标记已读，不归档，不回复邮件。
- 不提交 raw Gmail 邮件、真实账号、真实邮箱地址或凭据。
- 不在未批准前部署到 macmini 或修改 launchd。

## 默认假设

- v1 只做“邮件扫描 + 邮件摘要 + EPUB 投递”。
- 验收 fixture 允许来自真实 Gmail 抽样，但必须脱敏后才能进入仓库。
- 真实 Gmail 内容不作为测试断言；测试断言只验证解析、脱敏、scan 结构、
  摘要输入和 artifact 输出。
- 生产环境继续保持单一 `com.local.podsum` runner。
- 先完成离线可测试 MVP，再做 macmini 手动验证，最后才考虑定时启用。
