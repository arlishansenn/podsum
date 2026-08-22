#!/usr/bin/env python3
"""Email scan and summary feature for Podsum."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import email
import html as html_lib
import html.parser
import ipaddress
import imaplib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any, Iterator, NamedTuple

import podsum_runtime
import podsum_send_to_feishu as sender

_email_package_dir = str(Path(__file__).with_name("email"))
if hasattr(email, "__path__") and _email_package_dir not in email.__path__:
    email.__path__.insert(0, _email_package_dir)
from email import brief_agent, evidence_agent, graph as email_graph
from email.io import atomic_write_json
from email.need_store import empty_need_store
from email.schemas import EmailEvidencePack
from podsum_core.delivery import run_hermes_prompt, send_hermes_file, send_smtp_email
from podsum_core.epub_converter.markdown_processor import MarkdownProcessor


DEFAULT_OUTPUT_DIR = Path.home() / "Podcasts/AutoDownloads"
DEFAULT_ENV_FILE = podsum_runtime.default_env_file()
DEFAULT_PROMPT = Path(__file__).with_name("email_summary_prompt.md")
DEFAULT_EVIDENCE_PREPROCESS_PROMPT = Path(__file__).with_name("email_evidence_preprocess_prompt.md")
DEFAULT_LINK_POLICY = Path(__file__).with_name("email_link_policy.md")
DEFAULT_TOPIC_FILE = Path(__file__).with_name("topic.md")
DEFAULT_STATE_FILE = podsum_runtime.podsum_home() / "state.json"
DEFAULT_HERMES = sender.DEFAULT_HERMES
DEFAULT_TARGET = sender.DEFAULT_TARGET
DEFAULT_DELIVERY = "hermes"
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_TIMEOUT = 30
load_env_file = podsum_runtime.load_env_file
config_value = podsum_runtime.config_value
parse_bool = podsum_runtime.parse_bool
DEFAULT_SUMMARY_ENGINE = "podsum"
DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_RECENT_DAYS = 1
DEFAULT_LIMIT = 300
DEFAULT_FIXTURE_ACCOUNT = "fixture@example.invalid"
SELF_SUBJECT_PREFIX = "[Podsum]"
EVIDENCE_PACK_VERSION = "0.1"
INTEL_BRIEF_VERSION = "0.1"
SNIPPET_CHARS = 240
LINK_EXCERPT_CHARS = 1200
FETCH_BODY_CHARS = 4000
MAILPARSER_CLEANER_TIMEOUT_SECONDS = 15
MAILPARSER_SNIPPET_CHARS = 900
USER_AGENT = "PodsumEmailSummary/1.0"
URL_RE = re.compile(r"https?://[^\s<>)\"']+", re.IGNORECASE)
BODY_BLOCK_TAG_RE = re.compile(r"</(?:p|div|li|tr|td|h[1-6])\s*>|<br\s*/?>", re.IGNORECASE)
BODY_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)
LOW_SIGNAL_BODY_HINTS = (
    "view this email in your browser",
    "view in browser",
    "unsubscribe",
    "manage preferences",
    "manage your preferences",
    "privacy policy",
    "terms of use",
    "all rights reserved",
    "you received this email",
    "this email was sent to",
    "follow us",
    "advertisement",
    "sponsored",
    "match your following keywords",
    "new articles that match",
)

DEFAULT_POLICY: dict[str, Any] = {
    "object_type": "email_policy",
    "version": 1,
    "limits": {
        "max_links_per_email": 2,
        "max_links_total": 10,
        "timeout_seconds": 8,
        "excerpt_chars": LINK_EXCERPT_CHARS,
    },
    "skip_url_patterns": [
        "unsubscribe",
        "optout",
        "tracking",
        "track",
        "pixel",
        "login",
        "signin",
        "calendar",
        ".ics",
        "attachment",
        "download",
    ],
    "email_types": [
        {
            "name": "google_alert",
            "fetch_links": True,
            "match": {
                "subject_contains": ["google快讯", "google alert", "alert"],
                "from_contains": ["alerts"],
            },
            "summary_focus": "提炼 alert 中真正值得知道的新线索。",
        },
        {
            "name": "newsletter_article",
            "fetch_links": True,
            "match": {
                "subject_contains": ["newsletter", "digest", "weekly", "日报", "周报"],
                "snippet_contains": ["read more", "source:", "https://"],
            },
            "summary_focus": "优先读取公开文章链接，判断是否值得行动或记录。",
        },
        {
            "name": "digest",
            "fetch_links": True,
            "match": {
                "subject_contains": ["digest", "roundup", "汇总", "精选"],
            },
            "summary_focus": "从多条链接里识别最高价值条目。",
        },
        {
            "name": "personal",
            "fetch_links": False,
            "match": {
                "subject_contains": ["follow-up", "re:", "回复"],
                "snippet_contains": ["follow-up", "decision", "meeting"],
            },
            "summary_focus": "关注是否需要回复或做决定。",
        },
        {
            "name": "transactional",
            "fetch_links": False,
            "match": {
                "subject_contains": ["receipt", "invoice", "security", "验证", "账单", "登录"],
            },
            "summary_focus": "只提炼账号、账单、安全和到期风险。",
        },
        {
            "name": "marketing_low_signal",
            "fetch_links": False,
            "match": {
                "subject_contains": ["sale", "discount", "promo", "优惠", "促销"],
            },
            "summary_focus": "默认低信号，除非 snippet 显示明确行动价值。",
        },
    ],
}

# 自己没有内容、内容就是那张链接列表的三类。prompt 里的「逐条展开」规则按类型名点名，
# 改了这里就必须改 prompt，否则那条规则会静默失效。
DIGEST_EMAIL_TYPES = ("google_alert", "newsletter_article", "digest")

DEFAULT_TOPIC_MAP: dict[str, Any] = {
    "object_type": "email_topic_map",
    "version": 1,
    "topics": [],
    "default_behavior": "未命中 topic.md 的邮件只做低优先级补充，除非存在明确行动信号。",
}


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


def split_recipients(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\s]+", value or "") if item.strip()]


def infer_smtp_host(imap_host: str, user: str = "") -> str:
    host = (imap_host or "").strip()
    if host.startswith("imap."):
        return "smtp." + host[len("imap.") :]
    if "imap" in host:
        return host.replace("imap", "smtp", 1)
    domain = user.rsplit("@", 1)[-1] if "@" in user else ""
    if domain:
        return f"smtp.{domain}"
    return ""


def smtp_config(args: argparse.Namespace) -> dict[str, Any]:
    env_file = load_env_file(args.env_file)
    imap_host = args.imap_host or config_value(env_file, "PODSUM_EMAIL_IMAP_HOST", "IMAP_HOST", default=DEFAULT_IMAP_HOST)
    imap_user = args.imap_user or config_value(env_file, "PODSUM_EMAIL_IMAP_USER", "IMAP_USER", "GMAIL_USER")
    imap_pass = args.imap_pass or config_value(env_file, "PODSUM_EMAIL_IMAP_PASS", "IMAP_PASS", "GMAIL_APP_PASSWORD")
    smtp_user = args.smtp_user or config_value(env_file, "PODSUM_EMAIL_SMTP_USER", "SMTP_USER", default=imap_user)
    smtp_pass = args.smtp_pass or config_value(env_file, "PODSUM_EMAIL_SMTP_PASS", "SMTP_PASS", default=imap_pass)
    smtp_host = args.smtp_host or config_value(
        env_file,
        "PODSUM_EMAIL_SMTP_HOST",
        "SMTP_HOST",
        default=infer_smtp_host(imap_host, smtp_user or imap_user),
    )
    smtp_port = args.smtp_port or int(config_value(env_file, "PODSUM_EMAIL_SMTP_PORT", "SMTP_PORT", default=str(DEFAULT_SMTP_PORT)))
    mail_from = args.smtp_from or config_value(env_file, "PODSUM_EMAIL_SMTP_FROM", "SMTP_FROM", default=smtp_user or imap_user)
    recipient_value = args.smtp_to or config_value(env_file, "PODSUM_EMAIL_SMTP_TO", "EMAIL_TO", "SMTP_TO", default=smtp_user or imap_user)
    starttls = args.smtp_starttls or parse_bool(config_value(env_file, "PODSUM_EMAIL_SMTP_STARTTLS", "SMTP_STARTTLS", default="false"), False)
    use_ssl = (
        not starttls
        and not args.smtp_no_ssl
        and parse_bool(config_value(env_file, "PODSUM_EMAIL_SMTP_SSL", "SMTP_SSL", default="true"), True)
    )
    tls_verify = not args.smtp_no_tls_verify and parse_bool(config_value(env_file, "PODSUM_EMAIL_SMTP_TLS_VERIFY", "SMTP_TLS_VERIFY", default="true"), True)
    timeout = args.smtp_timeout or int(config_value(env_file, "PODSUM_EMAIL_SMTP_TIMEOUT", "SMTP_TIMEOUT", default=str(DEFAULT_SMTP_TIMEOUT)))
    return {
        "host": smtp_host,
        "port": smtp_port,
        "username": smtp_user,
        "password": smtp_pass,
        "mail_from": mail_from,
        "recipients": split_recipients(recipient_value),
        "use_ssl": use_ssl,
        "starttls": starttls,
        "tls_verify": tls_verify,
        "timeout": timeout,
    }


def email_html_body(markdown_text: str, scan: dict[str, Any], report_path: Path) -> str:
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S %z")
    title = f"Podsum Email Brief {scan.get('date', '')}".strip()
    account = html_lib.escape(str(scan.get("account", "")))
    window = html_lib.escape(str(scan.get("window", "")))
    raw_count = html_lib.escape(str(scan.get("raw_count", 0)))
    source = html_lib.escape(str(report_path))
    body_html = MarkdownProcessor.markdown_to_html(markdown_text)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ margin: 0; padding: 0; background: #f6f7f9; color: #1f2933; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.55; }}
    .wrap {{ max-width: 760px; margin: 0 auto; padding: 24px 18px 40px; background: #ffffff; }}
    .meta {{ margin: 0 0 20px; color: #5f6c7b; font-size: 13px; }}
    h1, h2, h3 {{ color: #111827; line-height: 1.25; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    h2 {{ font-size: 19px; margin: 26px 0 10px; border-top: 1px solid #e5e7eb; padding-top: 18px; }}
    h3 {{ font-size: 16px; margin: 18px 0 8px; }}
    p {{ margin: 0 0 12px; }}
    ul, ol {{ padding-left: 22px; }}
    li {{ margin: 6px 0; }}
    a {{ color: #0f766e; }}
    code {{ background: #f3f4f6; padding: 1px 4px; border-radius: 4px; }}
    blockquote {{ margin: 14px 0; padding: 10px 14px; border-left: 3px solid #cbd5e1; color: #475569; background: #f8fafc; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{html_lib.escape(title)}</h1>
    <p class="meta">账号: {account} · 扫描窗口: {window} · 原始邮件数: {raw_count} · 生成时间: {html_lib.escape(generated_at)}<br>源 Markdown: {source}</p>
    {body_html}
  </div>
</body>
</html>
"""


def clean_text(value: str, limit: int = SNIPPET_CHARS) -> str:
    value = remove_format_controls(value, " ")
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def clean_mailparser_snippet(value: str) -> str:
    value = remove_format_controls(value, " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return clean_text("\n".join(line for line in lines if line), MAILPARSER_SNIPPET_CHARS)


def remove_format_controls(value: str, replacement: str) -> str:
    return "".join(replacement if unicodedata.category(char) in {"Cf", "Cs"} else char for char in value)


def strip_html(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value))


def body_text_from_part(part: dict[str, Any]) -> str:
    text = str(part.get("text") or "")
    if part.get("content_type") == "text/html":
        text = BODY_BLOCK_TAG_RE.sub("\n", text)
        text = strip_html(text)
    return text


def normalize_body_text_for_blocks(value: str) -> str:
    value = remove_format_controls(html.unescape(value), "\n")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line)


def body_text_blocks(value: str) -> list[str]:
    normalized = normalize_body_text_for_blocks(value)
    return [clean_text(block, 1000) for block in re.split(r"\n+", normalized) if clean_text(block, 1000)]


def body_block_tokens(value: str) -> list[str]:
    return BODY_TOKEN_RE.findall(value.lower())


def is_low_signal_body_block(value: str) -> bool:
    text = clean_text(value, 500)
    if not text:
        return True
    tokens = body_block_tokens(text)
    lowered = text.lower()
    if len(tokens) < 4 and len(text) < 32:
        return True
    if any(hint in lowered for hint in LOW_SIGNAL_BODY_HINTS) and len(tokens) < 28:
        return True
    url_chars = sum(len(match.group(0)) for match in URL_RE.finditer(text))
    non_url_tokens = body_block_tokens(URL_RE.sub("", text))
    return bool(url_chars and url_chars / max(len(text), 1) > 0.75 and not non_url_tokens)


def select_body_excerpt(parts: list[dict[str, Any]], limit: int = SNIPPET_CHARS) -> str:
    preferred = [part for part in parts if part.get("content_type") == "text/plain"] or [
        part for part in parts if part.get("content_type") == "text/html"
    ]
    blocks: list[str] = []
    for part in preferred:
        blocks.extend(body_text_blocks(body_text_from_part(part)))
    for block in blocks:
        if not is_low_signal_body_block(block):
            return clean_text(block, limit)
    return ""


def normalize_url(value: str) -> str:
    value = remove_format_controls(value, "")
    value = value.strip().rstrip(".,;:)]}'\"")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


class LinkHTMLParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._active_href = ""
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        href = normalize_url(attrs_map.get("href", ""))
        if href:
            self._active_href = href
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return
        self.links.append(
            {
                "url": self._active_href,
                "anchor_text": clean_text(" ".join(self._active_text), 120),
                "context": clean_text(" ".join(self._active_text), 240),
                "source_content_type": "text/html",
                "position": str(len(self.links)),
            }
        )
        self._active_href = ""
        self._active_text = []


def unique_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for link in links:
        url = normalize_url(str(link.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        unique_link = {
            "url": url,
            "normalized_url": url,
            "anchor_text": clean_text(str(link.get("anchor_text") or ""), 120),
            "context": clean_text(str(link.get("context") or link.get("anchor_text") or ""), 240),
            "source_content_type": clean_text(str(link.get("source_content_type") or ""), 80),
            "position": str(link.get("position") or len(unique)),
            "policy_decision": str(link.get("policy_decision") or "pending"),
        }
        unique.append(unique_link)
    return unique


def text_context(value: str, start: int, end: int, radius: int = 90) -> str:
    left = max(0, start - radius)
    right = min(len(value), end + radius)
    return clean_text(value[left:right], 240)


def extract_links_from_text(text: str, content_type: str = "text/plain") -> list[dict[str, str]]:
    return [
        {
            "url": match.group(0),
            "anchor_text": "",
            "context": text_context(text, match.start(), match.end()),
            "source_content_type": content_type,
            "position": str(index),
        }
        for index, match in enumerate(URL_RE.finditer(text))
    ]


def extract_links_from_html(text: str) -> list[dict[str, str]]:
    parser = LinkHTMLParser()
    with contextlib.suppress(Exception):
        parser.feed(text)
    links = list(parser.links)
    links.extend(extract_links_from_text(text, "text/html"))
    return links


def message_body_parts(message: Message) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    message_parts = message.walk() if message.is_multipart() else [message]
    for index, part in enumerate(message_parts):
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition or content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset()
        text = decode_bytes(payload, charset)
        parts.append(
            {
                "index": index,
                "content_type": content_type,
                "charset": charset or "",
                "text": text,
                "char_count": len(text),
            }
        )
    return parts


def message_texts(message: Message) -> list[tuple[str, str]]:
    return [(str(part["content_type"]), str(part["text"])) for part in message_body_parts(message)]


def extract_message_links(message: Message) -> list[dict[str, str]]:
    return extract_links_from_body_parts(message_body_parts(message))


def extract_links_from_body_parts(parts: list[dict[str, Any]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for part in parts:
        content_type = str(part.get("content_type") or "")
        text = str(part.get("text") or "")
        if content_type == "text/html":
            links.extend(extract_links_from_html(text))
        else:
            links.extend(extract_links_from_text(text, content_type or "text/plain"))
    return unique_links(links)


def mailparser_node_module_paths() -> list[Path]:
    paths: list[Path] = []
    configured = os.environ.get("PODSUM_EMAIL_MAILPARSER_NODE_PATH", "")
    for raw_path in configured.split(os.pathsep):
        if raw_path.strip():
            paths.append(Path(raw_path).expanduser())
    paths.append(Path(__file__).with_name("node_modules"))
    return [path for path in paths if path.is_dir()]


def mailparser_cleaner_env() -> dict[str, str]:
    node_module_paths = mailparser_node_module_paths()
    if not node_module_paths:
        raise RuntimeError(
            "mailparser helper dependencies are missing. Run `npm install --omit=dev` "
            "in the deployed outputs directory, or set PODSUM_EMAIL_MAILPARSER_NODE_PATH "
            "to a node_modules directory containing mailparser and jsdom."
        )
    env = os.environ.copy()
    existing = [entry for entry in env.get("NODE_PATH", "").split(os.pathsep) if entry]
    env["NODE_PATH"] = os.pathsep.join([str(path) for path in node_module_paths] + existing)
    return env


def validate_mailparser_cleaned_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("mailparser helper returned a non-object JSON payload")
    snippet = clean_mailparser_snippet(str(value.get("snippet") or ""))
    links = value.get("links", [])
    if not isinstance(links, list):
        links = []
    clean_links = unique_links([link for link in links if isinstance(link, dict)])
    body_part_types = value.get("body_part_types", [])
    if not isinstance(body_part_types, list):
        body_part_types = []
    body_part_types = sorted({clean_text(str(item), 80) for item in body_part_types if str(item).strip()})
    attachment_shapes = value.get("attachment_shapes", [])
    if not isinstance(attachment_shapes, list):
        attachment_shapes = []
    clean_attachment_shapes: list[dict[str, Any]] = []
    for shape in attachment_shapes:
        if not isinstance(shape, dict):
            continue
        clean_attachment_shapes.append(
            {
                "content_type": clean_text(str(shape.get("content_type") or "application/octet-stream"), 120),
                "size_bytes": int(shape.get("size_bytes") or 0),
            }
        )
    return {
        "snippet": snippet,
        "links": clean_links,
        "body_part_count": int(value.get("body_part_count") or len(body_part_types)),
        "body_part_types": body_part_types,
        "attachment_count": int(value.get("attachment_count") or len(clean_attachment_shapes)),
        "attachment_shapes": clean_attachment_shapes,
    }


def mailparser_cleaned_message(raw_message: bytes) -> dict[str, Any]:
    node = os.environ.get("PODSUM_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError("mailparser helper requires Node.js, but no node executable was found")
    helper = Path(__file__).with_name("email_mailparser_cleaner.js")
    if not helper.exists():
        raise RuntimeError(f"mailparser helper script is missing: {helper}")
    env = mailparser_cleaner_env()
    try:
        completed = subprocess.run(
            [node, str(helper)],
            input=raw_message,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=MAILPARSER_CLEANER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"mailparser helper timed out after {MAILPARSER_CLEANER_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise RuntimeError(f"failed to run mailparser helper: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"mailparser helper failed with exit code {completed.returncode}: {clean_text(stderr, 300)}")
    try:
        parsed = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("mailparser helper returned invalid JSON") from exc
    return validate_mailparser_cleaned_payload(parsed)


def parse_policy_json(markdown: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", markdown, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("email policy must contain a fenced json object")
    parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        raise ValueError("email policy json must be an object")
    return parsed


def parse_topic_json(markdown: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", markdown, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("topic map must contain a fenced json object")
    parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        raise ValueError("topic map json must be an object")
    topics = parsed.get("topics", [])
    if not isinstance(topics, list):
        raise ValueError("topic map json field topics must be a list")
    return parsed


def load_link_policy(path: Path) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_POLICY))
    try:
        policy = parse_policy_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Email policy failed to load; using safe default policy: {exc}")
        return json.loads(json.dumps(DEFAULT_POLICY))
    default_policy = json.loads(json.dumps(DEFAULT_POLICY))
    default_policy.update(policy)
    default_policy.setdefault("limits", {}).update(policy.get("limits", {}))
    default_policy.setdefault("skip_url_patterns", policy.get("skip_url_patterns", DEFAULT_POLICY["skip_url_patterns"]))
    default_policy.setdefault("email_types", policy.get("email_types", DEFAULT_POLICY["email_types"]))
    return default_policy


def load_topic_map(path: Path) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_TOPIC_MAP))
    try:
        topic_map = parse_topic_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Email topic map failed to load; using empty topic map: {exc}")
        return json.loads(json.dumps(DEFAULT_TOPIC_MAP))
    default_topic_map = json.loads(json.dumps(DEFAULT_TOPIC_MAP))
    default_topic_map.update(topic_map)
    default_topic_map.setdefault("topics", topic_map.get("topics", []))
    return default_topic_map


def match_values(haystack: str, needles: list[str]) -> bool:
    haystack = haystack.lower()
    return any(needle.lower() in haystack for needle in needles)


def classify_email(item: dict[str, Any], policy: dict[str, Any]) -> str:
    subject = str(item.get("subject") or "")
    sender = str(item.get("from") or "")
    snippet = str(item.get("snippet") or "")
    for entry in policy.get("email_types", []):
        if not isinstance(entry, dict):
            continue
        match = entry.get("match") if isinstance(entry.get("match"), dict) else {}
        checks = [
            bool(match.get("subject_contains")) and match_values(subject, list(match.get("subject_contains", []))),
            bool(match.get("from_contains")) and match_values(sender, list(match.get("from_contains", []))),
            bool(match.get("snippet_contains")) and match_values(snippet, list(match.get("snippet_contains", []))),
        ]
        if any(checks):
            return str(entry.get("name") or "unknown")
    return "unknown"


def policy_for_type(email_type: str, policy: dict[str, Any]) -> dict[str, Any]:
    for entry in policy.get("email_types", []):
        if isinstance(entry, dict) and entry.get("name") == email_type:
            return entry
    return {"name": "unknown", "fetch_links": False}


def topic_priority_value(topic: dict[str, Any]) -> int:
    priority = str(topic.get("priority") or "normal").lower()
    return {"high": 0, "medium": 1, "normal": 1, "low": 2}.get(priority, 1)


def topic_keywords(topic: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("keywords", "aliases"):
        raw = topic.get(key, [])
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item).strip())
    return values


def item_topic_text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("from") or ""),
        str(item.get("subject") or ""),
        str(item.get("snippet") or ""),
        str(item.get("email_type") or ""),
    ]
    for link in item.get("links", []):
        if not isinstance(link, dict):
            continue
        parts.extend([str(link.get("anchor_text") or ""), str(link.get("context") or ""), str(link.get("url") or "")])
    for evidence in item.get("evidence", []):
        if not isinstance(evidence, dict):
            continue
        parts.extend([str(evidence.get("title") or ""), str(evidence.get("excerpt") or ""), str(evidence.get("email_context") or "")])
    return "\n".join(parts).lower()


WEAK_TOPIC_KEYWORDS = {
    "ai",
    "nb",
    "vis",
    "gui",
    "workflow",
    "power",
    "community",
    "github",
    "meeting",
    "ops",
    "qq",
    "ppt",
    "prd",
    "epub",
    "gmail",
    "imap",
    "security",
    "follow-up",
    "选择",
    "回复",
    "组织",
    "关系",
    "决策",
    "验证",
    "会议",
}


def item_subject_sender(item: dict[str, Any]) -> str:
    return clean_text(f"{item.get('from') or ''} {item.get('subject') or ''}", 1000).lower()


def keyword_in_text(keyword: str, text: str) -> bool:
    """ASCII token 按词边界匹配，避免 ai 命中 gmail、email。"""
    key = keyword.strip().lower()
    if not key:
        return False
    haystack = text.lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9+._-]*", key):
        return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack) is not None
    return key in haystack


def keyword_is_meaningful(keyword: str) -> bool:
    normalized = keyword.strip().lower()
    if not normalized or normalized in WEAK_TOPIC_KEYWORDS:
        return False
    if re.fullmatch(r"[a-z]{1,3}", normalized):
        return False
    return True


def keyword_matches_item(keyword: str, haystack: str, subject_sender: str) -> bool:
    if not keyword_in_text(keyword, haystack):
        return False
    if keyword_is_meaningful(keyword):
        return True
    return keyword_in_text(keyword, subject_sender) and len(keyword.strip()) >= 2


def match_item_topics(item: dict[str, Any], topic_map: dict[str, Any]) -> list[dict[str, Any]]:
    haystack = item_topic_text(item)
    subject_sender = item_subject_sender(item)
    matches: list[dict[str, Any]] = []
    for index, topic in enumerate(topic_map.get("topics", [])):
        if not isinstance(topic, dict):
            continue
        keywords = topic_keywords(topic)
        matched = [keyword for keyword in keywords if keyword_matches_item(keyword, haystack, subject_sender)]
        if not matched:
            continue
        topic_id = str(topic.get("id") or topic.get("name") or f"topic_{index + 1}")
        matches.append(
            {
                "id": topic_id,
                "name": str(topic.get("name") or topic_id),
                "priority": str(topic.get("priority") or "normal"),
                "description": str(topic.get("description") or ""),
                "summary_focus": str(topic.get("summary_focus") or ""),
                "examples": [str(item) for item in topic.get("examples", []) if str(item).strip()]
                if isinstance(topic.get("examples"), list)
                else [],
                "matched_keywords": matched[:8],
                "order": index,
            }
        )
    return sorted(matches, key=lambda value: (topic_priority_value(value), int(value.get("order", 0)), value.get("name", "")))


def apply_topics(scan: dict[str, Any], topic_map: dict[str, Any]) -> dict[str, Any]:
    topic_map_view = {
        "object_type": topic_map.get("object_type", "email_topic_map"),
        "version": topic_map.get("version", 1),
        "topic_count": len([topic for topic in topic_map.get("topics", []) if isinstance(topic, dict)]),
        "default_behavior": topic_map.get("default_behavior", DEFAULT_TOPIC_MAP["default_behavior"]),
    }
    scan["topic_map"] = topic_map_view
    hits: dict[str, dict[str, Any]] = {}
    for item in scan.get("items", []):
        if not isinstance(item, dict):
            continue
        topics = match_item_topics(item, topic_map)
        item["topics"] = topics
        for topic in topics:
            topic_id = str(topic.get("id") or "")
            if not topic_id:
                continue
            hit = hits.setdefault(
                topic_id,
                {
                    "id": topic_id,
                    "name": topic.get("name", topic_id),
                    "priority": topic.get("priority", "normal"),
                    "description": topic.get("description", ""),
                    "summary_focus": topic.get("summary_focus", ""),
                    "item_uids": [],
                    "matched_keywords": [],
                },
            )
            hit["item_uids"].append(str(item.get("uid") or ""))
            hit["matched_keywords"] = sorted(set(hit["matched_keywords"]) | set(topic.get("matched_keywords", [])))
    scan["topic_hits"] = sorted(hits.values(), key=lambda value: (topic_priority_value(value), value.get("name", "")))
    return scan


