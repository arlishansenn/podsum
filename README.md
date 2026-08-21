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

## 解读规则

把你自己的解读偏好写进
[`outputs/interpretation_rules.md`](outputs/interpretation_rules.md)，中文自然语
言，一行一条：

```text
- 这一集偏技术，多留代码细节和具体数字。
- 长度压到 600 字以内。
- 嘉宾的个人经历部分可以略过。
```

文件内容会注入 `outputs/hermes_interpretation_prompt.md` 的 `{rules}` 占位符，位
置在内置要求之后，所以和内置要求冲突时以你写的为准。HTML 注释会被剥离，剩下的内
容截断到 4000 字。

文件为空或只剩注释时，解读行为与没有这个文件时完全一致。换一个规则文件用
`--interpretation-rules <path>`。

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
