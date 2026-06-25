"""Compatibility EPUB writer for cleaned Markdown transcripts."""

from __future__ import annotations

from pathlib import Path

from podsum_core.epub_converter import create_epub_from_markdown


def write_epub(markdown: str, output: Path, *, title: str, author: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    ok = create_epub_from_markdown(
        markdown,
        str(output),
        title=title,
        author=author,
        preprocess=None,
        strip_timestamps=False,
        strip_fillers=False,
    )
    if not ok:
        raise RuntimeError(f"Failed to write EPUB: {output}")
    return output
