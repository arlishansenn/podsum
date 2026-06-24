#!/usr/bin/env python3
"""Send newly generated Podsum reports to Discord via Hermes."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any


DEFAULT_TRANSCRIPTS_ROOT = Path.home() / "Podcasts/AutoDownloads"
DEFAULT_STATE_FILE = Path.home() / "Library/Application Support/Podsum/feishu_sent.json"
DEFAULT_MEMORY_FILE = Path.home() / ".hermes/memories/MEMORY.md"
DEFAULT_INTERPRETATION_PROMPT = Path(__file__).with_name("hermes_interpretation_prompt.md")
DEFAULT_TARGET = "discord:1518857496788467832"
DEFAULT_HERMES = Path.home() / ".local/bin/hermes"
DEFAULT_AUDIO_RETENTION_DAYS = 14
DEFAULT_TRANSCRIPT_RETENTION_DAYS = 90
DEFAULT_UNTRANSCRIBED_AUDIO_RETENTION_DAYS = 30
DEFAULT_BUNDLE_RETENTION_DAYS = 90
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp4", ".m4v"}
TRANSCRIPT_EXCERPT_CHARS = 50000
MEMORY_EXCERPT_CHARS = 12000


def log(message: str) -> None:
    print(message, flush=True)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sent": {}}
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("sent", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path, max_chars: int | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n\n[内容已截断，用于控制 Hermes prompt 长度]"
    return text


def find_markdown_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/Transcripts/*.md"), key=lambda p: p.stat().st_mtime)


def reports_dir(root: Path) -> Path:
    return root / "Reports"


def find_report_files(root: Path) -> list[Path]:
    directory = reports_dir(root)
    if not directory.exists():
        return []
    files = [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in {".md", ".epub"}]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def find_audio_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "Transcripts" in path.parts:
            continue
        if path.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(path)
    return sorted(files, key=lambda p: p.stat().st_mtime)


def transcript_path_for_audio(audio_path: Path) -> Path:
    return audio_path.parent / "Transcripts" / f"{audio_path.stem}.md"


def parse_sent_at(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return None


def is_older_than(path: Path, days: int) -> bool:
    return path.stat().st_mtime < time.time() - days * 24 * 60 * 60


def sent_record_for(path: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    record = state["sent"].get(str(path))
    return record if isinstance(record, dict) else None


def sent_digest_matches(path: Path, state: dict[str, Any]) -> bool:
    record = sent_record_for(path, state)
    if not record:
        return False
    try:
        return record.get("sha256") == sha256_file(path)
    except FileNotFoundError:
        return False


def sent_older_than(path: Path, state: dict[str, Any], days: int) -> bool:
    record = sent_record_for(path, state)
    if not record:
        return False
    sent_at = parse_sent_at(record.get("sent_at"))
    if sent_at is None:
        sent_at = path.stat().st_mtime
    return sent_at < time.time() - days * 24 * 60 * 60


def pending_markdown_files(args: argparse.Namespace, state: dict[str, Any]) -> list[tuple[Path, str]]:
    pending: list[tuple[Path, str]] = []
    sent = state["sent"]
    for path in find_markdown_files(args.transcripts_root):
        digest = sha256_file(path)
        if sent.get(str(path), {}).get("sha256") == digest and not args.force:
            continue
        pending.append((path, digest))
    return pending


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text

    raw_meta = text[4:end]
    body = text[end + len("\n---\n") :].lstrip()
    meta: dict[str, Any] = {}
    for line in raw_meta.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        try:
            meta[key] = json.loads(value)
        except json.JSONDecodeError:
            meta[key] = value
    return meta, body


def transcript_info(path: Path, root: Path) -> dict[str, Any]:
    text = read_text(path)
    meta, body = parse_frontmatter(text)
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path

    podcast = str(meta.get("podcast") or path.parent.parent.name)
    episode = str(meta.get("episode") or path.stem)
    return {
        "path": path,
        "relative_path": rel,
        "podcast": podcast,
        "episode": episode,
        "audio": str(meta.get("audio") or ""),
        "model": str(meta.get("model") or ""),
        "transcribed_at": str(meta.get("transcribed_at") or ""),
        "body": body,
    }


def anchor_for(index: int, info: dict[str, Any]) -> str:
    raw = f"{index + 1}-{info['podcast']}-{info['episode']}".lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw)
    raw = raw.strip("-")
    return raw[:96] or f"episode-{index + 1}"


def markdown_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(#([^)]+)\)", r'<a href="#\2">\1</a>', escaped)
    return escaped


def markdown_to_xhtml(markdown: str) -> str:
    body: list[str] = []
    paragraph: list[str] = []
    in_table = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            body.append(f"<p>{inline_markdown_to_html(' '.join(paragraph))}</p>")
            paragraph = []

    def close_table() -> None:
        nonlocal in_table
        if in_table:
            body.append("</tbody></table>")
            in_table = False

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            close_table()
            continue

        anchor_match = re.fullmatch(r'<a id="([^"]+)"></a>', stripped)
        if anchor_match:
            flush_paragraph()
            close_table()
            body.append(f'<div id="{html.escape(anchor_match.group(1), quote=True)}"></div>')
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_table()
            level = len(heading.group(1))
            content = inline_markdown_to_html(heading.group(2))
            body.append(f"<h{level}>{content}</h{level}>")
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if not in_table:
                body.append("<table><tbody>")
                in_table = True
            row = "".join(f"<td>{inline_markdown_to_html(cell)}</td>" for cell in cells)
            body.append(f"<tr>{row}</tr>")
            continue

        close_table()
        paragraph.append(stripped)

    flush_paragraph()
    close_table()
    return "\n".join(body)


def write_epub(markdown_path: Path, title: str) -> Path:
    markdown = read_text(markdown_path)
    epub_path = markdown_path.with_suffix(".epub")
    uid = f"urn:uuid:{uuid.uuid4()}"
    xhtml_body = markdown_to_xhtml(markdown)

    content_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", sans-serif; line-height: 1.55; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }}
    table {{ border-collapse: collapse; width: 100%; }}
    td {{ border: 1px solid #ddd; padding: 4px 6px; vertical-align: top; }}
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
    <dc:creator>Podsum</dc:creator>
    <dc:modified>{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}</dc:modified>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="content"/>
  </spine>
</package>
"""

    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.xhtml", content_xhtml, compress_type=zipfile.ZIP_DEFLATED)

    return epub_path


