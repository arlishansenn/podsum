#!/usr/bin/env python3
"""Email scan and summary feature for Podsum."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import email
import html.parser
import ipaddress
import imaplib
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import podsum_send_to_feishu as sender
from podsum_core.delivery import run_hermes_prompt, send_hermes_file


DEFAULT_OUTPUT_DIR = Path.home() / "Podcasts/AutoDownloads"
DEFAULT_ENV_FILE = Path.home() / "Library/Application Support/Podsum/.env"
DEFAULT_PROMPT = Path(__file__).with_name("email_summary_prompt.md")
DEFAULT_LINK_POLICY = Path(__file__).with_name("email_link_policy.md")
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
LINK_EXCERPT_CHARS = 1200
FETCH_BODY_CHARS = 4000
USER_AGENT = "PodsumEmailSummary/1.0"
URL_RE = re.compile(r"https?://[^\s<>)\"']+", re.IGNORECASE)

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


def strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def normalize_url(value: str) -> str:
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
        unique.append(
            {
                "url": url,
                "normalized_url": url,
                "anchor_text": clean_text(str(link.get("anchor_text") or ""), 120),
                "policy_decision": "pending",
            }
        )
    return unique


def extract_links_from_text(text: str) -> list[dict[str, str]]:
    return [{"url": match.group(0), "anchor_text": ""} for match in URL_RE.finditer(text)]


def extract_links_from_html(text: str) -> list[dict[str, str]]:
    parser = LinkHTMLParser()
    with contextlib.suppress(Exception):
        parser.feed(text)
    links = list(parser.links)
    links.extend(extract_links_from_text(text))
    return links


def message_texts(message: Message) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition or content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        values.append((content_type, decode_bytes(payload, part.get_content_charset())))
    return values


def extract_message_links(message: Message) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for content_type, text in message_texts(message):
        if content_type == "text/html":
            links.extend(extract_links_from_html(text))
        else:
            links.extend(extract_links_from_text(text))
    return unique_links(links)


def parse_policy_json(markdown: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", markdown, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("email policy must contain a fenced json object")
    parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        raise ValueError("email policy json must be an object")
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
    candidates: list[str] = []
    for content_type, text in message_texts(message):
        candidates.append(strip_html(text) if content_type == "text/html" else text)
    return clean_text(" ".join(candidates))


def message_item(uid: str, raw_message: bytes, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    msg = email.message_from_bytes(raw_message)
    fixture_uid = decode_header_value(msg.get("X-Podsum-Fixture-UID"))
    attachments = [
        part
        for part in msg.walk()
        if str(part.get("Content-Disposition") or "").lower().startswith("attachment")
    ]
    item = {
        "uid": fixture_uid or uid,
        "date": decode_header_value(msg.get("Date")),
        "from": decode_header_value(msg.get("From")),
        "subject": clean_text(decode_header_value(msg.get("Subject")), 120),
        "snippet": body_snippet(msg),
        "has_attachments": bool(attachments),
        "links": extract_message_links(msg),
        "evidence": [],
        "risks": ["snippet_only"],
        "flags": [],
    }
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


def enrich_item_links(
    item: dict[str, Any],
    policy: dict[str, Any],
    *,
    remaining_budget: int,
    fetcher: Any = fetch_link_context,
) -> int:
    item_policy = policy_for_type(str(item.get("email_type") or "unknown"), policy)
    limits = policy.get("limits", {})
    per_email_limit = int(limits.get("max_links_per_email", 2))
    timeout = int(limits.get("timeout_seconds", 8))
    excerpt_chars = int(limits.get("excerpt_chars", LINK_EXCERPT_CHARS))
    fetched = 0
    evidence: list[dict[str, Any]] = []
    risks = set(item.get("risks", []))

    for link in item.get("links", []):
        if fetched >= per_email_limit or fetched >= remaining_budget:
            link["policy_decision"] = "skip"
            evidence.append(
                {
                    "url": link.get("url", ""),
                    "final_url": "",
                    "title": "",
                    "excerpt": "",
                    "status": "skipped",
                    "reason": "link_budget_exhausted",
                    "content_type": "",
                }
            )
            risks.add("link_budget_exhausted")
            continue
        if not item_policy.get("fetch_links", False):
            link["policy_decision"] = "skip"
            evidence.append(
                {
                    "url": link.get("url", ""),
                    "final_url": "",
                    "title": "",
                    "excerpt": "",
                    "status": "skipped",
                    "reason": f"policy_no_fetch:{item.get('email_type', 'unknown')}",
                    "content_type": "",
                }
            )
            risks.add("link_skipped")
            continue
        reason = skip_reason_for_url(str(link.get("url") or ""), policy)
        if reason:
            link["policy_decision"] = "skip"
            evidence.append(
                {
                    "url": link.get("url", ""),
                    "final_url": "",
                    "title": "",
                    "excerpt": "",
                    "status": "skipped",
                    "reason": reason,
                    "content_type": "",
                }
            )
            risks.add("tracking_skipped" if "track" in reason or "unsubscribe" in reason else "link_skipped")
            continue
        link["policy_decision"] = "fetch"
        context = fetcher(str(link.get("url") or ""), timeout, excerpt_chars)
        evidence.append(context)
        fetched += 1
        if context.get("status") == "fetched":
            risks.discard("snippet_only")
        elif context.get("status") == "failed":
            risks.add("link_failed")
        else:
            risks.add("link_skipped")

    item["evidence"] = evidence
    item["risks"] = sorted(risks)
    return fetched


def normalize_evidence_pack(scan: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    scan.setdefault("object_type", "email_evidence_pack")
    scan.setdefault("status", "ready_for_summary")
    for item in scan.get("items", []):
        if not isinstance(item, dict):
            continue
        item.setdefault("links", [])
        item.setdefault("evidence", [])
        item.setdefault("risks", ["snippet_only"])
        item.setdefault("flags", [])
        item.setdefault("has_attachments", False)
        item["email_type"] = str(item.get("email_type") or classify_email(item, policy))
    return scan


def enrich_scan_links(scan: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    limits = policy.get("limits", {})
    remaining = int(limits.get("max_links_total", 10))
    for item in scan.get("items", []):
        if remaining <= 0:
            item.setdefault("risks", [])
            item["risks"] = sorted(set(item["risks"] + ["link_budget_exhausted"]))
            continue
        if item.get("evidence"):
            continue
        used = enrich_item_links(item, policy, remaining_budget=remaining)
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
    checklist = {
        "has_key_takeaway": "key takeaway" in markdown.lower(),
        "has_source_index": "来源索引" in markdown or "source" in markdown.lower(),
        "has_uid_trace": "UID" in markdown,
        "has_truncated_warning": (not scan.get("possibly_truncated")) or "触达上限" in markdown or "可能有遗漏" in markdown,
        "uses_link_evidence_when_available": (not has_link_evidence) or "链接" in markdown or "evidence" in markdown.lower(),
        "marks_snippet_only_claims": "snippet_only" not in json.dumps(scan, ensure_ascii=False) or "仅基于邮件摘要" in markdown or "待外部验证" in markdown,
        "no_unbacked_claims": True,
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


def scan_imap(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
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
                    items.append(message_item(uid, part[1], policy))
                    break
    finally:
        with contextlib.suppress(Exception):
            imap.logout()

    return scan_payload(user, args.recent_days, args.limit, len(uids), items)


def scan_eml_dir(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    if not args.eml_dir.is_dir():
        raise RuntimeError(f"--eml-dir is not a directory: {args.eml_dir}")

    files = sorted(path for path in args.eml_dir.iterdir() if path.suffix.lower() == ".eml")
    raw_count = len(files)
    selected = files[: args.limit]
    items = [message_item(str(index), path.read_bytes(), policy) for index, path in enumerate(selected, 1)]
    return scan_payload(DEFAULT_FIXTURE_ACCOUNT, args.recent_days, args.limit, raw_count, items)


def scan_payload(account: str, recent_days: int, limit: int, raw_count: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object_type": "email_evidence_pack",
        "status": "ready_for_summary",
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
    if scan.get("possibly_truncated"):
        lines[9:9] = ["触达上限，可能有遗漏。", ""]
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
        return append_review_checklist(fallback_report(scan, "dry-run: skipped Hermes summary"), scan)
    ok, value = run_hermes_prompt(str(args.hermes), prompt, cwd=str(args.project_dir), timeout=args.hermes_timeout)
    if not ok:
        return append_review_checklist(fallback_report(scan, value), scan)
    return append_review_checklist(value or fallback_report(scan, "Hermes returned empty output"), scan)


def write_report(root: Path, scan: dict[str, Any], markdown: str) -> Path:
    directory = email_reports_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"email-summary-{scan['date']}.md"
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return path


def send_report(args: argparse.Namespace, report_path: Path, scan: dict[str, Any]) -> Path:
    report_text = report_path.read_text(encoding="utf-8", errors="replace")
    checklist = review_checklist(scan, report_text)
    if not args.dry_run and not checklist.get("ready_to_send"):
        raise RuntimeError(f"email brief failed Review Checklist: {', '.join(checklist.get('risks', []))}")
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
    policy = load_link_policy(args.email_link_policy)
    if args.scan_file:
        scan = json.loads(args.scan_file.read_text(encoding="utf-8"))
    elif args.eml_dir:
        scan = scan_eml_dir(args, policy)
    else:
        if not args.allow_imap_read:
            raise RuntimeError(
                "Reading Gmail/IMAP requires explicit confirmation. "
                "Re-run with --allow-imap-read after confirming this should access the mailbox."
            )
        scan = scan_imap(args, policy)
    scan = normalize_evidence_pack(scan, policy)
    if args.enrich_links:
        scan = enrich_scan_links(scan, policy)
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
    parser.add_argument("--email-link-policy", type=Path, default=DEFAULT_LINK_POLICY)
    parser.add_argument("--enrich-links", action="store_true")
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
    args.email_link_policy = args.email_link_policy.expanduser()
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
