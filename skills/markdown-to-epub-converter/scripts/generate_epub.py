#!/usr/bin/env python3
"""
CLI wrapper for markdown-to-epub conversion with optional preprocessing.

Usage:
    python3 generate_epub.py --input source.md --output out.epub \\
        --title "Book Title" --author "Author" --preprocess wechat

    python3 generate_epub.py --input source.md --output out.epub \\
        --preprocess clean --strip-timestamps
"""

import argparse
import json
import sys
from pathlib import Path

# 从当前脚本向上查找仓库根目录，并把 outputs 加入导入路径
_current_path = Path(__file__).resolve()
_outputs_dir = None
for _parent in _current_path.parents:
    _candidate = _parent / "outputs"
    if _candidate.is_dir():
        _outputs_dir = _candidate
        break

if _outputs_dir is None:
    raise RuntimeError("Could not locate repository outputs directory")

if str(_outputs_dir) not in sys.path:
    sys.path.insert(0, str(_outputs_dir))

from podsum_core.epub_converter import (
    create_epub_from_markdown,
    sanitize_filename,
    sanitize_title,
)


def main():
    parser = argparse.ArgumentParser(
        description="Convert Markdown to EPUB with optional preprocessing."
    )
    parser.add_argument("--input", required=True, help="Input Markdown file path")
    parser.add_argument("--output", required=True, help="Output EPUB file path")
    parser.add_argument("--title", default=None, help="Book title (default: first H1)")
    parser.add_argument("--author", default=None, help="Author name")
    parser.add_argument(
        "--preprocess",
        choices=["wechat", "clean"],
        default=None,
        help="Preprocessing mode: 'wechat' (WeChat Reading safe), 'clean' (strip links only)",
    )
    parser.add_argument(
        "--strip-timestamps",
        action="store_true",
        help="Remove [HH:MM:SS] timestamp patterns",
    )
    parser.add_argument(
        "--strip-fillers",
        action="store_true",
        help="Remove common oral fillers (Um, Uh, 嗯, 呃, etc.)",
    )

    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"Input file not found: {args.input}"}))
        sys.exit(1)

    markdown_content = src.read_text(encoding="utf-8")

    # Sanitize title if preprocessing is active (CLI arg bypasses content preprocessing)
    title = args.title
    if args.preprocess and title:
        title = sanitize_title(title, args.preprocess)

    # Sanitize the output *filename* too. WeChat Reading may fail during import
    # if the uploaded filename contains characters like '&', even when the EPUB
    # internals are valid XML.
    requested_output = Path(args.output)
    out = requested_output
    if args.preprocess:
        safe_name = sanitize_filename(requested_output.name, args.preprocess)
        out = requested_output.with_name(safe_name)

    ok = create_epub_from_markdown(
        markdown_content,
        str(out),
        title=title,
        author=args.author,
        preprocess=args.preprocess,
        strip_timestamps=args.strip_timestamps,
        strip_fillers=args.strip_fillers,
    )

    result = {
        "ok": ok,
        "input": str(src),
        "requested_output": str(requested_output),
        "output": str(out),
        "output_renamed": str(out) != str(requested_output),
        "size": out.stat().st_size if ok and out.exists() else 0,
        "preprocess": args.preprocess,
        "strip_timestamps": args.strip_timestamps,
        "strip_fillers": args.strip_fillers,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()