# Splitting an Existing EPUB into Smaller EPUBs

Use this when the user asks to split a large EPUB into several smaller EPUB attachments, especially when the EPUB is a course transcript or long book with clear section/week/chapter headings.

## Key lesson
If the user says “把这 3 个文件发过来” after discussing a single large EPUB, do **not** assume they mean three existing files in a directory. The likely intent is: split the large EPUB into 3 smaller EPUB files and send those. Confirm only if no source EPUB is clear.

## Robust workflow
1. Identify the source EPUB the user most recently uploaded or referenced.
2. Inspect the EPUB ZIP structure:
   - `mimetype`
   - `META-INF/container.xml`
   - OPF file, usually `EPUB/content.opf` or `OEBPS/content.opf`
   - XHTML/HTML content files
   - CSS/nav/toc files
3. Extract the readable XHTML body and locate natural split markers:
   - course weeks: `第 1 周`, `Week 1`, etc.
   - chapters: `<h1>` / `<h2>` with stable ids
   - sections from the OPF spine when already split into files
4. Build N new EPUBs, preserving EPUB rules:
   - `mimetype` must be the first ZIP entry and stored uncompressed.
   - include `META-INF/container.xml` pointing to the new OPF.
   - include a valid OPF manifest/spine.
   - include `nav.xhtml` and `toc.ncx` with only the selected sections.
   - include reused CSS when available.
5. Validate before delivery:
   - ZIP opens.
   - first entry is `mimetype` and equals `application/epub+zip`.
   - each output has nonzero size.
   - each output contains the expected headings/chapters and no missing/duplicate ranges.
6. If delivering to Feishu, use the `hermes-feishu-file-send` skill and report returned `message_id`s.

## Naming convention
Use names that make the split order and content obvious:

`<base_title>_part01.epub`, `<base_title>_part02.epub`, `<base_title>_part03.epub`

For course transcripts, include the range in the final response:
- part01: 第 1–2 周
- part02: 第 3–4 周
- part03: 第 5–6 周

## Pitfalls
- Do not just send pre-existing `.zip` or unrelated files when the user requested smaller EPUBs.
- Do not edit the original EPUB in place; write outputs to a separate directory.
- Do not treat a text path as successful Feishu delivery; require upload JSON with `ok: true`, `file_key`, and `message_id`.
- EPUB compatibility can matter more than file size. If the target reader is WeChat Reading / 微信读书, mimic the structure of a known-openable EPUB rather than using an overly minimal hand-rolled package:
  - `mimetype` first and uncompressed.
  - OPF manifest order: `chap_001.xhtml` → `style/main.css` with id `static_0` → `toc.ncx` → `nav.xhtml`.
  - OPF spine order: `chapter_0` first, then `nav`; avoid putting `nav` before the chapter.
  - Consider preserving `dc:language` as `en` if the source/known-good EPUB uses `en`, even when content is Chinese; some importers are picky.
  - Use conventional filenames (`EPUB/content.opf`, `EPUB/chap_001.xhtml`, `EPUB/toc.ncx`, `EPUB/nav.xhtml`) and compare against a known-good EPUB before delivery.
- Avoid capturing environment-specific paths in the skill body. Session-specific paths belong only in the current task output, not persistent instructions.
