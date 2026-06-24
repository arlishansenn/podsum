---
name: transcript-cleaner
description: Cleans ASR or Whisper Markdown transcripts, removes fillers and several repetition patterns, and generates cleaned Markdown, EPUB, and a JSON audit report. Use when the user asks to clean a transcript, remove repeated speech or ASR loops, merge repeated Chinese prefixes, or create a readable full-transcript EPUB.
requires:
  bins: python3
---

# Transcript Cleaner

Use the independent Transcript Cleaner project. It does not modify Podsum state,
join the scheduled podcast pipeline, or send files automatically.

## Run

Always use the bundled wrapper so the Python module is loaded from the deployed
Podsum project:

```bash
/usr/bin/python3 ~/.hermes/skills/media/transcript-cleaner/scripts/run_transcript_cleaner.py \
  "/absolute/path/source.md" \
  --output-dir "/absolute/path/output" \
  --title "Document title" \
  --author "Author"
```

Optional arguments:

- `--output-stem NAME`: set the three output filenames.
- `--min-prefix-chars N`: shared Chinese prefix threshold; default is `5`.
- `--project-root PATH`: override the deployed project root when testing.

## Workflow

1. Resolve the input and output paths to absolute paths.
2. Confirm the input is an existing UTF-8 Markdown file.
3. Never overwrite or delete the source transcript.
4. Run the wrapper once. Do not reimplement cleaning with ad hoc replacements.
5. Read the generated `.report.json`.
6. Confirm all three reported output paths exist.
7. Report character counts, rule hit counts, and output paths to the user.
8. Mention any `shared_prefix_candidate` entries because they were deliberately
   reported without changing the text.

## Outputs

For input `course.md`, the default outputs are:

```text
course_cleaned.md
course_cleaned.epub
course_cleaned.report.json
```

The report records source and output paths, character changes, rule hit counts,
individual edits, and SHA-256 hashes.

## Cleaning Model

The cleaner applies these operations in order:

1. Remove fillers and ASR noise.
2. Remove exact short-gap restarts.
3. Resolve adjacent exact clauses and prefix extensions.
4. Merge safe shared-prefix coordination.
5. Remove high-confidence prefix restarts.
6. Remove embedded repeated sentence blocks.
7. Remove adjacent repeated sentence blocks.

Shared-prefix coordination preserves both distinct endings:

```text
这个系统可以降低成本，这个系统可以提高效率。
-> 这个系统可以降低成本，提高效率。
```

The minimum prefix length is only a candidate threshold. Unsafe candidates stay
unchanged and appear as `shared_prefix_candidate` in the audit report.

## Safety

- Keep the source file as the authoritative raw transcript.
- Do not lower `--min-prefix-chars` below `5` unless the user explicitly asks.
- Do not send, publish, or replace another document unless explicitly asked.
- If the wrapper fails, return its exact stderr and do not claim outputs exist.
