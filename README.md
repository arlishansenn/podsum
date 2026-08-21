# Podsum

Podsum 是一个有状态的 macOS podcast pipeline：

1. 从每个配置的 RSS feed 下载最新一集；
2. 用 `mlx-whisper` 在本地转写音频；
3. 通过 Hermes 生成深度解读；
4. 把各集解读合并成一份 Markdown report；
5. 把 report 转成 EPUB，发送到配置的 target；
6. 记录每个阶段的状态，失败或中断的运行可以续跑。

还有一个可选的邮件摘要功能，从旧的 OpenClaw cron workflow 迁移而来：扫描最近的
IMAP/Gmail 邮件，生成结构化的 scan JSON 文件，用 Hermes 生成摘要，转成 EPUB，走同
一个 Podsum target 投递。离线测试用脱敏的 `.eml` fixture，不碰真实 Gmail 内容。

生产 runner、launchd 模板、feed 配置和运维文档在
[`outputs/`](outputs/README.md)。

独立的转写稿清洗项目在
[`outputs/transcript_cleaner/`](outputs/transcript_cleaner/README.md)。

## 安装

```bash
scripts/install.sh
```

把仓库同步到部署目录，建立 venv 与 Node 依赖，装好并加载定时任务。部署位置由
`PODSUM_HOME` 决定，默认 `~/Library/Application Support/Podsum`。

脚本只负责自己装上去的东西：

- 同步内容是**版本库中 `outputs/` 下被追踪的文件**，不维护手工排除列表。
- 用户内容（`feeds.json`、`topic.md`、`email_link_policy.md`、`interpretation_rules.md`）**存在即跳过**，缺席时从同名 `.example` 生成。
- 只删除自己装过、且已从追踪集消失的文件；认不出来的一律列出而不删。
- 状态文件与 `.env` 永不触碰。
- 定时任务正在执行时**拒绝重载**，不打断跑到一半的转写。

`ffmpeg`、`node`、`hermes` 等外部依赖**只检测不代装**，缺失时打印可直接复制的命令。
理由见 [`docs/adr/0001-install-failure-radius.md`](docs/adr/0001-install-failure-radius.md)。

跳过某一步：`--skip-venv`、`--skip-node`、`--skip-launchd`。

## 手动触发

定时任务每天 08:00 跑。不想等就用下面三档，按影响面从小到大。

先设解释器：

```bash
export PODSUM_PYTHON="$HOME/Library/Application Support/Podsum/.venv/bin/python"
export PODSUM_OUTPUTS="$HOME/Library/Application Support/Podsum/outputs"
```

**只看会发什么，不发信**。仍然会读真实邮箱，所以 `--allow-imap-read` 省不掉：

```bash
"$PODSUM_PYTHON" "$PODSUM_OUTPUTS/podsum.py" \
  email-summary --allow-imap-read --recent-days 1 --limit 20 --dry-run
```

打印 `would email HTML summary to <收件人>: <路径>`，Markdown 落在
`~/Podcasts/AutoDownloads/EmailReports/`，可以先读那份再决定。

**真发一封**，去掉 `--dry-run`：

```bash
"$PODSUM_PYTHON" "$PODSUM_OUTPUTS/podsum.py" \
  email-summary --allow-imap-read --recent-days 1 --limit 20
```

**完整跑一次定时任务**，与 08:00 逐字相同（含下载、转写、投递、清理）。会真的跑转写，
可能几十分钟：

```bash
launchctl kickstart -k gui/$(id -u)/com.local.podsum
tail -f ~/Library/Logs/podsum.log
```

`--allow-imap-read` 是刻意留在命令行、没有移进 `.env` 的确认：它的价值就在于每个要读
真实邮箱的地方都必须显式写一次。

`--email-delivery email` 时的 SMTP 配置全部有推断兜底——host 从 IMAP host 推，收发地址
和密码回落到 IMAP 那一套。发信被拒多半是这里，在 `.env` 里单独设
`PODSUM_EMAIL_SMTP_PASS` 即可。

## 解读规则

`outputs/interpretation_rules.md` 的内容注入
`outputs/hermes_interpretation_prompt.md` 的 `{rules}` 占位符，位置在内置要求之
后，冲突时以规则文件为准。

```text
你是 Podsum 的 podcast 深度解读器……
要求：
- ……（内置要求）

{rules}            ← outputs/interpretation_rules.md

MEMORY.md: ……
Podcast: ……
文字稿节选: ……
```

规则用中文自然语言，一行一条：

```text
- 这一集偏技术，多留代码细节和具体数字。
- 长度压到 600 字以内。
- 嘉宾的个人经历部分可以略过。
```

- HTML 注释会被剥离。文件初始内容全是注释，即零条规则。
- 剥离后超过 4000 字截断。
- 文件不存在时按零条规则处理。
- `--interpretation-rules <path>` 换用别的规则文件。

## Hermes Skills

Podsum 自己持有可读 Markdown 清洗/导出与投递流程所用的 Hermes skills 副本：

```text
skills/media/make-markdown-readable/          # clean Markdown + export EPUB
skills/social-media/hermes-feishu-file-send/  # frontmatter name: send-file
```

项目内的副本是 source of truth。用下面的命令把两个 skill 安装或刷新到当前 Hermes
profile：

```bash
./scripts/install-hermes-skills.sh --restart
```

安装脚本保留原有的 Hermes category 路径，所以 skill 内部的引用依然有效。

## 安装位置

开发副本和生产副本是两份独立的目录，之间没有任何自动同步。

```text
~/project/podsum                          # git clone，开发在这里
~/Library/Application Support/Podsum/     # 物化的生产副本，launchd 跑的是这份
```

生产副本里的关键路径（注意路径含空格，引用时要加引号）：

```text
~/Library/Application Support/Podsum/outputs      # 生产代码
~/Library/Application Support/Podsum/.venv        # 运行时 interpreter
~/Library/Application Support/Podsum/state.json   # 断点续跑状态
~/Library/Logs/podsum.log, podsum.err.log         # 运行日志
```

LaunchAgent `com.local.podsum` 直接指向生产副本，不经过 clone：

```text
/Users/admin/Library/Application Support/Podsum/.venv/bin/python \
  "/Users/admin/Library/Application Support/Podsum/outputs/podsum.py" run-once --cleanup
```

生产副本没有 `.git`。在 clone 里改完代码要手动同步过去才会生效。部署副本的运维细节
见 [`outputs/README.md`](outputs/README.md)。

## Python Runtime

Podsum 应当跑在它自己的 application virtual environment 里，而不是系统 Python。手
动执行命令前先把 `PODSUM_PYTHON` 指向该 venv 的 interpreter：

```bash
export PODSUM_PYTHON="$HOME/Library/Application Support/Podsum/.venv/bin/python"
```

Linux 部署可以把同一个变量指向自己的 service venv，例如
`/opt/podsum/.venv/bin/python`。

## 测试

```bash
"$PODSUM_PYTHON" -m unittest discover -s tests -v
```
