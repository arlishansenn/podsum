#!/usr/bin/env python3
"""Compatibility shim for the old EPUB export script name."""

from __future__ import annotations

import sys
from export_epub import main


if __name__ == "__main__":
    sys.exit(main())
