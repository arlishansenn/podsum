---
name: make-markdown-readable
description: Clean Markdown into human-readable reading artifacts and export formats such as EPUB. Use for ASR/Whisper repetition cleanup, readable Markdown generation, EPUB export, WeChat Reading-safe output, or direct EPUB export from already-clean Markdown. Pass file paths to the scripts; do not preview input content.
---

# Make Markdown Readable

Turn messy Markdown (ASR / Whisper / course / meeting notes) into a readable
artifact plus an EPUB.

Core model:

```text
source Markdown -> clean readable Markdown -> export format (EPUB)
```

## Before running

Confirm the input file with metadata only:

    stat <path>
    wc -c <path>

Do not preview or read the input content. The wrapper reads the file and
derives the title internally; you never need to open it.

## Default: clean + EPUB

    /usr/bin/python3 ~/.hermes/skills/media/make-markdown-readable/scripts/clean_markdown.py \
      /absolute/path/input.md \
      --output-dir /absolute/path/out

Outputs: cleaned Markdown, EPUB, and a full `.report.json` on disk.
The script prints only a compact summary; read the summary, not the report body.

## Export EPUB only

Use when the input is already readable and no cleanup is wanted:

    /usr/bin/python3 ~/.hermes/skills/media/make-markdown-readable/scripts/export_epub.py \
      --input /absolute/path/clean.md \
      --output /absolute/path/book.epub \
      --title "Book Title" \
      --author "Author"

Optional profiles before export: `--preprocess wechat|clean`,
`--strip-timestamps`, `--strip-fillers`.

## Delivery

Use the `send-file` skill only when the user asks to deliver the generated file.
