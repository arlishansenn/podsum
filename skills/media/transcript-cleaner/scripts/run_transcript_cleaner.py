#!/usr/bin/env python3
"""Run the deployed Transcript Cleaner from a stable Hermes skill entrypoint."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


DEFAULT_PROJECT_ROOT = Path("/Users/admin/Documents/Codex/podsum/outputs")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--output-stem")
    parser.add_argument("--title")
    parser.add_argument("--author")
    parser.add_argument("--min-prefix-chars", type=int, default=5)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(os.environ.get("PODSUM_PROJECT_ROOT", DEFAULT_PROJECT_ROOT)),
        help="Directory containing the transcript_cleaner Python package",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    source = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    project_root = args.project_root.expanduser().resolve()
    package = project_root / "transcript_cleaner"

    if not source.is_file():
        raise SystemExit(f"Input Markdown does not exist: {source}")
    if source.suffix.lower() != ".md":
        raise SystemExit(f"Input must be a Markdown file: {source}")
    if not (package / "__main__.py").is_file():
        raise SystemExit(
            "Transcript Cleaner package not found at "
            f"{package}. Use --project-root to set its parent directory."
        )
    if args.min_prefix_chars < 1:
        raise SystemExit("--min-prefix-chars must be >= 1")

    command = [
        "/usr/bin/python3",
        "-m",
        "transcript_cleaner",
        str(source),
        "--output-dir",
        str(output_dir),
        "--min-prefix-chars",
        str(args.min_prefix_chars),
    ]
    for option, value in (
        ("--output-stem", args.output_stem),
        ("--title", args.title),
        ("--author", args.author),
    ):
        if value:
            command.extend((option, value))

    completed = subprocess.run(command, cwd=project_root, check=False)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
