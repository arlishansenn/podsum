# Podsum

Podsum is a stateful macOS podcast pipeline that:

1. downloads the latest episode from each configured RSS feed;
2. transcribes audio locally with `mlx-whisper`;
3. generates deep interpretations through Hermes;
4. merges the interpretations into one Markdown report;
5. converts the report to EPUB and sends it to the configured target;
6. records each stage so failed or interrupted runs can resume.

It also includes an opt-in email summary feature migrated from the old
OpenClaw cron workflow: scan recent IMAP/Gmail messages, build a structured
scan JSON file, generate a Hermes summary, convert it to EPUB, and deliver it
through the same Podsum target. Offline acceptance tests use sanitized `.eml`
fixtures instead of real Gmail content.

The production runner, launchd templates, feed configuration and operational
documentation are in [`outputs/`](outputs/README.md).

The independent transcript cleaning project is in
[`outputs/transcript_cleaner/`](outputs/transcript_cleaner/README.md).

## Hermes Skills

Podsum owns the project-specific copies of the Hermes skills used by the
readable Markdown cleanup/export and delivery workflow:

```text
skills/media/make-markdown-readable/          # clean Markdown + export EPUB
skills/social-media/hermes-feishu-file-send/  # frontmatter name: send-file
```

The project copies are the source of truth. Install or refresh both skills in the
active Hermes profile with:

```bash
./scripts/install-hermes-skills.sh --restart
```

The installer preserves the existing Hermes category paths so references inside
the skills remain valid.

## Python Runtime

Podsum should run from its application virtual environment rather than the OS Python.
Set `PODSUM_PYTHON` to the virtual-environment interpreter before running manual commands:

```bash
export PODSUM_PYTHON="$HOME/Library/Application Support/Podsum/.venv/bin/python"
```

Linux deployments can point the same variable at their service venv, for example `/opt/podsum/.venv/bin/python`.

## Test

```bash
"$PODSUM_PYTHON" -m unittest discover -s tests -v
```
