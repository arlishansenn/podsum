# Transcript Cleaner

这是 Podsum 仓库中的独立子项目。它不接入 podcast 定时任务，不修改 Podsum state，也不会自动发送 Discord 或飞书。

## 输入输出

输入一个 UTF-8 Markdown 文字稿，输出：

```text
<name>_cleaned.md
<name>_cleaned.epub
<name>_cleaned.report.json
```

运行：

```sh
cd /Users/admin/Documents/Codex/podsum/outputs

/usr/bin/python3 -m transcript_cleaner \
  /path/to/source.md \
  --output-dir /path/to/output \
  --title "完整课程文字记录" \
  --author "黄德华"
```

共同前缀最小长度默认为 5 个中文字符，可调整：

```sh
/usr/bin/python3 -m transcript_cleaner input.md \
  --output-dir output \
  --min-prefix-chars 5
```

## 清理顺序

```text
1. 口头禅和 ASR 噪音
2. 句内短间隔完全重说
3. 相邻分句完全重复或后句扩展
4. 共同前缀局部重复
5. 嵌入式句块重复
6. 相邻句块重复
7. 生成 Markdown、EPUB 和 JSON 报告
```

### 共同前缀并列合并

模式：

```text
P + A，P + B
→ P + A，B
```

例如：

```text
这个系统可以降低成本，这个系统可以提高效率。
→ 这个系统可以降低成本，提高效率。
```

该规则只删除第二次共同前缀，`A` 和 `B` 都会保留。共同前缀至少包含 5 个中文字符。

5 个中文字符只是候选门槛。自动合并还要求共同前缀以明确的谓语/关系结构结束，例如“可以”“正在”“带来了大量”“进而带来更高的”。不满足安全条件的内容只写入报告，类型为 `shared_prefix_candidate`，正文保持不变。英文单词不会从中间截断。

### 高置信前缀重启

模式：

```text
P + 失败尾部，P + 完整尾部
→ P + 完整尾部
```

只有第一次尾部出现明确截断信号时才自动删除，例如：

- `……`、`…`、破折号或 `--`
- 连续 `xxxx`
- `嗯`、`呃`、`那个` 等犹豫结束

没有高置信截断信号时，程序执行无损并列合并，只折叠共同前缀，不删除两个不同尾部。

### 其他重复

```text
S + 短间隔 + S
→ S

前缀 + A。B。C。A。B。C。+ 后缀
→ 前缀 + A。B。C。+ 后缀

A。B。C。A。B。C。A。B。C。
→ A。B。C。
```

## JSON 报告

报告包含：

- 输入、Markdown 和 EPUB 路径
- 字符变化和各类规则命中次数
- 每次修改的类型、位置、置信度和截断后的前后文本
- 三个产物的 SHA-256

修改类型：

```text
fillers_and_noise
short_gap_repeat
shared_prefix_coordination
prefix_restart
adjacent_clause_repeat
prefix_extension
shared_prefix_candidate
embedded_sentence_block
adjacent_sentence_block
```

## 测试

从仓库根目录运行：

```sh
/usr/bin/python3 -m unittest tests.test_transcript_cleaner -v
```