def decode_bytes(value: bytes, charset: str | None = None) -> str:
    encodings = [charset or "", "utf-8", "gb18030", "gbk", "latin-1"]
    seen: set[str] = set()
    for encoding in encodings:
        encoding = encoding.strip().lower()
        if not encoding or encoding in seen or encoding == "unknown-8bit":
            continue
        seen.add(encoding)
        try:
            return value.decode(encoding, errors="replace")
        except LookupError:
            continue
    return value.decode("utf-8", errors="replace")


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
            decoded.append(decode_bytes(part, charset))
        else:
            decoded.append(part)
    return "".join(decoded)


def body_snippet(message: Message) -> str:
    return body_snippet_from_parts(message_body_parts(message))


def body_snippet_from_parts(parts: list[dict[str, Any]]) -> str:
    return select_body_excerpt(parts)


def attachment_shapes(message: Message) -> list[dict[str, Any]]:
    shapes: list[dict[str, Any]] = []
    for part in message.walk():
        disposition = str(part.get("Content-Disposition") or "").lower()
        if "attachment" not in disposition:
            continue
        payload = part.get_payload(decode=True) or b""
        shapes.append(
            {
                "content_type": part.get_content_type(),
                "size_bytes": len(payload),
            }
        )
    return shapes


def email_snippet_evidence(item: dict[str, Any]) -> dict[str, Any]:
    snippet = str(item.get("snippet") or "")
    if snippet:
        excerpt = snippet
        reason = "snippet_only"
        status = "available"
    else:
        excerpt = clean_text(
            " | ".join(
                value
                for value in [
                    f"From={item.get('from', '')}",
                    f"Subject={item.get('subject', '')}",
                    f"Date={item.get('date', '')}",
                ]
                if value and not value.endswith("=")
            )
        )
        reason = "metadata_only"
        status = "available" if excerpt else "missing"
    return {
        "type": "email_snippet",
        "source": "email",
        "uid": str(item.get("uid") or ""),
        "url": "",
        "final_url": "",
        "title": str(item.get("subject") or ""),
        "excerpt": excerpt,
        "status": status,
        "reason": reason,
        "content_type": "message/rfc822",
        "source_fields": ["from", "subject", "date", "snippet"],
        "link_count": len(item.get("links", []) if isinstance(item.get("links"), list) else []),
        "has_attachments": bool(item.get("has_attachments")),
        "attachment_count": int(item.get("attachment_count") or 0),
        "body_part_count": int(item.get("body_part_count") or 0),
        "body_part_types": list(item.get("body_part_types", [])) if isinstance(item.get("body_part_types"), list) else [],
    }


def has_email_snippet_evidence(item: dict[str, Any]) -> bool:
    return any(
        isinstance(evidence, dict) and evidence.get("type") == "email_snippet"
        for evidence in item.get("evidence", [])
    )


def has_link_evidence(item: dict[str, Any]) -> bool:
    return any(
        isinstance(evidence, dict) and evidence.get("type") == "public_link"
        for evidence in item.get("evidence", [])
    )


def normalize_existing_evidence(item: dict[str, Any]) -> None:
    evidence_items = item.get("evidence", [])
    if not isinstance(evidence_items, list):
        item["evidence"] = []
        return
    normalized: list[dict[str, Any]] = []
    for evidence in evidence_items:
        if not isinstance(evidence, dict):
            continue
        evidence.setdefault("uid", str(item.get("uid") or ""))
        if not evidence.get("type"):
            if evidence.get("source") == "email":
                evidence["type"] = "email_snippet"
            elif evidence.get("url") or evidence.get("status") in {"fetched", "failed", "skipped"}:
                evidence["type"] = "public_link"
        if evidence.get("type") == "email_snippet":
            evidence.setdefault("source", "email")
        elif evidence.get("type") == "public_link":
            evidence.setdefault("source", "link")
            clean_url = public_output_url(str(evidence.get("url") or ""))
            clean_final_url = public_output_url(str(evidence.get("final_url") or evidence.get("url") or ""))
            if clean_url:
                evidence["url"] = clean_url
            if clean_final_url:
                evidence["final_url"] = clean_final_url
        if evidence.get("type") == "public_link" and evidence.get("status") == "skipped":
            continue
        normalized.append(evidence)
    item["evidence"] = normalized


def refresh_email_snippet_evidence(item: dict[str, Any]) -> None:
    defaults = email_snippet_evidence(item)
    for evidence in item.get("evidence", []):
        if not isinstance(evidence, dict) or evidence.get("type") != "email_snippet":
            continue
        for key, value in defaults.items():
            evidence[key] = value


def ensure_email_snippet_evidence(item: dict[str, Any]) -> None:
    item.setdefault("evidence", [])
    if not isinstance(item["evidence"], list):
        item["evidence"] = []
    normalize_existing_evidence(item)
    if not has_email_snippet_evidence(item):
        item["evidence"].insert(0, email_snippet_evidence(item))
    else:
        refresh_email_snippet_evidence(item)
    risks = set(item.get("risks", []))
    risks.discard("snippet_only")
    risks.discard("metadata_only")
    if not any(isinstance(evidence, dict) and evidence.get("status") == "fetched" for evidence in item["evidence"]):
        risks.add("snippet_only" if item.get("snippet") else "metadata_only")
    item["risks"] = sorted(risks)


def message_item(uid: str, raw_message: bytes, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    msg = email.message_from_bytes(raw_message)
    fixture_uid = decode_header_value(msg.get("X-Podsum-Fixture-UID"))
    cleaned = mailparser_cleaned_message(raw_message)
    snippet = str(cleaned.get("snippet") or "")
    links = cleaned.get("links", [])
    attachments = cleaned.get("attachment_shapes", [])
    attachment_count = int(cleaned.get("attachment_count") or len(attachments))
    body_part_types = cleaned.get("body_part_types", [])
    body_part_count = int(cleaned.get("body_part_count") or len(body_part_types))
    item = {
        "uid": fixture_uid or uid,
        "date": decode_header_value(msg.get("Date")),
        "from": decode_header_value(msg.get("From")),
        "subject": clean_text(decode_header_value(msg.get("Subject")), 120),
        "snippet": snippet,
        "has_attachments": bool(attachments),
        "attachment_count": attachment_count,
        "attachment_shapes": attachments,
        "body_part_count": body_part_count,
        "body_part_types": body_part_types,
        "links": links,
        "evidence": [],
        "risks": [],
        "flags": [],
    }
    ensure_email_snippet_evidence(item)
    item["email_type"] = classify_email(item, policy or DEFAULT_POLICY)
    return item


def is_private_hostname(hostname: str) -> bool:
    if not hostname:
        return True
    lowered = hostname.lower()
    if lowered in {"localhost"} or lowered.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(lowered)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    for _family, _type, _proto, _canon, sockaddr in addresses:
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def skip_reason_for_url(url: str, policy: dict[str, Any]) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return "unsupported_scheme"
    if not parsed.hostname:
        return "missing_hostname"
    lowered = url.lower()
    for pattern in policy.get("skip_url_patterns", []):
        if str(pattern).lower() in lowered:
            return f"skip_pattern:{pattern}"
    if is_private_hostname(parsed.hostname):
        return "private_or_local_host"
    return ""


def html_title_and_text(raw: bytes, charset: str | None = None) -> tuple[str, str]:
    text = decode_bytes(raw[:FETCH_BODY_CHARS], charset)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = clean_text(strip_html(title_match.group(1)), 200) if title_match else ""
    body = clean_text(strip_html(text), FETCH_BODY_CHARS)
    return title, body


def fetch_link_context(url: str, timeout: int, excerpt_chars: int = LINK_EXCERPT_CHARS) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            final_url = response.geturl()
            if "text/html" not in content_type.lower():
                return {
                    "url": url,
                    "final_url": final_url,
                    "title": "",
                    "excerpt": "",
                    "status": "skipped",
                    "reason": "non_html_content",
                    "content_type": content_type,
                }
            charset = response.headers.get_content_charset()
            title, body = html_title_and_text(response.read(FETCH_BODY_CHARS), charset)
            return {
                "url": url,
                "final_url": final_url,
                "title": title,
                "excerpt": clean_text(body, excerpt_chars),
                "status": "fetched",
                "reason": "",
                "content_type": content_type,
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "final_url": "",
            "title": "",
            "excerpt": "",
            "status": "failed",
            "reason": str(exc).strip(),
            "content_type": "",
        }


def link_evidence(value: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(value)
    enriched.setdefault("type", "public_link")
    enriched.setdefault("source", "link")
    enriched.setdefault("uid", "")
    enriched.setdefault("url", "")
    enriched.setdefault("final_url", "")
    enriched.setdefault("title", "")
    enriched.setdefault("excerpt", "")
    enriched.setdefault("status", "")
    enriched.setdefault("reason", "")
    enriched.setdefault("content_type", "")
    enriched.setdefault("anchor_text", "")
    enriched.setdefault("email_context", "")
    enriched.setdefault("source_content_type", "")
    return enriched


def public_link_evidence_urls(item: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for evidence in item.get("evidence", []):
        if not isinstance(evidence, dict) or evidence.get("type") != "public_link":
            continue
        url = normalize_url(str(evidence.get("url") or ""))
        if url:
            urls.add(url)
    return urls


HARD_SKIP_URL_TERMS = (
    "unsubscribe",
    "optout",
    "opt-out",
    "manage alert",
    "manage-alert",
    "create alert",
    "create-alert",
    "login",
    "signin",
    "sign-in",
    "tracking",
    "track",
    "pixel",
    "share=",
    "share?",
    "/share/",
    "calendar",
    ".ics",
    "attachment",
    "download",
)
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "oid",
    "ref",
    "ref_src",
    "spm",
    "vt",
}


def hard_skip_reason_for_url(url: str) -> str:
    raw = url.strip()
    lowered = raw.lower()
    if not raw:
        return "hard_skip:empty_url"
    if lowered.startswith("mailto:"):
        return "hard_skip:mailto"
    parsed = urllib.parse.urlsplit(raw)
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return "hard_skip:localhost"
    try:
        if host and ipaddress.ip_address(host).is_private:
            return "hard_skip:private"
    except ValueError:
        pass
    combined = " ".join([lowered, urllib.parse.unquote_plus(lowered)])
    for term in HARD_SKIP_URL_TERMS:
        if term in combined:
            reason = term.strip("/=").replace(" ", "_").replace("-", "_")
            return f"hard_skip:{reason}"
    return ""


def canonical_link_target(url: str) -> str:
    raw = url.strip()
    parsed = urllib.parse.urlsplit(raw)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if host.endswith("google.com") and path.startswith("/url"):
        for key, value in query_pairs:
            if key.lower() in {"url", "q"} and value:
                target = canonical_link_target(value)
                if target:
                    return target
    kept = [(key, value) for key, value in query_pairs if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")]
    query = urllib.parse.urlencode(kept, doseq=True)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, "")) if parsed.scheme and parsed.netloc else ""


def public_output_url(url: str) -> str:
    return canonical_link_target(url) or normalize_url(url)


def topic_candidate_text(item: dict[str, Any], link: dict[str, Any], canonical_url: str) -> str:
    parsed = urllib.parse.urlsplit(canonical_url)
    parts = [
        str(item.get("subject") or ""),
        str(item.get("snippet") or ""),
        str(link.get("anchor_text") or ""),
        str(link.get("context") or ""),
        parsed.netloc,
        parsed.path,
    ]
    return "\n".join(parts).lower()


def topic_match_terms(topic: dict[str, Any]) -> list[str]:
    terms = topic_keywords(topic)
    for key in ("description", "summary_focus"):
        value = str(topic.get(key) or "").strip()
        if value:
            terms.extend([part for part in re.split(r"[,，;；\n]", value) if part.strip()])
    for key in ("examples",):
        values = topic.get(key, [])
        if isinstance(values, list):
            terms.extend(str(item) for item in values if str(item).strip())
    return [term.strip().lower() for term in terms if term.strip()]


def topic_negative_terms(topic: dict[str, Any]) -> list[str]:
    values = topic.get("non_examples", [])
    if not isinstance(values, list):
        return []
    return [str(item).strip().lower() for item in values if str(item).strip()]


def match_link_topics(item: dict[str, Any], link: dict[str, Any], canonical_url: str, topic_map: dict[str, Any]) -> list[dict[str, Any]]:
    haystack = topic_candidate_text(item, link, canonical_url)
    parsed = urllib.parse.urlsplit(canonical_url)
    link_haystack = "\n".join([str(link.get("anchor_text") or ""), str(link.get("context") or ""), parsed.netloc, parsed.path]).lower()
    matches: list[dict[str, Any]] = []
    for index, topic in enumerate(topic_map.get("topics", [])):
        if not isinstance(topic, dict):
            continue
        if any(term in haystack for term in topic_negative_terms(topic)):
            continue
        terms = topic_match_terms(topic)
        matched = [term for term in terms if term in haystack]
        if not matched:
            continue
        link_matched = [term for term in terms if term in link_haystack]
        topic_id = str(topic.get("id") or topic.get("name") or f"topic_{index + 1}")
        priority = str(topic.get("priority") or "normal")
        matches.append({"id": topic_id, "name": str(topic.get("name") or topic_id), "priority": priority, "matched_keywords": matched[:8], "link_match_count": len(link_matched), "order": index})
    return sorted(matches, key=lambda value: (topic_priority_value(value), int(value.get("order", 0)), value.get("name", "")))


def build_link_triage(item: dict[str, Any], policy: dict[str, Any], topic_map: dict[str, Any] | None, remaining_budget: int) -> dict[str, Any]:
    limits = policy.get("limits", {})
    per_email_limit = int(limits.get("max_links_per_email", 2))
    per_email_budget = min(per_email_limit, remaining_budget)
    links = item.get("links", []) if isinstance(item.get("links"), list) else []
    groups: list[dict[str, Any]] = []
    canonical_seen: dict[str, int] = {}
    hard_skipped = 0
    deduped = 0
    unmapped = 0
    topic_map = topic_map or {}
    topic_gate_active = any(isinstance(topic, dict) for topic in topic_map.get("topics", []))

    for index, link in enumerate(links):
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "")
        hard_reason = hard_skip_reason_for_url(url) or skip_reason_for_url(url, policy)
        canonical = canonical_link_target(url)
        if hard_reason:
            link["policy_decision"] = "skip"
            hard_skipped += 1
            groups.append({"decision": "skip", "reason": hard_reason, "url": url, "canonical_url": canonical, "link_indexes": [index], "topics": [], "score": 0})
            continue
        if not canonical:
            link["policy_decision"] = "skip"
            hard_skipped += 1
            groups.append({"decision": "skip", "reason": "hard_skip:invalid_url", "url": url, "canonical_url": "", "link_indexes": [index], "topics": [], "score": 0})
            continue
        if canonical in canonical_seen:
            link["policy_decision"] = "dedupe"
            deduped += 1
            groups[canonical_seen[canonical]]["link_indexes"].append(index)
            groups.append({"decision": "dedupe", "reason": "dedupe:canonical_target", "url": url, "canonical_url": canonical, "link_indexes": [index], "deduped_to": canonical_seen[canonical], "topics": [], "score": 0})
            continue
        canonical_seen[canonical] = len(groups)
        topics = match_link_topics(item, link, canonical, topic_map) if topic_gate_active else []
        if topic_gate_active and not topics:
            link["policy_decision"] = "defer"
            unmapped += 1
            groups.append({"decision": "defer", "reason": "defer:unmapped_topic", "url": url, "canonical_url": canonical, "link_indexes": [index], "topics": [], "score": 0})
            continue
        priority_score = 0
        if topics:
            priority_score = {"high": 300, "medium": 200, "normal": 150, "low": 100}.get(str(topics[0].get("priority") or "normal"), 150)
        link_match_score = sum(int(topic.get("link_match_count") or 0) for topic in topics) * 50
        score = priority_score + link_match_score + max(0, 100 - index)
        groups.append({"decision": "candidate", "reason": "topic_match" if topics else "topic_gate_inactive", "url": url, "canonical_url": canonical, "link_indexes": [index], "topics": topics, "score": score})

    selected: list[int] = []
    domain_counts: dict[str, int] = {}
    candidates = [idx for idx, group in enumerate(groups) if group.get("decision") == "candidate"]
    candidates.sort(key=lambda idx: (-int(groups[idx].get("score") or 0), idx))
    for idx in candidates:
        if len(selected) >= per_email_budget:
            break
        domain = urllib.parse.urlsplit(str(groups[idx].get("canonical_url") or "")).netloc.lower()
        if domain_counts.get(domain, 0) >= 1 and len(candidates) - len(selected) > 1:
            continue
        selected.append(idx)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    for idx in candidates:
        group = groups[idx]
        first_link_index = int(group.get("link_indexes", [0])[0])
        link = links[first_link_index]
        if idx in selected:
            group["decision"] = "fetch"
            group["reason"] = "fetch:topic_budget" if topic_gate_active else "fetch:budget"
            link["policy_decision"] = "fetch"
        else:
            group["decision"] = "defer"
            group["reason"] = "defer:budget"
            link["policy_decision"] = "defer"

    deferred = len([group for group in groups if group.get("decision") == "defer"])
    if topic_gate_active and unmapped:
        risks = set(item.get("risks", []))
        risks.add("unmapped_alert_topic")
        item["risks"] = sorted(risks)
    return {
        "total_links": len([link for link in links if isinstance(link, dict)]),
        "hard_skipped_count": hard_skipped,
        "candidate_group_count": len(candidates),
        "selected_fetch_count": len(selected),
        "deferred_count": deferred,
        "deduped_count": deduped,
        "unmapped_topic_count": unmapped,
        "groups": groups,
    }


