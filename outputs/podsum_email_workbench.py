#!/usr/bin/env python3
"""Manual local Web Workbench for Podsum email-summary artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import posixpath
import re
import shlex
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import podsum_email_summary as email_summary
import podsum_runtime
from email import need_store, object_harness
from email.io import atomic_write_json
from email.schemas import EvidenceNeed, EvidenceNeedEvent, transition_need


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ROOT = email_summary.DEFAULT_OUTPUT_DIR
DEFAULT_POLICY_FILE = email_summary.DEFAULT_LINK_POLICY
DEFAULT_TOPIC_FILE = email_summary.DEFAULT_TOPIC_FILE
MAX_POST_BYTES = 1024 * 1024
BRIEF_STATUSES = {"draft", "needs_revision", "approved"}
REVIEW_MARK_KEYS = {"ignore", "important", "type_override", "needs_link_review"}
NEED_STATUSES = ("open", "watching", "blocked", "stale", "fulfilled_now", "superseded", "closed")
NEED_URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
NEED_ACTION_EVENTS = {"close": "close", "stale": "mark_stale"}


def today_string() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass(frozen=True)
class WorkbenchConfig:
    root: Path
    date: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    policy_file: Path = DEFAULT_POLICY_FILE
    topic_file: Path = DEFAULT_TOPIC_FILE

    @property
    def reports_dir(self) -> Path:
        return self.root / "EmailReports"

    @property
    def scan_path(self) -> Path:
        return self.reports_dir / f"email-scan-{self.date}.json"

    @property
    def summary_path(self) -> Path:
        return self.reports_dir / f"email-summary-{self.date}.md"

    @property
    def epub_path(self) -> Path:
        return self.reports_dir / f"email-summary-{self.date}.epub"

    @property
    def review_path(self) -> Path:
        return self.reports_dir / f"email-review-{self.date}.json"


def default_review(date: str) -> dict[str, Any]:
    return {
        "object_type": "email_review",
        "version": 1,
        "date": date,
        "email_marks": {},
        "brief_status": "draft",
        "brief_override_markdown": "",
        "checklist_decision": "",
        "updated_at": "",
    }


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_review(config: WorkbenchConfig) -> dict[str, Any]:
    review = default_review(config.date)
    if not config.review_path.exists():
        return review
    try:
        loaded = json.loads(config.review_path.read_text(encoding="utf-8"))
    except Exception:
        return review
    if isinstance(loaded, dict):
        review.update(loaded)
    if not isinstance(review.get("email_marks"), dict):
        review["email_marks"] = {}
    if review.get("brief_status") not in BRIEF_STATUSES:
        review["brief_status"] = "draft"
    review["date"] = config.date
    return review


def artifact_info(path: Path) -> dict[str, Any]:
    exists = path.exists()
    info: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "size": 0,
        "mtime": "",
    }
    if exists:
        stat = path.stat()
        info["size"] = stat.st_size
        info["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime))
    return info


def path_is_inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def context_payload(config: WorkbenchConfig) -> dict[str, Any]:
    artifacts = {
        "scan": artifact_info(config.scan_path),
        "summary": artifact_info(config.summary_path),
        "epub": artifact_info(config.epub_path),
        "review": artifact_info(config.review_path),
        "policy": artifact_info(config.policy_file),
        "topics": artifact_info(config.topic_file),
    }
    return {
        "date": config.date,
        "root": str(config.root),
        "reports_dir": str(config.reports_dir),
        "server": {
            "host": config.host,
            "port": config.port,
            "mode": "manual-local-workbench",
            "renderer_contract": object_harness.RENDERER_CONTRACT,
            "safe_defaults": {
                "reads_imap": False,
                "calls_hermes": False,
                "sends": False,
                "launchd": False,
            },
        },
        "artifacts": artifacts,
        "missing": [name for name, info in artifacts.items() if name in {"scan", "summary"} and not info["exists"]],
    }


def policy_payload(config: WorkbenchConfig) -> dict[str, Any]:
    if not config.policy_file.exists():
        return {
            "missing": True,
            "path": str(config.policy_file),
            "markdown": "",
            "policy": json.loads(json.dumps(email_summary.DEFAULT_POLICY)),
            "error": "",
        }
    markdown = config.policy_file.read_text(encoding="utf-8")
    try:
        policy = email_summary.parse_policy_json(markdown)
        return {
            "missing": False,
            "path": str(config.policy_file),
            "markdown": markdown,
            "policy": policy,
            "error": "",
        }
    except Exception as exc:
        return {
            "missing": False,
            "path": str(config.policy_file),
            "markdown": markdown,
            "policy": None,
            "error": str(exc),
        }


def topic_payload(config: WorkbenchConfig) -> dict[str, Any]:
    if not config.topic_file.exists():
        return {
            "missing": True,
            "path": str(config.topic_file),
            "markdown": "",
            "topic_map": json.loads(json.dumps(email_summary.DEFAULT_TOPIC_MAP)),
            "error": "",
        }
    markdown = config.topic_file.read_text(encoding="utf-8")
    try:
        topic_map = email_summary.parse_topic_json(markdown)
        return {
            "missing": False,
            "path": str(config.topic_file),
            "markdown": markdown,
            "topic_map": topic_map,
            "error": "",
        }
    except Exception as exc:
        return {
            "missing": False,
            "path": str(config.topic_file),
            "markdown": markdown,
            "topic_map": None,
            "error": str(exc),
        }


def need_counts(needs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in NEED_STATUSES}
    for need in needs:
        status = str(need.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def need_sort_key(need: dict[str, Any]) -> tuple[int, int, str]:
    open_rank = 0 if need.get("status") == "open" else 1
    urgency_rank = NEED_URGENCY_ORDER.get(str(need.get("urgency") or ""), 99)
    return (open_rank, urgency_rank, str(need.get("need_id") or ""))


def needs_payload(config: WorkbenchConfig) -> dict[str, Any]:
    store = need_store.load_need_store(config.reports_dir)
    needs = sorted(store["needs"], key=need_sort_key)
    return {
        "store": {
            "object_type": store["object_type"],
            "object_version": store["object_version"],
            "needs": needs,
        },
        "needs": needs,
        "counts": need_counts(needs),
        "path": str(need_store.need_store_path(config.reports_dir)),
    }


def harness_session(config: WorkbenchConfig, object_type: str, scenario: str) -> object_harness.ObjectHarnessSession:
    object_harness.validate_scenario(scenario)
    return current_harness_session(config, object_type)


def current_harness_session(config: WorkbenchConfig, object_type: str) -> object_harness.ObjectHarnessSession:
    validated_object_type = object_harness.validate_object_type(object_type)
    current_object, risks, missing_fields = current_harness_object(config, validated_object_type)
    return object_harness.new_session_from_object(validated_object_type, current_object, risks, missing_fields)


def current_harness_object(config: WorkbenchConfig, object_type: object_harness.ObjectType) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    if object_type == "email_topic_map":
        payload = topic_payload(config)
        current_object = current_nested_object(payload, "topic_map", "email_topic_map")
    elif object_type == "email_evidence_policy":
        payload = policy_payload(config)
        current_object = current_nested_object(payload, "policy", "email_policy")
    elif object_type == "email_evidence_pack":
        payload = load_evidence_pack(config)
        current_object = current_nested_object(payload, "scan", "email_evidence_pack")
    elif object_type == "email_intel_brief":
        payload = intel_brief_payload(config)
        current_object = json.loads(json.dumps(payload))
    else:
        payload = needs_payload(config)
        current_object = json.loads(json.dumps(payload["store"]))
    risks = current_artifact_risks(payload)
    missing_fields = object_harness.missing_fields_for(object_type, current_object)
    return current_object, tuple(risks), missing_fields


def current_nested_object(payload: dict[str, Any], field_name: str, fallback_object_type: str) -> dict[str, Any]:
    value = payload.get(field_name)
    if isinstance(value, dict):
        return json.loads(json.dumps(value))
    return {
        "object_type": fallback_object_type,
        "missing": bool(payload.get("missing")),
        "path": str(payload.get("path") or ""),
        "error": str(payload.get("error") or ""),
    }


def current_artifact_risks(payload: dict[str, Any]) -> list[str]:
    risks = ["current_workbench_artifact"]
    if payload.get("missing"):
        risks.append("missing_current_artifact")
    if payload.get("error"):
        risks.append("current_artifact_error")
    return risks


def update_need_action(config: WorkbenchConfig, need_id: str, body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "")
    event_type = NEED_ACTION_EVENTS.get(action)
    if event_type is None:
        raise ValueError("action must be close or stale")
    store = need_store.load_need_store(config.reports_dir)
    matching = [item for item in store["needs"] if item.get("need_id") == need_id]
    if not matching:
        raise KeyError("need not found")
    reason = str(body.get("reason") or f"Manual {action} from Workbench")
    event = EvidenceNeedEvent(
        event_type=event_type,
        at=now_stamp(),
        actor="Workbench",
        reason=reason,
        added_evidence_refs=(),
        resolved_by=(),
    )
    next_need = transition_need(EvidenceNeed.from_dict(matching[0]), event)
    next_store = need_store.replace_need(store, next_need)
    need_store.save_need_store(config.reports_dir, next_store)
    return needs_payload(config)


def load_evidence_pack(config: WorkbenchConfig) -> dict[str, Any]:
    review = load_review(config)
    if not config.scan_path.exists():
        return {
            "missing": True,
            "path": str(config.scan_path),
            "scan": None,
            "review": review,
            "error": "",
        }
    try:
        scan = json.loads(config.scan_path.read_text(encoding="utf-8"))
        policy = email_summary.load_link_policy(config.policy_file)
        topic_map = email_summary.load_topic_map(config.topic_file)
        scan = email_summary.normalize_evidence_pack(scan, policy)
        scan = email_summary.apply_topics(scan, topic_map)
    except Exception as exc:
        return {
            "missing": False,
            "path": str(config.scan_path),
            "scan": None,
            "review": review,
            "error": str(exc),
        }
    scan = json.loads(json.dumps(scan))
    marks = review.get("email_marks", {})
    for item in scan.get("items", []):
        if isinstance(item, dict):
            uid = str(item.get("uid") or "")
            item["_review"] = marks.get(uid, {}) if isinstance(marks.get(uid, {}), dict) else {}
    return {
        "missing": False,
        "path": str(config.scan_path),
        "scan": scan,
        "review": review,
        "error": "",
    }


SOURCE_RE = re.compile(
    r"UID=(?P<uid>[^|\n`]+).*?"
    r"From=(?P<from>[^|\n`]*).*?"
    r"Subject=(?P<subject>[^|\n`]*).*?"
    r"Date=(?P<date>[^|\n`]*).*?"
    r"email://(?P<scan_date>[^/`\s]+)/(?P<source_uid>[^`\s]+)",
    flags=re.IGNORECASE,
)
SECTION_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$", flags=re.MULTILINE)


def parse_source_index(markdown: str) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in SOURCE_RE.finditer(markdown):
        source = {key: value.strip() for key, value in match.groupdict().items()}
        source_uid = source.get("source_uid") or source.get("uid") or ""
        if source_uid in seen:
            continue
        seen.add(source_uid)
        source["uid"] = source_uid
        sources.append(source)
    return sources


def parse_markdown_sections(markdown: str) -> list[dict[str, Any]]:
    matches = list(SECTION_RE.finditer(markdown))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        sections.append(
            {
                "title": match.group("title").strip(),
                "chars": len(body),
                "lines": len([line for line in body.splitlines() if line.strip()]),
            }
        )
    return sections


def brief_source_coverage(markdown: str, scan: dict[str, Any] | None) -> dict[str, Any]:
    sources = parse_source_index(markdown)
    source_uids = {str(source.get("source_uid") or source.get("uid") or "") for source in sources}
    items = scan.get("items", []) if isinstance(scan, dict) else []
    scan_uids = {str(item.get("uid") or "") for item in items if isinstance(item, dict)}
    missing = sorted(uid for uid in scan_uids if uid and uid not in source_uids)
    return {
        "source_count": len(source_uids),
        "item_count": len(scan_uids),
        "covered_count": len(scan_uids - set(missing)),
        "missing_uids": missing,
        "complete": not missing and bool(scan_uids),
    }


def intel_brief_payload(config: WorkbenchConfig) -> dict[str, Any]:
    review = load_review(config)
    missing = not config.summary_path.exists()
    markdown = "" if missing else config.summary_path.read_text(encoding="utf-8", errors="replace")
    override = str(review.get("brief_override_markdown") or "")
    effective = override or markdown
    evidence = load_evidence_pack(config)
    scan = evidence.get("scan") if isinstance(evidence.get("scan"), dict) else None
    return {
        "object_type": "email_intel_brief",
        "object_version": email_summary.INTEL_BRIEF_VERSION,
        "missing": missing,
        "path": str(config.summary_path),
        "markdown": markdown,
        "effective_markdown": effective,
        "source": "review_override" if override else "summary_file",
        "source_index": parse_source_index(effective),
        "sections": parse_markdown_sections(effective),
        "source_coverage": brief_source_coverage(effective, scan),
        "review": review,
    }


def checklist_payload(config: WorkbenchConfig) -> dict[str, Any]:
    evidence = load_evidence_pack(config)
    brief = intel_brief_payload(config)
    review = load_review(config)
    scan = evidence.get("scan") if isinstance(evidence.get("scan"), dict) else {
        "date": config.date,
        "possibly_truncated": False,
        "items": [],
    }
    markdown = str(brief.get("effective_markdown") or "")
    checklist = email_summary.review_checklist(scan, markdown)
    approved = review.get("brief_status") == "approved"
    return {
        "checklist": checklist,
        "brief_status": review.get("brief_status", "draft"),
        "approved": approved,
        "delivery_ready": bool(checklist.get("ready_to_send")) and approved,
        "missing": {
            "scan": evidence.get("missing", False),
            "summary": brief.get("missing", False),
        },
    }


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def commands_payload(config: WorkbenchConfig) -> dict[str, Any]:
    podsum = Path(__file__).with_name("podsum.py")
    python = podsum_runtime.podsum_python()
    scan = str(config.scan_path)
    root = str(config.root)
    policy_file = str(config.policy_file)
    topic_file = str(config.topic_file)
    commands = {
        "generate_scan_manual_imap": shell_join(
            [
                python,
                str(podsum),
                "email-summary",
                "--output",
                root,
                "--summary-engine",
                "podsum",
                "--allow-imap-read",
                "--email-link-policy",
                policy_file,
                "--email-topic-file",
                topic_file,
                "--no-send",
            ]
        ),
        "regenerate_summary_no_send": shell_join(
            [
                python,
                str(podsum),
                "email-summary",
                "--scan-file",
                scan,
                "--output",
                root,
                "--summary-engine",
                "podsum",
                "--email-link-policy",
                policy_file,
                "--email-topic-file",
                topic_file,
                "--no-send",
            ]
        ),
        "regenerate_summary_with_link_enrichment": shell_join(
            [
                python,
                str(podsum),
                "email-summary",
                "--scan-file",
                scan,
                "--output",
                root,
                "--summary-engine",
                "podsum",
                "--email-link-policy",
                policy_file,
                "--email-topic-file",
                topic_file,
                "--enrich-links",
                "--no-send",
            ]
        ),
        "manual_delivery_after_approval": shell_join(
            [
                python,
                str(podsum),
                "email-summary",
                "--scan-file",
                scan,
                "--output",
                root,
                "--summary-engine",
                "podsum",
                "--email-link-policy",
                policy_file,
                "--email-topic-file",
                topic_file,
            ]
        ),
    }
    return {
        "commands": commands,
        "notes": [
            "Workbench only displays commands; it never executes IMAP, Hermes summary, send, or launchd actions.",
            "EmailIntelBrief uses Podsum's local summary engine by default; Hermes is not the email summary engine.",
            f"Generated commands use Podsum Python: {python}",
            "Run manual_delivery_after_approval only after ReviewChecklistPanel passes and the Brief is approved.",
        ],
    }


def merge_email_marks(review: dict[str, Any], marks: Any) -> None:
    if not isinstance(marks, dict):
        raise ValueError("email_marks must be an object")
    current = review.setdefault("email_marks", {})
    if not isinstance(current, dict):
        current = {}
        review["email_marks"] = current
    for uid, mark in marks.items():
        if not isinstance(mark, dict):
            raise ValueError("each email mark must be an object")
        uid_text = str(uid)
        existing = current.get(uid_text, {})
        if not isinstance(existing, dict):
            existing = {}
        for key, value in mark.items():
            if key not in REVIEW_MARK_KEYS:
                raise ValueError(f"unsupported email mark: {key}")
            existing[key] = value
        current[uid_text] = existing


def save_review_update(config: WorkbenchConfig, update: dict[str, Any]) -> dict[str, Any]:
    review = load_review(config)
    if "email_marks" in update:
        merge_email_marks(review, update["email_marks"])
    if "brief_status" in update:
        status = str(update["brief_status"])
        if status not in BRIEF_STATUSES:
            raise ValueError(f"brief_status must be one of {', '.join(sorted(BRIEF_STATUSES))}")
        review["brief_status"] = status
    if "brief_override_markdown" in update:
        review["brief_override_markdown"] = str(update["brief_override_markdown"])
    if "checklist_decision" in update:
        review["checklist_decision"] = str(update["checklist_decision"])
    review["updated_at"] = now_stamp()
    atomic_write_json(config.review_path, review)
    return review


def save_policy(config: WorkbenchConfig, markdown: str) -> dict[str, Any]:
    parsed = email_summary.parse_policy_json(markdown)
    atomic_write_text(config.policy_file, markdown.rstrip() + "\n")
    return parsed


def save_topics(config: WorkbenchConfig, markdown: str) -> dict[str, Any]:
    parsed = email_summary.parse_topic_json(markdown)
    atomic_write_text(config.topic_file, markdown.rstrip() + "\n")
    return parsed


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, status: int, body: str, content_type: str) -> None:
    raw = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length > MAX_POST_BYTES:
        raise ValueError("request body is too large")
    raw = handler.rfile.read(length)
    parsed = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("request body must be a json object")
    return parsed


def has_path_traversal(path: str) -> bool:
    decoded = urllib.parse.unquote(path)
    if ".." in decoded.split("/"):
        return True
    normalized = posixpath.normpath(decoded)
    return ".." in normalized.split("/")


def make_handler(config: WorkbenchConfig) -> type[BaseHTTPRequestHandler]:
    class EmailWorkbenchHandler(BaseHTTPRequestHandler):
        server_version = "PodsumEmailWorkbench/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}", flush=True)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if has_path_traversal(path):
                json_response(self, 403, {"ok": False, "error": "path traversal is not allowed"})
                return
            try:
                if path in {"", "/", "/index.html"}:
                    text_response(self, 200, INDEX_HTML, "text/html")
                elif path == "/harness":
                    text_response(self, 200, HARNESS_HTML, "text/html")
                elif path == "/styles.css":
                    text_response(self, 200, STYLES_CSS, "text/css")
                elif path == "/app.js":
                    text_response(self, 200, APP_JS, "application/javascript")
                elif path == "/api/context":
                    json_response(self, 200, {"ok": True, **context_payload(config)})
                elif path == "/api/evidence-pack":
                    json_response(self, 200, {"ok": True, **load_evidence_pack(config)})
                elif path == "/api/intel-brief":
                    json_response(self, 200, {"ok": True, **intel_brief_payload(config)})
                elif path == "/api/policy":
                    json_response(self, 200, {"ok": True, **policy_payload(config)})
                elif path == "/api/topics":
                    json_response(self, 200, {"ok": True, **topic_payload(config)})
                elif path == "/api/checklist":
                    json_response(self, 200, {"ok": True, **checklist_payload(config)})
                elif path == "/api/needs":
                    json_response(self, 200, {"ok": True, **needs_payload(config)})
                elif path == "/api/commands":
                    json_response(self, 200, {"ok": True, **commands_payload(config)})
                elif path == "/api/harness/catalog":
                    json_response(self, 200, {"ok": True, "catalog": object_harness.list_catalog()})
                elif path == "/api/harness/session":
                    query = urllib.parse.parse_qs(parsed.query)
                    session = harness_session(
                        config,
                        str(query.get("object_type", ["email_evidence_pack"])[0]),
                        str(query.get("scenario", [object_harness.CURRENT_SCENARIO])[0]),
                    )
                    json_response(self, 200, {"ok": True, "session": session.to_dict()})
                else:
                    json_response(self, 404, {"ok": False, "error": "not found"})
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if has_path_traversal(path):
                json_response(self, 403, {"ok": False, "error": "path traversal is not allowed"})
                return
            try:
                body = read_json_body(self)
                if path == "/api/review":
                    review = save_review_update(config, body)
                    json_response(self, 200, {"ok": True, "review": review})
                elif path == "/api/policy":
                    markdown = str(body.get("markdown") or "")
                    policy = save_policy(config, markdown)
                    json_response(self, 200, {"ok": True, "policy": policy})
                elif path == "/api/topics":
                    markdown = str(body.get("markdown") or "")
                    topic_map = save_topics(config, markdown)
                    json_response(self, 200, {"ok": True, "topic_map": topic_map})
                elif path.startswith("/api/needs/") and path.endswith("/action"):
                    need_id = urllib.parse.unquote(path.removeprefix("/api/needs/").removesuffix("/action").strip("/"))
                    json_response(self, 200, {"ok": True, **update_need_action(config, need_id, body)})
                elif path == "/api/harness/session":
                    session = harness_session(config, str(body.get("object_type") or ""), str(body.get("scenario") or object_harness.CURRENT_SCENARIO))
                    json_response(self, 200, {"ok": True, "session": session.to_dict()})
                elif path == "/api/harness/event":
                    session = object_harness.session_from_dict(body.get("session") if isinstance(body.get("session"), dict) else {})
                    event_payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
                    next_session = object_harness.apply_event(
                        session,
                        str(body.get("event_type") or ""),
                        event_payload,
                        str(body.get("actor") or "ObjectHarness"),
                    )
                    json_response(self, 200, {"ok": True, "session": next_session.to_dict()})
                elif path == "/api/harness/import":
                    fixture = body.get("fixture") if isinstance(body.get("fixture"), dict) else {}
                    session = object_harness.import_session_fixture(
                        str(body.get("object_type") or ""),
                        str(body.get("scenario") or object_harness.CURRENT_SCENARIO),
                        fixture,
                    )
                    json_response(self, 200, {"ok": True, "session": session.to_dict()})
                elif path == "/api/harness/export":
                    session = object_harness.session_from_dict(body.get("session") if isinstance(body.get("session"), dict) else {})
                    json_response(self, 200, {"ok": True, "export": object_harness.export_session_fixture(session)})
                else:
                    json_response(self, 404, {"ok": False, "error": "not found"})
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})

    return EmailWorkbenchHandler


def create_server(config: WorkbenchConfig) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((config.host, config.port), make_handler(config))


def run(args: argparse.Namespace) -> int:
    config = WorkbenchConfig(
        root=args.root.expanduser(),
        date=args.date,
        host=args.host,
        port=args.port,
        policy_file=args.policy_file.expanduser(),
        topic_file=args.topic_file.expanduser(),
    )
    if config.host != "127.0.0.1":
        print("Warning: email-workbench is intended for local use; 127.0.0.1 is the safe default.", flush=True)
    server = create_server(config)
    host, port = server.server_address[:2]
    print(f"Podsum Email Workbench listening on http://{host}:{port}", flush=True)
    print("Manual review only: this server does not read IMAP, call Hermes, send, or modify launchd.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Podsum Email Workbench.", flush=True)
    finally:
        server.server_close()
    return 0


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--date", default=today_string())
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--policy-file", type=Path, default=DEFAULT_POLICY_FILE)
    parser.add_argument("--topic-file", type=Path, default=DEFAULT_TOPIC_FILE)


HARNESS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Podsum Email Object Harness</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <header>
    <div>
      <p class="eyebrow">VIS Object Test Harness</p>
      <h1>Podsum Email Object Harness</h1>
      <p>Load the current Workbench artifacts into a local feel-testing harness. No IMAP, no link fetching, no Hermes, no sending, no launchd changes.</p>
    </div>
    <div class="toolbar">
      <a class="command" href="/">Formal Workbench</a>
    </div>
  </header>
  <main class="layout">
    <section class="panel">
      <h2>Object selector</h2>
      <label>Object type <select id="harnessObjectType" data-testid="harness-object-type"></select></label>
      <p class="notice">Source: current formal Workbench artifact for the selected object.</p>
      <button id="harnessLoad" data-testid="harness-load">Load current artifact</button>
      <button id="harnessClear" data-testid="harness-clear">Clear data</button>
      <button id="harnessValidate" data-testid="harness-validate">Validate object</button>
      <button id="harnessMockSkill" data-testid="harness-mock-skill">Mock Skill</button>
    </section>
    <section class="panel wide">
      <h2>Shared renderer payload</h2>
      <div id="harnessSummary" class="stats"></div>
      <pre id="harnessObject" data-testid="harness-object-json"></pre>
    </section>
    <section class="panel wide">
      <h2>Debug panel</h2>
      <pre id="harnessDebug" data-testid="harness-debug-json"></pre>
    </section>
  </main>
  <script>
    let harnessCatalog = null;
    let harnessSession = null;
    async function harnessApi(path, options) {
      const response = await fetch(path, options || {});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Harness API failed');
      return data;
    }
    function renderHarness() {
      document.getElementById('harnessSummary').innerHTML = [
        `<div class="stat"><strong>Object</strong><span>${harnessSession.selected_object_type}</span></div>`,
        `<div class="stat"><strong>Source</strong><span>${harnessSession.fixture_source}</span></div>`,
        `<div class="stat"><strong>State</strong><span>${harnessSession.lifecycle_status}</span></div>`,
        `<div class="stat"><strong>Events</strong><span>${harnessSession.event_log.length}</span></div>`
      ].join('');
      document.getElementById('harnessObject').textContent = JSON.stringify(harnessSession.renderer, null, 2);
      document.getElementById('harnessDebug').textContent = JSON.stringify({
        risks: harnessSession.risks,
        missing_fields: harnessSession.missing_fields,
        event_log: harnessSession.event_log,
        version_history: harnessSession.version_history
      }, null, 2);
    }
    async function loadHarnessSession() {
      const objectType = document.getElementById('harnessObjectType').value;
      const data = await harnessApi(`/api/harness/session?object_type=${encodeURIComponent(objectType)}&scenario=current`);
      harnessSession = data.session;
      renderHarness();
    }
    async function sendHarnessEvent(eventType, payload) {
      const data = await harnessApi('/api/harness/event', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session: harnessSession, event_type: eventType, payload: payload || {}, actor: 'HarnessUI'})
      });
      harnessSession = data.session;
      renderHarness();
    }
    async function bootHarness() {
      const data = await harnessApi('/api/harness/catalog');
      harnessCatalog = data.catalog;
      document.getElementById('harnessObjectType').innerHTML = harnessCatalog.object_types.map((item) => `<option value="${item}">${item}</option>`).join('');
      document.getElementById('harnessLoad').addEventListener('click', loadHarnessSession);
      document.getElementById('harnessClear').addEventListener('click', () => sendHarnessEvent('clear_data', {}));
      document.getElementById('harnessValidate').addEventListener('click', () => sendHarnessEvent('validate_object', {}));
      document.getElementById('harnessMockSkill').addEventListener('click', () => sendHarnessEvent(harnessSession.selected_object_type === 'email_intel_brief' ? 'mock_brief_agent' : 'mock_evidence_agent', {}));
      await loadHarnessSession();
    }
    bootHarness().catch((error) => { document.getElementById('harnessDebug').textContent = error.message; });
  </script>
</body>
</html>
"""


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Podsum Email Workbench</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <header class="topbar">
    <div>
      <h1>Podsum Email Workbench</h1>
      <p id="contextLine">Loading...</p>
    </div>
    <div id="statusPills" class="pills"></div>
  </header>
  <main class="shell">
    <nav class="nav">
      <button class="nav-button" data-view="topics">EmailTopicMap</button>
      <button class="nav-button" data-view="policy">EmailEvidencePolicy</button>
      <button class="nav-button active" data-view="evidence">EmailEvidencePack</button>
      <button class="nav-button" data-view="brief">EmailIntelBrief</button>
      <button class="nav-button" data-view="needs">Needs</button>
    </nav>
    <section class="workspace">
      <section id="topicsView" class="view">
        <div class="section-head">
          <h2>EmailTopicMap</h2>
          <button id="saveTopics">Save topics</button>
        </div>
        <p class="notice">核心可视化工作对象：定义 Hermes 用户正在跟踪的话题。EmailEvidencePack 会按 topic.md 命中邮件，EmailIntelBrief 必须先围绕这些 topic 展开；修改 topic 不会自动重跑已有 summary。</p>
        <div id="topicSummary" class="topic-summary"></div>
        <div id="topicGuide" class="topic-guide"></div>
        <label class="field-label" for="topicEditor">EmailTopicMap spec (advanced)</label>
        <textarea id="topicEditor" spellcheck="false"></textarea>
        <div id="topicStatus" class="notice"></div>
      </section>
      <section id="policyView" class="view">
        <div class="section-head">
          <h2>EmailEvidencePolicy</h2>
          <button id="savePolicy">Save policy</button>
        </div>
        <p class="notice">核心可视化工作对象：控制邮件类型规则、链接策略、安全跳过规则和 evidence 生成边界。EmailPolicyPanel 是它的 GUI 编辑面板；修改 policy 只影响后续 scan/enrich，不会自动重跑。</p>
        <div id="policySummary" class="policy-summary"></div>
        <label class="field-label" for="policyEditor">EmailPolicy spec (advanced)</label>
        <textarea id="policyEditor" spellcheck="false"></textarea>
        <div id="policyStatus" class="notice"></div>
      </section>
      <section id="evidenceView" class="view active">
        <div class="section-head">
          <h2>EmailEvidencePack</h2>
          <div class="filters">
            <select id="typeFilter"></select>
            <select id="riskFilter"></select>
            <label><input id="attachmentFilter" type="checkbox"> attachments</label>
            <label><input id="evidenceFilter" type="checkbox"> link evidence</label>
          </div>
        </div>
        <div id="evidenceStats" class="stats"></div>
        <div class="evidence-grid">
          <div id="emailList" class="email-list"></div>
          <article id="emailDetail" class="detail"></article>
        </div>
      </section>
      <section id="needsView" class="view">
        <div class="section-head">
          <h2>EvidenceNeedQueue</h2>
          <div class="filters">
            <select id="needStatusFilter"></select>
            <select id="needUrgencyFilter"></select>
            <select id="needTopicFilter"></select>
            <select id="needSort">
              <option value="urgency">sort: urgency</option>
              <option value="status">sort: status</option>
              <option value="topic_id">sort: topic</option>
            </select>
          </div>
        </div>
        <p class="notice">Needs are read from email-needs.json. Open needs are highlighted but do not block approval or delivery.</p>
        <div id="needStats" class="stats"></div>
        <div id="needList" class="need-list"></div>
      </section>
      <section id="briefView" class="view">
        <div class="section-head">
          <h2>EmailIntelBrief</h2>
          <div class="brief-actions">
            <button id="saveBrief">Save override</button>
            <button id="markNeedsRevision">Needs revision</button>
            <button id="approveBrief">Approve Brief</button>
          </div>
        </div>
        <div id="briefStatus" class="notice"></div>
        <div id="briefSummary" class="stats"></div>
        <div class="brief-grid">
          <article id="briefRendered" class="brief-rendered"></article>
          <div>
            <label class="field-label" for="briefEditor">Brief override</label>
            <textarea id="briefEditor" spellcheck="false"></textarea>
            <div id="sourceIndex" class="source-index"></div>
          </div>
        </div>
      </section>
    </section>
    <aside class="side">
      <section class="panel">
        <h3>ReviewChecklistPanel</h3>
        <div id="checklist"></div>
      </section>
      <section class="panel">
        <h3>Next Commands</h3>
        <div id="commands"></div>
      </section>
    </aside>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


