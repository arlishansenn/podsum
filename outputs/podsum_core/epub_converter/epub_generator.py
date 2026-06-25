"""
EPUB file generation module.

This module handles creating proper EPUB3 files from parsed markdown structure
using the ebooklib library.
"""

import html
import re
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime

try:
    from ebooklib import epub
except ModuleNotFoundError:
    epub = None

from .markdown_processor import (
    Chapter, Section, EbookMetadata, MarkdownProcessor
)
from .preprocess import preprocess_markdown, sanitize_title


class EPUBGenerator:
    """Generate EPUB3 files from markdown chapters and sections."""

    # Default CSS for EPUB styling
    DEFAULT_CSS = """
    body {
        font-family: Georgia, serif;
        font-size: 75%;
        line-height: 1.5;
        margin: 0;
        padding: 1em;
    }

    h1 {
        font-size: 1.3em;
        font-weight: bold;
        margin: 1.5em 0 0.75em 0;
        color: #1a1a1a;
        page-break-before: always;
    }

    h2 {
        font-size: 1.15em;
        font-weight: bold;
        margin: 1.25em 0 0.5em 0;
        color: #2c3e50;
    }

    h3 {
        font-size: 1.05em;
        font-weight: bold;
        margin: 1em 0 0.5em 0;
        color: #34495e;
    }

    h4, h5, h6 {
        font-size: 0.95em;
        font-weight: bold;
        margin: 0.75em 0 0.5em 0;
    }

    p {
        margin: 0.75em 0;
        text-align: justify;
        font-size: 1em;
    }

    a {
        color: #0066cc;
        text-decoration: none;
    }

    a:visited {
        color: #663399;
    }

    strong {
        font-weight: bold;
    }

    em {
        font-style: italic;
    }

    code, pre {
        font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', 'Fira Mono', 'Roboto Mono', 'Consolas', 'Courier New', monospace;
        font-size: 0.72em;
    }

    code {
        background-color: #f5f5f5;
        padding: 0.15em 0.4em;
        border-radius: 3px;
        border: 1px solid #e0e0e0;
    }

    pre {
        background-color: #f8f8f8;
        border: 1px solid #e0e0e0;
        border-left: 3px solid #0066cc;
        padding: 0.8em;
        overflow-x: auto;
        margin: 1em 0;
        border-radius: 4px;
        line-height: 1.3;
        tab-size: 2;
        -moz-tab-size: 2;
    }

    pre code {
        padding: 0;
        background-color: transparent;
        border: none;
        font-size: 1em;
        line-height: 1.3;
    }

    /* Line numbers in code blocks */
    .line-number {
        display: inline-block;
        width: 2.2em;
        text-align: right;
        padding-right: 0.3em;
        color: #999;
        user-select: none;
        -webkit-user-select: none;
        -moz-user-select: none;
        border-right: 1px solid #ddd;
        margin-right: 0.4em;
    }

    .line-content {
        display: inline;
    }

    /* Syntax highlighting (Pygments-based) */
    .syn-keyword {
        color: #0066cc;
        font-weight: 600;
    }

    .syn-string {
        color: #22863a;
    }

    .syn-comment {
        color: #6a737d;
        font-style: italic;
    }

    .syn-number {
        color: #005cc5;
    }

    .syn-function {
        color: #6f42c1;
    }

    .syn-class {
        color: #d73a49;
        font-weight: 600;
    }

    blockquote {
        border-left: 4px solid #0066cc;
        margin: 1em 0;
        padding-left: 1em;
        color: #555;
        font-style: italic;
    }

    ul, ol {
        margin: 0.75em 0;
        padding-left: 2em;
    }

    li {
        margin: 0.5em 0;
    }

    table {
        border-collapse: collapse;
        width: 100%;
        margin: 1.5em 0;
        font-size: 0.88em;
        border: 1px solid #ddd;
    }

    thead {
        background-color: #0066cc;
        color: white;
    }

    th {
        padding: 0.75em 1em;
        text-align: left;
        font-weight: bold;
        border: 1px solid #0052a3;
    }

    td {
        padding: 0.65em 1em;
        border: 1px solid #ddd;
        text-align: left;
    }

    tbody tr:nth-child(even) {
        background-color: #f8f9fa;
    }

    tbody tr:hover {
        background-color: #f0f0f0;
    }

    /* Code in table cells */
    td code, th code {
        font-size: 0.85em;
    }

    hr {
        border: none;
        border-top: 2px solid #ddd;
        margin: 2em 0;
    }

    .toc-entry {
        margin: 0.5em 0;
    }

    .toc-entry-h2 {
        margin-left: 1.5em;
    }

    .toc-entry-h3 {
        margin-left: 3em;
    }
    """

    def __init__(self, metadata: EbookMetadata = None):
        """
        Initialize EPUB generator.

        Args:
            metadata: EbookMetadata object with title, author, etc.
        """
        self.metadata = metadata or EbookMetadata()
        self.book = None
        self.chapters = []
        self.toc_items = []

    def generate(self, chapters: List[Chapter], output_path: str) -> bool:
        """
        Generate EPUB file from chapters.

        Args:
            chapters: List of Chapter objects from markdown parser
            output_path: Path where EPUB file should be saved

        Returns:
            True if successful, False otherwise
        """
        try:
            self.chapters = chapters
            self._create_book()
            self._add_chapters()
            self._add_style()
            self._add_toc()
            self._write_epub(output_path)
            return True
        except Exception as e:
            print(f"Error generating EPUB: {e}")
            raise

    def _create_book(self) -> None:
        """Create and configure EPUB book object."""
        self.book = epub.EpubBook()

        # Set metadata
        self.book.set_identifier(self.metadata.identifier or str(uuid.uuid4()))
        self.book.set_title(self.metadata.title or "Untitled Book")
        self.book.set_language(self.metadata.language)

        if self.metadata.author:
            self.book.add_author(self.metadata.author)

    def _add_chapters(self) -> None:
        """Add chapters to EPUB."""
        all_items = []

        for chapter_idx, chapter in enumerate(self.chapters):
            # Create chapter HTML file
            chapter_html = self._render_chapter(chapter)
            chapter_file = epub.EpubHtml(
                title=chapter.title,
                file_name=f'chap_{chapter_idx + 1:03d}.xhtml',
                lang=self.metadata.language
            )
            # ebooklib's set_content expects the body content, not full XHTML
            # Extract just the body content
            body_match = chapter_html.find('<body>')
            if body_match != -1:
                body_start = body_match + 6
                body_end = chapter_html.find('</body>')
                body_content = chapter_html[body_start:body_end]
            else:
                body_content = chapter_html

            chapter_file.set_content(body_content)

            self.book.add_item(chapter_file)
            all_items.append(chapter_file)

            # Add chapter to TOC
            section_items = []
            for section in chapter.sections:
                section_items.append(section)

            self.toc_items.append((chapter, section_items))

    def _render_chapter(self, chapter: Chapter) -> str:
        """
        Render chapter to XHTML.

        Args:
            chapter: Chapter object

        Returns:
            XHTML string
        """
        html_parts = []

        # Chapter title
        if chapter.title:
            html_parts.append(f'<h1 id="{chapter.anchor}">{self._escape_html(chapter.title)}</h1>')

        # Chapter content
        if chapter.content:
            html_parts.append(self._render_content(chapter.content))

        # Sections
        for section in chapter.sections:
            html_parts.append(f'<h{section.level} id="{section.anchor}">{self._escape_html(section.title)}</h{section.level}>')
            if section.content:
                html_parts.append(self._render_content(section.content))

        # Ensure we have some content
        content = '\n'.join(html_parts)
        if not content.strip():
            # Add empty paragraph if no content
            content = '<p></p>'

        # Wrap in proper XHTML document
        xhtml = f"""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{self.metadata.language}">
<head>
    <title>{self._escape_html(chapter.title)}</title>
</head>
<body>
{content}
</body>
</html>"""

        return xhtml

    def _render_content(self, content: str) -> str:
        """
        Render markdown content to HTML.

        Args:
            content: Markdown content

        Returns:
            HTML string
        """
        if not content:
            return ""

        # Use markdown processor to convert to HTML
        html = MarkdownProcessor.markdown_to_html(content)
        return html

    def _add_style(self) -> None:
        """Add CSS styling to EPUB."""
        if not self.book:
            return

        style = epub.EpubItem()
        style.file_name = 'style/main.css'
        style.media_type = 'text/css'
        style.set_content(self.DEFAULT_CSS)

        self.book.add_item(style)

        # Add CSS to all chapters
        for item in self.book.items:
            if isinstance(item, epub.EpubHtml):
                item.add_link(
                    rel='stylesheet',
                    href='style/main.css',
                    type='text/css'
                )

    def _add_toc(self) -> None:
        """Add table of contents to EPUB."""
        if not self.book or not self.toc_items:
            return

        toc_items = []

        for chapter, sections in self.toc_items:
            # Find chapter item in book
            chapter_link = None
            for item in self.book.items:
                if isinstance(item, epub.EpubHtml) and item.title == chapter.title:
                    chapter_link = item
                    break

            if chapter_link:
                if sections:
                    # Chapter with sections - create nested structure
                    section_links = []
                    for section in sections:
                        # Add anchor to section headers in rendered HTML
                        section_links.append(
                            epub.Link(
                                f"{chapter_link.file_name}#{section.anchor}",
                                section.title,
                                f"sec_{section.anchor}"
                            )
                        )
                    # Add chapter with nested sections as tuple
                    toc_items.append((chapter_link, section_links))
                else:
                    # Chapter without sections
                    toc_items.append(chapter_link)

        if toc_items:
            self.book.toc = tuple(toc_items)

    def _write_epub(self, output_path: str) -> None:
        """
        Write EPUB file to disk.

        Args:
            output_path: Path where EPUB should be saved
        """
        if not self.book:
            raise ValueError("Book not initialized")

        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Add navigation items (must be added before setting spine)
        ncx = epub.EpubNcx()
        nav = epub.EpubNav()

        self.book.add_item(ncx)
        self.book.add_item(nav)

        # Define spine - only include HTML items, nav is implicit
        html_items = [item for item in self.book.items if isinstance(item, epub.EpubHtml)]
        self.book.spine = html_items

        # Write EPUB
        epub.write_epub(output_path, self.book, {})

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        if not text:
            return ""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;'))


