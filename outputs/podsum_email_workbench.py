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


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ROOT = email_summary.DEFAULT_OUTPUT_DIR
DEFAULT_POLICY_FILE = email_summary.DEFAULT_LINK_POLICY
MAX_POST_BYTES = 1024 * 1024
BRIEF_STATUSES = {"draft", "needs_revision", "approved"}
REVIEW_MARK_KEYS = {"ignore", "important", "type_override", "needs_link_review"}


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


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


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
    }
    return {
        "date": config.date,
        "root": str(config.root),
        "reports_dir": str(config.reports_dir),
        "server": {
            "host": config.host,
            "port": config.port,
            "mode": "manual-local-workbench",
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
        scan = email_summary.normalize_evidence_pack(scan, policy)
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


def parse_source_index(markdown: str) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for match in SOURCE_RE.finditer(markdown):
        sources.append({key: value.strip() for key, value in match.groupdict().items()})
    return sources


def intel_brief_payload(config: WorkbenchConfig) -> dict[str, Any]:
    review = load_review(config)
    missing = not config.summary_path.exists()
    markdown = "" if missing else config.summary_path.read_text(encoding="utf-8", errors="replace")
    override = str(review.get("brief_override_markdown") or "")
    effective = override or markdown
    return {
        "missing": missing,
        "path": str(config.summary_path),
        "markdown": markdown,
        "effective_markdown": effective,
        "source": "review_override" if override else "summary_file",
        "source_index": parse_source_index(effective),
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
    python = "/usr/bin/python3"
    scan = str(config.scan_path)
    root = str(config.root)
    commands = {
        "generate_scan_manual_imap": shell_join(
            [
                python,
                str(podsum),
                "email-summary",
                "--output",
                root,
                "--allow-imap-read",
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
            ]
        ),
    }
    return {
        "commands": commands,
        "notes": [
            "Workbench only displays commands; it never executes IMAP, Hermes, send, or launchd actions.",
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
                elif path == "/api/checklist":
                    json_response(self, 200, {"ok": True, **checklist_payload(config)})
                elif path == "/api/commands":
                    json_response(self, 200, {"ok": True, **commands_payload(config)})
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
      <button class="nav-button" data-view="policy">EmailPolicyPanel</button>
      <button class="nav-button active" data-view="evidence">EmailEvidencePack</button>
      <button class="nav-button" data-view="brief">EmailIntelBrief</button>
    </nav>
    <section class="workspace">
      <section id="policyView" class="view">
        <div class="section-head">
          <h2>EmailPolicyPanel</h2>
          <button id="savePolicy">Save policy</button>
        </div>
        <p class="notice">核心可视化工作对象：控制邮件分类、链接补全和 evidence 生成边界。修改 policy 只影响后续 scan/enrich，不会自动重跑。</p>
        <div id="policySummary" class="policy-summary"></div>
        <label class="field-label" for="policyEditor">EmailPolicy spec</label>
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
.stats, .policy-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.stat, .panel, .detail, .email-row, .brief-rendered, .policy-card {
  background: var(--panel);
  border: 1px solid var(--line);
}
.stat, .policy-card { padding: 10px; }
.stat strong { display: block; font-size: 18px; }
.policy-card strong { display: block; margin-bottom: 6px; }
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
  checklist: null,
  commands: null,
  selectedUid: null,
  filters: { type: "", risk: "", attachments: false, evidence: false },
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
  const fetchedCount = items.filter((item) => (item.evidence || []).some((ev) => ev.status === "fetched")).length;
  document.getElementById("evidenceStats").innerHTML = [
    `<div class="stat"><strong>${scan.raw_count ?? 0}</strong><span>raw messages</span></div>`,
    `<div class="stat"><strong>${items.length}</strong><span>loaded items</span></div>`,
    `<div class="stat"><strong>${scan.possibly_truncated ? "yes" : "no"}</strong><span>possibly truncated</span></div>`,
    `<div class="stat"><strong>${fetchedCount}</strong><span>items with fetched evidence</span></div>`,
  ].join("");
  fillFilter("typeFilter", "", "all types", Object.keys(typeCounts));
  fillFilter("riskFilter", "", "all risks", Object.keys(riskCounts));
  const itemsToShow = filteredItems();
  if (!state.selectedUid && itemsToShow[0]) state.selectedUid = String(itemsToShow[0].uid || "");
  document.getElementById("emailList").innerHTML = itemsToShow.map((item) => {
    const review = item._review || {};
    const uid = String(item.uid || "");
    const riskBadges = (item.risks || []).slice(0, 3).map((risk) => badge(risk, "warn")).join(" ");
    return `<button class="email-row ${uid === state.selectedUid ? "selected" : ""}" data-uid="${escapeHtml(uid)}">
      <h4>${escapeHtml(item.subject || "(no subject)")}</h4>
      <div class="meta">UID ${escapeHtml(uid)} · ${escapeHtml(item.email_type || "unknown")}</div>
      <div class="meta">${escapeHtml(item.from || "")}</div>
      <div>${riskBadges} ${review.important ? badge("important", "good") : ""} ${review.ignore ? badge("ignored", "bad") : ""}</div>
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
  const links = (item.links || []).map((link) => `<li>${escapeHtml(link.url)} ${badge(link.policy_decision || "pending")}</li>`).join("");
  const evidence = (item.evidence || []).map((ev) => `<li>${badge(ev.status || "")} ${escapeHtml(ev.title || ev.reason || ev.url || "")}<br><span class="meta">${escapeHtml(ev.excerpt || "")}</span></li>`).join("");
  target.innerHTML = `
    <h3>${escapeHtml(item.subject || "(no subject)")}</h3>
    <p class="meta">UID ${escapeHtml(uid)} · ${escapeHtml(item.date || "")}</p>
    <p class="meta">${escapeHtml(item.from || "")}</p>
    <p>${badge(item.email_type || "unknown")} ${(item.risks || []).map((risk) => badge(risk, "warn")).join(" ")}</p>
    <h3>Snippet</h3>
    <pre>${escapeHtml(item.snippet || "")}</pre>
    <h3>Links</h3>
    <ul>${links || "<li>none</li>"}</ul>
    <h3>Evidence</h3>
    <ul>${evidence || "<li>none</li>"}</ul>
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
  document.getElementById("briefRendered").innerHTML = markdownToHtml(markdown || "No EmailIntelBrief loaded.");
  document.getElementById("briefEditor").value = brief.review?.brief_override_markdown || "";
  document.getElementById("briefStatus").textContent =
    `${brief.source || "summary_file"} · status: ${brief.review?.brief_status || "draft"} · ${brief.path || ""}`;
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

function renderPolicy() {
  document.getElementById("policyEditor").value = state.policy?.markdown || "";
  document.getElementById("policyStatus").textContent = state.policy?.error
    ? `Policy parse error: ${state.policy.error}`
    : `Policy loaded from ${state.policy?.path || ""}`;
  renderPolicySummary();
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
    `<div class="policy-card"><strong>link budget</strong><p>per email: ${escapeHtml(limits.max_links_per_email ?? "")}</p><p>total: ${escapeHtml(limits.max_links_total ?? "")}</p><p>timeout: ${escapeHtml(limits.timeout_seconds ?? "")}s</p></div>`,
    `<div class="policy-card"><strong>fetch links</strong>${fetchTypes.map((name) => badge(name, "good")).join(" ") || badge("none", "warn")}</div>`,
    `<div class="policy-card"><strong>snippet only</strong>${noFetchTypes.map((name) => badge(name)).join(" ") || badge("none", "warn")}</div>`,
    `<div class="policy-card"><strong>skip patterns</strong><p>${escapeHtml((policy.skip_url_patterns || []).join(", "))}</p></div>`,
    `<div class="policy-card"><strong>email types</strong><p>${escapeHtml(types.map((item) => item.name).join(", "))}</p></div>`,
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
  document.getElementById("evidenceView").classList.toggle("active", view === "evidence");
  document.getElementById("policyView").classList.toggle("active", view === "policy");
  document.getElementById("briefView").classList.toggle("active", view === "brief");
}

async function saveReview(update) {
  await api("/api/review", { method: "POST", body: JSON.stringify(update) });
  await refresh();
}

async function refresh() {
  const [context, evidence, brief, policy, checklist, commands] = await Promise.all([
    api("/api/context"),
    api("/api/evidence-pack"),
    api("/api/intel-brief"),
    api("/api/policy"),
    api("/api/checklist"),
    api("/api/commands"),
  ]);
  Object.assign(state, { context, evidence, brief, policy, checklist, commands });
  renderContext();
  renderEvidence();
  renderBrief();
  renderPolicy();
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
