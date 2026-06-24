---
name: transcript-cleaner
description: Cleans non-semantic repetition from Chinese ASR or Whisper Markdown transcripts by running a deterministic wrapper that generates cleaned Markdown, EPUB, and a JSON audit report. Use when the user asks to remove speech restarts, stutters, ASR loops, repeated sentence blocks, or create a readable full-transcript EPUB. For long transcript files, pass the file path directly to the wrapper and never load the source body into agent context.
---

# Transcript Cleaner

Use the independent Transcript Cleaner project. It does not modify Podsum state,
join the scheduled podcast pipeline, or send files automatically.

## Context boundary

- Load this skill before inspecting or processing the source transcript.
- Do not call `read_file` on the source Markdown, cleaned Markdown, or EPUB.
- Do not copy transcript content into prompts, tool arguments, or chat history.
- Do not sample the body to decide whether cleaning is needed. The user's request
  and the file path are sufficient.
- Inspect only filesystem metadata before execution: existence, suffix, and size.
- After execution, read only the generated `.report.json`, with a bounded line or
  byte limit when the report is large.
- If manual semantic review is explicitly requested, start a separate review task
  using bounded excerpts after the deterministic cleaning workflow completes.

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
2. Confirm the input exists and has a `.md` suffix without reading its body.
3. Never overwrite or delete the source transcript.
4. Run the wrapper once. Do not reimplement cleaning with ad hoc replacements.
5. Read only the generated `.report.json`; do not read either Markdown file.
6. Confirm all three reported output paths exist.
7. Check `residual_patterns`; report every remaining short-gap candidate.
8. Separate edits with `auto_applied: true` from `report_only: true`.
9. Report character counts, rule hit counts, candidates, and output paths.

Do not run source inspection in parallel with `skill_view`. Skill instructions
must be loaded before selecting task tools.

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

The cleaner removes high-confidence non-semantic repetition and preserves
deliberate emphasis, contrast, progression, teaching restatement, and natural
topic-word recurrence.

Automatic operations include:

1. Remove fillers, ASR noise, and word-level stutters.
2. Remove exact short-gap restarts where `A` has at least 3 Chinese characters
   and `gap` has fewer than 15 characters. Punctuation counts toward length.
3. Resolve exact adjacent clauses, prefix extensions, and safe shared prefixes.
4. Remove embedded and adjacent exact sentence-block loops.

Short-gap deletion also requires a pause/oral filler gap and no new number,
entity, cause, negation, contrast, or emphasis signal.

Safe shared-prefix coordination preserves both distinct endings:

```text
这个系统可以降低成本，这个系统可以提高效率。
-> 这个系统可以降低成本，提高效率。
```

Unsafe shared prefixes and near-duplicate rewrites stay unchanged and appear as
`shared_prefix_candidate` or `near_duplicate_candidate`. Remaining ambiguous
`A + gap + A` patterns appear in `residual_patterns`.

## Safety

- Keep the source file as the authoritative raw transcript.
- Treat transcript bodies as file artifacts for scripts, not conversation context.
- Do not lower `--min-prefix-chars` below `5` unless the user explicitly asks.
- Never claim a `report_only` candidate was removed.
- Treat non-empty `residual_patterns` as review work, not automatic failure.
- Do not send, publish, or replace another document unless explicitly asked.
- If the wrapper fails, return its exact stderr and do not claim outputs exist.