def link_evidence_payload(
    item: dict[str, Any],
    link: dict[str, Any],
    *,
    status: str,
    reason: str,
    title: str = "",
    excerpt: str = "",
    final_url: str = "",
    content_type: str = "",
) -> dict[str, Any]:
    return link_evidence(
        {
            "uid": str(item.get("uid") or ""),
            "url": link.get("url", ""),
            "final_url": final_url,
            "title": title,
            "excerpt": excerpt,
            "status": status,
            "reason": reason,
            "content_type": content_type,
            "anchor_text": link.get("anchor_text", ""),
            "email_context": link.get("context", ""),
            "source_content_type": link.get("source_content_type", ""),
        }
    )


def skip_pending_links(item: dict[str, Any], reason: str) -> None:
    ensure_email_snippet_evidence(item)
    skipped = 0
    for link in item.get("links", []):
        if not isinstance(link, dict):
            continue
        url = normalize_url(str(link.get("url") or ""))
        if not url:
            continue
        link["policy_decision"] = "skip"
        skipped += 1
    if skipped:
        risks = set(item.get("risks", []))
        risks.add(reason)
        item["risks"] = sorted(risks)


def enrich_item_links(
    item: dict[str, Any],
    policy: dict[str, Any],
    *,
    remaining_budget: int,
    fetcher: Any = fetch_link_context,
    topic_map: dict[str, Any] | None = None,
) -> int:
    item_policy = policy_for_type(str(item.get("email_type") or "unknown"), policy)
    limits = policy.get("limits", {})
    timeout = int(limits.get("timeout_seconds", 8))
    excerpt_chars = int(limits.get("excerpt_chars", LINK_EXCERPT_CHARS))
    fetched = 0
    ensure_email_snippet_evidence(item)
    normalize_existing_evidence(item)
    evidence: list[dict[str, Any]] = [existing for existing in item.get("evidence", []) if isinstance(existing, dict)]
    existing_public_urls = public_link_evidence_urls(item)
    risks = set(item.get("risks", []))
    triage = build_link_triage(item, policy, topic_map, remaining_budget)
    item["link_triage"] = triage
    risks.update(item.get("risks", []))

    if not item_policy.get("fetch_links", False):
        reason = f"policy_no_fetch:{item.get('email_type', 'unknown')}"
        for group in item["link_triage"].get("groups", []):
            if isinstance(group, dict) and group.get("decision") == "fetch":
                group["decision"] = "skip"
                group["reason"] = reason
        for link in item.get("links", []):
            if not isinstance(link, dict):
                continue
            link["policy_decision"] = "skip"
            risks.add("link_skipped")
        item["evidence"] = evidence
        item["risks"] = sorted(risks)
        item["link_triage"]["selected_fetch_count"] = 0
        return 0

    links = item.get("links", []) if isinstance(item.get("links"), list) else []
    for group in triage.get("groups", []):
        if not isinstance(group, dict):
            continue
        link_indexes = group.get("link_indexes", [])
        if not isinstance(link_indexes, list) or not link_indexes:
            continue
        link_index = int(link_indexes[0])
        if link_index < 0 or link_index >= len(links) or not isinstance(links[link_index], dict):
            continue
        link = links[link_index]
        canonical_url = str(group.get("canonical_url") or "")
        existing_key = normalize_url(canonical_url or str(link.get("url") or ""))
        if existing_key and existing_key in existing_public_urls:
            continue
        decision = str(group.get("decision") or "")
        reason = str(group.get("reason") or "")
        if decision == "fetch":
            context = fetcher(canonical_url, timeout, excerpt_chars)
            context.setdefault("url", canonical_url)
            context.setdefault("uid", str(item.get("uid") or ""))
            context.setdefault("anchor_text", link.get("anchor_text", ""))
            context.setdefault("email_context", link.get("context", ""))
            context.setdefault("source_content_type", link.get("source_content_type", ""))
            context["url"] = public_output_url(str(context.get("url") or canonical_url))
            if context.get("final_url"):
                context["final_url"] = public_output_url(str(context.get("final_url") or ""))
            evidence.append(link_evidence(context))
            if existing_key:
                existing_public_urls.add(existing_key)
            fetched += 1
            if context.get("status") == "fetched":
                risks.discard("snippet_only")
            elif context.get("status") == "failed":
                risks.add("link_failed")
            else:
                risks.add("link_skipped")
        elif decision == "skip":
            risks.add("tracking_skipped" if "track" in reason or "unsubscribe" in reason else "link_skipped")
        elif decision == "defer" and reason == "defer:budget":
            risks.add("link_budget_exhausted")

    item["evidence"] = evidence
    item["risks"] = sorted(risks)
    return fetched


