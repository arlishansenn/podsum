# Transcript-derived EPUB cleanup

When an EPUB was generated from lecture/audio transcripts and the user reports repeated sentences, ASR loops, or oral filler.

## Preferred: use transcript-cleaner skill

The standalone `transcript-cleaner` skill (`media/transcript-cleaner`) handles all ASR cleanup:
- Short-gap repeats, prefix extensions, adjacent clause repeats
- Embedded and adjacent repeated sentence blocks
- Shared-prefix coordination merges
- Filler removal

Run it first, then convert the cleaned Markdown to EPUB:

Do not read or sample the source transcript with `read_file`. Pass its path
directly to the cleaner. Read only the generated JSON report. Transcript content
belongs in the deterministic script process, not in the agent conversation.

```bash
# 1. Clean transcript
python3 ~/.hermes/skills/media/transcript-cleaner/scripts/run_transcript_cleaner.py \
  source.md --output-dir ./out --title "Title" --author "Author"

# 2. Convert to EPUB (with WeChat-safe preprocessing if needed)
python3 ~/.hermes/skills/markdown-to-epub-converter/scripts/generate_epub.py \
  --input ./out/cleaned.md --output ./out/cleaned.epub \
  --title "Title" --preprocess wechat
```

## Failure path

If `transcript-cleaner` is unavailable or fails:

1. Preserve the source unchanged.
2. Return the exact wrapper error.
3. Do not load the transcript into context.
4. Do not improvise a manual regex cleanup.
5. Stop before EPUB generation unless the user explicitly requests conversion
   of the uncleaned source.

## Reporting format

Keep it concise: source chain, output paths, before/after counts, verification summary.