def _inline_markdown(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _markdown_to_xhtml(markdown: str) -> str:
    body: List[str] = []
    paragraph: List[str] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            body.append(f"<p>{_inline_markdown(' '.join(paragraph))}</p>")
            paragraph = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush()
            level = len(heading.group(1))
            body.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue
        if line.startswith(">"):
            flush()
            body.append(f"<blockquote>{_inline_markdown(line.lstrip('> ').strip())}</blockquote>")
            continue
        paragraph.append(line)
    flush()
    return "\n".join(body)


def _write_minimal_epub(markdown: str, output_path: str, *, title: Optional[str], author: Optional[str]) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    book_title = title or "Untitled"
    book_author = author or "Unknown"
    uid = f"urn:uuid:{uuid.uuid4()}"
    xhtml_body = _markdown_to_xhtml(markdown)
    content_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(book_title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; line-height: 1.65; }}
    p {{ margin: 0.75em 0; }}
    blockquote {{ margin: 1em; color: #555; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  </style>
</head>
<body>
{xhtml_body}
</body>
</html>
"""
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0" xml:lang="zh-CN">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{uid}</dc:identifier>
    <dc:title>{html.escape(book_title)}</dc:title>
    <dc:language>zh-CN</dc:language>
    <dc:creator>{html.escape(book_author)}</dc:creator>
    <meta property="dcterms:modified">{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="content"/>
  </spine>
</package>
"""
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.xhtml", content_xhtml, compress_type=zipfile.ZIP_DEFLATED)


def create_epub_from_markdown(markdown_content: str, output_path: str,
                              title: Optional[str] = None,
                              author: Optional[str] = None,
                              preprocess: Optional[str] = None,
                              strip_timestamps: bool = False,
                              strip_fillers: bool = False) -> bool:
    """
    Convenience function to create EPUB from markdown content.

    Args:
        markdown_content: Raw markdown text
        output_path: Path where EPUB should be saved
        title: Book title (optional, will use first H1 if not provided)
        author: Author name (optional)
        preprocess: Preprocessing mode. ``None`` = no-op, ``"wechat"`` = WeChat
            Reading safe (strip links, replace ``&``, remove metadata),
            ``"clean"`` = strip links only.
        strip_timestamps: Remove ``[HH:MM:SS]`` patterns.
        strip_fillers: Remove common oral fillers (Um, Uh, 嗯, 呃, etc.).

    Returns:
        True if successful, False otherwise
    """
    # Preprocess if requested
    if preprocess:
        markdown_content = preprocess_markdown(
            markdown_content,
            mode=preprocess,
            strip_timestamps=strip_timestamps,
            strip_fillers=strip_fillers,
        )
        if title:
            title = sanitize_title(title, preprocess)

    if epub is None:
        _write_minimal_epub(markdown_content, output_path, title=title, author=author)
        return True

    # Parse markdown
    processor = MarkdownProcessor()
    result = processor.process(markdown_content)

    # Create metadata
    metadata = result['metadata']
    if title:
        metadata.title = title
    if author:
        metadata.author = author

    # Generate EPUB
    generator = EPUBGenerator(metadata)
    return generator.generate(result['chapters'], output_path)