STYLES_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --line: #d9dee7;
  --text: #172033;
  --muted: #647086;
  --accent: #0f766e;
  --warn: #a15c07;
  --bad: #b42318;
  --good: #067647;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--text);
}
button, select, textarea, input {
  font: inherit;
}
button {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text);
  min-height: 32px;
  padding: 6px 10px;
  cursor: pointer;
}
button:hover { border-color: var(--accent); }
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
h1 { font-size: 18px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 0; }
h3 { font-size: 14px; margin: 0 0 8px; }
p { margin: 0; }
.shell {
  display: grid;
  grid-template-columns: 190px minmax(420px, 1fr) 360px;
  min-height: calc(100vh - 70px);
}
.nav, .side {
  border-right: 1px solid var(--line);
  background: var(--panel);
  padding: 12px;
}
.side {
  border-right: 0;
  border-left: 1px solid var(--line);
  overflow: auto;
}
.nav-button {
  width: 100%;
  text-align: left;
  margin-bottom: 8px;
}
.nav-button.active {
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.workspace {
  padding: 14px;
  overflow: auto;
}
.view { display: none; }
.view.active { display: block; }
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.filters, .brief-actions, .pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.pill, .badge {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--line);
  background: #fff;
  padding: 2px 7px;
  font-size: 12px;
  min-height: 22px;
}
.pill.warn, .badge.warn { border-color: #f2c078; color: var(--warn); }
.pill.bad, .badge.bad { border-color: #f4a29b; color: var(--bad); }
.pill.good, .badge.good { border-color: #9bd4b5; color: var(--good); }
.pill.usable, .badge.usable { border-color: #7fcac2; color: var(--accent); }
.pill.weak, .badge.weak { border-color: #f2c078; color: var(--warn); }
.pill.failed, .badge.failed { border-color: #f4a29b; color: var(--bad); }
.pill.skipped, .badge.skipped { border-color: #b8c0cc; color: var(--muted); }
.stats, .policy-summary, .topic-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.stat, .panel, .detail, .email-row, .brief-rendered, .policy-card, .topic-card, .need-card {
  background: var(--panel);
  border: 1px solid var(--line);
}
.stat, .policy-card, .topic-card { padding: 10px; }
.need-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.need-card {
  padding: 10px;
}
.need-card.open {
  border-color: var(--warn);
  box-shadow: inset 3px 0 0 var(--warn);
}
.need-card h3 {
  margin-top: 0;
}
.need-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.stat strong { display: block; font-size: 18px; }
.policy-card strong, .topic-card strong { display: block; margin-bottom: 6px; }
.topic-guide {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.topic-card ul { margin: 6px 0 0 18px; padding: 0; }
.topic-card li { margin-bottom: 4px; }
.evidence-grid, .brief-grid {
  display: grid;
  grid-template-columns: minmax(260px, 38%) 1fr;
  gap: 12px;
}
.email-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: calc(100vh - 210px);
  overflow: auto;
}
.email-row {
  padding: 9px;
  text-align: left;
}
.email-row.selected {
  outline: 2px solid var(--accent);
}
.email-row h4 {
  margin: 0 0 4px;
  font-size: 14px;
}
.meta, .hint, #contextLine {
  color: var(--muted);
  font-size: 12px;
}
.detail, .brief-rendered, .panel {
  padding: 12px;
}
.detail pre, .command {
  white-space: pre-wrap;
  word-break: break-word;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 12px;
  font-size: 12px;
}
th, td {
  border: 1px solid var(--line);
  padding: 6px;
  text-align: left;
  vertical-align: top;
  word-break: break-word;
}
.panel {
  margin-bottom: 12px;
}
textarea {
  width: 100%;
  min-height: 140px;
  resize: vertical;
  border: 1px solid var(--line);
  padding: 8px;
  background: #fff;
}
#policyEditor { min-height: 440px; }
#topicEditor { min-height: 420px; }
#briefEditor { min-height: 360px; }
.brief-rendered {
  min-height: 360px;
  overflow: auto;
}
.brief-rendered h1 { font-size: 20px; }
.brief-rendered h2 { font-size: 16px; margin-top: 16px; }
.source-index button {
  display: block;
  width: 100%;
  text-align: left;
  margin-top: 6px;
}
.notice {
  color: var(--muted);
  font-size: 12px;
  margin: 6px 0;
}
.field-label {
  display: block;
  margin: 0 0 6px;
  font-size: 12px;
  color: var(--muted);
}
.command {
  border: 1px solid var(--line);
  background: #fbfcfd;
  padding: 8px;
  margin: 8px 0;
  font-size: 12px;
}
@media (max-width: 1100px) {
  .shell { grid-template-columns: 150px 1fr; }
  .side { grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--line); }
}
@media (max-width: 760px) {
  .shell, .evidence-grid, .brief-grid { grid-template-columns: 1fr; }
  .nav { border-right: 0; border-bottom: 1px solid var(--line); }
  .topbar, .section-head { align-items: flex-start; flex-direction: column; }
}
"""


APP_JS = r"""
const state = {
  context: null,
  evidence: null,
  brief: null,
  policy: null,
  topics: null,
  checklist: null,
  commands: null,
  needs: null,
  selectedUid: null,
  filters: { type: "", risk: "", attachments: false, evidence: false },
  needFilters: { status: "", urgency: "", topic: "", sort: "urgency" },
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function badge(value, kind = "") {
  return `<span class="badge ${kind}">${escapeHtml(value)}</span>`;
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  return lines.map((line) => {
    if (line.startsWith("# ")) return `<h1>${escapeHtml(line.slice(2))}</h1>`;
    if (line.startsWith("## ")) return `<h2>${escapeHtml(line.slice(3))}</h2>`;
    if (line.startsWith("- ")) return `<p>• ${escapeHtml(line.slice(2))}</p>`;
    if (!line.trim()) return "<br>";
    return `<p>${escapeHtml(line)}</p>`;
  }).join("");
}

function countBy(items, getter) {
  const counts = {};
  for (const item of items) {
    const values = getter(item);
    for (const value of Array.isArray(values) ? values : [values]) {
      if (!value) continue;
      counts[value] = (counts[value] || 0) + 1;
    }
  }
  return counts;
}

function policyForType(emailType) {
  const types = state.policy?.policy?.email_types || [];
  return types.find((item) => item.name === emailType) || {
    name: emailType || "unknown",
    fetch_links: false,
    summary_focus: "",
  };
}

function topicLabel(topic) {
  return topic?.name || topic?.id || "unknown topic";
}

function evidenceByType(item, evidenceType) {
  return (item.evidence || []).filter((ev) => ev.type === evidenceType);
}

function baseEvidenceItem(item) {
  return evidenceByType(item, "email_snippet")[0] || {
    type: "email_snippet",
    status: item.snippet ? "available" : "missing",
    title: item.subject || "(no subject)",
    excerpt: item.snippet || "",
    reason: item.snippet ? "" : "missing_snippet",
  };
}

function linkEvidenceItems(item) {
  return evidenceByType(item, "public_link");
}

function evidenceHealth(item) {
  const base = baseEvidenceItem(item);
  const linkEvidence = linkEvidenceItems(item);
  const risks = item.risks || [];
  const fetchedLink = linkEvidence.some((ev) => ev.status === "fetched");
  const failedLink = linkEvidence.some((ev) => ev.status === "failed") || risks.includes("link_failed");
  const skippedLink = linkEvidence.some((ev) => ev.status === "skipped")
    || risks.some((risk) => ["link_skipped", "tracking_skipped", "link_budget_exhausted"].includes(risk));
  const snippetLength = String(base.excerpt || item.snippet || "").trim().length;
  if (failedLink) return "failed";
  if (fetchedLink && snippetLength > 0) return "good";
  if (skippedLink) return "skipped";
  if (snippetLength >= 40) return "usable";
  return "weak";
}

function healthLabel(health) {
  return {
    good: "strong evidence",
    usable: "usable snippet",
    weak: "weak snippet",
    failed: "link failed",
    skipped: "link skipped",
  }[health] || health;
}

function renderContext() {
  const c = state.context;
  document.getElementById("contextLine").textContent =
    `${c.date} · ${c.root} · ${c.server.mode}`;
  const pills = [];
  for (const [name, info] of Object.entries(c.artifacts)) {
    const cls = info.exists ? "good" : (name === "scan" || name === "summary" ? "bad" : "warn");
    pills.push(`<span class="pill ${cls}">${name}: ${info.exists ? "ok" : "missing"}</span>`);
  }
  document.getElementById("statusPills").innerHTML = pills.join("");
}

function scanItems() {
  return state.evidence?.scan?.items || [];
}

function filteredItems() {
  return scanItems().filter((item) => {
    if (state.filters.type && item.email_type !== state.filters.type) return false;
    if (state.filters.risk && !(item.risks || []).includes(state.filters.risk)) return false;
    if (state.filters.attachments && !item.has_attachments) return false;
    if (state.filters.evidence && !(item.evidence || []).some((ev) => ev.status === "fetched")) return false;
    return true;
  });
}

function renderEvidence() {
  if (state.evidence?.missing) {
    document.getElementById("evidenceStats").innerHTML = `<div class="stat"><strong>Missing</strong><span>scan JSON not found</span></div>`;
    document.getElementById("emailList").innerHTML = "";
    document.getElementById("emailDetail").innerHTML = "No EmailEvidencePack loaded.";
    return;
  }
  const scan = state.evidence.scan;
  const items = scanItems();
  const typeCounts = countBy(items, (item) => item.email_type || "unknown");
  const riskCounts = countBy(items, (item) => item.risks || []);
  const fetchedCount = items.filter((item) => linkEvidenceItems(item).some((ev) => ev.status === "fetched")).length;
  const healthCounts = countBy(items, (item) => evidenceHealth(item));
  document.getElementById("evidenceStats").innerHTML = [
    `<div class="stat"><strong>${scan.raw_count ?? 0}</strong><span>raw messages</span></div>`,
    `<div class="stat"><strong>${items.length}</strong><span>loaded items</span></div>`,
    `<div class="stat"><strong>${scan.possibly_truncated ? "yes" : "no"}</strong><span>possibly truncated</span></div>`,
    `<div class="stat"><strong>${fetchedCount}</strong><span>items with public link evidence</span></div>`,
    `<div class="stat"><strong>${healthCounts.good || 0}</strong><span>strong evidence</span></div>`,
    `<div class="stat"><strong>${healthCounts.usable || 0}</strong><span>usable snippet only</span></div>`,
  ].join("");
  fillFilter("typeFilter", "", "all types", Object.keys(typeCounts));
  fillFilter("riskFilter", "", "all risks", Object.keys(riskCounts));
  const itemsToShow = filteredItems();
  if (!state.selectedUid && itemsToShow[0]) state.selectedUid = String(itemsToShow[0].uid || "");
  document.getElementById("emailList").innerHTML = itemsToShow.map((item) => {
    const review = item._review || {};
    const uid = String(item.uid || "");
    const health = evidenceHealth(item);
    const riskBadges = (item.risks || []).slice(0, 3).map((risk) => badge(risk, "warn")).join(" ");
    const topicBadges = (item.topics || []).slice(0, 3).map((topic) => badge(topicLabel(topic), "usable")).join(" ");
    const linkCount = (item.links || []).length;
    const pendingLinks = (item.links || []).filter((link) => !link.policy_decision || link.policy_decision === "pending").length;
    return `<button class="email-row ${uid === state.selectedUid ? "selected" : ""}" data-uid="${escapeHtml(uid)}">
      <h4>${escapeHtml(item.subject || "(no subject)")}</h4>
      <div class="meta">UID ${escapeHtml(uid)} · ${escapeHtml(item.email_type || "unknown")} · ${linkCount} links</div>
      <div class="meta">${escapeHtml(item.from || "")}</div>
      <div>${badge(healthLabel(health), health)} ${topicBadges} ${pendingLinks ? badge(`${pendingLinks} pending links`, "warn") : ""} ${riskBadges} ${review.important ? badge("important", "good") : ""} ${review.ignore ? badge("ignored", "bad") : ""}</div>
    </button>`;
  }).join("");
  for (const row of document.querySelectorAll(".email-row")) {
    row.addEventListener("click", () => {
      state.selectedUid = row.dataset.uid;
      renderEvidence();
      showView("evidence");
    });
  }
  renderEmailDetail(items.find((item) => String(item.uid || "") === state.selectedUid) || itemsToShow[0]);
}

function fillFilter(id, selected, label, values) {
  const select = document.getElementById(id);
  const current = select.value;
  select.innerHTML = [`<option value="">${label}</option>`]
    .concat(values.sort().map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`))
    .join("");
  select.value = current || selected;
}

function renderEmailDetail(item) {
  const target = document.getElementById("emailDetail");
  if (!item) {
    target.innerHTML = "No email selected.";
    return;
  }
  const uid = String(item.uid || "");
  const review = item._review || {};
  const policy = policyForType(item.email_type || "unknown");
  const health = evidenceHealth(item);
  const baseEvidence = baseEvidenceItem(item);
  const links = item.links || [];
  const linkEvidence = linkEvidenceItems(item);
  const pendingLinks = links.filter((link) => !link.policy_decision || link.policy_decision === "pending").length;
  const nextAction = links.length === 0
    ? "No links detected; use base evidence."
    : pendingLinks > 0
      ? "Run email-summary --scan-file ... --enrich-links --no-send."
      : policy.fetch_links
        ? "Review link evidence results before approving the Brief."
        : `No fetch: policy_no_fetch:${policy.name || "unknown"}.`;
  const triage = item.link_triage || {};
  const triageGroups = triage.groups || [];
  const triageSummary = item.link_triage ? [
      `total ${triage.total_links || 0}`,
      `fetch ${triage.selected_fetch_count || 0}`,
      `defer ${triage.deferred_count || 0}`,
      `skip ${triage.hard_skipped_count || 0}`,
      `dedupe ${triage.deduped_count || 0}`,
      `unmapped ${triage.unmapped_topic_count || 0}`,
    ].join(" · ") : "No link triage recorded.";
  const triageRows = triageGroups.map((group) => `<tr>
      <td>${escapeHtml(group.decision || "")}</td>
      <td>${escapeHtml(group.reason || "")}</td>
      <td>${escapeHtml((group.topics || []).map((topic) => topic.name || topic.id || "").join(", "))}</td>
      <td>${escapeHtml(group.canonical_url || group.url || "")}</td>
    </tr>`).join("");
  const linkRows = links.map((link) => `<tr>
      <td>${escapeHtml(link.policy_decision || "pending")}</td>
      <td>${escapeHtml(link.anchor_text || "")}</td>
      <td>${escapeHtml(link.context || "")}</td>
      <td>${escapeHtml(link.url || "")}</td>
    </tr>`).join("");
  const evidenceRows = linkEvidence.map((ev) => `<li>
      ${badge(ev.status || "unknown", ev.status === "fetched" ? "good" : ev.status === "failed" ? "failed" : "skipped")}
      ${escapeHtml(ev.title || ev.reason || ev.url || "")}
      <br><span class="meta">${escapeHtml(ev.url || "")}</span>
      <br><span class="meta">${escapeHtml(ev.excerpt || "")}</span>
    </li>`).join("");
  const riskBadges = (item.risks || []).map((risk) => badge(risk, "warn")).join(" ");
  const topicRows = (item.topics || []).map((topic) => `<li>
      ${badge(topicLabel(topic), topic.priority === "high" ? "good" : "usable")}
      <span class="meta">matched: ${escapeHtml((topic.matched_keywords || []).join(", "))}</span>
      <br><span class="meta">${escapeHtml(topic.description || "")}</span>
      <br><span class="meta">${escapeHtml(topic.summary_focus || "")}</span>
    </li>`).join("");
  target.innerHTML = `
    <h3>${escapeHtml(item.subject || "(no subject)")}</h3>
    <p class="meta">UID ${escapeHtml(uid)} · ${escapeHtml(item.date || "")}</p>
    <p class="meta">${escapeHtml(item.from || "")}</p>
    <p>${badge(item.email_type || "unknown")} ${badge(healthLabel(health), health)} ${item.has_attachments ? badge("attachment", "warn") : ""}</p>

    <h3>1. Topic Match</h3>
    <ul>${topicRows || "<li>No EmailTopicMap match.</li>"}</ul>

    <h3>2. Base Evidence</h3>
    <p class="meta">${escapeHtml(baseEvidence.status || "available")} · ${escapeHtml(baseEvidence.reason || "email_snippet")}</p>
    <pre>${escapeHtml(baseEvidence.excerpt || item.snippet || "")}</pre>

    <h3>3. Link Decision</h3>
    <p>${badge(policy.fetch_links ? "fetch links" : "snippet only", policy.fetch_links ? "good" : "skipped")} ${escapeHtml(policy.summary_focus || "")}</p>
    <p class="notice">${escapeHtml(nextAction)}</p>
    <p class="meta">${escapeHtml(triageSummary)}</p>
    <table>
      <thead><tr><th>triage</th><th>reason</th><th>topics</th><th>canonical url</th></tr></thead>
      <tbody>${triageRows || `<tr><td colspan="4">No triage groups.</td></tr>`}</tbody>
    </table>
    <table>
      <thead><tr><th>decision</th><th>anchor</th><th>context</th><th>raw url</th></tr></thead>
      <tbody>${linkRows || `<tr><td colspan="4">No links detected.</td></tr>`}</tbody>
    </table>

    <h3>4. Link Evidence</h3>
    <ul>${evidenceRows || "<li>No public link evidence.</li>"}</ul>

    <h3>5. Risks</h3>
    <p>${riskBadges || badge("none", "good")}</p>

    <h3>Review marks</h3>
    <p>${review.ignore ? badge("ignore", "bad") : ""} ${review.important ? badge("important", "good") : ""} ${review.needs_link_review ? badge("needs_link_review", "warn") : ""} ${review.type_override ? badge(`type: ${review.type_override}`) : ""}</p>
    <div class="filters">
      <button data-mark="important">Toggle important</button>
      <button data-mark="ignore">Toggle ignore</button>
      <button data-mark="needs_link_review">Toggle link review</button>
    </div>`;
  for (const button of target.querySelectorAll("[data-mark]")) {
    button.addEventListener("click", async () => {
      const key = button.dataset.mark;
      await saveReview({ email_marks: { [uid]: { [key]: !review[key] } } });
    });
  }
}

function renderBrief() {
  const brief = state.brief || {};
  const markdown = brief.effective_markdown || "";
  const coverage = brief.source_coverage || {};
  const sections = brief.sections || [];
  document.getElementById("briefRendered").innerHTML = markdownToHtml(markdown || "No EmailIntelBrief loaded.");
  document.getElementById("briefEditor").value = brief.review?.brief_override_markdown || "";
  document.getElementById("briefStatus").textContent =
    `${brief.object_type || "email_intel_brief"} ${brief.object_version || ""} · ${brief.source || "summary_file"} · status: ${brief.review?.brief_status || "draft"} · ${brief.path || ""}`;
  document.getElementById("briefSummary").innerHTML = [
    `<div class="stat"><strong>${sections.length}</strong><span>sections</span></div>`,
    `<div class="stat"><strong>${coverage.covered_count ?? 0}/${coverage.item_count ?? 0}</strong><span>source coverage</span></div>`,
    `<div class="stat"><strong>${brief.source_index?.length || 0}</strong><span>source index rows</span></div>`,
    `<div class="stat"><strong>${coverage.complete ? "yes" : "no"}</strong><span>coverage complete</span></div>`,
  ].join("");
  document.getElementById("sourceIndex").innerHTML = `<h3>来源索引</h3>` + (brief.source_index || []).map((source) =>
    `<button data-source-uid="${escapeHtml(source.source_uid || source.uid)}">UID ${escapeHtml(source.uid)} · ${escapeHtml(source.subject)}</button>`
  ).join("");
  for (const button of document.querySelectorAll("[data-source-uid]")) {
    button.addEventListener("click", () => {
      state.selectedUid = button.dataset.sourceUid;
      showView("evidence");
      renderEvidence();
    });
  }
}

function needRefUid(ref) {
  const text = String(ref || "");
  const uriMatch = text.match(/email:\/\/[^/]+\/([^\s`]+)/);
  if (uriMatch) return uriMatch[1];
  const refMatch = text.match(/^email:([^\s`]+)$/);
  return refMatch ? refMatch[1] : "";
}

function filteredNeeds() {
  const needs = state.needs?.needs || [];
  const urgencyOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  const statusOrder = { open: 0, watching: 1, blocked: 2, stale: 3, fulfilled_now: 4, superseded: 5, closed: 6 };
  return needs.filter((need) => {
    if (state.needFilters.status && need.status !== state.needFilters.status) return false;
    if (state.needFilters.urgency && need.urgency !== state.needFilters.urgency) return false;
    if (state.needFilters.topic && need.topic_id !== state.needFilters.topic) return false;
    return true;
  }).sort((left, right) => {
    if (state.needFilters.sort === "status") {
      return (statusOrder[left.status] ?? 99) - (statusOrder[right.status] ?? 99) || (urgencyOrder[left.urgency] ?? 99) - (urgencyOrder[right.urgency] ?? 99);
    }
    if (state.needFilters.sort === "topic_id") {
      return String(left.topic_id || "").localeCompare(String(right.topic_id || "")) || (urgencyOrder[left.urgency] ?? 99) - (urgencyOrder[right.urgency] ?? 99);
    }
    return (left.status === "open" ? 0 : 1) - (right.status === "open" ? 0 : 1) || (urgencyOrder[left.urgency] ?? 99) - (urgencyOrder[right.urgency] ?? 99);
  });
}

function renderNeeds() {
  const needs = state.needs?.needs || [];
  const counts = state.needs?.counts || {};
  fillFilter("needStatusFilter", "", "all statuses", [...new Set(needs.map((need) => need.status).filter(Boolean))]);
  fillFilter("needUrgencyFilter", "", "all urgencies", [...new Set(needs.map((need) => need.urgency).filter(Boolean))]);
  fillFilter("needTopicFilter", "", "all topics", [...new Set(needs.map((need) => need.topic_id).filter(Boolean))]);
  document.getElementById("needSort").value = state.needFilters.sort;
  document.getElementById("needStats").innerHTML = [
    `<div class="stat"><strong>${needs.length}</strong><span>total needs</span></div>`,
    `<div class="stat"><strong>${counts.open || 0}</strong><span>open</span></div>`,
    `<div class="stat"><strong>${counts.watching || 0}</strong><span>watching</span></div>`,
    `<div class="stat"><strong>${counts.closed || 0}</strong><span>closed</span></div>`,
  ].join("");
  document.getElementById("needList").innerHTML = filteredNeeds().map((need) => {
    const refs = need.known_source_refs || [];
    const refButtons = refs.map((ref) => {
      const uid = needRefUid(ref);
      return uid
        ? `<button data-need-ref-uid="${escapeHtml(uid)}">Evidence UID ${escapeHtml(uid)}</button>`
        : `<span class="badge">${escapeHtml(ref)}</span>`;
    }).join(" ");
    const auditRows = (need.audit_trail || []).map((entry) => `<tr>
      <td>${escapeHtml(entry.at)}</td><td>${escapeHtml(entry.actor)}</td><td>${escapeHtml(entry.event_type)}</td><td>${escapeHtml(entry.old_status)} → ${escapeHtml(entry.new_status)}</td><td>${escapeHtml(entry.reason)}</td>
    </tr>`).join("");
    const canAct = !["closed", "superseded"].includes(need.status);
    return `<article class="need-card ${need.status === "open" ? "open" : ""}" data-testid="need-card" data-need-id="${escapeHtml(need.need_id)}">
      <h3>${escapeHtml(need.need_id)}</h3>
      <p>${badge(need.status, need.status === "open" ? "warn" : need.status === "closed" ? "good" : "")} ${badge(need.urgency, need.urgency === "critical" || need.urgency === "high" ? "bad" : "")} ${badge(need.topic_id || "no topic", "usable")}</p>
      <p><strong>claim_or_question</strong></p>
      <p>${escapeHtml(need.claim_or_question || "")}</p>
      <p class="meta">why_needed: ${escapeHtml(need.why_needed || "")}</p>
      <p class="meta">needed_evidence: ${escapeHtml((need.needed_evidence || []).join(", "))}</p>
      <div class="need-actions">
        <button data-need-brief="${escapeHtml(need.source_brief_id || "")}">Source brief</button>
        ${refButtons || "<span class='meta'>No known evidence refs.</span>"}
        ${canAct ? `<button data-need-action="close" data-need-id="${escapeHtml(need.need_id)}">Close</button><button data-need-action="stale" data-need-id="${escapeHtml(need.need_id)}">Mark stale</button>` : ""}
      </div>
      <details><summary>Audit trail (${escapeHtml((need.audit_trail || []).length)})</summary>
        <table><thead><tr><th>at</th><th>actor</th><th>event</th><th>status</th><th>reason</th></tr></thead><tbody>${auditRows || `<tr><td colspan="5">No audit events.</td></tr>`}</tbody></table>
      </details>
    </article>`;
  }).join("") || `<div class="need-card">No evidence needs.</div>`;
  for (const button of document.querySelectorAll("[data-need-ref-uid]")) {
    button.addEventListener("click", () => {
      state.selectedUid = button.dataset.needRefUid;
      showView("evidence");
      renderEvidence();
    });
  }
  for (const button of document.querySelectorAll("[data-need-brief]")) {
    button.addEventListener("click", () => showView("brief"));
  }
  for (const button of document.querySelectorAll("[data-need-action]")) {
    button.addEventListener("click", async () => {
      await api(`/api/needs/${encodeURIComponent(button.dataset.needId)}/action`, {
        method: "POST",
        body: JSON.stringify({ action: button.dataset.needAction }),
      });
      await refresh();
      showView("needs");
    });
  }
}

function renderPolicy() {
  document.getElementById("policyEditor").value = state.policy?.markdown || "";
  document.getElementById("policyStatus").textContent = state.policy?.error
    ? `Policy parse error: ${state.policy.error}`
    : `Policy loaded from ${state.policy?.path || ""}`;
  renderPolicySummary();
}

function renderTopics() {
  document.getElementById("topicEditor").value = state.topics?.markdown || "";
  document.getElementById("topicStatus").textContent = state.topics?.error
    ? `Topic parse error: ${state.topics.error}`
    : `Topic map loaded from ${state.topics?.path || ""}`;
  renderTopicSummary();
  renderTopicGuide();
}

function renderTopicSummary() {
  const topicMap = state.topics?.topic_map;
  const target = document.getElementById("topicSummary");
  if (!topicMap) {
    target.innerHTML = `<div class="topic-card">${badge("invalid", "bad")} Topic JSON cannot be parsed.</div>`;
    return;
  }
  const topics = topicMap.topics || [];
  const priorities = countBy(topics, (topic) => topic.priority || "normal");
  const keywordCount = topics.reduce((sum, topic) => sum + ((topic.keywords || []).length), 0);
  const highTopics = topics.filter((topic) => topic.priority === "high").map((topic) => topicLabel(topic));
  target.innerHTML = [
    `<div class="topic-card"><strong>1. Tracked Topics</strong><p>${escapeHtml(topics.map((topic) => topicLabel(topic)).join(", ") || "none")}</p></div>`,
    `<div class="topic-card"><strong>2. Priority</strong><p>high: ${escapeHtml(priorities.high || 0)} · medium: ${escapeHtml(priorities.medium || 0)} · low: ${escapeHtml(priorities.low || 0)}</p>${highTopics.map((name) => badge(name, "good")).join(" ") || badge("no high priority", "warn")}</div>`,
    `<div class="topic-card"><strong>3. Match Surface</strong><p>${escapeHtml(keywordCount)} keywords / aliases. Matching uses sender, subject, snippet, links and evidence excerpts.</p></div>`,
    `<div class="topic-card"><strong>4. Default Behavior</strong><p>${escapeHtml(topicMap.default_behavior || "")}</p></div>`,
  ].join("");
}

function renderTopicGuide() {
  const topics = state.topics?.topic_map?.topics || [];
  const target = document.getElementById("topicGuide");
  if (!topics.length) {
    target.innerHTML = `<div class="topic-card">${badge("empty", "warn")} No tracked topics.</div>`;
    return;
  }
  target.innerHTML = topics.map((topic) => {
    const examples = (topic.examples || []).slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    const nonExamples = (topic.non_examples || []).slice(0, 2).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    return `<div class="topic-card">
      <strong>${escapeHtml(topicLabel(topic))}</strong>
      <p>${escapeHtml(topic.description || topic.summary_focus || "")}</p>
      <p class="meta">priority: ${escapeHtml(topic.priority || "normal")} · keywords: ${escapeHtml((topic.keywords || []).length)}</p>
      <p class="meta">Examples</p>
      <ul>${examples || "<li>No examples.</li>"}</ul>
      <p class="meta">Not this</p>
      <ul>${nonExamples || "<li>No non-examples.</li>"}</ul>
    </div>`;
  }).join("");
}

function renderPolicySummary() {
  const policy = state.policy?.policy;
  const target = document.getElementById("policySummary");
  if (!policy) {
    target.innerHTML = `<div class="policy-card">${badge("invalid", "bad")} Policy JSON cannot be parsed.</div>`;
    return;
  }
  const limits = policy.limits || {};
  const types = policy.email_types || [];
  const fetchTypes = types.filter((item) => item.fetch_links).map((item) => item.name);
  const noFetchTypes = types.filter((item) => !item.fetch_links).map((item) => item.name);
  target.innerHTML = [
    `<div class="policy-card"><strong>1. Type Rules</strong><p>${escapeHtml(types.map((item) => item.name).join(", "))}</p></div>`,
    `<div class="policy-card"><strong>2. Link Strategy</strong><p>per email: ${escapeHtml(limits.max_links_per_email ?? "")} · total: ${escapeHtml(limits.max_links_total ?? "")} · timeout: ${escapeHtml(limits.timeout_seconds ?? "")}s</p>${fetchTypes.map((name) => badge(name, "good")).join(" ") || badge("none", "warn")}</div>`,
    `<div class="policy-card"><strong>3. Snippet-only Types</strong>${noFetchTypes.map((name) => badge(name, "skipped")).join(" ") || badge("none", "warn")}</div>`,
    `<div class="policy-card"><strong>4. Safety / Skip Rules</strong><p>${escapeHtml((policy.skip_url_patterns || []).join(", "))}</p></div>`,
  ].join("");
}

function renderChecklist() {
  const payload = state.checklist || {};
  const checklist = payload.checklist || {};
  const lines = Object.entries(checklist)
    .filter(([key]) => key !== "risks")
    .map(([key, value]) => `<p>${badge(value ? "pass" : "fail", value ? "good" : "bad")} ${escapeHtml(key)}</p>`);
  const risks = (checklist.risks || []).map((risk) => badge(risk, "bad")).join(" ");
  document.getElementById("checklist").innerHTML = [
    `<p>brief_status: ${badge(payload.brief_status || "draft")}</p>`,
    ...lines,
    `<p>risks: ${risks || badge("none", "good")}</p>`,
    `<p>${payload.delivery_ready ? badge("delivery command visible", "good") : badge("delivery locked", "warn")}</p>`,
  ].join("");
}

function renderCommands() {
  const commands = state.commands?.commands || {};
  const deliveryReady = state.checklist?.delivery_ready;
  const visible = Object.entries(commands).filter(([key]) => key !== "manual_delivery_after_approval" || deliveryReady);
  document.getElementById("commands").innerHTML = visible.map(([key, command]) =>
    `<div><strong>${escapeHtml(key)}</strong><pre class="command">${escapeHtml(command)}</pre></div>`
  ).join("");
}

function showView(view) {
  for (const button of document.querySelectorAll(".nav-button")) {
    button.classList.toggle("active", button.dataset.view === view);
  }
  document.getElementById("topicsView").classList.toggle("active", view === "topics");
  document.getElementById("evidenceView").classList.toggle("active", view === "evidence");
  document.getElementById("policyView").classList.toggle("active", view === "policy");
  document.getElementById("briefView").classList.toggle("active", view === "brief");
  document.getElementById("needsView").classList.toggle("active", view === "needs");
}

async function saveReview(update) {
  await api("/api/review", { method: "POST", body: JSON.stringify(update) });
  await refresh();
}

async function refresh() {
  const [context, evidence, brief, policy, topics, checklist, commands, needs] = await Promise.all([
    api("/api/context"),
    api("/api/evidence-pack"),
    api("/api/intel-brief"),
    api("/api/policy"),
    api("/api/topics"),
    api("/api/checklist"),
    api("/api/commands"),
    api("/api/needs"),
  ]);
  Object.assign(state, { context, evidence, brief, policy, topics, checklist, commands, needs });
  renderContext();
  renderEvidence();
  renderBrief();
  renderPolicy();
  renderTopics();
  renderNeeds();
  renderChecklist();
  renderCommands();
}

function bindEvents() {
  for (const button of document.querySelectorAll(".nav-button")) {
    button.addEventListener("click", () => showView(button.dataset.view));
  }
  document.getElementById("typeFilter").addEventListener("change", (event) => {
    state.filters.type = event.target.value;
    renderEvidence();
  });
  document.getElementById("riskFilter").addEventListener("change", (event) => {
    state.filters.risk = event.target.value;
    renderEvidence();
  });
  document.getElementById("attachmentFilter").addEventListener("change", (event) => {
    state.filters.attachments = event.target.checked;
    renderEvidence();
  });
  document.getElementById("evidenceFilter").addEventListener("change", (event) => {
    state.filters.evidence = event.target.checked;
    renderEvidence();
  });
  document.getElementById("needStatusFilter").addEventListener("change", (event) => {
    state.needFilters.status = event.target.value;
    renderNeeds();
  });
  document.getElementById("needUrgencyFilter").addEventListener("change", (event) => {
    state.needFilters.urgency = event.target.value;
    renderNeeds();
  });
  document.getElementById("needTopicFilter").addEventListener("change", (event) => {
    state.needFilters.topic = event.target.value;
    renderNeeds();
  });
  document.getElementById("needSort").addEventListener("change", (event) => {
    state.needFilters.sort = event.target.value;
    renderNeeds();
  });
  document.getElementById("saveBrief").addEventListener("click", () => {
    saveReview({ brief_override_markdown: document.getElementById("briefEditor").value });
  });
  document.getElementById("markNeedsRevision").addEventListener("click", () => {
    saveReview({ brief_status: "needs_revision" });
  });
  document.getElementById("approveBrief").addEventListener("click", () => {
    saveReview({ brief_status: "approved" });
  });
  document.getElementById("savePolicy").addEventListener("click", async () => {
    const status = document.getElementById("policyStatus");
    try {
      await api("/api/policy", {
        method: "POST",
        body: JSON.stringify({ markdown: document.getElementById("policyEditor").value }),
      });
      status.textContent = "Policy saved. It will affect future scan/enrich runs only.";
      await refresh();
    } catch (error) {
      status.textContent = `Policy save failed: ${error.message}`;
    }
  });
  document.getElementById("saveTopics").addEventListener("click", async () => {
    const status = document.getElementById("topicStatus");
    try {
      await api("/api/topics", {
        method: "POST",
        body: JSON.stringify({ markdown: document.getElementById("topicEditor").value }),
      });
      status.textContent = "Topics saved. Regenerate EvidencePack/Brief to apply topic changes.";
      await refresh();
    } catch (error) {
      status.textContent = `Topic save failed: ${error.message}`;
    }
  });
}

bindEvents();
refresh().catch((error) => {
  document.getElementById("contextLine").textContent = `Failed to load Workbench: ${error.message}`;
});
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Podsum Email Workbench.")
    add_args(parser)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
