#!/usr/bin/env python3
"""Clean one transcript Markdown file and generate Markdown, EPUB and JSON report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence

from .cleaner import clean_text
from .epub import write_epub


def default_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def run(args: argparse.Namespace) -> int:
    source = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    raw = source.read_text(encoding="utf-8", errors="replace")
    result = clean_text(raw, min_prefix_cjk=args.min_prefix_chars)
    stem = args.output_stem or f"{source.stem}_cleaned"
    markdown_path = output_dir / f"{stem}.md"
    epub_path = output_dir / f"{stem}.epub"
    report_path = output_dir / f"{stem}.report.json"
    title = args.title or default_title(result.text, source.stem)

    markdown_path.write_text(result.text, encoding="utf-8")
    write_epub(result.text, epub_path, title=title, author=args.author)
    report = {
        "source": str(source),
        "markdown": str(markdown_path),
        "epub": str(epub_path),
        "title": title,
        "author": args.author,
        "min_prefix_chars": args.min_prefix_chars,
        "stats": result.stats.to_dict(),
        "edits": [edit.to_dict() for edit in result.edits],
        "sha256": {
            "source": hashlib.sha256(source.read_bytes()).hexdigest(),
            "markdown": hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
            "epub": hashlib.sha256(epub_path.read_bytes()).hexdigest(),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--output-stem")
    parser.add_argument("--title")
    parser.add_argument("--author", default="Podsum Transcript Cleaner")
    parser.add_argument("--min-prefix-chars", type=int, default=5)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.min_prefix_chars < 1:
        parser.error("--min-prefix-chars must be >= 1")
    return run(args)
