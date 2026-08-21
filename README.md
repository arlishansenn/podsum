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

每集解读用的完整 prompt 是
[`outputs/hermes_interpretation_prompt.md`](outputs/hermes_interpretation_prompt.md)。
里面写死了内置要求（输出结构、长度区间、忌用的句式等），由项目维护，平时不用动。
它中间留了一个 `{rules}` 占位符：

```text
你是 Podsum 的 podcast 深度解读器……
要求：
- ……（内置要求，到这里结束）

{rules}            ← outputs/interpretation_rules.md 的内容填进这里

MEMORY.md: ……
Podcast: ……
文字稿节选: ……
```

想调解读口味，只改
[`outputs/interpretation_rules.md`](outputs/interpretation_rules.md)，不碰 prompt
模板。中文自然语言，一行一条：

```text
- 这一集偏技术，多留代码细节和具体数字。
- 长度压到 600 字以内。
- 嘉宾的个人经历部分可以略过。
```

因为规则填在内置要求**之后**，同一件事两边说法不同时以你写的为准。上面第二条就会
盖掉内置的「800-1500 中文字」。

这个文件仓库里已经有了，初始内容是一段 HTML 注释写的使用说明。注释会被剥离，所以
开箱状态等于「一条规则都没有」，解读行为和这个功能上线前逐字相同。你在注释外面写
字，规则才开始生效；把字删光就回到初始状态。整个文件删掉也不报错，同样按「没有规
则」处理。

剥离注释后的内容超过 4000 字会被截断。临时换一份规则用
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
