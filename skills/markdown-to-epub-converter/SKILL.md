---
name: markdown-to-epub-converter
description: Convert Markdown documents and chat summaries into formatted EPUB files. Use for direct Markdown-to-EPUB conversion, EPUB regeneration, compatibility fixes, and EPUB structure validation. When the source is a long ASR or Whisper transcript requiring repetition cleanup, delegate cleanup to the transcript-cleaner skill and never load the transcript body into agent context.
---

# Markdown to EPUB Converter Skill

This skill transforms markdown documents into professional EPUB ebook files. Perfect for converting research documents, blog posts, articles, or chat conversation summaries into portable, device-agnostic ebook formats.

## Routing and context boundary

Choose exactly one path before using file tools:

1. **Direct conversion**: convert the supplied Markdown path with
   `scripts/generate_epub.py`.
2. **ASR/transcript cleanup**: load `transcript-cleaner`, run its wrapper, then
   convert or use its generated EPUB. Do not inspect the source body here.
3. **Existing EPUB repair/split**: use the relevant reference workflow.

For path-based conversion:

- Do not call `read_file` on long Markdown input merely to inspect, preview, find
  repetition, derive a title, or verify formatting.
- Pass the path directly to the deterministic script.
- Use filesystem metadata or bounded structural commands for validation.
- Read document content only when the user explicitly requests editorial or
  semantic review, and keep that review separate from conversion.
- Load this skill and any delegated skill before starting source-file operations.

## Overview

The skill accepts markdown content in multiple formats and generates a properly formatted EPUB3 file that works across all major ebook readers including:
- Apple Books
- Amazon Kindle (via Kindle for Mac/Windows/iOS/Android)
- Google Play Books
- Kobo and other EPUB readers
- Any standard EPUB reader

## Input Formats

### Existing EPUB revision / source recovery

When the user asks to revise an already-delivered EPUB but the Markdown source is missing:

1. Search for the original `.md` by title first (`search_files(target='files')` and content search).
2. If only the `.epub` exists, recover a Markdown source instead of editing the EPUB directly:
   - Unzip the EPUB to a temp directory (`python zipfile` or `unzip`).
   - Locate `*.xhtml` / `*.html` content files from `OEBPS`, `EPUB`, or the OPF manifest.
   - Extract readable body text with BeautifulSoup, preserving headings/paragraphs/lists where possible.
   - Create the missing `.md` in the appropriate source-doc directory, keeping the original title/numbering.
3. Make article edits in Markdown, then regenerate EPUBs into a dated output directory such as `~/draft/epub-YYYY-MM-DD/`.
4. Verify each generated EPUB reports `ok: True` and has nonzero size.
5. When delivery is requested, load and use the platform-neutral `send-file`
   skill. Do not call a platform-specific sender unless `send-file` explicitly
   delegates to it, and do not treat a text path as a delivered attachment.

### Splitting an existing EPUB into smaller EPUBs

When the user asks to split one large EPUB into multiple smaller EPUB files,
preserve EPUB structure rather than exporting plain text or sending unrelated
existing files. Inspect the EPUB ZIP, locate natural chapter/week headings,
create new valid EPUBs with fresh OPF/nav/toc files, verify section coverage,
then deliver through `send-file` when requested. See
`references/splitting-existing-epub.md` for the checklist and pitfalls.

### WeChat Reading / 微信读书 compatibility cleanup

When a generated EPUB fails in 微信读书, do not assume size is the cause. First clean the Markdown source: remove external links, source/subtitle references, and requested transcript filler words, then regenerate using this skill's existing `create_epub_from_markdown(...)` script rather than hand-writing an EPUB structure. For details, see `references/wechat-reading-epub-compatibility.md`.

If a known-good EPUB from the same generator opens but the target EPUB does not, use small probe EPUBs to isolate the trigger before changing the generator. Start with a first-1K-character probe (not a 25% split); if that fails, test safe metadata/title, then one-paragraph/one-sentence probes, and only later test length thresholds. See `references/wechat-reading-epub-debugging.md`.

### Transcript cleanup and source tracing

When the user points at an already-generated EPUB and says it has repeated sentences or ASR-style oral filler:

1. Locate the source Markdown path using filenames, report metadata, or generator
   configuration without reading the transcript body.
2. Load and run `transcript-cleaner`; do not implement cleanup in this skill.
3. Read only its generated report and verify the reported files.
4. Generate the EPUB only if the cleaner did not already create the required
   delivery variant.
