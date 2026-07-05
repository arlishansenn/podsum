#!/usr/bin/env python3
"""Email scan and summary feature for Podsum."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import email
import imaplib
import json
import os
import re
import ssl
import time
from email.message import Message
from pathlib import Path
from typing import Any

import podsum_send_to_feishu as sender
from podsum_core.delivery import run_hermes_prompt, send_hermes_file


DEFAULT_OUTPUT_DIR = Path.home() / "Podcasts/AutoDownloads"
DEFAULT_ENV_FILE = Path.home() / "Library/Application Support/Podsum/.env"
DEFAULT_PROMPT = Path(__file__).with_name("email_summary_prompt.md")
DEFAULT_STATE_FILE = Path.home() / "Library/Application Support/Podsum/state.json"
DEFAULT_HERMES = sender.DEFAULT_HERMES
DEFAULT_TARGET = sender.DEFAULT_TARGET
DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_RECENT_DAYS = 1
DEFAULT_LIMIT = 300
DEFAULT_FIXTURE_ACCOUNT = "fixture@example.invalid"
SNIPPET_CHARS = 240


def log(message: str) -> None:
    print(message, flush=True)


def error_text(exc: BaseException) -> str:
    if exc.args and isinstance(exc.args[0], bytes):
        raw = exc.args[0]
        for encoding in ("utf-8", "gb18030", "gbk"):
            try:
                return raw.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
    return str(exc).strip()


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def today_string() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'").strip('"')
        values[key.strip()] = value
    return values


def config_value(env_file: dict[str, str], file_name: str, *env_names: str, default: str = "") -> str:
    for name in (file_name, *env_names):
        value = os.environ.get(name)
        if value:
            return value
    value = env_file.get(file_name)
    if value:
        return value
    return default


def parse_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def clean_text(value: str, limit: int = SNIPPET_CHARS) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
    except Exception:
        return value
    decoded: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def body_snippet(message: Message) -> str:
    candidates: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            if content_type not in {"text/plain", "text/html"}:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if content_type == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            candidates.append(text)
    else:
        payload = message.get_payload(decode=True)
        if payload:
            candidates.append(payload.decode(message.get_content_charset() or "utf-8", errors="replace"))
    return clean_text(" ".join(candidates))


def message_item(uid: str, raw_message: bytes) -> dict[str, Any]:
    msg = email.message_from_bytes(raw_message)
    fixture_uid = decode_header_value(msg.get("X-Podsum-Fixture-UID"))
    attachments = [
        part
        for part in msg.walk()
        if str(part.get("Content-Disposition") or "").lower().startswith("attachment")
    ]
    return {
        "uid": fixture_uid or uid,
        "date": decode_header_value(msg.get("Date")),
        "from": decode_header_value(msg.get("From")),
        "subject": clean_text(decode_header_value(msg.get("Subject")), 120),
        "snippet": body_snippet(msg),
        "has_attachments": bool(attachments),
        "flags": [],
    }


def scan_imap(args: argparse.Namespace) -> dict[str, Any]:
    env_file = load_env_file(args.env_file)
    host = args.imap_host or config_value(env_file, "PODSUM_EMAIL_IMAP_HOST", "IMAP_HOST", default=DEFAULT_IMAP_HOST)
    port = args.imap_port or int(config_value(env_file, "PODSUM_EMAIL_IMAP_PORT", "IMAP_PORT", default=str(DEFAULT_IMAP_PORT)))
    user = args.imap_user or config_value(env_file, "PODSUM_EMAIL_IMAP_USER", "IMAP_USER", "GMAIL_USER")
    password = args.imap_pass or config_value(env_file, "PODSUM_EMAIL_IMAP_PASS", "IMAP_PASS", "GMAIL_APP_PASSWORD")
    mailbox = args.mailbox or config_value(env_file, "PODSUM_EMAIL_IMAP_MAILBOX", "IMAP_MAILBOX", default=DEFAULT_MAILBOX)
    tls_verify = parse_bool(config_value(env_file, "PODSUM_EMAIL_IMAP_TLS_VERIFY", "IMAP_REJECT_UNAUTHORIZED", default="true"), True)

    if not user or not password:
        raise RuntimeError(
            "missing IMAP credentials: set PODSUM_EMAIL_IMAP_USER/"
            f"PODSUM_EMAIL_IMAP_PASS in {args.env_file}"
        )

    context = ssl.create_default_context()
    if not tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    since = (dt.datetime.now() - dt.timedelta(days=args.recent_days)).strftime("%d-%b-%Y")
    items: list[dict[str, Any]] = []
    imap = imaplib.IMAP4_SSL(host, port, ssl_context=context)
    try:
        imap.login(user, password)
        status, _ = imap.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError(f"failed to select mailbox: {mailbox}")
        status, data = imap.uid("SEARCH", None, "SINCE", since)
        if status != "OK":
            raise RuntimeError("IMAP search failed")
        uids = data[0].split() if data and data[0] else []
        selected = uids[-args.limit :]
        for uid_bytes in selected:
            uid = uid_bytes.decode("ascii", errors="replace")
            status, fetched = imap.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not fetched:
                continue
            for part in fetched:
                if isinstance(part, tuple) and len(part) >= 2:
                    items.append(message_item(uid, part[1]))
                    break
    finally:
        with contextlib.suppress(Exception):
            imap.logout()

    return scan_payload(user, args.recent_days, args.limit, len(uids), items)


def scan_eml_dir(args: argparse.Namespace) -> dict[str, Any]:
    if not args.eml_dir.is_dir():
        raise RuntimeError(f"--eml-dir is not a directory: {args.eml_dir}")

    files = sorted(path for path in args.eml_dir.iterdir() if path.suffix.lower() == ".eml")
    raw_count = len(files)
    selected = files[: args.limit]
    items = [message_item(str(index), path.read_bytes()) for index, path in enumerate(selected, 1)]
    return scan_payload(DEFAULT_FIXTURE_ACCOUNT, args.recent_days, args.limit, raw_count, items)


def scan_payload(account: str, recent_days: int, limit: int, raw_count: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "date": today_string(),
        "account": account,
        "window": f"{recent_days}d",
        "scan_limit": limit,
        "raw_count": raw_count,
        "possibly_truncated": raw_count >= limit,
        "items": items,
    }


def email_reports_dir(root: Path) -> Path:
    return root / "EmailReports"


def write_scan(root: Path, scan: dict[str, Any]) -> Path:
    directory = email_reports_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"email-scan-{scan['date']}.json"
    path.write_text(json.dumps(scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def fallback_report(scan: dict[str, Any], reason: str) -> str:
    lines = [
        f"# Podsum Email Summary {scan['date']}",
        "",
        f"生成时间: {now_stamp()}",
        f"账号: {scan.get('account', '')}",
        f"扫描窗口: {scan.get('window', '')}",
        f"原始邮件数: {scan.get('raw_count', 0)}",
        "",
        "## 总览",
        "",
        f"Hermes 摘要失败：{reason}",
        "",
        "## 来源索引",
        "",
    ]
    for item in scan.get("items", []):
        lines.append(
            f"- UID={item.get('uid')} | From={item.get('from')} | "
            f"Subject={item.get('subject')} | Date={item.get('date')} | "
            f"`email://{scan['date']}/{item.get('uid')}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_report(args: argparse.Namespace, scan: dict[str, Any]) -> str:
    scan_json = json.dumps(scan, ensure_ascii=False, indent=2)
    prompt = args.email_summary_prompt.read_text(encoding="utf-8").format(
        date=scan["date"],
        generated_at=now_stamp(),
        account=scan.get("account", ""),
        window=scan.get("window", ""),
        raw_count=scan.get("raw_count", 0),
        scan_date=scan["date"],
        scan_json=scan_json,
    )
    if args.dry_run:
        return fallback_report(scan, "dry-run: skipped Hermes summary")
    ok, value = run_hermes_prompt(str(args.hermes), prompt, cwd=str(args.project_dir), timeout=args.hermes_timeout)
    if not ok:
        return fallback_report(scan, value)
    return value or fallback_report(scan, "Hermes returned empty output")


def write_report(root: Path, scan: dict[str, Any], markdown: str) -> Path:
    directory = email_reports_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"email-summary-{scan['date']}.md"
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return path


def send_report(args: argparse.Namespace, report_path: Path, scan: dict[str, Any]) -> Path:
    epub_path = sender.write_epub(report_path, f"Podsum Email Summary {scan['date']}")
    message = (
        "[[as_document]]\n"
        f"Podsum Email Summary {scan['date']}\n\n"
        f"账号: {scan.get('account', '')}\n"
        f"扫描窗口: {scan.get('window', '')}\n"
        f"原始邮件数: {scan.get('raw_count', 0)}\n"
        f"源 Markdown: {report_path}\n"
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n\n"
        f"MEDIA:{epub_path}"
    )
    subject = f"[Podsum] {scan['date']} Email Summary"
    if args.dry_run:
        log(f"would send email summary: {epub_path}")
    else:
        log(send_hermes_file(str(args.hermes), args.target, subject, message))
    return epub_path


def run(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    if args.scan_file:
        scan = json.loads(args.scan_file.read_text(encoding="utf-8"))
    elif args.eml_dir:
        scan = scan_eml_dir(args)
    else:
        if not args.allow_imap_read:
            raise RuntimeError(
                "Reading Gmail/IMAP requires explicit confirmation. "
                "Re-run with --allow-imap-read after confirming this should access the mailbox."
            )
        scan = scan_imap(args)
    scan_path = write_scan(args.output, scan)
    report_path = write_report(args.output, scan, render_report(args, scan))
    epub_path = None if args.no_send else send_report(args, report_path, scan)
    return scan_path, report_path, epub_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan recent email and send a Podsum email summary.")
    add_args(parser)
    return parser


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--scan-file", type=Path)
    parser.add_argument("--eml-dir", type=Path)
    parser.add_argument("--imap-host", default="")
    parser.add_argument("--imap-port", type=int, default=0)
    parser.add_argument("--imap-user", default="")
    parser.add_argument("--imap-pass", default="")
    parser.add_argument("--mailbox", default="")
    parser.add_argument("--recent-days", type=int, default=DEFAULT_RECENT_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--email-summary-prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--hermes", type=Path, default=DEFAULT_HERMES)
    parser.add_argument("--hermes-timeout", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument(
        "--allow-imap-read",
        action="store_true",
        help="Explicitly allow reading the configured Gmail/IMAP mailbox. Not needed with --scan-file or --eml-dir.",
    )


def normalize_args(args: argparse.Namespace) -> None:
    args.output = args.output.expanduser()
    args.env_file = args.env_file.expanduser()
    args.email_summary_prompt = args.email_summary_prompt.expanduser()
    args.project_dir = args.project_dir.expanduser()
    args.hermes = args.hermes.expanduser()
    if args.scan_file:
        args.scan_file = args.scan_file.expanduser()
    if args.eml_dir:
        args.eml_dir = args.eml_dir.expanduser()
    if args.recent_days < 1:
        raise SystemExit("--recent-days must be >= 1")
    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    if args.hermes_timeout < 1:
        raise SystemExit("--hermes-timeout must be >= 1")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    normalize_args(args)
    try:
        scan_path, report_path, epub_path = run(args)
    except Exception as exc:
        log(f"Email summary failed: {error_text(exc)}")
        return 1
    log(f"Wrote email scan: {scan_path}")
    log(f"Wrote email summary: {report_path}")
    if epub_path:
        log(f"Wrote email summary EPUB: {epub_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
