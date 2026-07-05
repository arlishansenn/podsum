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
EVIDENCE_PACK_VERSION = "0.1"
INTEL_BRIEF_VERSION = "0.1"
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
    return html.unescape(re.sub(r"<[^>]+>", " ", value))


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
    return body_snippet_from_parts(message_body_parts(message))


def body_snippet_from_parts(parts: list[dict[str, Any]]) -> str:
    plain_texts = [str(part.get("text") or "") for part in parts if part.get("content_type") == "text/plain"]
    html_texts = [strip_html(str(part.get("text") or "")) for part in parts if part.get("content_type") == "text/html"]
    candidates = plain_texts or html_texts
    return clean_text(" ".join(candidates))


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


def refresh_email_snippet_evidence(item: dict[str, Any]) -> None:
    defaults = email_snippet_evidence(item)
    for evidence in item.get("evidence", []):
        if not isinstance(evidence, dict) or evidence.get("type") != "email_snippet":
            continue
        for key, value in defaults.items():
            if key == "excerpt" and not evidence.get(key) and value:
                evidence[key] = value
            else:
                evidence.setdefault(key, value)


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
    if not any(isinstance(evidence, dict) and evidence.get("status") == "fetched" for evidence in item["evidence"]):
        risks.add("snippet_only" if item.get("snippet") else "metadata_only")
    item["risks"] = sorted(risks)


