# WeChat Reading EPUB compatibility

微信读书 / WeChat Reading is stricter than general EPUB readers. Common failures:

- **`&` in title/metadata** — fails even when correctly escaped as `&amp;`. Replace with `和` or remove.
- **`:` in title/metadata** — WeChat Reading fails on colon in EPUB metadata. Replace with `：` (fullwidth).
- **External links** — `[text](url)`, bare `https://...`, YouTube links, `.vtt` file paths.
- **Source metadata lines** — `Source:`, `Course materials:`, `Subtitle file:`.

## Built-in fix (preferred)

Use `--preprocess wechat` on the CLI or `preprocess='wechat'` in the Python API. This handles all three issues above, plus automatically sanitizes the `--title` parameter.

```bash
python3 generate_epub.py --input src.md --output out.epub \
  --title "MS&E 435" --preprocess wechat
# EPUB title will be "MS和E 435" — safe for 微信读书
```

## Manual fix (legacy, for reference only)

If you cannot use the built-in preprocessing, clean the Markdown source before EPUB generation:

1. Replace `&` with `和` throughout (including `&amp;`).
2. Convert `[text](url)` → `text`.
3. Remove bare URLs.
4. Remove lines starting with `- Source:`, `- Course materials:`, `- Subtitle file:`, `- Processing:`, `- Note:`.
5. Remove `.vtt` file references.
6. Sanitize the first H1 line.

Then call `create_epub_from_markdown()` as usual.

## Debugging

If a known-good EPUB from the same generator opens but the target EPUB does not, use small probe EPUBs to isolate the trigger. Start with a first-1K-character probe; if that fails, test safe metadata/title, then one-paragraph probes. See `references/wechat-reading-epub-debugging.md`.