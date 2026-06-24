# WeChat Reading EPUB compatibility debugging

Use this when a generated EPUB imports/opens in ordinary readers but fails in 微信读书, especially for long transcripts converted from Markdown.

## Durable lesson

Do not jump straight to file-size or EPUB-container explanations. A tiny slice from the same source can still fail if the trigger is in the opening title/metadata/content. Conversely, known-good EPUBs can contain tables, lists, headings, and even relative links and still open.

## Isolation workflow

1. **Compare against a known-good EPUB from the same generator**
   - Inspect ZIP entry order and names: `mimetype`, `META-INF/container.xml`, `EPUB/content.opf`, `EPUB/chap_001.xhtml`, `EPUB/style/main.css`, `EPUB/toc.ncx`, `EPUB/nav.xhtml`.
   - Compare OPF, XHTML tag counts, nav/toc, CSS, and link counts.
   - If structure matches and a known-good regenerated EPUB opens, deprioritize container-structure theories.

2. **Create very small slices before percentage splits**
   - Start with the first ~1K characters, not 25%.
   - If 1K fails, length/single-chapter DOM size is unlikely to be the primary cause.
   - Then make even smaller probes: one paragraph, one sentence, title-only + one sentence.

3. **Sanitize metadata/title separately from body**
   - Create a probe with a plain Chinese title and author.
   - Remove risky characters from title and metadata first: `&`, colon variants if suspicious, long mixed English course codes, unusual punctuation.
   - Do not include diagnostic blockquotes in the probe; they add extra variables.

4. **Sanitize body in layers**
   - Keep one version with original body but safe metadata.
   - Then test body replacements: remove/replace `&`, raw URLs, markdown/HTML links, control characters, long English tokens, timestamps, transcript artifacts, and unusual Unicode punctuation.
   - Track each probe name so the user can report which one opens.

5. **Only after tiny probes pass, test size thresholds**
   - Generate 1K, 5K, 10K, 25K, 50K, full single-chapter.
   - If a threshold appears, split into multiple valid XHTML chapters rather than hand-writing a new EPUB container.

## Probe naming convention

Use names that encode the variable being tested, for example:

- `前1K原始标题测试.epub`
- `前1K纯中文标题测试.epub`
- `一句话纯正文测试.epub`
- `知识资产模板替换一句话测试.epub`
- `前5K无英文符号测试.epub`

## Pitfalls

- Do not assume `href` alone is fatal: a known-good EPUB may contain relative links and still open.
- Do not assume tables/lists/headings are fatal if a known-good regenerated EPUB with those elements opens.
- Do not hand-write EPUB internals prematurely; continue using `create_epub_from_markdown(...)` until the failing variable is isolated.
- When sending `.epub` to Feishu, use the dedicated Feishu file-send skill/API and verify `ok: true`, `file_key`, and `message_id`.