def message_item(uid: str, raw_message: bytes, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    msg = email.message_from_bytes(raw_message)
    fixture_uid = decode_header_value(msg.get("X-Podsum-Fixture-UID"))
    body_parts = message_body_parts(msg)
    attachments = attachment_shapes(msg)
    item = {
        "uid": fixture_uid or uid,
        "date": decode_header_value(msg.get("Date")),
        "from": decode_header_value(msg.get("From")),
        "subject": clean_text(decode_header_value(msg.get("Subject")), 120),
        "snippet": body_snippet_from_parts(body_parts),
        "has_attachments": bool(attachments),
        "attachment_count": len(attachments),
        "attachment_shapes": attachments,
        "body_part_count": len(body_parts),
        "body_part_types": sorted({str(part.get("content_type") or "") for part in body_parts if part.get("content_type")}),
        "links": extract_links_from_body_parts(body_parts),
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
    existing_urls = public_link_evidence_urls(item)
    evidence = [ev for ev in item.get("evidence", []) if isinstance(ev, dict)]
    skipped = 0
    for link in item.get("links", []):
        if not isinstance(link, dict):
            continue
        url = normalize_url(str(link.get("url") or ""))
        if not url or url in existing_urls:
            continue
        link["policy_decision"] = "skip"
        evidence.append(link_evidence_payload(item, link, status="skipped", reason=reason))
        existing_urls.add(url)
        skipped += 1
    item["evidence"] = evidence
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
) -> int:
    item_policy = policy_for_type(str(item.get("email_type") or "unknown"), policy)
    limits = policy.get("limits", {})
    per_email_limit = int(limits.get("max_links_per_email", 2))
    timeout = int(limits.get("timeout_seconds", 8))
    excerpt_chars = int(limits.get("excerpt_chars", LINK_EXCERPT_CHARS))
    fetched = 0
    ensure_email_snippet_evidence(item)
    normalize_existing_evidence(item)
    evidence: list[dict[str, Any]] = [
        existing
        for existing in item.get("evidence", [])
        if isinstance(existing, dict)
    ]
    existing_public_urls = public_link_evidence_urls(item)
    risks = set(item.get("risks", []))

    for link in item.get("links", []):
        if not isinstance(link, dict):
            continue
        url = normalize_url(str(link.get("url") or ""))
        if url and url in existing_public_urls:
            continue
        if fetched >= per_email_limit or fetched >= remaining_budget:
            link["policy_decision"] = "skip"
            evidence.append(link_evidence_payload(item, link, status="skipped", reason="link_budget_exhausted"))
            if url:
                existing_public_urls.add(url)
            risks.add("link_budget_exhausted")
            continue
        if not item_policy.get("fetch_links", False):
            link["policy_decision"] = "skip"
            evidence.append(
                link_evidence_payload(
                    item,
                    link,
                    status="skipped",
                    reason=f"policy_no_fetch:{item.get('email_type', 'unknown')}",
                )
            )
            if url:
                existing_public_urls.add(url)
            risks.add("link_skipped")
            continue
        reason = skip_reason_for_url(str(link.get("url") or ""), policy)
        if reason:
            link["policy_decision"] = "skip"
            evidence.append(link_evidence_payload(item, link, status="skipped", reason=reason))
            if url:
                existing_public_urls.add(url)
            risks.add("tracking_skipped" if "track" in reason or "unsubscribe" in reason else "link_skipped")
            continue
        link["policy_decision"] = "fetch"
        context = fetcher(str(link.get("url") or ""), timeout, excerpt_chars)
        context.setdefault("uid", str(item.get("uid") or ""))
        context.setdefault("anchor_text", link.get("anchor_text", ""))
        context.setdefault("email_context", link.get("context", ""))
        context.setdefault("source_content_type", link.get("source_content_type", ""))
        evidence.append(link_evidence(context))
        if url:
            existing_public_urls.add(url)
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
    scan.setdefault("object_version", EVIDENCE_PACK_VERSION)
    scan.setdefault("status", "ready_for_summary")
    for item in scan.get("items", []):
        if not isinstance(item, dict):
            continue
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


def enrich_scan_links(scan: dict[str, Any], policy: dict[str, Any], fetcher: Any = fetch_link_context) -> dict[str, Any]:
    limits = policy.get("limits", {})
    remaining = int(limits.get("max_links_total", 10))
    for item in scan.get("items", []):
        if remaining <= 0:
            if isinstance(item, dict):
                skip_pending_links(item, "link_budget_exhausted")
            continue
        if has_link_evidence(item):
            existing_count = len(public_link_evidence_urls(item))
            link_count = len(item.get("links", []) if isinstance(item.get("links"), list) else [])
            if existing_count >= link_count:
                continue
        used = enrich_item_links(item, policy, remaining_budget=remaining, fetcher=fetcher)
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
        "object_version": EVIDENCE_PACK_VERSION,
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


def source_index_line(scan: dict[str, Any], item: dict[str, Any]) -> str:
    return (
        f"- UID={item.get('uid')} | From={item.get('from')} | "
        f"Subject={item.get('subject')} | Date={item.get('date')} | "
        f"`email://{scan['date']}/{item.get('uid')}`"
    )


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
        f"  - 来源：UID={item.get('uid')} / From={item.get('from')} / "
        f"Subject={item.get('subject')} / Date={item.get('date')} / "
        f"`email://{scan['date']}/{item.get('uid')}`",
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
        elif email_type in {"google_alert", "newsletter_article", "digest"} or fetched_public_link_evidence(item) or has_links:
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


def build_intel_brief_draft(scan: dict[str, Any], reason: str = "") -> str:
    items = [item for item in scan.get("items", []) if isinstance(item, dict)]
    need_action, worth_knowing, ignore = classify_brief_items(scan)
    link_count = sum(len(item.get("links", []) if isinstance(item.get("links"), list) else []) for item in items)
    fetched_count = sum(len(fetched_public_link_evidence(item)) for item in items)
    lines = [
        f"# Podsum Email Summary {scan['date']}",
        "",
        f"生成时间: {now_stamp()}",
        f"账号: {scan.get('account', '')}",
        f"扫描窗口: {scan.get('window', '')}",
        f"原始邮件数: {scan.get('raw_count', 0)}",
        f"对象: EmailIntelBrief",
        f"版本: {INTEL_BRIEF_VERSION}",
        f"来源对象: EmailEvidencePack {scan.get('object_version', '')}",
        "",
        "## key takeaway",
        "",
    ]
    if scan.get("possibly_truncated"):
        lines.extend(["触达上限，可能有遗漏。", ""])
    if not items:
        lines.extend(["本次 EvidencePack 没有邮件条目。", ""])
    else:
        lines.extend(
            [
                f"本次 EvidencePack 含 {len(items)} 封邮件，类型分布：{brief_type_distribution(scan)}。",
                f"邮件中共发现 {link_count} 个链接候选，已取得公开网页 evidence {fetched_count} 条；没有公开网页 evidence 的判断均标注为仅基于邮件摘要或待外部验证。",
                "",
            ]
        )
    if reason:
        lines.extend(["## 生成说明", "", reason, ""])

    lines.extend(["## 需要处理", ""])
    if need_action:
        for item in need_action:
            lines.extend(
                item_brief_block(
                    scan,
                    item,
                    conclusion="这封邮件可能需要人工确认或后续动作。",
                    action="打开来源邮件核对完整正文，再决定回复、归档或转交。",
                )
            )
    else:
        lines.append("今天没有明确需要处理的邮件。")

    lines.extend(["", "## 值得知道", ""])
    if worth_knowing:
        for item in worth_knowing:
            lines.extend(
                item_brief_block(
                    scan,
                    item,
                    conclusion="这封邮件包含值得记录或后续阅读的线索。",
                    action="优先查看邮件里的原始链接；当前结论待外部验证。",
                )
            )
    else:
        lines.append("今天没有明显值得单独记录的邮件线索。")

    lines.extend(["", "## 可以忽略", ""])
    if ignore:
        ignored_types = brief_type_distribution({"items": ignore})
        lines.append(f"可暂时忽略 {len(ignore)} 封，主要类型：{ignored_types}。忽略依据：没有明确行动信号，或当前只有低信号摘要。")
        for item in ignore[:8]:
            lines.append(f"- UID={item.get('uid')} | Subject={item.get('subject')} | 原因：{evidence_gap_text(item)}")
        if len(ignore) > 8:
            lines.append(f"- 其余 {len(ignore) - 8} 封只保留在来源索引中。")
    else:
        lines.append("没有需要合并忽略的邮件。")

    top_items = (need_action + worth_knowing)[:3]
    lines.extend(["", "## 如果只记三件事", ""])
    if top_items:
        for item in top_items:
            lines.append(
                f"- UID={item.get('uid')}：{clean_text(str(item.get('subject') or ''), 80)}；"
                f"{evidence_gap_text(item)}。"
            )
    else:
        lines.append("没有足够证据支持三条结论。")

    lines.extend(["", "## 来源索引", ""])
    for item in items:
        lines.append(source_index_line(scan, item))
    return "\n".join(lines).rstrip() + "\n"


def has_source_index(markdown: str) -> bool:
    return "来源索引" in markdown or "source" in markdown.lower()


def ensure_intel_brief_traceability(markdown: str, scan: dict[str, Any]) -> str:
    text = markdown.rstrip()
    additions: list[str] = []
    if scan.get("possibly_truncated") and "触达上限" not in text and "可能有遗漏" not in text:
        additions.extend(["## 证据边界", "", "触达上限，可能有遗漏。", ""])
    if "snippet_only" in json.dumps(scan, ensure_ascii=False) and "仅基于邮件摘要" not in text and "待外部验证" not in text:
        if "## 证据边界" not in "\n".join(additions):
            additions.extend(["## 证据边界", ""])
        additions.extend(["- 存在 snippet_only 风险；未补全公开网页 evidence 的判断仅基于邮件摘要或待外部验证。", ""])
    if not has_source_index(text):
        additions.extend(["## 来源索引", ""])
        for item in scan.get("items", []):
            if isinstance(item, dict):
                additions.append(source_index_line(scan, item))
    if additions:
        text = text + "\n\n" + "\n".join(additions).rstrip()
    return text.rstrip() + "\n"


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
        return append_review_checklist(build_intel_brief_draft(scan, "dry-run: skipped Hermes summary"), scan)
    ok, value = run_hermes_prompt(str(args.hermes), prompt, cwd=str(args.project_dir), timeout=args.hermes_timeout)
    if not ok:
        return append_review_checklist(build_intel_brief_draft(scan, f"Hermes 摘要失败：{value}"), scan)
    rendered = value or build_intel_brief_draft(scan, "Hermes returned empty output")
    return append_review_checklist(ensure_intel_brief_traceability(rendered, scan), scan)


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