def normalize_evidence_pack(scan: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    scan.setdefault("object_type", "email_evidence_pack")
    scan.setdefault("object_version", EVIDENCE_PACK_VERSION)
    scan.setdefault("status", "ready_for_summary")
    for item in scan.get("items", []):
        if not isinstance(item, dict):
            continue
        item["snippet"] = select_body_excerpt(
            [{"content_type": "text/plain", "text": clean_mailparser_snippet(str(item.get("snippet") or ""))}],
            MAILPARSER_SNIPPET_CHARS,
        )
        links = item.get("links", [])
        if isinstance(links, list) and links:
            item["links"] = unique_links([link for link in links if isinstance(link, dict)])
        else:
            item["links"] = unique_links(extract_links_from_text(str(item.get("snippet") or ""), "snippet"))
        item.setdefault("evidence", [])
        item.setdefault("risks", [])
        item.setdefault("flags", [])
        item.setdefault("has_attachments", False)
        item.setdefault("attachment_count", 1 if item.get("has_attachments") else 0)
        item.setdefault("attachment_shapes", [])
        item.setdefault("body_part_count", 1 if item.get("snippet") else 0)
        item.setdefault("body_part_types", ["snippet"] if item.get("snippet") else [])
        item["email_type"] = str(item.get("email_type") or classify_email(item, policy))
        ensure_email_snippet_evidence(item)
    return scan


def enrich_scan_links(scan: dict[str, Any], policy: dict[str, Any], fetcher: Any = fetch_link_context, topic_map: dict[str, Any] | None = None) -> dict[str, Any]:
    limits = policy.get("limits", {})
    remaining = int(limits.get("max_links_total", 10))
    for item in scan.get("items", []):
        if remaining <= 0:
            if isinstance(item, dict):
                item["link_triage"] = build_link_triage(item, policy, topic_map, 0)
                for link in item.get("links", []) if isinstance(item.get("links"), list) else []:
                    if isinstance(link, dict) and link.get("policy_decision") == "defer":
                        link.pop("policy_decision", None)
                skip_pending_links(item, "link_budget_exhausted")
            continue
        if has_link_evidence(item):
            existing_count = len(public_link_evidence_urls(item))
            link_count = len(item.get("links", []) if isinstance(item.get("links"), list) else [])
            if existing_count >= link_count:
                continue
        used = enrich_item_links(item, policy, remaining_budget=remaining, fetcher=fetcher, topic_map=topic_map)
        remaining -= used
    scan["status"] = "enriched"
    return scan


def review_checklist(scan: dict[str, Any], markdown: str) -> dict[str, Any]:
    markdown = markdown.split("\n## Review Checklist", 1)[0]
    has_link_evidence = any(
        evidence.get("status") == "fetched"
        for item in scan.get("items", [])
        for evidence in item.get("evidence", [])
        if isinstance(evidence, dict)
    )
    topic_count = int(scan.get("topic_map", {}).get("topic_count") or 0) if isinstance(scan.get("topic_map"), dict) else 0
    has_topic_contract = topic_count > 0 or bool(scan.get("topic_hits"))
    topic_names = [
        str(hit.get("name") or "")
        for hit in scan.get("topic_hits", [])
        if isinstance(hit, dict) and str(hit.get("name") or "").strip()
    ]
    forbidden_patterns = (
        r"\bEmailEvidencePack\b",
        r"\bEmailTopicMap\b",
        r"\bneed_id\b",
        r"\bsnippet_only\b",
        r"\blink_triage\b",
        r"\bhard_skip\b",
        r"\bskip\b",
        r"Review Checklist",
        r"topic\.md",
        r"这封邮件命中",
        r"对象:",
        r"来源对象:",
        r"处理方式:",
    )
    checklist = {
        "has_key_takeaway": "key takeaway" in markdown.lower() or "## 今天先看" in markdown,
        "has_topic_expansion": (not has_topic_contract)
        or "## 情报线索" in markdown
        or "## 今天先看" in markdown
        or any(name and name in markdown for name in topic_names),
        "has_source_index": "email://" in markdown,
        "has_uid_trace": "UID" in markdown,
        "has_truncated_warning": (not scan.get("possibly_truncated")) or "触达上限" in markdown or "可能有遗漏" in markdown,
        "uses_link_evidence_when_available": (not has_link_evidence) or "链接" in markdown,
        "marks_snippet_only_claims": "snippet_only" not in json.dumps(scan, ensure_ascii=False) or "仅基于邮件摘要" in markdown or "待外部验证" in markdown,
        "no_unbacked_claims": True,
        "no_internal_markers": not any(re.search(pattern, markdown, flags=re.IGNORECASE) for pattern in forbidden_patterns),
    }
    checklist["ready_to_send"] = all(checklist.values())
    checklist["risks"] = [key for key, value in checklist.items() if key != "ready_to_send" and value is False]
    return checklist


def append_review_checklist(markdown: str, scan: dict[str, Any]) -> str:
    checklist = review_checklist(scan, markdown)
    lines = [
        markdown.rstrip(),
        "",
        "## Review Checklist",
        "",
    ]
    for key, value in checklist.items():
        if key == "risks":
            lines.append(f"- risks: {', '.join(value) if value else 'none'}")
        else:
            lines.append(f"- {key}: {str(value).lower()}")
    return "\n".join(lines).rstrip() + "\n"


class ImapSettings(NamedTuple):
    host: str
    port: int
    user: str
    password: str
    mailboxes: list[str]
    tls_verify: bool


class MailboxMessages(NamedTuple):
    """一个 mailbox 里取回的原始邮件。

    `uid_count` 是 SEARCH 命中的总数，比取回的封数多就说明 limit 把尾巴切掉了；
    截断判断只能按 mailbox 各自算，合计跟 limit 比会让两个都没满的文件夹加出假警报。
    """

    mailbox: str
    uid_count: int
    messages: list[tuple[str, bytes]]


def parse_mailboxes(value: str) -> list[str]:
    """mailbox 配置是逗号分隔列表。

    exmail 的反垃圾把订阅邮件整批扔进 Junk，只扫 INBOX 会静默漏掉一半 brief 素材。
    单值写法保持原样，所以老配置不用改。
    """
    names: list[str] = []
    for name in value.split(","):
        name = name.strip()
        if name and name not in names:
            names.append(name)
    return names or [DEFAULT_MAILBOX]


def imap_settings(args: argparse.Namespace) -> ImapSettings:
    """解析 IMAP 连接配置。

    scan 和 fixture capture 读同一批配置键，解析只写在这里，两边不会再各自跑偏。
    """
    env_file = load_env_file(args.env_file)
    settings = ImapSettings(
        host=args.imap_host or config_value(env_file, "PODSUM_EMAIL_IMAP_HOST", "IMAP_HOST", default=DEFAULT_IMAP_HOST),
        port=args.imap_port or int(config_value(env_file, "PODSUM_EMAIL_IMAP_PORT", "IMAP_PORT", default=str(DEFAULT_IMAP_PORT))),
        user=args.imap_user or config_value(env_file, "PODSUM_EMAIL_IMAP_USER", "IMAP_USER", "GMAIL_USER"),
        password=args.imap_pass or config_value(env_file, "PODSUM_EMAIL_IMAP_PASS", "IMAP_PASS", "GMAIL_APP_PASSWORD"),
        mailboxes=parse_mailboxes(
            args.mailbox or config_value(env_file, "PODSUM_EMAIL_IMAP_MAILBOX", "IMAP_MAILBOX", default=DEFAULT_MAILBOX)
        ),
        tls_verify=parse_bool(config_value(env_file, "PODSUM_EMAIL_IMAP_TLS_VERIFY", "IMAP_REJECT_UNAUTHORIZED", default="true"), True),
    )
    if not settings.user or not settings.password:
        raise RuntimeError(
            "missing IMAP credentials: set PODSUM_EMAIL_IMAP_USER/"
            f"PODSUM_EMAIL_IMAP_PASS in {args.env_file}"
        )
    return settings


@contextlib.contextmanager
def imap_session(settings: ImapSettings) -> Iterator[imaplib.IMAP4]:
    context = ssl.create_default_context()
    if not settings.tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    imap = imaplib.IMAP4_SSL(settings.host, settings.port, ssl_context=context)
    try:
        imap.login(settings.user, settings.password)
        yield imap
    finally:
        # logout 失败不该盖掉正在往外抛的真错误。
        with contextlib.suppress(Exception):
            imap.logout()


def imap_search_since(recent_days: int) -> str:
    return (dt.datetime.now() - dt.timedelta(days=recent_days)).strftime("%d-%b-%Y")


def fetch_mailbox_messages(imap: imaplib.IMAP4, mailboxes: list[str], since: str, limit: int) -> list[MailboxMessages]:
    """逐个 mailbox 取最近 `limit` 封原始邮件，按 mailbox 分组返回。

    limit 是每个 mailbox 各自取尾部，不是全部 mailbox 合起来的额度。
    UID 只在自己的 mailbox 里唯一，跨文件夹会撞号，所以分组保留 mailbox 名，
    由调用方决定要不要给 UID 加前缀。

    原始邮件整批留在内存里，上限是 limit × mailbox 数；limit 默认 300 够用，
    真要扫到上万封再改成流式。
    """
    groups: list[MailboxMessages] = []
    for mailbox in mailboxes:
        status, _ = imap.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError(f"failed to select mailbox: {mailbox}")
        status, data = imap.uid("SEARCH", None, "SINCE", since)
        if status != "OK":
            raise RuntimeError(f"IMAP search failed in mailbox: {mailbox}")
        uids = data[0].split() if data and data[0] else []
        messages: list[tuple[str, bytes]] = []
        for uid_bytes in uids[-limit:]:
            uid = uid_bytes.decode("ascii", errors="replace")
            status, fetched = imap.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not fetched:
                continue
            for part in fetched:
                if isinstance(part, tuple) and len(part) >= 2:
                    messages.append((uid, part[1]))
                    break
        groups.append(MailboxMessages(mailbox, len(uids), messages))
    return groups


def scan_imap(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    settings = imap_settings(args)
    with imap_session(settings) as imap:
        groups = fetch_mailbox_messages(imap, settings.mailboxes, imap_search_since(args.recent_days), args.limit)

    multi_mailbox = len(settings.mailboxes) > 1
    items: list[dict[str, Any]] = []
    for group in groups:
        for uid, raw_message in group.messages:
            # 单 mailbox 保持裸 UID，下游的 email:// 链接和 topic item_uids 就不变。
            item = message_item(f"{group.mailbox}:{uid}" if multi_mailbox else uid, raw_message, policy)
            item["mailbox"] = group.mailbox
            items.append(item)

    scan = scan_payload(settings.user, args.recent_days, args.limit, sum(group.uid_count for group in groups), items)
    scan["possibly_truncated"] = any(group.uid_count >= args.limit for group in groups)
    scan["mailboxes"] = settings.mailboxes
    return scan


def scan_eml_dir(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    if not args.eml_dir.is_dir():
        raise RuntimeError(f"--eml-dir is not a directory: {args.eml_dir}")

    files = sorted(path for path in args.eml_dir.iterdir() if path.suffix.lower() == ".eml")
    raw_count = len(files)
    selected = files[: args.limit]
    items = [message_item(str(index), path.read_bytes(), policy) for index, path in enumerate(selected, 1)]
    return scan_payload(DEFAULT_FIXTURE_ACCOUNT, args.recent_days, args.limit, raw_count, items)


def is_self_mail(item: dict[str, Any], account: str) -> bool:
    """Podsum 自己发出去的邮件。

    投递目标和扫描邮箱是同一个地址时，brief 会把自己读回来，并且逐日放大：
    今天的 brief 是明天的输入。两条判据缺一不可——按地址挡不住将来从别的地址
    发的 brief，按主题挡不住 SMTP Connection Test 这类没有前缀的自发邮件。
    """
    subject = str(item.get("subject") or "").strip()
    if subject.startswith(SELF_SUBJECT_PREFIX):
        return True
    if not account:
        return False
    _, address = email.utils.parseaddr(str(item.get("from") or ""))
    return address.strip().lower() == account.strip().lower()


def podsum_subject(date: str, kind: str) -> str:
    """Podsum 自己发出去的邮件主题。

    和 is_self_mail 的主题判据同源：发件主题在别处硬编码的话，改了发件端而扫描端
    不动，自发邮件过滤会静默失效，brief 又开始把自己读回来。
    """
    return f"{SELF_SUBJECT_PREFIX} {date} {kind}"


def scan_payload(account: str, recent_days: int, limit: int, raw_count: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    kept = [item for item in items if not is_self_mail(item, account)]
    dropped = len(items) - len(kept)
    if dropped:
        # 不打日志的话，「今天怎么只有 3 封」会变成一个查不出来的问题。
        log(f"Dropped {dropped} Podsum-authored message(s) from the scan.")
    items = kept
    return {
        "object_type": "email_evidence_pack",
        "object_version": EVIDENCE_PACK_VERSION,
        "status": "ready_for_summary",
        "date": today_string(),
        "account": account,
        "window": f"{recent_days}d",
        "scan_limit": limit,
        "raw_count": raw_count,
        "possibly_truncated": raw_count >= limit,
        "dropped_self_count": dropped,
        "items": items,
    }


def email_reports_dir(root: Path) -> Path:
    return root / "EmailReports"


def write_scan(root: Path, scan: dict[str, Any]) -> Path:
    directory = email_reports_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"email-scan-{scan['date']}.json"
    atomic_write_json(path, scan)
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
        "## 来源补充（待嵌入正文）",
        "",
    ]
    if scan.get("possibly_truncated"):
        lines[9:9] = ["触达上限，可能有遗漏。", ""]
    for item in scan.get("items", []):
        lines.append(
            f"- {source_markdown_link(scan, item)} | From={item.get('from')} | "
            f"Subject={item.get('subject')} | Date={item.get('date')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def source_index_line(scan: dict[str, Any], item: dict[str, Any]) -> str:
    return (
        f"- UID={item.get('uid')} | From={item.get('from')} | "
        f"Subject={item.get('subject')} | Date={item.get('date')} | "
        f"`email://{scan['date']}/{item.get('uid')}`"
    )


def source_markdown_link(scan: dict[str, Any], item: dict[str, Any]) -> str:
    uid = item.get("uid")
    return f"[UID {uid}](email://{scan['date']}/{uid})"


def compact_topic_ref(topic: dict[str, Any]) -> dict[str, Any]:
    return {
        key: topic.get(key)
        for key in ("id", "name", "priority", "matched_keywords", "summary_focus", "item_uids")
        if key in topic
    }


def compact_evidence_for_llm(evidence: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "type": evidence.get("type"),
        "status": evidence.get("status"),
        "url": evidence.get("url"),
        "final_url": evidence.get("final_url"),
        "title": clean_text(str(evidence.get("title") or ""), 180),
        "excerpt": clean_text(str(evidence.get("excerpt") or ""), 900),
        "content_type": evidence.get("content_type"),
    }
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def evidence_boundaries_for_llm(item: dict[str, Any]) -> list[str]:
    risks = set(item.get("risks", []) if isinstance(item.get("risks"), list) else [])
    boundaries: list[str] = []
    if "snippet_only" in risks:
        boundaries.append("只有邮件摘要或片段，缺少完整正文或公开网页证据。")
    if "metadata_only" in risks:
        boundaries.append("只有邮件元数据，不能据此扩写结论。")
    if {"link_failed", "link_budget_exhausted", "unmapped_alert_topic"} & risks:
        boundaries.append("公开网页证据不完整。")
    if item.get("links") and not fetched_public_link_evidence(item):
        boundaries.append("没有可用的 fetched public_link evidence。")
    return list(dict.fromkeys(boundaries))


def compact_item_for_llm(scan: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    evidence = [entry for entry in item.get("evidence", []) if isinstance(entry, dict)]
    fetched = [entry for entry in evidence if entry.get("type") == "public_link" and entry.get("status") == "fetched"]
    compact = {
        "uid": item.get("uid"),
        "date": item.get("date"),
        "from": item.get("from"),
        "subject": item.get("subject"),
        "source_ref": f"email://{scan.get('date')}/{item.get('uid')}",
        "email_type": item.get("email_type"),
        "snippet": clean_text(str(item.get("snippet") or ""), 900),
        "evidence_boundaries": evidence_boundaries_for_llm(item),
        "flags": item.get("flags", []),
        "has_attachments": item.get("has_attachments"),
        "attachment_count": item.get("attachment_count"),
        "topics": [compact_topic_ref(topic) for topic in item.get("topics", []) if isinstance(topic, dict)],
        "fetched_public_link_evidence": [compact_evidence_for_llm(entry) for entry in fetched[:5]],
        "email_snippet_evidence": [
            compact_evidence_for_llm(entry)
            for entry in evidence
            if entry.get("type") == "email_snippet"
        ][:1],
    }
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def llm_brief_input(scan: dict[str, Any]) -> dict[str, Any]:
    topic_map = scan.get("topic_map") if isinstance(scan.get("topic_map"), dict) else {}
    return {
        "object_type": "email_evidence_pack_llm_brief_input",
        "source_object_type": scan.get("object_type", "email_evidence_pack"),
        "source_object_version": scan.get("object_version", EVIDENCE_PACK_VERSION),
        "date": scan.get("date"),
        "account": scan.get("account"),
        "window": scan.get("window"),
        "scan_limit": scan.get("scan_limit"),
        "raw_count": scan.get("raw_count"),
        "possibly_truncated": scan.get("possibly_truncated"),
        "status": scan.get("status"),
        "topic_map": {
            "object_type": topic_map.get("object_type", "email_topic_map"),
            "version": topic_map.get("version"),
            "topic_count": topic_map.get("topic_count"),
            "default_behavior": topic_map.get("default_behavior"),
        },
        "topic_hits": [compact_topic_ref(hit) for hit in scan.get("topic_hits", []) if isinstance(hit, dict)],
        "items": [compact_item_for_llm(scan, item) for item in scan.get("items", []) if isinstance(item, dict)],
    }


def public_source_digest(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "title": clean_text(str(evidence.get("title") or ""), 180),
            "url": public_output_url(str(evidence.get("final_url") or evidence.get("url") or "")),
            "excerpt": clean_text(str(evidence.get("excerpt") or ""), 420),
        }.items()
        if value not in (None, "", [])
    }


def clean_digest_text_list(value: Any, limit: int = 8, chars: int = 220) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for entry in value[:limit]:
        text = clean_text(str(entry or ""), chars)
        if text:
            cleaned.append(text)
    return cleaned


def clean_digest_topic_relevance(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in value[:8]:
        if not isinstance(entry, dict):
            continue
        item = {
            key: clean_text(str(entry.get(key) or ""), 160)
            for key in ("id", "name", "relevance", "why")
            if entry.get(key)
        }
        if item:
            cleaned.append(item)
    return cleaned


def clean_digest_public_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in value[:4]:
        if not isinstance(entry, dict):
            continue
        url = public_output_url(str(entry.get("url") or ""))
        source = {
            key: clean_text(str(entry.get(key) or ""), 420 if key in {"claim", "evidence_excerpt", "excerpt"} else 180)
            for key in ("title", "claim", "evidence_excerpt", "excerpt")
            if entry.get(key)
        }
        if url and not hard_skip_reason_for_url(url):
            source["url"] = url
        if source:
            cleaned.append(source)
    return cleaned


def clean_digest_item(item: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in ("uid", "date", "from", "subject", "source_ref", "email_type"):
        value = item.get(key) or base.get(key)
        if value not in (None, "", []):
            normalized[key] = clean_text(str(value), 240)
    if item.get("clean_summary"):
        normalized["clean_summary"] = clean_text(str(item.get("clean_summary") or ""), 700)
    elif base.get("clean_summary"):
        normalized["clean_summary"] = base["clean_summary"]
    key_facts = clean_digest_text_list(item.get("key_facts"), chars=260)
    if key_facts:
        normalized["key_facts"] = key_facts
    action_signal = str(item.get("action_signal") or "").strip()
    if action_signal:
        normalized["action_signal"] = clean_text(action_signal, 40)
    topic_relevance = clean_digest_topic_relevance(item.get("topic_relevance"))
    if topic_relevance:
        normalized["topic_relevance"] = topic_relevance
    elif base.get("topics"):
        normalized["topics"] = base["topics"]
    public_sources = clean_digest_public_sources(item.get("public_sources"))
    if public_sources:
        normalized["public_sources"] = public_sources
    elif base.get("public_sources"):
        normalized["public_sources"] = base["public_sources"]
    evidence_limits = clean_digest_text_list(item.get("evidence_limits"), chars=220)
    if evidence_limits:
        normalized["evidence_limits"] = evidence_limits
    elif base.get("evidence_limits"):
        normalized["evidence_limits"] = base["evidence_limits"]
    return {key: value for key, value in normalized.items() if value not in (None, "", [])}


def deterministic_evidence_digest(scan: dict[str, Any], mode: str = "deterministic_fallback") -> dict[str, Any]:
    return {
        "object_type": "email_evidence_digest",
        "source_object_type": scan.get("object_type", "email_evidence_pack"),
        "source_object_version": scan.get("object_version", EVIDENCE_PACK_VERSION),
        "date": scan.get("date"),
        "account": scan.get("account"),
        "window": scan.get("window"),
        "scan_limit": scan.get("scan_limit"),
        "raw_count": scan.get("raw_count"),
        "possibly_truncated": scan.get("possibly_truncated"),
        "topic_hits": [compact_topic_ref(hit) for hit in scan.get("topic_hits", []) if isinstance(hit, dict)],
        "items": [
            {
                key: value
                for key, value in {
                    "uid": item.get("uid"),
                    "date": item.get("date"),
                    "from": item.get("from"),
                    "subject": item.get("subject"),
                    "source_ref": f"email://{scan.get('date')}/{item.get('uid')}",
                    "email_type": item.get("email_type"),
                    "clean_summary": clean_text(str(item.get("snippet") or ""), 600),
                    "topics": [compact_topic_ref(topic) for topic in item.get("topics", []) if isinstance(topic, dict)],
                    "public_sources": [public_source_digest(entry) for entry in fetched_public_link_evidence(item)[:4]],
                    "evidence_limits": evidence_boundaries_for_llm(item),
                }.items()
                if value not in (None, "", [])
            }
            for item in scan.get("items", [])
            if isinstance(item, dict)
        ],
    }


def json_object_from_text(value: str) -> dict[str, Any] | None:
    text = value.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else None
    return None


def normalize_evidence_digest(value: dict[str, Any], scan: dict[str, Any], mode: str) -> dict[str, Any]:
    fallback = deterministic_evidence_digest(scan, mode)
    digest = value if isinstance(value, dict) else {}
    items = digest.get("items")
    if not isinstance(items, list):
        return fallback
    fallback_items = {
        str(item.get("uid") or ""): item
        for item in fallback.get("items", [])
        if isinstance(item, dict)
    }
    seen_uids: set[str] = set()
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid") or "")
        if not uid or uid in seen_uids:
            continue
        base = fallback_items.get(uid, {})
        if not base:
            continue
        normalized_items.append(clean_digest_item(item, base))
        seen_uids.add(uid)
    for uid, base in fallback_items.items():
        if uid not in seen_uids:
            normalized_items.append(base)
    if not normalized_items:
        return fallback
    return {
        "object_type": "email_evidence_digest",
        "source_object_type": scan.get("object_type", "email_evidence_pack"),
        "source_object_version": scan.get("object_version", EVIDENCE_PACK_VERSION),
        "date": scan.get("date"),
        "account": scan.get("account"),
        "window": scan.get("window"),
        "scan_limit": scan.get("scan_limit"),
        "raw_count": scan.get("raw_count"),
        "possibly_truncated": scan.get("possibly_truncated"),
        "topic_hits": [compact_topic_ref(hit) for hit in scan.get("topic_hits", []) if isinstance(hit, dict)],
        "items": normalized_items,
    }


def preprocessed_evidence_digest(args: argparse.Namespace, scan: dict[str, Any]) -> dict[str, Any]:
    if getattr(args, "no_llm_evidence_preprocess", False):
        return deterministic_evidence_digest(scan, "deterministic_no_llm")
    seed_json = json.dumps(llm_brief_input(scan), ensure_ascii=False, indent=2)
    prompt = args.email_evidence_preprocess_prompt.read_text(encoding="utf-8").format(
        date=scan["date"],
        account=scan.get("account", ""),
        window=scan.get("window", ""),
        raw_count=scan.get("raw_count", 0),
        preprocess_input=seed_json,
    )
    ok, value = run_hermes_prompt(str(args.hermes), prompt, cwd=str(args.project_dir), timeout=args.hermes_timeout)
    if not ok:
        return deterministic_evidence_digest(scan, f"llm_preprocess_failed:{clean_text(value, 120)}")
    parsed = json_object_from_text(value)
    if not parsed:
        return deterministic_evidence_digest(scan, "llm_preprocess_invalid_json")
    return normalize_evidence_digest(parsed, scan, "llm_preprocess")


def fetched_public_link_evidence(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        evidence
        for evidence in item.get("evidence", [])
        if isinstance(evidence, dict)
        and evidence.get("type") == "public_link"
        and evidence.get("status") == "fetched"
    ]


def evidence_excerpt(item: dict[str, Any], limit: int = 180) -> str:
    fetched_links = fetched_public_link_evidence(item)
    if fetched_links:
        evidence = fetched_links[0]
        parts = [str(evidence.get("title") or ""), str(evidence.get("excerpt") or "")]
        return clean_text(" | ".join(part for part in parts if part), limit)
    for evidence in item.get("evidence", []):
        if isinstance(evidence, dict) and evidence.get("type") == "email_snippet":
            return clean_text(str(evidence.get("excerpt") or ""), limit)
    return clean_text(str(item.get("snippet") or ""), limit)


def evidence_gap_text(item: dict[str, Any]) -> str:
    risks = set(item.get("risks", []))
    gaps: list[str] = []
    if "snippet_only" in risks:
        gaps.append("仅基于邮件摘要")
    if "metadata_only" in risks:
        gaps.append("仅基于邮件元数据")
    if "link_failed" in risks:
        gaps.append("链接抓取失败")
    if "tracking_skipped" in risks:
        gaps.append("tracking/unsubscribe 类链接已跳过")
    if "link_skipped" in risks:
        gaps.append("链接未抓取或被策略跳过")
    if "link_budget_exhausted" in risks:
        gaps.append("链接抓取预算耗尽")
    if item.get("links") and not fetched_public_link_evidence(item):
        gaps.append("待外部验证")
    return "；".join(dict.fromkeys(gaps)) or "无明显证据缺口"


def item_brief_block(scan: dict[str, Any], item: dict[str, Any], *, conclusion: str, action: str) -> list[str]:
    excerpt = evidence_excerpt(item)
    return [
        f"- 结论：{conclusion}",
        f"  - 依据：{excerpt or '没有可用正文片段'}（{evidence_gap_text(item)}）",
        f"  - 建议动作：{action}",
        f"  - 来源：{source_markdown_link(scan, item)} | From={item.get('from')} | Subject={item.get('subject')} | Date={item.get('date')}",
    ]


def classify_brief_items(scan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    need_action: list[dict[str, Any]] = []
    worth_knowing: list[dict[str, Any]] = []
    ignore: list[dict[str, Any]] = []
    for item in scan.get("items", []):
        if not isinstance(item, dict):
            continue
        email_type = str(item.get("email_type") or "unknown")
        risks = set(item.get("risks", []))
        has_links = bool(item.get("links"))
        if email_type in {"personal", "transactional"} or item.get("has_attachments"):
            need_action.append(item)
        elif item.get("topics") or email_type in DIGEST_EMAIL_TYPES or fetched_public_link_evidence(item) or has_links:
            worth_knowing.append(item)
        elif "metadata_only" in risks:
            ignore.append(item)
        else:
            ignore.append(item)
    return need_action, worth_knowing, ignore


def brief_type_distribution(scan: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for item in scan.get("items", []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("email_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return "，".join(f"{key} {value} 封" for key, value in sorted(counts.items())) or "无邮件"


def topic_groups(scan: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for hit in scan.get("topic_hits", []):
        if not isinstance(hit, dict):
            continue
        uids = {str(uid) for uid in hit.get("item_uids", [])}
        items = [
            item
            for item in scan.get("items", [])
            if isinstance(item, dict) and str(item.get("uid") or "") in uids
        ]
        if not items:
            continue
        group = dict(hit)
        group["items"] = items
        groups.append(group)
    return groups


def topic_name_list(scan: dict[str, Any]) -> str:
    names = [str(hit.get("name") or hit.get("id")) for hit in scan.get("topic_hits", []) if isinstance(hit, dict)]
    return "，".join(names) if names else "未命中 topic.md 中的跟踪话题"


def topic_map_source_line(scan: dict[str, Any]) -> str:
    topic_map = scan.get("topic_map", {}) if isinstance(scan.get("topic_map"), dict) else {}
    version = topic_map.get("version", "")
    topic_count = topic_map.get("topic_count", 0)
    return f"EmailTopicMap v{version} ({topic_count} topics)"


ACTION_SIGNAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"new sign-?in|sign in|login|password|security alert|安全提醒|新登录|有新的登录|密码|账号安全", re.IGNORECASE), "确认账号安全"),
    (re.compile(r"monthly statement|statement|invoice|billing|payment|账单|月结|付款|缴费|扣款", re.IGNORECASE), "核对账单或账户文件"),
    (re.compile(r"trial|free plan|downgraded|subscription|试用|套餐|订阅|降级", re.IGNORECASE), "确认订阅或数据保留"),
    (re.compile(r"action required|please confirm|please review|please reply|follow-?up|会议|截止|请确认|请回复|需要.{0,12}处理", re.IGNORECASE), "需要人工确认"),
)

NOTIFICATION_ACTIONS = {
    reason for _pattern, reason in ACTION_SIGNAL_PATTERNS if reason != "需要人工确认"
}


def item_plaintext(item: dict[str, Any]) -> str:
    values = [item.get("from"), item.get("subject"), item.get("snippet")]
    for evidence in item.get("evidence", []) if isinstance(item.get("evidence"), list) else []:
        if not isinstance(evidence, dict):
            continue
        values.extend([evidence.get("title"), evidence.get("excerpt"), evidence.get("anchor_text"), evidence.get("email_context")])
    return clean_text(" ".join(str(value or "") for value in values), 4000)


def item_content_text(item: dict[str, Any]) -> str:
    values = [item.get("subject"), item.get("snippet")]
    for evidence in item.get("evidence", []) if isinstance(item.get("evidence"), list) else []:
        if not isinstance(evidence, dict):
            continue
        values.extend([evidence.get("title"), evidence.get("excerpt"), evidence.get("anchor_text"), evidence.get("email_context")])
    return clean_text(" ".join(str(value or "") for value in values), 4000)


def is_internal_test_email(item: dict[str, Any]) -> bool:
    text = item_plaintext(item).lower()
    subject = str(item.get("subject") or "").strip().lower()
    return (
        subject in {"smtp connection test", "imap connection test"}
        or "test email from the imap/smtp email skill" in text
        or "this is a test email" in text and "imap/smtp" in text
    )


def is_bulk_or_newsletter_sender(item: dict[str, Any]) -> bool:
    sender_subject = item_subject_sender(item)
    bulk_hints = (
        "google alerts",
        "newsletter",
        "substack",
        "beehiiv",
        "nikkei",
        "the rundown",
        "therundown",
        "no-reply",
        "noreply",
        "updates@",
        "marketing@",
        "crew@",
        "news@",
        "mail.",
    )
    return any(hint in sender_subject for hint in bulk_hints)


def action_reason(item: dict[str, Any]) -> str:
    text = item_content_text(item)
    for pattern, reason in ACTION_SIGNAL_PATTERNS:
        if pattern.search(text):
            return reason
    email_type = str(item.get("email_type") or "")
    if email_type == "transactional" or item.get("has_attachments"):
        return "需要人工确认"
    if email_type == "personal" and not is_bulk_or_newsletter_sender(item):
        return "需要人工确认"
    return ""


def delivery_topic_match(item: dict[str, Any], topic: dict[str, Any]) -> bool:
    text = item_plaintext(item)
    subject_sender = item_subject_sender(item)
    matched = [str(keyword or "").strip() for keyword in topic.get("matched_keywords", []) if str(keyword or "").strip()]
    return any(keyword_matches_item(keyword, text, subject_sender) for keyword in matched)


def delivery_primary_topic(item: dict[str, Any]) -> dict[str, Any] | None:
    topics = [topic for topic in item.get("topics", []) if isinstance(topic, dict) and delivery_topic_match(item, topic)]
    if not topics:
        return None
    return sorted(topics, key=lambda value: (topic_priority_value(value), str(value.get("name") or "")))[0]


def delivery_item_score(item: dict[str, Any]) -> int:
    if is_internal_test_email(item):
        return -10000
    score = 0
    if action_reason(item):
        score += 100
    topic = delivery_primary_topic(item)
    if topic:
        score += 40 - topic_priority_value(topic) * 5
    if fetched_public_link_evidence(item):
        score += 15
    if str(item.get("email_type") or "") in {"newsletter_article", "digest"}:
        score += 8
    if str(item.get("email_type") or "") == "google_alert":
        score += 4
    if "snippet_only" in set(item.get("risks", []) if isinstance(item.get("risks"), list) else []):
        score -= 2
    return score


def delivery_excerpt(item: dict[str, Any], limit: int = 150) -> str:
    text = evidence_excerpt(item, limit * 2)
    text = re.sub(r"^(read online|listen online|view it in your browser|view in browser)\s*[-|:]*\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*read more\s*\d*", " ", text, flags=re.IGNORECASE)
    return clean_text(text, limit)


def is_pure_notification(item: dict[str, Any]) -> bool:
    return action_reason(item) in NOTIFICATION_ACTIONS


def delivery_takeaway(item: dict[str, Any]) -> str:
    """一条线索的判断。禁止 `标题；正文截断`：那不是归纳。"""
    excerpt = delivery_excerpt(item)
    subject = clean_text(str(item.get("subject") or ""), 90)
    action = action_reason(item)
    topic = delivery_primary_topic(item)
    topic_name = str(topic.get("name") or "") if topic else ""
    body = excerpt or subject
    if not body:
        return action or topic_name
    if is_pure_notification(item) and action:
        return f"{action}：{body}"
    if action and topic_name:
        return f"{action} / {topic_name}：{body}"
    if action:
        return f"{action}：{body}"
    return body


def delivery_item_line(scan: dict[str, Any], item: dict[str, Any]) -> str:
    # 段落标题已经承载分类；行首再写「线索：」没有信息量。
    return f"- {source_markdown_link(scan, item)} {delivery_takeaway(item)}"


def delivery_evidence_boundary(scan: dict[str, Any], displayed_items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if scan.get("possibly_truncated"):
        lines.append("本次扫描触达上限，可能有遗漏。")
    if any("snippet_only" in set(item.get("risks", []) if isinstance(item.get("risks"), list) else []) for item in displayed_items):
        lines.append("部分条目仅基于邮件摘要；公开链接未抓取时只作为线索，不作事实验证。")
    elif any(item.get("links") and not fetched_public_link_evidence(item) for item in displayed_items):
        lines.append("部分条目包含未抓取的公开链接，只作为线索处理。")
    return lines


def unique_delivery_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        uid = str(item.get("uid") or "")
        if not uid or uid in seen or is_internal_test_email(item):
            continue
        seen.add(uid)
        unique.append(item)
    return unique


def is_digest_item(item: dict[str, Any]) -> bool:
    """链接列表进订阅摘要；命中跟踪话题的 newsletter 按情报条目写。"""
    email_type = str(item.get("email_type") or "")
    if email_type in {"google_alert", "digest"}:
        return True
    if email_type == "newsletter_article":
        return delivery_primary_topic(item) is None
    return False


def merge_digest_groups(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """同主题的多封 digest 合成一张列表，按链接地址去重。

    按邮件主题判重会整封丢弃：相邻两天的同名快讯里，只在其中一封出现的条目会
    彻底消失。去重键必须是链接地址。
    """
    # 按 url 建索引，去重键就是索引键本身，不需要第二张表跟着 groups 一起维护。
    by_url: dict[str, dict[str, dict[str, str]]] = {}
    sources: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(items, key=lambda value: str(value.get("date") or "")):
        subject = clean_text(str(item.get("subject") or ""), 90) or "订阅摘要"
        sources.setdefault(subject, []).append(item)
        links = by_url.setdefault(subject, {})
        for link in item.get("links") or []:
            if not isinstance(link, dict):
                continue
            url = str(link.get("normalized_url") or link.get("url") or "").strip()
            if not url or url in links:
                continue
            links[url] = {
                "url": url,
                "title": clean_text(str(link.get("anchor_text") or "").strip(), 90) or url,
                "context": clean_text(str(link.get("context") or "").strip(), 110),
            }
    return {
        subject: {"items": sources[subject], "links": list(links.values())}
        for subject, links in by_url.items()
        if links
    }


def digest_link_line(link: dict[str, str]) -> str:
    """一条链接一行：标题是点击目标，后面压一行摘要，只扫列表就能决定点不点开。"""
    line = f"- [{link['title']}]({link['url']})"
    return f"{line} — {link['context']}" if link["context"] else line


def digest_section_lines(scan: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    """digest 自己的展示区。空列表表示没有可展示的 digest，调用方据此整节省略。"""
    groups = merge_digest_groups(items)
    if not groups:
        return []
    lines = ["", "## 订阅摘要", ""]
    for subject, group in groups.items():
        # 合并去重之后仍要能追回是哪几封邮件带来的，否则 digest 区没有溯源。
        sources = " ".join(source_markdown_link(scan, item) for item in group["items"])
        lines.append(f"### {subject}")
        lines.append(f"来源：{sources}")
        lines.extend(digest_link_line(link) for link in group["links"])
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return lines


def build_intel_brief_draft(scan: dict[str, Any], reason: str = "", *, notice: str = "") -> str:
    all_items = unique_delivery_items([item for item in scan.get("items", []) if isinstance(item, dict)])
    # digest 有自己的展示区：它的价值是整张链接列表，挤进 top5 只会被截断成一行。
    digest_items = [item for item in all_items if is_digest_item(item)]
    items = [item for item in all_items if not is_digest_item(item)]
    action_items = [item for item in items if action_reason(item)]
    intel_items = [item for item in items if not action_reason(item) and delivery_primary_topic(item)]
    other_items = [
        item
        for item in items
        if item not in action_items
        and item not in intel_items
        and (item.get("snippet") or item.get("subject"))
    ]
    top_items = sorted(intel_items, key=lambda item: (-delivery_item_score(item), str(item.get("date") or "")))[:5]
    displayed: list[dict[str, Any]] = []
    lines = [f"# Morning Brief - {scan['date']}", "", "## 今天先看", ""]
    if top_items:
        for item in top_items:
            lines.append(delivery_item_line(scan, item))
            displayed.append(item)
    else:
        lines.append("- 今天没有需要优先处理或记录的邮件线索。")

    displayed_uids = {str(item.get("uid") or "") for item in displayed}
    if action_items:
        lines.extend(["", "## 需要处理", ""])
        for item in action_items[:5]:
            lines.append(delivery_item_line(scan, item))
            displayed.append(item)
            displayed_uids.add(str(item.get("uid") or ""))

    topic_sections: dict[str, list[dict[str, Any]]] = {}
    for item in intel_items:
        uid = str(item.get("uid") or "")
        if uid in displayed_uids:
            continue
        topic = delivery_primary_topic(item)
        if not topic:
            continue
        topic_name = str(topic.get("name") or topic.get("id") or "情报线索")
        topic_sections.setdefault(topic_name, []).append(item)
    remaining_other = [item for item in other_items if str(item.get("uid") or "") not in displayed_uids]
    if topic_sections:
        lines.extend(["", "## 情报线索", ""])
        for topic_name, topic_items in topic_sections.items():
            lines.append(f"### {topic_name}")
            for item in sorted(topic_items, key=lambda value: -delivery_item_score(value))[:3]:
                lines.append(delivery_item_line(scan, item))
                displayed.append(item)
                displayed_uids.add(str(item.get("uid") or ""))
            lines.append("")
        if lines[-1] == "":
            lines.pop()
    if remaining_other:
        lines.extend(["", "## 其他", ""])
        for item in remaining_other[:5]:
            lines.append(delivery_item_line(scan, item))
            displayed.append(item)

    digest_lines = digest_section_lines(scan, digest_items)
    if digest_lines:
        lines.extend(digest_lines)
        displayed.extend(digest_items)

    boundaries = delivery_evidence_boundary(scan, displayed)
    if notice:
        boundaries = [notice, *boundaries]
    if boundaries:
        lines.extend(["", "## 证据边界", ""])
        lines.extend(f"- {line}" for line in boundaries)
    return "\n".join(lines).rstrip() + "\n"


def has_source_index(markdown: str) -> bool:
    return "email://" in markdown


def ensure_intel_brief_traceability(markdown: str, scan: dict[str, Any]) -> str:
    text = markdown.rstrip()
    additions: list[str] = []
    if scan.get("possibly_truncated") and "触达上限" not in text and "可能有遗漏" not in text:
        additions.extend(["## 证据边界", "", "触达上限，可能有遗漏。", ""])
    if "snippet_only" in json.dumps(scan, ensure_ascii=False) and "仅基于邮件摘要" not in text and "待外部验证" not in text:
        if "## 证据边界" not in "\n".join(additions):
            additions.extend(["## 证据边界", ""])
        additions.extend(["- 部分条目仅基于邮件摘要；公开链接未抓取时只作为线索，不作事实验证。", ""])
    if additions:
        text = text + "\n\n" + "\n".join(additions).rstrip()
    return text.rstrip() + "\n"


LOW_SIGNAL_BRIEF_PATTERNS = (
    "没有可用信息",
    "没有可用的新证据",
    "没有有效新线索",
    "没有实质性行业情报",
    "没有真正可用",
    "没有直接线索",
    "没有直接涉及",
    "今天没有可用",
    "没有看到必须",
    "没有明确需要立即",
    "没有必须立即",
    "没有必须立刻",
    "没有完整日志",
    "没有业务内容",
    "不形成可用判断",
    "不能确认完整",
    "不足以证明",
    "没有直接讨论",
    "不能仅凭摘要",
    "不能从",
    "不应从",
    "可靠判断",
    "可以忽略",
    "只看到关键词命中",
    "只显示“有文章匹配",
    "只显示\"有文章匹配",
    "没有具体标题",
    "没有具体文章标题",
    "没有标题、链接或正文",
    "没有可读标题",
    "没有露出具体文章标题",
    "未看到发送环境",
    "无法判断是否有实质",
    "无法判断其中是否",
    "不能确认是否有实质",
    "不能据此判断",
    "不能据此形成",
    "不能提炼观点",
    "不值得展开",
)

LOW_SIGNAL_BRIEF_REGEXES = (
    re.compile(r"还包含一个.*"),
    re.compile(r"还包含.*(?:链接|页面)[。；;]?$"),
)


def markdown_heading_level(block: str) -> int:
    match = re.match(r"^(#{2,6})\s+\S", block.strip())
    return len(match.group(1)) if match else 0


def prune_low_signal_block(block: str) -> str:
    block = re.sub(r"，但", "。但", block)
    parts = re.findall(r".+?(?:[。！？；;]|$)", block, flags=re.DOTALL)
    kept: list[str] = []
    for part in parts:
        text = re.sub(r"^\s*但", "", part.strip())
        if not text:
            continue
        if any(pattern in text for pattern in LOW_SIGNAL_BRIEF_PATTERNS):
            continue
        if any(pattern.search(text) for pattern in LOW_SIGNAL_BRIEF_REGEXES):
            continue
        kept.append(text)
    return "".join(kept).strip()


def prune_empty_signal_sections(markdown: str) -> str:
    blocks = [block.strip() for block in re.split(r"\n{2,}", markdown.strip()) if block.strip()]
    if not blocks:
        return markdown
    kept: list[str] = []
    for block in blocks:
        if block.startswith("# ") or markdown_heading_level(block):
            kept.append(block)
            continue
        cleaned = prune_low_signal_block(block)
        if cleaned:
            kept.append(cleaned)
    pruned: list[str] = []
    for index, block in enumerate(kept):
        level = markdown_heading_level(block)
        if level:
            next_level = 0
            for later in kept[index + 1 :]:
                next_level = markdown_heading_level(later)
                if next_level or later:
                    break
            if next_level and next_level <= level:
                continue
            if index == len(kept) - 1:
                continue
        pruned.append(block)
    return "\n\n".join(pruned).rstrip() + "\n"


def render_summary_prompt(prompt_path: Path, scan: dict[str, Any], payload: dict[str, Any]) -> str:
    """把 scan 填进 email_summary_prompt.md 的占位符。

    podsum 与 hermes 两条引擎填的是同一组占位符，只有 scan_json 的来源不同：前者给
    原始 scan，后者给预处理摘要。分开写的话，prompt 里加一个占位符就会在漏改的那条
    路径上抛 KeyError。
    """
    return prompt_path.read_text(encoding="utf-8").format(
        date=scan["date"],
        generated_at=now_stamp(),
        account=scan.get("account", ""),
        window=scan.get("window", ""),
        raw_count=scan.get("raw_count", 0),
        scan_date=scan["date"],
        scan_json=json.dumps(payload, ensure_ascii=False, indent=2),
    )


def template_fallback_brief(scan: dict[str, Any], reason: str, cause: str) -> str:
    """降级必须写进正文，否则没人知道今天这份是模板产出的。"""
    return build_intel_brief_draft(scan, reason, notice=f"降级：{cause}，本篇由确定性模板产出。")


def llm_brief_markdown(
    scan: dict[str, Any],
    *,
    hermes: str,
    prompt_path: Path,
    project_dir: Path,
    timeout: int,
    reason: str = "",
) -> str:
    """LLM 写 brief，失败或空输出回落到确定性模板。

    模板是兜底不是常态：不调 LLM 的 brief 只能一行一封邮件地罗列，没有判断。
    降级必须写进正文，否则没人知道今天这份是模板产出的。
    """
    prompt = render_summary_prompt(prompt_path, scan, scan)
    ok, value = run_hermes_prompt(str(hermes), prompt, cwd=str(project_dir), timeout=timeout)
    if not ok:
        return template_fallback_brief(scan, reason, f"LLM 调用失败（{value}）")
    if not str(value or "").strip():
        return template_fallback_brief(scan, reason, "LLM 返回空输出")
    return ensure_intel_brief_traceability(prune_empty_signal_sections(value), scan)


def render_report(args: argparse.Namespace, scan: dict[str, Any]) -> str:
    summary_engine = getattr(args, "summary_engine", DEFAULT_SUMMARY_ENGINE)
    if summary_engine == "podsum":
        reason = "dry-run: Podsum local summary engine; no external summary engine called" if args.dry_run else ""
        pack = EmailEvidencePack.from_dict(scan)
        topic_map = scan.get("topic_map", {}) if isinstance(scan.get("topic_map"), dict) else {}
        return brief_agent.compose_with_need_store(pack, topic_map, empty_need_store(), "", {}, reason).email_intel_brief.markdown
    if args.dry_run:
        pack = EmailEvidencePack.from_dict(scan)
        topic_map = scan.get("topic_map", {}) if isinstance(scan.get("topic_map"), dict) else {}
        return brief_agent.compose_with_need_store(pack, topic_map, empty_need_store(), "", {}, "dry-run: skipped Hermes summary").email_intel_brief.markdown
    prompt = render_summary_prompt(args.email_summary_prompt, scan, preprocessed_evidence_digest(args, scan))
    ok, value = run_hermes_prompt(str(args.hermes), prompt, cwd=str(args.project_dir), timeout=args.hermes_timeout)
    if not ok:
        return build_intel_brief_draft(scan, f"Hermes 摘要失败：{value}")
    rendered = value or build_intel_brief_draft(scan, "Hermes returned empty output")
    rendered = prune_empty_signal_sections(rendered)
    return ensure_intel_brief_traceability(rendered, scan)


def write_report(root: Path, scan: dict[str, Any], markdown: str) -> Path:
    directory = email_reports_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"email-summary-{scan['date']}.md"
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return path


def make_brief_writer(args: argparse.Namespace) -> Any:
    """brief 的执笔人。dry-run 返回 None，表示不调 LLM、保持确定性输出。

    模板是兜底不是常态：不调 LLM 的 brief 只能一行一封邮件地罗列，没有判断。
    """
    if args.dry_run:
        return None

    def write(scan: dict[str, Any], reason: str) -> str:
        return llm_brief_markdown(
            scan,
            hermes=str(args.hermes),
            prompt_path=args.email_summary_prompt,
            project_dir=args.project_dir,
            timeout=args.hermes_timeout,
            reason=reason,
        )

    return write


def run_podsum_email_graph(
    args: argparse.Namespace,
    scan: dict[str, Any] | None,
    scan_path: Path | None,
    policy: dict[str, Any],
    topic_map: dict[str, Any],
    reason: str,
) -> tuple[Path, Path, dict[str, Any]]:
    if not email_graph.langgraph_available():
        raise RuntimeError("LangGraph is required for --summary-engine podsum email-summary orchestration")
    artifact_dir = email_reports_dir(args.output)
    source_scan = scan or _read_scan_file(scan_path)
    run_id = f"email-summary-{source_scan.get('date', now_stamp())}-{int(time.time())}"
    context = email_graph.build_email_run_context(
        policy,
        topic_map,
        scan,
        args.enrich_links,
        fetch_link_context,
        reason,
        {},
        make_brief_writer(args),
    )
    initial_state = email_graph.initial_email_run_state(
        run_id,
        str(source_scan.get("account", "")),
        str(source_scan.get("date", "")),
        artifact_dir,
        scan_path,
    )
    final_state = email_graph.run_email_run_graph(initial_state, context)
    evidence_pack_path = Path(final_state["evidence_pack_path"])
    brief_path = Path(final_state["brief_path"])
    persisted_scan = _read_scan_file(evidence_pack_path)
    return evidence_pack_path, brief_path, persisted_scan


def _read_scan_file(scan_path: Path | None) -> dict[str, Any]:
    if scan_path is None:
        raise ValueError("scan_path is required when scan is not provided")
    value = json.loads(scan_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"email scan file must contain a JSON object: {scan_path}")
    return value


def send_report(args: argparse.Namespace, report_path: Path, scan: dict[str, Any]) -> Path | None:
    report_text = report_path.read_text(encoding="utf-8", errors="replace")
    checklist = review_checklist(scan, report_text)
    if not args.dry_run and not checklist.get("ready_to_send"):
        raise RuntimeError(f"email brief failed Review Checklist: {', '.join(checklist.get('risks', []))}")
    delivery = getattr(args, "delivery", DEFAULT_DELIVERY)
    if delivery == "email":
        config = smtp_config(args)
        subject = podsum_subject(scan["date"], "Email Brief")
        body = (
            f"Podsum Email Brief {scan['date']}\n\n"
            f"账号: {scan.get('account', '')}\n"
            f"扫描窗口: {scan.get('window', '')}\n"
            f"原始邮件数: {scan.get('raw_count', 0)}\n"
            f"源 Markdown: {report_path}\n"
            f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n\n"
            f"{report_text}\n"
        )
        html_body = email_html_body(report_text, scan, report_path)
        if args.dry_run:
            recipients = ", ".join(config["recipients"]) or "(missing recipient)"
            log(f"would email HTML summary to {recipients}: {report_path}")
        else:
            log(
                send_smtp_email(
                    host=config["host"],
                    port=config["port"],
                    username=config["username"],
                    password=config["password"],
                    mail_from=config["mail_from"],
                    recipients=config["recipients"],
                    subject=subject,
                    body=body,
                    html_body=html_body,
                    timeout=config["timeout"],
                    use_ssl=config["use_ssl"],
                    starttls=config["starttls"],
                    tls_verify=config["tls_verify"],
                )
            )
        return None

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
    subject = podsum_subject(scan["date"], "Email Summary")
    if args.dry_run:
        log(f"would send email summary: {epub_path}")
    else:
        target = podsum_runtime.resolve_target(
            args.target, podsum_runtime.load_env_file(args.env_file), args.env_file
        )
        log(send_hermes_file(str(args.hermes), target, subject, message))
    return epub_path


def run(args: argparse.Namespace) -> tuple[Path, Path | None, Path | None]:
    policy = load_link_policy(args.email_link_policy)
    topic_map = load_topic_map(args.email_topic_file)
    summary_engine = getattr(args, "summary_engine", DEFAULT_SUMMARY_ENGINE)
    if args.scan_file:
        scan = None
        scan_path = args.scan_file
    elif args.eml_dir:
        scan = scan_eml_dir(args, policy)
        scan_path = None
    else:
        if not args.allow_imap_read:
            raise RuntimeError(
                "Reading Gmail/IMAP requires explicit confirmation. "
                "Re-run with --allow-imap-read after confirming this should access the mailbox."
            )
        scan = scan_imap(args, policy)
        scan_path = None

    # 收件箱被清空的那天没有证据可写。空 brief 会被 review_checklist 判为不合格并
    # 抛错——那不是失败，是无事发生。判空必须在写文件之前：写过就已经覆盖掉当天
    # 那份好的 summary 了。scan 记录照写，那是「今天确实跑过、确实没邮件」的凭据。
    source_scan = scan if scan is not None else _read_scan_file(scan_path)
    if not source_scan.get("items"):
        empty_pack = evidence_agent.build_evidence_pack(
            source_scan, policy, topic_map, enrich_links=False, fetcher=fetch_link_context
        ).to_dict()
        log("no mail in window; skip brief")
        return write_scan(args.output, empty_pack), None, None

    if summary_engine == "podsum":
        reason = "dry-run: Podsum local summary engine; no external summary engine called" if args.dry_run else ""
        scan_path, report_path, persisted_scan = run_podsum_email_graph(
            args,
            scan,
            scan_path,
            policy,
            topic_map,
            reason,
        )
        epub_path = None if args.no_send else send_report(args, report_path, persisted_scan)
        return scan_path, report_path, epub_path

    source_scan = scan if scan is not None else _read_scan_file(scan_path)
    persisted_scan = evidence_agent.build_evidence_pack(source_scan, policy, topic_map, args.enrich_links, fetch_link_context).to_dict()
    persisted_scan_path = write_scan(args.output, persisted_scan)
    report_path = email_reports_dir(args.output) / f"email-summary-{persisted_scan['date']}.md"
    if args.dry_run:
        composition = brief_agent.compose_and_persist(
            EmailEvidencePack.from_dict(persisted_scan),
            topic_map,
            email_reports_dir(args.output),
            str(report_path),
            {},
            "dry-run: skipped Hermes summary",
        )
        report_path = write_report(args.output, persisted_scan, composition.email_intel_brief.markdown)
    else:
        report_path = write_report(args.output, persisted_scan, render_report(args, persisted_scan))
    epub_path = None if args.no_send else send_report(args, report_path, persisted_scan)
    return persisted_scan_path, report_path, epub_path


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
    parser.add_argument("--email-evidence-preprocess-prompt", type=Path, default=DEFAULT_EVIDENCE_PREPROCESS_PROMPT)
    parser.add_argument("--email-link-policy", type=Path, default=DEFAULT_LINK_POLICY)
    parser.add_argument("--email-topic-file", type=Path, default=DEFAULT_TOPIC_FILE)
    parser.add_argument("--summary-engine", choices=("podsum", "hermes"), default=DEFAULT_SUMMARY_ENGINE)
    parser.add_argument("--enrich-links", action="store_true")
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--delivery", choices=("hermes", "email"), default="")
    parser.add_argument("--smtp-host", default="")
    parser.add_argument("--smtp-port", type=int, default=0)
    parser.add_argument("--smtp-user", default="")
    parser.add_argument("--smtp-pass", default="")
    parser.add_argument("--smtp-from", default="")
    parser.add_argument("--smtp-to", default="")
    parser.add_argument("--smtp-starttls", action="store_true")
    parser.add_argument("--smtp-no-ssl", action="store_true")
    parser.add_argument("--smtp-no-tls-verify", action="store_true")
    parser.add_argument("--smtp-timeout", type=int, default=0)
    parser.add_argument("--hermes", type=Path, default=DEFAULT_HERMES)
    parser.add_argument("--hermes-timeout", type=int, default=180)
    parser.add_argument("--no-llm-evidence-preprocess", action="store_true")
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
    args.email_evidence_preprocess_prompt = args.email_evidence_preprocess_prompt.expanduser()
    args.email_link_policy = args.email_link_policy.expanduser()
    args.email_topic_file = args.email_topic_file.expanduser()
    args.project_dir = args.project_dir.expanduser()
    args.hermes = args.hermes.expanduser()
    args.delivery = podsum_runtime.resolve_delivery(
        getattr(args, "delivery", "") or "", podsum_runtime.load_env_file(args.env_file)
    )
    if args.smtp_port < 0:
        raise SystemExit("--smtp-port must be >= 0")
    if args.smtp_timeout < 0:
        raise SystemExit("--smtp-timeout must be >= 0")
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


def run_and_report(args: argparse.Namespace) -> int:
    """跑一次 email summary，并把产出报给用户。

    两个入口（podsum.py 的子命令和本模块的 main）共用这一段。分开写过一次，代价是
    加一行日志要记得改两处：空扫描日的守卫就差点只落在其中一边。
    """
    try:
        scan_path, report_path, epub_path = run(args)
    except Exception as exc:
        log(f"Email summary failed: {error_text(exc)}")
        return 1
    if report_path is None:
        # 空扫描日只写了 scan。没写 brief 就不许说写了，否则日志里会出现一个
        # 不存在的路径。
        return 0
    log(f"Wrote email scan: {scan_path}")
    log(f"Wrote email summary: {report_path}")
    if epub_path:
        log(f"Wrote email summary EPUB: {epub_path}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    normalize_args(args)
    return run_and_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