5. Report the cleaner's counts, output paths, EPUB size, and validation result.

### Transcript-derived EPUB cleanup / 转写稿深度清理

When revising an EPUB generated from speech transcripts, delegate all transcript
cleanup to `transcript-cleaner`. Do not read the source Markdown body and do not
write ad hoc cleanup regexes. See `references/transcript-epub-cleanup.md`.

### Option 1: Raw Markdown Text
Provide markdown content directly in your message:

```
Convert this markdown to EPUB:
# My Book Title
## Chapter 1
This is chapter one content...
```

### Option 2: File Path
Provide a path to a markdown file to be converted.

## How It Works

1. **Preprocessing (optional)**: Cleans Markdown for platform compatibility:
   - `wechat` mode: strip external links, replace `&`→`和`, remove source metadata
   - `clean` mode: strip external links only
   - Optional: `--strip-timestamps`, `--strip-fillers`

2. **Markdown Parsing**: Analyzes your markdown and automatically:
   - Treats H1 headers (`#`) as chapter boundaries
   - Treats H2 headers (`##`) as section headings within chapters
   - Preserves formatting (bold, italic, links, lists, code blocks)

3. **Structure Generation**: Creates proper EPUB structure:
   - Automatic table of contents from chapters
   - Navigation document (EPUB3 standard)
   - Metadata (title, language, etc.)

4. **File Creation**: Generates a valid EPUB3 file ready for download and use

## Preprocessing Modes

| Mode | What it does | When to use |
|------|-------------|-------------|
| `wechat` | Strip links, `&`→`和`, `:`→`：`, remove source metadata, and sanitize output filename | 微信读书 / WeChat Reading |
| `clean` | Strip external links, remove source metadata | General cleaner output |

Additional flags:
- `--strip-timestamps`: Remove `[HH:MM:SS]` patterns (for prose reading, not transcript fidelity)
- `--strip-fillers`: Remove oral fillers (`Um`, `Uh`, `嗯`, `呃`, etc.)

## Usage Examples

### CLI (recommended)
```bash
# WeChat Reading safe EPUB
python3 ~/.hermes/skills/markdown-to-epub-converter/scripts/generate_epub.py \
  --input /path/to/source.md \
  --output /path/to/output.epub \
  --title "Book Title" --author "Author" \
  --preprocess wechat

# Clean EPUB with timestamps stripped
python3 ~/.hermes/skills/markdown-to-epub-converter/scripts/generate_epub.py \
  --input /path/to/source.md \
  --output /path/to/output.epub \
  --preprocess clean --strip-timestamps
```

### Python API (with preprocess)
```python
from epub_generator import create_epub_from_markdown

ok = create_epub_from_markdown(
    markdown_content,
    str(output_path),
    title='Book Title',
    author='Author',
    preprocess='wechat',        # or 'clean', or None
    strip_timestamps=False,
    strip_fillers=False,
)
```

### Direct file conversion from an existing Markdown path

When converting a local `.md` file, use the bundled CLI and pass the path
directly. Do not read the document into agent context first:

```bash
python3 ~/.hermes/skills/markdown-to-epub-converter/scripts/generate_epub.py \
  --input /absolute/path/input.md \
  --output /absolute/path/output.epub \
  --title "Book Title" \
  --author "Author Name"
```

Verify `ok: True` and nonzero file size before sending or reporting success.
When delivery is requested, use the `send-file` skill so Discord, Feishu, and
other supported surfaces route through one interface.

### Example 1: Convert a Blog Post
"Convert this markdown blog post to EPUB:
# How to Build a Simple Web Server
## Introduction
...content..."

### Example 2: Convert a Research Summary
"I have research notes in markdown format. Convert them to an EPUB ebook. The content is:
# Research Project: Machine Learning Basics
## Chapter 1: Fundamentals
..."

### Example 3: Convert a Chat Summary
"Summarize our conversation so far as markdown and convert it to an EPUB for reference"

## Output

The skill generates a downloadable EPUB file that includes:
- Professional formatting
- Automatic table of contents
- Proper chapter structure
- Support for markdown formatting elements:
  - Headers (all levels)
  - Bold and italic text
  - Hyperlinks
  - Lists (ordered and unordered)
  - Code blocks and inline code
  - Blockquotes
  - Horizontal rules

## Markdown Elements Supported

