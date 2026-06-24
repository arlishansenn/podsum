#!/usr/bin/env python3
"""Prepare a local file for sending/import-sensitive readers.

Copies the file to a safe filename when the original basename contains
characters known to break downstream importers such as WeChat Reading.
Does not modify or delete the source file.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


def safe_name(name: str, mode: str = "wechat") -> str:
    if mode != "wechat":
        return name
    safe = name.replace("&amp;", "和").replace("&", "和")
    safe = safe.replace("MS&E", "MSE")
    safe = safe.replace(":", "_").replace("：", "_")
    safe = re.sub(r'[\\/<>"|?*]', "_", safe)
    safe = re.sub(r"[ \t]+", "_", safe).strip("._ ")
    return safe or "file"


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy a file to a send-safe filename if needed.")
    parser.add_argument("--file", required=True, help="Absolute path to the source file")
    parser.add_argument("--mode", choices=["wechat", "none"], default="wechat")
    args = parser.parse_args()

    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"file not found: {src}"}, ensure_ascii=False))
        sys.exit(1)

    name = safe_name(src.name, args.mode)
    dst = src.with_name(name)
    renamed = dst != src
    if renamed:
        shutil.copy2(src, dst)
    print(json.dumps({
        "ok": True,
        "source": str(src),
        "path_to_send": str(dst),
        "renamed": renamed,
        "original_name": src.name,
        "safe_name": dst.name,
        "size": dst.stat().st_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
