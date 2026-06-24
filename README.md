# Podsum

Podsum is a stateful macOS podcast pipeline that:

1. downloads the latest episode from each configured RSS feed;
2. transcribes audio locally with `mlx-whisper`;
3. generates deep interpretations through Hermes;
4. merges the interpretations into one Markdown report;
5. converts the report to EPUB and sends it to the configured target;
6. records each stage so failed or interrupted runs can resume.

The production runner, launchd templates, feed configuration and operational
documentation are in [`outputs/`](outputs/README.md).

The independent transcript cleaning project is in
[`outputs/transcript_cleaner/`](outputs/transcript_cleaner/README.md).

## Test

```bash
/usr/bin/python3 -m unittest discover -s tests -v
```