| Element | Markdown | Support | Notes |
|---------|----------|---------|-------|
| Headers | `# H1` through `###### H6` | Full | Auto TOC generation |
| Bold | `**text**` or `__text__` | Full | |
| Italic | `*text*` or `_text_` | Full | |
| Links | `[text](url)` | Full | Clickable in ebooks |
| Lists | `- item` or `1. item` | Full | Nested lists supported |
| Code blocks | ` ```language ` | **Enhanced** | Syntax highlighting ready, monospace fonts |
| Inline code | ` `code` ` | **Enhanced** | Styled background, borders |
| Tables | Markdown tables | **Enhanced** | Styled headers, alternating rows |
| Blockquotes | `> quote` | Full | Styled with left border |
| Horizontal rule | `---` or `***` | Full | |

## Advanced Features

### Enhanced Code Block Support

Code blocks are beautifully formatted with:
- **Premium monospace fonts**: SF Mono, Monaco, Fira Code, Consolas, and more
- **Styled backgrounds**: Subtle gray background with blue accent border
- **Language detection**: Specify language after ` ``` ` for future syntax highlighting
- **Proper escaping**: HTML characters are safely escaped
- **Overflow handling**: Horizontal scrolling for long lines

Example:
```python
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)
```

### Enhanced Table Support

Tables are rendered with professional styling:
- **Styled headers**: Blue background with white text
- **Alternating rows**: Zebra striping for readability
- **Cell padding**: Comfortable spacing for easy reading
- **Inline formatting**: Code, bold, italic, and links work in cells
- **Responsive**: Tables adapt to different screen sizes

Example:
| Feature | Status | Notes |
|---------|--------|-------|
| Headers | ✓ | Full support |
| Code | ✓ | Enhanced styling |
| Tables | ✓ | Professional layout |

### Custom Title and Metadata
You can specify EPUB metadata:
- Book title (defaults to first H1 header)
- Author name
- Language
- Publication date

### Chapter Organization
Chapters are automatically detected from:
- H1 headers (`#`) as primary chapter breaks
- Logical content sections between H1s
- Automatic page breaks between chapters

### Styling
The generated EPUB uses clean, readable default styling that:
- Respects the reader's font preferences
- Works on all screen sizes
- Maintains proper spacing and hierarchy
- Includes appropriate margins and padding

## Technical Details

- **Format**: EPUB3 (compatible with all modern readers)
- **Encoding**: UTF-8
- **HTML Version**: XHTML 1.1
- **CSS Support**: Responsive styling

## Downloading Your EPUB

After generation, the file will be available for download. You can then:
1. Download the EPUB to your computer
2. Open it with your preferred ebook reader
3. Transfer to your Kindle, iPad, or other device
4. Upload directly to Kindle via email or cloud

## Tips for Best Results

1. **Use Proper Markdown Structure**: The skill works best when markdown follows standard conventions (H1 for titles, H2 for sections)

2. **Clear Chapter Breaks**: Use H1 headers to clearly mark chapter divisions

3. **Descriptive Headers**: Headers become the table of contents, so make them clear and descriptive

4. **Content Organization**: Place content logically between headers

5. **Supported Formatting**: Stick to basic markdown formatting for best compatibility across all readers

## Troubleshooting

**EPUB doesn't open in 微信读书**: Use `--preprocess wechat`. This mode now sanitizes all three layers:
1. Markdown body/H1: ``&``→``和`` and external links removed.
2. EPUB metadata title: if you passed `--title`, it is sanitized too.
3. Output filename: if `--output` contains fragile characters such as `&`, the CLI writes to a safe filename and reports `requested_output`, `output`, and `output_renamed: true`.

Do not send/import an EPUB whose visible filename still contains `&`. 微信读书 may fail before reading the EPUB internals.

**EPUB doesn't open**: Ensure your markdown is properly formatted. Check for matching brackets in links and proper syntax.

**Table of contents is empty**: Make sure your markdown includes H1 headers to define chapters.

**Formatting looks different**: EPUB readers apply their own fonts and styling. This is normal and expected behavior.

## Scripts

- `epub_generator.py` - Core EPUB file creation and formatting
- `markdown_processor.py` - Markdown parsing and structure extraction
- `preprocess.py` - Markdown preprocessing for platform compatibility
- `generate_epub.py` - CLI wrapper with preprocessing flags

## Future Enhancements

- Auto-generated cover pages with custom images
- Kindle-specific optimizations (.mobi format)
- Custom CSS styling per user preferences
- Multi-document merging
- Image embedding and optimization
- Advanced metadata support
