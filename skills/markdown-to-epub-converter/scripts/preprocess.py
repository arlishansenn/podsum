"""
Markdown preprocessing for EPUB reader compatibility.

Cleans Markdown source before EPUB generation to avoid issues with
platforms like WeChat Reading (微信读书) that are stricter than
general EPUB readers.
"""

import re
from typing import Optional


# ── mode-specific presets ──────────────────────────────────────────

def preprocess_markdown(
    text: str,
    mode: Optional[str] = None,
    strip_timestamps: bool = False,
    strip_fillers: bool = False,
) -> str:
    """Apply preprocessing to Markdown text before EPUB generation.

    Args:
        text: Raw Markdown content.
        mode: Preprocessing preset.
            - ``None``: no-op, return as-is.
            - ``"wechat"``: WeChat Reading safe — strip links, replace ``&``,
              remove source metadata lines.
            - ``"clean"``: strip links only, keep ``&``.
        strip_timestamps: Remove ``[HH:MM:SS]`` / ``**[HH:MM:SS]**`` patterns.
        strip_fillers: Remove common oral fillers (``Um``, ``Uh``, ``嗯``, ``呃``, etc.).

    Returns:
        Preprocessed Markdown text.
    """
    if mode is None:
        return text

    if mode == "wechat":
        text = _strip_external_links(text)
        text = _strip_bare_urls(text)
        text = _strip_source_metadata(text)
        text = _normalize_ampersand(text)
        text = _sanitize_title_line(text)
    elif mode == "clean":
        text = _strip_external_links(text)
        text = _strip_bare_urls(text)
        text = _strip_source_metadata(text)

    if strip_timestamps:
        text = _strip_timestamps(text)

    if strip_fillers:
        text = _strip_fillers(text)

    return text


# ── individual cleaners ────────────────────────────────────────────

def _strip_external_links(text: str) -> str:
    """Convert [text](url) → text, remove bare markdown links."""
    # Markdown links: [text](url)
    text = re.sub(r'\[([^\]]*?)\]\(https?://[^\)]+\)', r'\1', text)
    return text


def _strip_bare_urls(text: str) -> str:
    """Remove bare http/https URLs."""
    text = re.sub(r'https?://\S+', '', text)
    return text


def _strip_source_metadata(text: str) -> str:
    """Remove lines that are source/course metadata.

    Matches lines like:
      - Source: ...
      - Course materials: ...
      - Subtitle file: ...
      - `state_xxx.en.vtt`
      - Youtube link lines
    """
    lines = text.splitlines()
    keep = []
    for line in lines:
        stripped = line.strip()
        if any(
            stripped.startswith(prefix)
            for prefix in (
                "- Source:",
                "- Course materials:",
                "- Subtitle file:",
                "- Processing:",
                "- Note:",
                "Source:",
                "Course materials:",
                "Subtitle file:",
            )
        ):
            continue
        if re.search(r'\.vtt`', stripped):
            continue
        if re.search(r'https?://www\.youtube\.com', stripped) and not stripped.startswith('#'):
            continue
        keep.append(line)
    return '\n'.join(keep)


def _normalize_ampersand(text: str) -> str:
    """Replace & with 和 throughout. WeChat Reading fails on & even when escaped."""
    text = text.replace('&amp;', '和')
    text = text.replace('&', '和')
    text = text.replace('MS&E', 'MSE')
    return text


def _sanitize_title_line(text: str) -> str:
    """Ensure the first H1 is safe for WeChat Reading."""
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith('#'):
        # Replace MS&E in title
        lines[0] = lines[0].replace('MS&E', 'MSE').replace('&', '和')
    return '\n'.join(lines)


def _strip_timestamps(text: str) -> str:
    """Remove timestamp patterns like [00:00:01] or **[00:00:01]**."""
    text = re.sub(r'\*\*\[?\d{2}:\d{2}:\d{2}\]?\*\*', '', text)
    text = re.sub(r'\[?\d{2}:\d{2}:\d{2}\]?', '', text)
    return text


# Oral fillers — English and Chinese
_FILLER_PATTERNS = [
    (r'\b[Uu]m\b', ''),
    (r'\b[Uu]h\b', ''),
    (r'\b[Yy]ou know\b', ''),
    (r'\b[Ll]ike\b', ''),
    (r'\b[Rr]ight\?', ''),
    (r'嗯', ''),
    (r'呃', ''),
    (r'啊', ''),
]


def _strip_fillers(text: str) -> str:
    """Remove common oral fillers from text."""
    for pattern, replacement in _FILLER_PATTERNS:
        text = re.sub(pattern, replacement, text)
    # Collapse multiple spaces
    text = re.sub(r'  +', ' ', text)
    return text


def sanitize_title(title: str, mode: str) -> str:
    """Apply the same preprocessing to a title string that the markdown body gets.

    Args:
        title: The title string (e.g. from ``--title`` CLI arg).
        mode: Preprocessing mode (``"wechat"`` or ``"clean"``).

    Returns:
        Sanitized title string.
    """
    if mode == "wechat":
        title = title.replace("&amp;", "和")
        title = title.replace("&", "和").replace("MS&E", "MSE")
        # WeChat Reading also fails on fragile metadata punctuation in some cases.
        title = title.replace(":", "：")
    return title


def sanitize_filename(filename: str, mode: str) -> str:
    """Sanitize an EPUB filename for target-reader compatibility.

    WeChat Reading can fail before opening the EPUB if the *uploaded filename*
    contains characters such as ``&``. This is separate from OPF/title/body
    sanitization, so callers must sanitize the output path too.
    """
    if mode != "wechat":
        return filename

    name = filename.replace("&amp;", "和").replace("&", "和")
    name = name.replace("MS&E", "MSE")
    # Avoid punctuation that often travels through upload/import pipelines poorly.
    name = name.replace(":", "_").replace("：", "_")
    name = re.sub(r'[\\/<>"|?*]', "_", name)
    name = re.sub(r"[ \t]+", "_", name).strip("._ ")
    return name or "book.epub"