#!/usr/bin/env python3
"""Create sanitized EML fixtures for Podsum email-summary tests."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import email
import email.utils
import imaplib
import re
import ssl
from email.message import EmailMessage, Message
from pathlib import Path

import podsum_email_summary as email_summary


CAPTURE_RECENT_DAYS = 7
CAPTURE_LIMIT = 20
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")
URL_RE = re.compile(r"https?://[^\s<>)\"']+")


def text_payloads(message: Message) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    if message.is_multipart():
        parts = message.walk()
    else:
        parts = [message]

    for part in parts:
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition or content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        values.append((content_type, payload.decode(part.get_content_charset() or "utf-8", errors="replace")))
    return values


def has_attachment(message: Message) -> bool:
    return any(str(part.get("Content-Disposition") or "").lower().startswith("attachment") for part in message.walk())


def placeholder_links(text: str) -> list[str]:
    links = URL_RE.findall(text)
    if not links:
        return ["https://example.invalid/item-1"]
    return [f"https://example.invalid/item-{index}" for index, _link in enumerate(links[:5], 1)]


def synthetic_text(kind: str, index: int, original: str) -> str:
    links = placeholder_links(original)
    target_len = max(120, min(len(original), 800))
    base = (
        f"Fixture {kind} message {index:03d}. "
        "This sanitized body preserves approximate length and link shape without real content. "
        + " ".join(links)
    )
    while len(base) < target_len:
        base += " Sanitized fixture sentence for parser coverage."
    return base[:target_len]


def synthetic_html(index: int, original: str) -> str:
    links = placeholder_links(original)
    items = "".join(f'<li><a href="{link}">Fixture link {i}</a></li>' for i, link in enumerate(links, 1))
    return (
        "<html><body>"
        f"<h1>Fixture HTML message {index:03d}</h1>"
        "<p>This sanitized HTML preserves structure without real content.</p>"
        f"<ul>{items}</ul>"
        "</body></html>"
    )


def sanitized_message(raw: bytes, index: int) -> EmailMessage:
    original = email.message_from_bytes(raw)
    texts = text_payloads(original)
    plain = next((text for kind, text in texts if kind == "text/plain"), "")
    html = next((text for kind, text in texts if kind == "text/html"), "")
    if not plain and html:
        plain = re.sub(r"<[^>]+>", " ", html)
    if not html and plain and "html" in str(original.get_content_type()).lower():
        html = plain

    message = EmailMessage()
    fixture_date = dt.datetime(2026, 7, 5, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))) + dt.timedelta(minutes=index)
    message["X-Podsum-Fixture-UID"] = str(index)
    message["From"] = f"Fixture Sender {index:03d} <sender-{index:03d}@example.invalid>"
    message["To"] = "Fixture Recipient <recipient@example.invalid>"
    message["Subject"] = f"Fixture Email {index:03d}"
    message["Date"] = email.utils.format_datetime(fixture_date)
    message["Message-ID"] = f"<fixture-{index:03d}@example.invalid>"

    message.set_content(synthetic_text("plain", index, plain or html))
    if html:
        message.add_alternative(synthetic_html(index, html), subtype="html")
    if has_attachment(original):
        message.add_attachment(
            b"redacted fixture attachment placeholder\n",
            maintype="application",
            subtype="octet-stream",
            filename=f"redacted-attachment-{index:03d}.bin",
        )
    return message


def assert_sanitized(path: Path) -> None:
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    leaks = EMAIL_RE.findall(raw_text)
    leaks = [value for value in leaks if not value.endswith("@example.invalid")]
    if leaks:
        raise RuntimeError(f"sanitized fixture still contains non-fixture email address: {path}")
    message = email.message_from_bytes(path.read_bytes())
    searchable = "\n".join(text for _kind, text in text_payloads(message))
    if PHONE_RE.search(searchable):
        raise RuntimeError(f"sanitized fixture may still contain phone-like data: {path}")


def write_sanitized(raw_messages: list[bytes], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, raw in enumerate(raw_messages, 1):
        message = sanitized_message(raw, index)
        path = output_dir / f"fixture-{index:03d}.eml"
        path.write_bytes(message.as_bytes())
        assert_sanitized(path)
        paths.append(path)
    return paths


def raw_messages_from_dir(input_dir: Path) -> list[bytes]:
    if not input_dir.is_dir():
        raise RuntimeError(f"--input-dir is not a directory: {input_dir}")
    return [path.read_bytes() for path in sorted(input_dir.iterdir()) if path.suffix.lower() == ".eml"]


def fetch_raw_messages(args: argparse.Namespace) -> list[bytes]:
    env_file = email_summary.load_env_file(args.env_file)
    host = args.imap_host or email_summary.config_value(env_file, "PODSUM_EMAIL_IMAP_HOST", "IMAP_HOST", default=email_summary.DEFAULT_IMAP_HOST)
    port = args.imap_port or int(email_summary.config_value(env_file, "PODSUM_EMAIL_IMAP_PORT", "IMAP_PORT", default=str(email_summary.DEFAULT_IMAP_PORT)))
    user = args.imap_user or email_summary.config_value(env_file, "PODSUM_EMAIL_IMAP_USER", "IMAP_USER", "GMAIL_USER")
    password = args.imap_pass or email_summary.config_value(env_file, "PODSUM_EMAIL_IMAP_PASS", "IMAP_PASS", "GMAIL_APP_PASSWORD")
    mailbox = args.mailbox or email_summary.config_value(env_file, "PODSUM_EMAIL_IMAP_MAILBOX", "IMAP_MAILBOX", default=email_summary.DEFAULT_MAILBOX)
    tls_verify = email_summary.parse_bool(email_summary.config_value(env_file, "PODSUM_EMAIL_IMAP_TLS_VERIFY", "IMAP_REJECT_UNAUTHORIZED", default="true"), True)
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
    raw_messages: list[bytes] = []
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
        for uid in uids[-args.limit :]:
            status, fetched = imap.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not fetched:
                continue
            for part in fetched:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw_messages.append(part[1])
                    break
    finally:
        with contextlib.suppress(Exception):
            imap.logout()
    return raw_messages


def redact(args: argparse.Namespace) -> int:
    paths = write_sanitized(raw_messages_from_dir(args.input_dir), args.output_dir)
    for path in paths:
        print(f"wrote sanitized fixture: {path}")
    return 0


def capture(args: argparse.Namespace) -> int:
    if not args.allow_imap_read:
        raise RuntimeError(
            "Capturing fixtures reads Gmail/IMAP. "
            "Re-run with --allow-imap-read after confirming this should access the mailbox."
        )
    paths = write_sanitized(fetch_raw_messages(args), args.output_dir)
    for path in paths:
        print(f"wrote sanitized fixture: {path}")
    print("Review sanitized fixtures manually before committing them.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create sanitized .eml fixtures for Podsum email-summary tests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    redact_parser = subparsers.add_parser("redact")
    redact_parser.add_argument("--input-dir", type=Path, required=True)
    redact_parser.add_argument("--output-dir", type=Path, required=True)
    redact_parser.set_defaults(func=redact)

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--output-dir", type=Path, required=True)
    capture_parser.add_argument("--env-file", type=Path, default=email_summary.DEFAULT_ENV_FILE)
    capture_parser.add_argument("--imap-host", default="")
    capture_parser.add_argument("--imap-port", type=int, default=0)
    capture_parser.add_argument("--imap-user", default="")
    capture_parser.add_argument("--imap-pass", default="")
    capture_parser.add_argument("--mailbox", default=email_summary.DEFAULT_MAILBOX)
    capture_parser.add_argument("--recent-days", type=int, default=CAPTURE_RECENT_DAYS)
    capture_parser.add_argument("--limit", type=int, default=CAPTURE_LIMIT)
    capture_parser.add_argument(
        "--allow-imap-read",
        action="store_true",
        help="Explicitly allow reading the configured Gmail/IMAP mailbox to create sanitized fixtures.",
    )
    capture_parser.set_defaults(func=capture)
    return parser


def normalize_args(args: argparse.Namespace) -> None:
    if hasattr(args, "input_dir"):
        args.input_dir = args.input_dir.expanduser()
    if hasattr(args, "output_dir"):
        args.output_dir = args.output_dir.expanduser()
    if hasattr(args, "env_file"):
        args.env_file = args.env_file.expanduser()
    if hasattr(args, "recent_days") and args.recent_days < 1:
        raise SystemExit("--recent-days must be >= 1")
    if hasattr(args, "limit") and args.limit < 1:
        raise SystemExit("--limit must be >= 1")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    normalize_args(args)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"Email fixture tool failed: {email_summary.error_text(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
