---
name: make-markdown-readable
description: Clean Markdown into human-readable reading artifacts and export formats such as EPUB. Use for ASR/Whisper repetition cleanup, readable Markdown generation, EPUB export, WeChat Reading-safe output, or direct EPUB export from already-clean Markdown. Never load long source bodies into agent context; pass file paths to the scripts.
---

# Make Markdown Readable

This skill turns Markdown into a human-readable reading artifact.

Core model:

```text
source Markdown
  -> clean readable Markdown
  -> export format (currently EPUB)
```

## Context boundary

- Do not read long source Markdown bodies into agent context.
- Pass file paths directly to deterministic scripts.
- Inspect only metadata before execution: path, suffix, size.
- After execution, read reports or JSON summaries with bounded output.
- Use `send-file` only when the user asks to deliver the generated file.

## Default path: clean + EPUB

Use this for ASR, Whisper, podcast, course, meeting, or other messy Markdown
that should become readable:

```bash
/usr/bin/python3 ~/.hermes/skills/media/make-markdown-readable/scripts/clean_markdown.py \
  /absolute/path/input.md \
  --output-dir /absolute/path/out
```

Outputs:

- readable Markdown
- EPUB
- JSON audit report

## Clean-only semantics

If the user only wants readable Markdown, run the default path and use the
cleaned Markdown output from the report. The EPUB is an export artifact and can
be ignored.

## Export-only EPUB

Use this when the input Markdown is already readable and no cleanup is wanted:

```bash
/usr/bin/python3 ~/.hermes/skills/media/make-markdown-readable/scripts/export_epub.py \
  --input /absolute/path/clean.md \
  --output /absolute/path/book.epub \
  --title "Book Title" \
  --author "Author"
```

Optional cleaning profiles before export:

- `--preprocess wechat`: WeChat Reading-safe text/title/filename cleanup.
- `--preprocess clean`: general link/source cleanup.
- `--strip-timestamps`: remove `[HH:MM:SS]` markers.
- `--strip-fillers`: remove common oral fillers.

## Compatibility entrypoints

These old script names remain as shims during migration:

- `scripts/run_transcript_cleaner.py` -> `scripts/clean_markdown.py`
- `scripts/generate_epub.py` -> `scripts/export_epub.py`

## Ownership

- Cleanup is implemented in `outputs/transcript_cleaner`.
- EPUB generation is implemented in `outputs/podsum_core/epub_converter`.
- This skill exposes those implementations; do not recreate cleanup or EPUB logic here.