def hermes_interpretation(args: argparse.Namespace, info: dict[str, Any]) -> str:
    memory = ""
    if args.memory_file.exists():
        memory = read_text(args.memory_file, MEMORY_EXCERPT_CHARS)

    transcript = info["body"]
    if len(transcript) > TRANSCRIPT_EXCERPT_CHARS:
        transcript = transcript[:TRANSCRIPT_EXCERPT_CHARS] + "\n\n[文字稿已截断，仅用于生成顶部解读]"

    template = read_text(args.interpretation_prompt)
    prompt = template.format(
        memory=memory,
        podcast=info["podcast"],
        episode=info["episode"],
        transcript=transcript,
    )
    command = [str(args.hermes), "-z", prompt]
    try:
        result = subprocess.run(
            command,
            cwd=str(args.project_dir),
            text=True,
            capture_output=True,
            timeout=args.hermes_timeout,
        )
    except Exception as exc:
        return f"Hermes 解读失败：{exc}"

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or output or "unknown Hermes error").strip()
        return f"Hermes 解读失败：{detail}"
    return output or "Hermes 未返回解读。"


def build_bundle(args: argparse.Namespace, pending: list[tuple[Path, str]]) -> tuple[Path, list[dict[str, Any]]]:
    infos = [transcript_info(path, args.transcripts_root) for path, _digest in pending]
    for index, info in enumerate(infos):
        info["anchor"] = anchor_for(index, info)
        log(f"Interpreting with Hermes: {info['podcast']} - {info['episode']}")
        if args.dry_run and not args.dry_run_interpret:
            info["interpretation"] = "dry-run: skipped Hermes interpretation."
        else:
            info["interpretation"] = hermes_interpretation(args, info)

    directory = reports_dir(args.transcripts_root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    bundle_path = directory / f"podsum-{stamp}.md"

    lines: list[str] = []
    lines.append(f"# Podsum {time.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    lines.append("")
    lines.append("## 目录")
    lines.append("")

    for index, info in enumerate(infos, 1):
        title = f"{info['podcast']} - {info['episode']}"
        lines.append(f"{index}. [{title}](#{info['anchor']})")

    lines.append("")
    lines.append("## 深度解读")
    lines.append("")
    for index, info in enumerate(infos, 1):
        title = f"{info['podcast']} - {info['episode']}"
        lines.append(f'<a id="{info["anchor"]}"></a>')
        lines.append("")
        lines.append(f"### {index}. {title}")
        lines.append("")
        lines.append("| 字段 | 内容 |")
        lines.append("| --- | --- |")
        lines.append(f"| 文件 | `{markdown_escape(str(info['relative_path']))}` |")
        if info["audio"]:
            lines.append(f"| 音频 | `{markdown_escape(info['audio'])}` |")
        if info["model"]:
            lines.append(f"| 模型 | `{markdown_escape(info['model'])}` |")
        if info["transcribed_at"]:
            lines.append(f"| 转写时间 | `{markdown_escape(info['transcribed_at'])}` |")
        lines.append("")
        lines.append(str(info["interpretation"]).strip())
        lines.append("")

    bundle_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return bundle_path, infos


def remove_path(path: Path, dry_run: bool) -> bool:
    if dry_run:
        log(f"would delete: {path}")
        return False
    path.unlink()
    log(f"deleted: {path}")
    return True


def remove_empty_dirs(root: Path, dry_run: bool) -> int:
    removed = 0
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if directory == root:
            continue
        try:
            next(directory.iterdir())
        except StopIteration:
            if dry_run:
                log(f"would remove empty directory: {directory}")
            else:
                directory.rmdir()
                log(f"removed empty directory: {directory}")
                removed += 1
        except OSError:
            continue
    return removed


def cleanup_files(args: argparse.Namespace, state: dict[str, Any]) -> None:
    audio_deleted = 0
    transcript_deleted = 0

    if args.audio_retention_days >= 0:
        for audio_path in find_audio_files(args.transcripts_root):
            md_path = transcript_path_for_audio(audio_path)
            if md_path.exists():
                if is_older_than(audio_path, args.audio_retention_days) and sent_digest_matches(md_path, state):
                    audio_deleted += int(remove_path(audio_path, args.dry_run))
            elif args.untranscribed_audio_retention_days >= 0 and is_older_than(audio_path, args.untranscribed_audio_retention_days):
                audio_deleted += int(remove_path(audio_path, args.dry_run))

    if args.transcript_retention_days >= 0:
        for md_path in find_markdown_files(args.transcripts_root):
            if sent_digest_matches(md_path, state) and sent_older_than(md_path, state, args.transcript_retention_days):
                transcript_deleted += int(remove_path(md_path, args.dry_run))
                if not args.dry_run:
                    state["sent"].pop(str(md_path), None)

    report_deleted = 0
    if args.bundle_retention_days >= 0:
        for report_path in find_report_files(args.transcripts_root):
            if is_older_than(report_path, args.bundle_retention_days):
                report_deleted += int(remove_path(report_path, args.dry_run))

    removed_dirs = remove_empty_dirs(args.transcripts_root, args.dry_run)
    if not args.dry_run:
        save_state(args.state, state)
    log(
        "Cleanup complete: "
        f"{audio_deleted} audio file(s), {transcript_deleted} transcript(s), "
        f"{report_deleted} report(s), {removed_dirs} empty dir(s) removed."
    )


def build_message(path: Path, root: Path, count: int, source_md: Path | None = None) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    source_line = ""
    if source_md is not None:
        try:
            source_rel = source_md.relative_to(root)
        except ValueError:
            source_rel = source_md
        source_line = f"源 Markdown: {source_rel}\n"
    return (
        "[[as_document]]\n"
        f"Podsum 合并文字稿 EPUB\n\n"
        f"文件: {rel}\n"
        f"{source_line}"
        f"包含: {count} 个 podcast 文字稿\n"
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n\n"
        f"MEDIA:{path}"
    )


def send_file(args: argparse.Namespace, path: Path, count: int, source_md: Path | None = None) -> None:
    message = build_message(path, args.transcripts_root, count, source_md)
    command = [
        str(args.hermes),
        "send",
        "--to",
        args.target,
        "--subject",
        f"[Podsum] {time.strftime('%Y-%m-%d')} {count} 篇合并文字稿 EPUB",
        message,
    ]
    if args.dry_run:
        log(f"would send: {path}")
        return

    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "hermes send failed").strip())
    log((result.stdout or "sent").strip())


