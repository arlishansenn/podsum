#!/usr/bin/env python3
"""Compatibility shim for the old transcript-cleaner script name."""

from __future__ import annotations

import sys
from clean_markdown import main


if __name__ == "__main__":
    sys.exit(main())
