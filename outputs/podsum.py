#!/usr/bin/env python3
"""Unified Podsum runner with episode-level state."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import podcast_downloader as downloader
import podsum_email_summary as email_summary
import podsum_email_workbench as email_workbench
import podsum_runtime
import podsum_send_to_feishu as sender


DEFAULT_STATE_FILE = podsum_runtime.podsum_home() / "state.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Podcasts/AutoDownloads"
DEFAULT_FEEDS_FILE = Path(__file__).with_name("feeds.json")

# 所有取 Path 的 CLI 参数。子命令只带其中一部分，可选参数默认为 None。
PATH_ARGS = (
    "feeds_file",
    "state",
    "output",
    "memory_file",
    "interpretation_prompt",
    "interpretation_rules",
    "project_dir",
    "hermes",
    "old_download_state",
    "old_sent_state",
    "email_env_file",
    "email_scan_file",
    "email_eml_dir",
    "email_summary_prompt",
    "email_evidence_preprocess_prompt",
    "email_link_policy",
    "email_topic_file",
    "root",
    "policy_file",
    "topic_file",
)


def log(message: str) -> None:
    print(message, flush=True)


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "episodes": {}, "feeds": {}}
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    if "sent" in state and "episodes" not in state:
        state = repair_legacy_sent_state(path, state)
    state.setdefault("version", 1)
    state.setdefault("episodes", {})
    state.setdefault("feeds", {})
    return state


def repair_legacy_sent_state(path: Path, legacy: dict[str, Any]) -> dict[str, Any]:
    repaired: dict[str, Any] = {"version": 1, "episodes": {}, "feeds": {}}
    sent = legacy.get("sent", {})
    if not isinstance(sent, dict):
        return repaired

    for transcript_name, sent_record in sent.items():
        if not isinstance(sent_record, dict):
            continue
        transcript_path = Path(transcript_name).expanduser()
        meta: dict[str, Any] = {}
        if transcript_path.exists():
            text = transcript_path.read_text(encoding="utf-8", errors="replace")
            meta, _body = sender.parse_frontmatter(text)
        audio_path = Path(str(meta.get("audio") or transcript_path.parent.parent / f"{transcript_path.stem}.mp3"))
        key = hashlib.sha256(str(transcript_path).encode("utf-8")).hexdigest()
        repaired["episodes"][key] = {
            "podcast": str(meta.get("podcast") or transcript_path.parent.parent.name),
            "author": "",
            "feed_url": "",
            "episode": str(meta.get("episode") or transcript_path.stem),
            "guid": key,
            "published": "",
            "enclosure_url": "",
            "audio_path": str(audio_path),
            "transcript_path": str(transcript_path),
            "status": "sent",
            "attempts": {
                "download": 0,
                "transcribe": 0,
                "interpret": 0,
                "send": 1,
            },
            "error": None,
            "transcript_sha256": str(sent_record.get("sha256") or ""),
            "sent_at": str(sent_record.get("sent_at") or ""),
            "bundle_path": str(sent_record.get("bundle_path") or ""),
            "epub_path": str(sent_record.get("epub_path") or ""),
            "updated_at": now_stamp(),
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(repaired, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)
    return repaired


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


def episode_record(
    podcast: downloader.Podcast,
    episode: downloader.Episode,
    audio_path: Path,
    status: str,
) -> dict[str, Any]:
    return {
        "podcast": podcast.title,
        "author": podcast.author,
        "feed_url": podcast.feed_url,
        "episode": episode.title,
        "guid": episode.guid,
        "published": episode.published or "",
        "enclosure_url": episode.enclosure_url,
        "audio_path": str(audio_path),
        "transcript_path": str(downloader.transcript_path_for_audio(audio_path)),
        "status": status,
        "attempts": {
            "download": 0,
            "transcribe": 0,
            "interpret": 0,
            "send": 0,
        },
        "error": None,
        "updated_at": now_stamp(),
    }


def latest_episode(feed_url: str, timeout: int) -> downloader.Episode | None:
    feed_xml = downloader.fetch_bytes(feed_url, timeout)
    episodes = downloader.parse_feed(feed_xml)
    return episodes[0] if episodes else None


def download_latest(args: argparse.Namespace, state: dict[str, Any]) -> int:
    podcasts = downloader.read_feeds_file(args.feeds_file)
    downloaded_count = 0

    for podcast in podcasts:
        log(f"Checking: {podcast.title}")
        try:
            episode = latest_episode(podcast.feed_url, args.timeout)
        except Exception as exc:
            log(f"  failed to read feed: {exc}")
            state["feeds"][podcast.feed_url] = {
                "title": podcast.title,
                "last_checked_at": now_stamp(),
                "error": str(exc),
            }
            save_state(args.state, state)
            continue

        if episode is None:
            log("  no playable episode found")
            continue

        key = downloader.episode_key(podcast, episode)
        existing = state["episodes"].get(key)
        if existing and existing.get("status") not in {"failed_download"}:
            log(f"  already recorded: {episode.title}")
            continue

        try:
            prefix = downloader.date_prefix(episode.published)
        except Exception:
            prefix = time.strftime("%Y-%m-%d")
        filename = downloader.sanitize_name(f"{prefix} {episode.title}") + downloader.extension_from_url(episode.enclosure_url)
        show_dir = args.output / downloader.sanitize_name(podcast.title)
        destination = show_dir / filename
        record = existing or episode_record(podcast, episode, destination, "seen")
        record["attempts"]["download"] = int(record["attempts"].get("download", 0)) + 1
        record["updated_at"] = now_stamp()
        state["episodes"][key] = record
        save_state(args.state, state)

        if destination.exists():
            log(f"  already present: {episode.title}")
        else:
            log(f"  downloading: {episode.title}")
            try:
                downloader.download_episode(
                    episode.enclosure_url,
                    destination,
                    args.timeout,
                    args.progress_interval,
                    args.min_rate_kbps,
                    args.slow_grace_seconds,
                )
            except downloader.DownloadError as exc:
                record["status"] = "failed_download"
                record["error"] = str(exc)
                record["updated_at"] = now_stamp()
                save_state(args.state, state)
                log(f"  failed to download: {exc}")
                continue

        record["audio_path"] = str(destination)
        record["transcript_path"] = str(downloader.transcript_path_for_audio(destination))
        record["status"] = "downloaded"
        record["downloaded_at"] = now_stamp()
        record["error"] = None
        record["updated_at"] = now_stamp()
        save_state(args.state, state)
        downloaded_count += 1

        state["feeds"][podcast.feed_url] = {
            "title": podcast.title,
            "last_checked_at": now_stamp(),
        }
        save_state(args.state, state)

    return downloaded_count


def run_once(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    downloaded_count = 0 if args.skip_download else download_latest(args, state)
    log(f"Downloaded {downloaded_count} new episode(s).")
    if args.skip_transcribe:
        log("Transcription skipped.")
    else:
        transcribed_count = transcribe_ready(args, state)
        log(f"Transcribed {transcribed_count} episode(s).")
    if args.skip_send:
        log("Send skipped.")
    else:
        sent_count = send_ready(args, state)
        log(f"Sent {sent_count} episode(s).")
    # cleanup 是本次运行已完成工作的收尾，不能被下游可选步骤的失败挡住：
    # 邮件摘要一失败就提前返回，会让当天所有 retention 策略静默停摆。
    email_result = 0
    if email_summary_requested(args):
        email_result = run_email_summary(email_summary_args_from_podsum(args))
    else:
        log("Email summary skipped.")
    cleanup_if_requested(args, state)
    return email_result


def download(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    downloaded_count = download_latest(args, state)
    log(f"Downloaded {downloaded_count} new episode(s).")
    return 0


def transcribe_ready(args: argparse.Namespace, state: dict[str, Any]) -> int:
    count = 0
    for key, episode in state["episodes"].items():
        if episode.get("status") not in {"downloaded", "failed_transcribe"}:
            continue
        audio_path = Path(str(episode.get("audio_path") or "")).expanduser()
        transcript_path = Path(str(episode.get("transcript_path") or "")).expanduser()
        if not audio_path.exists():
            episode["status"] = "failed_transcribe"
            episode["error"] = f"missing audio: {audio_path}"
            episode["updated_at"] = now_stamp()
            save_state(args.state, state)
            continue
        if transcript_path.exists():
            log(f"  transcript already present: {episode.get('episode')}")
            episode["status"] = "transcribed"
            episode["transcribed_at"] = now_stamp()
            episode["error"] = None
            episode["updated_at"] = now_stamp()
            save_state(args.state, state)
            count += 1
            continue

        episode["attempts"]["transcribe"] = int(episode["attempts"].get("transcribe", 0)) + 1
        episode["updated_at"] = now_stamp()
        save_state(args.state, state)
        log(f"Transcribing: {episode.get('podcast')} - {episode.get('episode')}")
        try:
            transcript = downloader.transcribe_with_whisper(audio_path, args)
            downloader.write_markdown(
                transcript_path,
                str(episode.get("podcast") or audio_path.parent.name),
                str(episode.get("episode") or audio_path.stem),
                audio_path,
                transcript,
                args.transcribe_model,
            )
        except Exception as exc:
            episode["status"] = "failed_transcribe"
            episode["error"] = str(exc)
            episode["updated_at"] = now_stamp()
            save_state(args.state, state)
            log(f"  failed to transcribe: {exc}")
            continue
        episode["status"] = "transcribed"
        episode["transcribed_at"] = now_stamp()
        episode["error"] = None
        episode["updated_at"] = now_stamp()
        save_state(args.state, state)
        count += 1
    return count


def transcribe(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    count = transcribe_ready(args, state)
    log(f"Transcribed {count} episode(s).")
    return 0


def pending_transcripts(state: dict[str, Any]) -> list[tuple[str, Path, str]]:
    pending: list[tuple[str, Path, str]] = []
    sent_by_path: dict[str, dict[str, Any]] = {}
    for episode in state["episodes"].values():
        if episode.get("status") != "sent":
            continue
        transcript_path = str(episode.get("transcript_path") or "")
        digest = str(episode.get("transcript_sha256") or "")
        if transcript_path and digest:
            sent_by_path[transcript_path] = {
                "sha256": digest,
                "sent_at": str(episode.get("sent_at") or ""),
                "bundle_path": str(episode.get("bundle_path") or ""),
                "epub_path": str(episode.get("epub_path") or ""),
            }

    for key, episode in state["episodes"].items():
        if episode.get("status") not in {"transcribed", "failed_send"}:
            continue
        path = Path(str(episode.get("transcript_path") or "")).expanduser()
        if not path.exists():
            episode["status"] = "failed_send"
            episode["error"] = f"missing transcript: {path}"
            episode["updated_at"] = now_stamp()
            continue
        digest = sender.sha256_file(path)
        sent_record = sent_by_path.get(str(path))
        if sent_record and sent_record.get("sha256") == digest:
            episode["status"] = "sent"
            episode["transcript_sha256"] = digest
            episode["sent_at"] = sent_record.get("sent_at", "")
            episode["bundle_path"] = sent_record.get("bundle_path", "")
            episode["epub_path"] = sent_record.get("epub_path", "")
            episode["error"] = None
            episode["updated_at"] = now_stamp()
            continue
        pending.append((key, path, digest))
    return pending


def send_ready(args: argparse.Namespace, state: dict[str, Any]) -> int:
    pending = pending_transcripts(state)
    if not pending:
        save_state(args.state, state)
        return 0

    compatible = argparse.Namespace(
        transcripts_root=args.output,
        state=args.state,
        memory_file=args.memory_file,
        interpretation_prompt=args.interpretation_prompt,
        interpretation_rules=args.interpretation_rules,
        project_dir=args.project_dir,
        target=args.target,
        hermes=args.hermes,
        hermes_timeout=args.hermes_timeout,
        force=False,
        cleanup=False,
        dry_run=False,
        dry_run_interpret=False,
        audio_retention_days=sender.DEFAULT_AUDIO_RETENTION_DAYS,
        transcript_retention_days=sender.DEFAULT_TRANSCRIPT_RETENTION_DAYS,
        untranscribed_audio_retention_days=sender.DEFAULT_UNTRANSCRIBED_AUDIO_RETENTION_DAYS,
        bundle_retention_days=sender.DEFAULT_BUNDLE_RETENTION_DAYS,
    )

    for key, _path, _digest in pending:
        episode = state["episodes"][key]
        episode.setdefault("attempts", {})
        episode["attempts"]["interpret"] = int(episode["attempts"].get("interpret", 0)) + 1
        episode["attempts"]["send"] = int(episode["attempts"].get("send", 0)) + 1
        episode["updated_at"] = now_stamp()
    save_state(args.state, state)

    try:
        bundle_path, _infos = sender.build_bundle(compatible, [(path, digest) for _key, path, digest in pending])
        epub_path = sender.write_epub(bundle_path, f"Podsum {time.strftime('%Y-%m-%d')}")
        sender.send_file(compatible, epub_path, len(pending), bundle_path)
    except Exception as exc:
        for key, _path, _digest in pending:
            episode = state["episodes"][key]
            episode["status"] = "failed_send"
            episode["error"] = str(exc)
            episode["updated_at"] = now_stamp()
        save_state(args.state, state)
        log(f"  failed to send: {exc}")
        return 0

    sent_at = now_stamp()
    for key, path, digest in pending:
        episode = state["episodes"][key]
        episode["status"] = "sent"
        episode["transcript_sha256"] = digest
        episode["bundle_path"] = str(bundle_path)
        episode["epub_path"] = str(epub_path)
        episode["sent_at"] = sent_at
        episode["error"] = None
        episode["updated_at"] = sent_at
    save_state(args.state, state)
    return len(pending)


def cleanup_if_requested(args: argparse.Namespace, state: dict[str, Any]) -> None:
    if not getattr(args, "cleanup", False):
        return
    sent_state = {"sent": {}}
    for episode in state["episodes"].values():
        if episode.get("status") != "sent":
            continue
        transcript_path = str(episode.get("transcript_path") or "")
        digest = str(episode.get("transcript_sha256") or "")
        if not transcript_path or not digest:
            continue
        sent_state["sent"][transcript_path] = {
            "sha256": digest,
            "sent_at": str(episode.get("sent_at") or ""),
            "bundle_path": str(episode.get("bundle_path") or ""),
            "epub_path": str(episode.get("epub_path") or ""),
        }
    with tempfile.NamedTemporaryFile(prefix="podsum-cleanup-", suffix=".json") as tmp:
        cleanup_state_path = Path(tmp.name)
        save_state(cleanup_state_path, sent_state)
        compatible = argparse.Namespace(
            transcripts_root=args.output,
            state=cleanup_state_path,
            dry_run=False,
            audio_retention_days=args.audio_retention_days,
            transcript_retention_days=args.transcript_retention_days,
            untranscribed_audio_retention_days=args.untranscribed_audio_retention_days,
            bundle_retention_days=args.bundle_retention_days,
        )
        sender.cleanup_files(compatible, sent_state)
    save_state(args.state, state)


def send(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    count = send_ready(args, state)
    log(f"Sent {count} episode(s).")
    cleanup_if_requested(args, state)
    return 0


def email_summary_requested(args: argparse.Namespace) -> bool:
    """邮件摘要开关：CLI 给了就无条件生效，否则看 .env / 环境变量。

    开关移出 launchd 配置，改投递方式或开关邮件摘要不必再动定时任务。
    读取真实邮箱的确认开关刻意没有一起移出——它的价值就在于每次都要显式写一遍。
    """
    if getattr(args, "email_summary", False):
        return True
    env_file = podsum_runtime.load_env_file(args.email_env_file)
    return podsum_runtime.parse_bool(
        podsum_runtime.config_value(env_file, podsum_runtime.PODSUM_EMAIL_SUMMARY_ENV), False
    )


def email_summary_args_from_podsum(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        output=args.output,
        env_file=args.email_env_file,
        scan_file=args.email_scan_file,
        eml_dir=args.email_eml_dir,
        imap_host=args.email_imap_host,
        imap_port=args.email_imap_port,
        imap_user=args.email_imap_user,
        imap_pass=args.email_imap_pass,
        mailbox=args.email_mailbox,
        recent_days=args.email_recent_days,
        limit=args.email_limit,
        email_summary_prompt=args.email_summary_prompt,
        email_evidence_preprocess_prompt=args.email_evidence_preprocess_prompt,
        email_link_policy=args.email_link_policy,
        email_topic_file=args.email_topic_file,
        summary_engine=args.email_summary_engine,
        enrich_links=args.email_enrich_links,
        project_dir=args.project_dir,
        target=args.target,
        hermes=args.hermes,
        hermes_timeout=args.hermes_timeout,
        no_llm_evidence_preprocess=args.email_no_llm_evidence_preprocess,
        dry_run=args.email_dry_run,
        no_send=args.email_no_send,
        allow_imap_read=args.email_allow_imap_read,
        delivery=args.email_delivery,
        smtp_host=args.email_smtp_host,
        smtp_port=args.email_smtp_port,
        smtp_user=args.email_smtp_user,
        smtp_pass=args.email_smtp_pass,
        smtp_from=args.email_smtp_from,
        smtp_to=args.email_smtp_to,
        smtp_starttls=args.email_smtp_starttls,
        smtp_no_ssl=args.email_smtp_no_ssl,
        smtp_no_tls_verify=args.email_smtp_no_tls_verify,
        smtp_timeout=args.email_smtp_timeout,
    )


def run_email_summary(args: argparse.Namespace) -> int:
    email_summary.normalize_args(args)
    try:
        scan_path, report_path, epub_path = email_summary.run(args)
    except Exception as exc:
        log(f"Email summary failed: {email_summary.error_text(exc)}")
        return 1
    if report_path is None:
        return 0
    log(f"Wrote email scan: {scan_path}")
    log(f"Wrote email summary: {report_path}")
    if epub_path:
        log(f"Wrote email summary EPUB: {epub_path}")
    return 0


def email_summary_command(args: argparse.Namespace) -> int:
    return run_email_summary(args)


def email_workbench_command(args: argparse.Namespace) -> int:
    return email_workbench.run(args)


def load_json_if_exists(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    return value if isinstance(value, dict) else default


def migrate_state(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    old_download = load_json_if_exists(args.old_download_state, {"downloaded": {}})
    old_sent = load_json_if_exists(args.old_sent_state, {"sent": {}})
    sent = old_sent.get("sent", {})
    migrated = 0

    for key, item in old_download.get("downloaded", {}).items():
        audio_path = Path(str(item.get("path") or "")).expanduser()
        if not audio_path:
            continue
        transcript_path = Path(str(item.get("transcript_path") or downloader.transcript_path_for_audio(audio_path)))
        sent_record = sent.get(str(transcript_path))
        if not isinstance(sent_record, dict):
            continue
        expected_digest = sent_record.get("sha256")
        if not transcript_path.exists() or expected_digest != sha256_file(transcript_path):
            continue
        record = {
            "podcast": str(item.get("podcast") or audio_path.parent.name),
            "author": "",
            "feed_url": str(item.get("feed_url") or ""),
            "episode": str(item.get("episode") or audio_path.stem),
            "guid": str(item.get("guid") or key),
            "published": str(item.get("published") or ""),
            "enclosure_url": str(item.get("url") or item.get("enclosure_url") or ""),
            "audio_path": str(audio_path),
            "transcript_path": str(transcript_path),
            "status": "sent",
            "attempts": {
                "download": 1,
                "transcribe": 0,
                "interpret": 0,
                "send": 0,
            },
            "error": None,
            "downloaded_at": str(item.get("downloaded_at") or ""),
            "transcript_sha256": str(expected_digest),
            "sent_at": str(sent_record.get("sent_at") or ""),
            "bundle_path": str(sent_record.get("bundle_path") or ""),
            "epub_path": str(sent_record.get("epub_path") or ""),
            "updated_at": now_stamp(),
        }

        state["episodes"][str(key)] = record
        migrated += 1

    save_state(args.state, state)
    log(f"Migrated {migrated} episode(s).")
    return 0


def status(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    counts: dict[str, int] = {}
    for episode in state["episodes"].values():
        status_value = str(episode.get("status") or "unknown")
        counts[status_value] = counts.get(status_value, 0) + 1
    log(f"Episodes: {len(state['episodes'])}")
    for key in sorted(counts):
        log(f"{key}: {counts[key]}")
    return 0


def retry_failed(args: argparse.Namespace) -> int:
    return run_once(args)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--feeds-file", type=Path, default=DEFAULT_FEEDS_FILE)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--progress-interval", type=int, default=15)
    parser.add_argument("--min-rate-kbps", type=float, default=10.0)
    parser.add_argument("--slow-grace-seconds", type=int, default=60)
    parser.add_argument("--transcribe-model", default=downloader.DEFAULT_TRANSCRIBE_MODEL)
    parser.add_argument("--transcribe-verbose", action="store_true")
    parser.add_argument("--memory-file", type=Path, default=sender.DEFAULT_MEMORY_FILE)
    parser.add_argument("--interpretation-prompt", type=Path, default=sender.DEFAULT_INTERPRETATION_PROMPT)
    parser.add_argument("--interpretation-rules", type=Path, default=sender.DEFAULT_INTERPRETATION_RULES, help="用户手写的自然语言解读规则文件，注入 prompt 的 {rules} 占位符。")
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--target", default=sender.DEFAULT_TARGET)
    parser.add_argument("--hermes", type=Path, default=sender.DEFAULT_HERMES)
    parser.add_argument("--hermes-timeout", type=int, default=180)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--audio-retention-days", type=int, default=sender.DEFAULT_AUDIO_RETENTION_DAYS)
    parser.add_argument("--transcript-retention-days", type=int, default=sender.DEFAULT_TRANSCRIPT_RETENTION_DAYS)
    parser.add_argument("--untranscribed-audio-retention-days", type=int, default=sender.DEFAULT_UNTRANSCRIBED_AUDIO_RETENTION_DAYS)
    parser.add_argument("--bundle-retention-days", type=int, default=sender.DEFAULT_BUNDLE_RETENTION_DAYS)


def add_run_email_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email-summary", action="store_true", help="Scan recent email and send a Podsum email summary after podcast delivery.")
    parser.add_argument("--email-env-file", type=Path, default=email_summary.DEFAULT_ENV_FILE)
    parser.add_argument("--email-scan-file", type=Path)
    parser.add_argument("--email-eml-dir", type=Path)
    parser.add_argument("--email-imap-host", default="")
    parser.add_argument("--email-imap-port", type=int, default=0)
    parser.add_argument("--email-imap-user", default="")
    parser.add_argument("--email-imap-pass", default="")
    parser.add_argument("--email-mailbox", default="")
    parser.add_argument("--email-recent-days", type=int, default=email_summary.DEFAULT_RECENT_DAYS)
    parser.add_argument("--email-limit", type=int, default=email_summary.DEFAULT_LIMIT)
    parser.add_argument("--email-summary-prompt", type=Path, default=email_summary.DEFAULT_PROMPT)
    parser.add_argument("--email-evidence-preprocess-prompt", type=Path, default=email_summary.DEFAULT_EVIDENCE_PREPROCESS_PROMPT)
    parser.add_argument("--email-link-policy", type=Path, default=email_summary.DEFAULT_LINK_POLICY)
    parser.add_argument("--email-topic-file", type=Path, default=email_summary.DEFAULT_TOPIC_FILE)
    parser.add_argument("--email-summary-engine", choices=("podsum", "hermes"), default=email_summary.DEFAULT_SUMMARY_ENGINE)
    parser.add_argument("--email-enrich-links", action="store_true")
    parser.add_argument("--email-no-llm-evidence-preprocess", action="store_true")
    parser.add_argument("--email-dry-run", action="store_true")
    parser.add_argument("--email-no-send", action="store_true")
    parser.add_argument(
        "--email-allow-imap-read",
        action="store_true",
        help="Explicitly allow the email summary step to read the configured Gmail/IMAP mailbox.",
    )
    parser.add_argument("--email-delivery", choices=("hermes", "email"), default="")
    parser.add_argument("--email-smtp-host", default="")
    parser.add_argument("--email-smtp-port", type=int, default=0)
    parser.add_argument("--email-smtp-user", default="")
    parser.add_argument("--email-smtp-pass", default="")
    parser.add_argument("--email-smtp-from", default="")
    parser.add_argument("--email-smtp-to", default="")
    parser.add_argument("--email-smtp-starttls", action="store_true")
    parser.add_argument("--email-smtp-no-ssl", action="store_true")
    parser.add_argument("--email-smtp-no-tls-verify", action="store_true")
    parser.add_argument("--email-smtp-timeout", type=int, default=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Podsum as one stateful pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-once")
    add_common_args(run_parser)
    add_run_email_args(run_parser)
    run_parser.add_argument("--skip-download", action="store_true")
    run_parser.add_argument("--skip-transcribe", action="store_true")
    run_parser.add_argument("--skip-send", action="store_true")
    run_parser.set_defaults(func=run_once)

    download_parser = subparsers.add_parser("download")
    add_common_args(download_parser)
    download_parser.set_defaults(func=download)

    transcribe_parser = subparsers.add_parser("transcribe")
    add_common_args(transcribe_parser)
    transcribe_parser.set_defaults(func=transcribe)

    send_parser = subparsers.add_parser("send")
    add_common_args(send_parser)
    send_parser.set_defaults(func=send)

    email_parser = subparsers.add_parser("email-summary")
    email_summary.add_args(email_parser)
    email_parser.set_defaults(func=email_summary_command)

    workbench_parser = subparsers.add_parser("email-workbench")
    email_workbench.add_args(workbench_parser)
    workbench_parser.set_defaults(func=email_workbench_command)

    retry_parser = subparsers.add_parser("retry-failed")
    add_common_args(retry_parser)
    add_run_email_args(retry_parser)
    retry_parser.add_argument("--skip-download", action="store_true")
    retry_parser.add_argument("--skip-transcribe", action="store_true")
    retry_parser.add_argument("--skip-send", action="store_true")
    retry_parser.set_defaults(func=retry_failed)

    migrate_parser = subparsers.add_parser("migrate-state")
    add_common_args(migrate_parser)
    migrate_parser.add_argument(
        "--old-download-state",
        type=Path,
        default=Path.home() / "Library/Application Support/PodcastDownloader/state.json",
    )
    migrate_parser.add_argument(
        "--old-sent-state",
        type=Path,
        default=podsum_runtime.podsum_home() / "feishu_sent.json",
    )
    migrate_parser.set_defaults(func=migrate_state)

    status_parser = subparsers.add_parser("status")
    add_common_args(status_parser)
    status_parser.set_defaults(func=status)

    return parser


def normalize_args(args: argparse.Namespace) -> None:
    """就地把本次子命令带到的路径参数展开 `~`。缺席或为 None 的参数原样跳过。"""
    for name in PATH_ARGS:
        value = getattr(args, name, None)
        if value is not None:
            setattr(args, name, value.expanduser())


def main() -> int:
    downloader.ensure_tool_path()
    parser = build_parser()
    args = parser.parse_args()
    normalize_args(args)
    with contextlib.suppress(BrokenPipeError):
        return int(args.func(args))
    return 1


if __name__ == "__main__":
    sys.exit(main())
