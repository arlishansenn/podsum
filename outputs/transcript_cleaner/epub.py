"""Minimal EPUB 3 generator for cleaned Markdown transcripts."""

from __future__ import annotations

import datetime as dt
import html
import re
import uuid
import zipfile
from pathlib import Path


def inline_markdown(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def markdown_to_xhtml(markdown: str) -> str:
    body: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            body.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
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
            body.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue
        if line.startswith(">"):
            flush()
            body.append(f"<blockquote>{inline_markdown(line.lstrip('> ').strip())}</blockquote>")
            continue
        paragraph.append(line)
    flush()
    return "\n".join(body)


def write_epub(markdown: str, output: Path, *, title: str, author: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    uid = f"urn:uuid:{uuid.uuid4()}"
    xhtml_body = markdown_to_xhtml(markdown)
    content_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
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
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>zh-CN</dc:language>
    <dc:creator>{html.escape(author)}</dc:creator>
    <meta property="dcterms:modified">{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
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
    return output