def run(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    pending = pending_markdown_files(args, state)

    if not pending:
        log("Sent 0 transcript(s).")
        if args.cleanup:
            cleanup_files(args, state)
        return 0

    bundle_path, _infos = build_bundle(args, pending)
    epub_path = write_epub(bundle_path, f"Podsum {time.strftime('%Y-%m-%d')}")
    log(f"Wrote EPUB: {epub_path}")
    log(f"Sending merged transcript EPUB: {epub_path}")
    send_file(args, epub_path, len(pending), bundle_path)
    if not args.dry_run:
        for path, digest in pending:
            state["sent"][str(path)] = {
                "sha256": digest,
                "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "target": args.target,
                "bundle_path": str(bundle_path),
                "epub_path": str(epub_path),
            }
        save_state(args.state, state)

    log(f"Sent {len(pending)} transcript(s) in 1 merged EPUB bundle.")
    if args.cleanup:
        cleanup_files(args, state)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send Podsum Markdown reports to Discord.")
    parser.add_argument("--transcripts-root", type=Path, default=DEFAULT_TRANSCRIPTS_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--memory-file", type=Path, default=DEFAULT_MEMORY_FILE)
    parser.add_argument("--interpretation-prompt", type=Path, default=DEFAULT_INTERPRETATION_PROMPT)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--hermes", type=Path, default=DEFAULT_HERMES)
    parser.add_argument("--hermes-timeout", type=int, default=180)
    parser.add_argument("--force", action="store_true", help="Resend even if the file hash was already sent.")
    parser.add_argument("--cleanup", action="store_true", help="Delete old sent transcripts and audio after delivery.")
    parser.add_argument("--audio-retention-days", type=int, default=DEFAULT_AUDIO_RETENTION_DAYS, help=f"Delete audio this many days after its transcript has been sent. Use -1 to disable. Default: {DEFAULT_AUDIO_RETENTION_DAYS}.")
    parser.add_argument("--transcript-retention-days", type=int, default=DEFAULT_TRANSCRIPT_RETENTION_DAYS, help=f"Delete sent Markdown transcripts this many days after sending. Use -1 to disable. Default: {DEFAULT_TRANSCRIPT_RETENTION_DAYS}.")
    parser.add_argument("--untranscribed-audio-retention-days", type=int, default=DEFAULT_UNTRANSCRIBED_AUDIO_RETENTION_DAYS, help=f"Delete audio with no Markdown transcript after this many days. Use -1 to disable. Default: {DEFAULT_UNTRANSCRIBED_AUDIO_RETENTION_DAYS}.")
    parser.add_argument("--bundle-retention-days", type=int, default=DEFAULT_BUNDLE_RETENTION_DAYS, help=f"Delete merged Podsum reports after this many days. Use -1 to disable. Default: {DEFAULT_BUNDLE_RETENTION_DAYS}.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-run-interpret", action="store_true", help="Call Hermes for interpretations even in --dry-run mode.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.transcripts_root = args.transcripts_root.expanduser()
    args.state = args.state.expanduser()
    args.memory_file = args.memory_file.expanduser()
    args.interpretation_prompt = args.interpretation_prompt.expanduser()
    args.project_dir = args.project_dir.expanduser()
    args.hermes = args.hermes.expanduser()
    if args.audio_retention_days < -1:
        parser.error("--audio-retention-days must be >= -1")
    if args.transcript_retention_days < -1:
        parser.error("--transcript-retention-days must be >= -1")
    if args.untranscribed_audio_retention_days < -1:
        parser.error("--untranscribed-audio-retention-days must be >= -1")
    if args.bundle_retention_days < -1:
        parser.error("--bundle-retention-days must be >= -1")
    if args.hermes_timeout < 1:
        parser.error("--hermes-timeout must be >= 1")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
