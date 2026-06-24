#!/usr/bin/env python3
"""
Download new episodes from Apple Podcasts subscriptions.

Default behavior:
- Reads followed shows from Apple Podcasts' local MTLibrary.sqlite.
- Downloads only episodes not seen before.
- Saves state in ~/Library/Application Support/PodcastDownloader/state.json.
- Limits each feed to a few new episodes per run to avoid accidental bulk pulls.
"""

from __future__ import annotations

import argparse
import contextlib
import email.utils
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DB = (
    Path.home()
    / "Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite"
)
DEFAULT_OUTPUT_DIR = Path.home() / "Podcasts/AutoDownloads"
DEFAULT_STATE_DIR = Path.home() / "Library/Application Support/PodcastDownloader"
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "state.json"
DEFAULT_FEEDS_FILE = Path(__file__).with_name("feeds.json")
USER_AGENT = "PodcastDownloader/1.0 (+local script)"
DEFAULT_TRANSCRIBE_MODEL = "mlx-community/whisper-tiny-mlx"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp4", ".m4v"}
EXTRA_TOOL_PATHS = ("/opt/homebrew/bin", "/usr/local/bin")


@dataclass(frozen=True)
class Podcast:
    title: str
    author: str
    feed_url: str


@dataclass(frozen=True)
class Episode:
    title: str
    guid: str
    enclosure_url: str
    published: str | None


class DownloadError(RuntimeError):
    def __init__(self, phase: str, message: str):
        super().__init__(f"{phase}: {message}")
        self.phase = phase


class TranscriptionError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def ensure_tool_path() -> None:
    current = os.environ.get("PATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    for path in reversed(EXTRA_TOOL_PATHS):
        if path not in parts and Path(path).exists():
            parts.insert(0, path)
    os.environ["PATH"] = os.pathsep.join(parts)


def sanitize_name(value: str, fallback: str = "untitled") -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:160] or fallback


def read_subscriptions(db_path: Path) -> list[Podcast]:
    if not db_path.exists():
        raise FileNotFoundError(f"Apple Podcasts database not found: {db_path}")

    query = """
        SELECT COALESCE(ZTITLE, ''), COALESCE(ZAUTHOR, ''), COALESCE(ZFEEDURL, '')
        FROM ZMTPODCAST
        WHERE ZSUBSCRIBED = 1 AND ZFEEDURL IS NOT NULL AND ZFEEDURL != ''
        ORDER BY ZTITLE COLLATE NOCASE
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(query).fetchall()

    return [Podcast(title=row[0], author=row[1], feed_url=row[2]) for row in rows]


def read_feeds_file(path: Path) -> list[Podcast]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    feeds = raw.get("feeds", raw) if isinstance(raw, dict) else raw
    podcasts: list[Podcast] = []
    for feed in feeds:
        title = str(feed.get("title", "")).strip()
        feed_url = str(feed.get("feed_url") or feed.get("url") or "").strip()
        author = str(feed.get("author", "")).strip()
        if title and feed_url:
            podcasts.append(Podcast(title=title, author=author, feed_url=feed_url))
    return podcasts


def get_podcasts(args: argparse.Namespace) -> list[Podcast]:
    podcasts: list[Podcast] = []
    if not args.feeds_only:
        podcasts = read_subscriptions(args.db)
        if podcasts:
            return podcasts

    feeds = read_feeds_file(args.feeds_file)
    if feeds:
        if not args.feeds_only:
            log(f"Apple Podcasts subscriptions empty; using feeds file: {args.feeds_file}")
        return feeds

    return podcasts


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"downloaded": {}, "feeds": {}}
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("downloaded", {})
    state.setdefault("feeds", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def write_markdown(path: Path, podcast: str, episode: str, audio_path: Path, transcript: str, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"podcast: {json.dumps(podcast, ensure_ascii=False)}\n")
        f.write(f"episode: {json.dumps(episode, ensure_ascii=False)}\n")
        f.write(f"audio: {json.dumps(str(audio_path), ensure_ascii=False)}\n")
        f.write(f"model: {json.dumps(model, ensure_ascii=False)}\n")
        f.write(f"transcribed_at: {json.dumps(time.strftime('%Y-%m-%dT%H:%M:%S%z'), ensure_ascii=False)}\n")
        f.write("---\n\n")
        f.write(f"# {episode}\n\n")
        f.write(transcript.strip())
        f.write("\n")
    tmp.replace(path)


def fetch_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def child_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def enclosure_url(item: ET.Element) -> str:
    for child in item:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag == "enclosure":
            url = child.attrib.get("url", "").strip()
            mime = child.attrib.get("type", "").lower()
            if url and (mime.startswith("audio/") or mime.startswith("video/") or not mime):
                return url
    return ""


def parse_feed(feed_xml: bytes) -> list[Episode]:
    root = ET.fromstring(feed_xml)
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")
    episodes: list[Episode] = []

    for item in items:
        url = enclosure_url(item)
        if not url:
            continue
        title = child_text(item, ("title",)) or "untitled"
        guid = child_text(item, ("guid",)) or url
        published = child_text(item, ("pubdate", "published", "updated")) or None
        episodes.append(Episode(title=title, guid=guid, enclosure_url=url, published=published))

    return sorted(episodes, key=episode_sort_time, reverse=True)


def episode_sort_time(episode: Episode) -> float:
    if not episode.published:
        return 0.0
    try:
        parsed = email.utils.parsedate_to_datetime(episode.published)
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0
    return parsed.timestamp()


def episode_key(podcast: Podcast, episode: Episode) -> str:
    raw = f"{podcast.feed_url}\n{episode.guid}\n{episode.enclosure_url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def extension_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return ".mp3"


def date_prefix(value: str | None) -> str:
    if not value:
        return time.strftime("%Y-%m-%d")
    parsed = email.utils.parsedate_to_datetime(value)
    return parsed.strftime("%Y-%m-%d")


def unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for i in range(2, 1000):
        next_candidate = directory / f"{stem} ({i}){suffix}"
        if not next_candidate.exists():
            return next_candidate
    raise RuntimeError(f"Could not find unique filename for {candidate}")


def format_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "unknown size"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def format_rate(bytes_downloaded: int, elapsed: float) -> str:
    if elapsed <= 0:
        return "unknown rate"
    return f"{(bytes_downloaded / 1024 / elapsed):.1f} KB/s"


def content_length(response: Any) -> int | None:
    raw = response.headers.get("Content-Length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def download_episode(
    url: str,
    destination: Path,
    timeout: int,
    progress_interval: int,
    min_rate_kbps: float,
    slow_grace_seconds: int,
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None

    try:
        log("    connecting...")
        response = urllib.request.urlopen(request, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DownloadError("connection", str(exc)) from exc

    try:
        with response:
            total_size = content_length(response)
            status = getattr(response, "status", "unknown")
            log(f"    connected: HTTP {status}, {format_size(total_size)}")

            started = time.monotonic()
            last_report = started
            downloaded = 0
            chunk_size = 1024 * 256

            with tempfile.NamedTemporaryFile(delete=False, dir=destination.parent) as tmp:
                tmp_path = Path(tmp.name)
                while True:
                    try:
                        chunk = response.read(chunk_size)
                    except TimeoutError as exc:
                        phase = "transfer-stalled" if downloaded else "transfer-no-data"
                        raise DownloadError(phase, f"no data for {timeout}s after HTTP response") from exc
                    except OSError as exc:
                        phase = "transfer" if downloaded else "transfer-no-data"
                        raise DownloadError(phase, str(exc)) from exc

                    if not chunk:
                        break

                    tmp.write(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    elapsed = now - started
                    rate_kbps = downloaded / 1024 / elapsed if elapsed > 0 else 0

                    if now - last_report >= progress_interval:
                        if total_size:
                            percent = downloaded / total_size * 100
                            log(f"    progress: {format_size(downloaded)} / {format_size(total_size)} ({percent:.1f}%), {format_rate(downloaded, elapsed)}")
                        else:
                            log(f"    progress: {format_size(downloaded)}, {format_rate(downloaded, elapsed)}")
                        last_report = now

                    if elapsed >= slow_grace_seconds and rate_kbps < min_rate_kbps:
                        raise DownloadError(
                            "transfer-slow",
                            f"average {rate_kbps:.1f} KB/s below {min_rate_kbps:.1f} KB/s for {int(elapsed)}s",
                        )

        tmp_path.replace(destination)
        elapsed = max(time.monotonic() - started, 0.001)
        log(f"    complete: {format_size(downloaded)}, {format_rate(downloaded, elapsed)}")
    except Exception:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
        raise


def transcript_path_for_audio(audio_path: Path) -> Path:
    return audio_path.parent / "Transcripts" / f"{audio_path.stem}.md"


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


def format_timestamp(seconds: float | int | None) -> str:
    if seconds is None:
        return "00:00:00"
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def transcribe_with_whisper(audio_path: Path, args: argparse.Namespace) -> str:
    try:
        import mlx_whisper
    except ImportError as exc:
        raise TranscriptionError("mlx-whisper is not installed for /usr/bin/python3") from exc

    log(f"    local MLX model: {args.transcribe_model}")
    log(f"    audio: {audio_path} ({format_size(audio_path.stat().st_size)})")
    try:
        result = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=args.transcribe_model,
            verbose=args.transcribe_verbose,
            word_timestamps=False,
        )
    except Exception as exc:
        raise TranscriptionError(f"mlx-whisper failed: {exc}") from exc

    segments = result.get("segments") if isinstance(result, dict) else None
    if segments:
        lines: list[str] = []
        for segment in segments:
            start = format_timestamp(segment.get("start"))
            text = str(segment.get("text", "")).strip()
            if text:
                lines.append(f"[{start}] {text}")
        return "\n\n".join(lines)

    if isinstance(result, dict):
        return str(result.get("text", "")).strip()
    return str(result).strip()


def transcribe_downloaded_items(args: argparse.Namespace, state: dict[str, Any]) -> int:
    downloaded: dict[str, Any] = state["downloaded"]
    total = 0
    wanted = {item.casefold() for item in args.only} if args.only else None

    for key, item in downloaded.items():
        audio_path = Path(item.get("path", "")).expanduser()
        if not audio_path.exists():
            log(f"Transcription skipped, missing audio: {audio_path}")
            continue

        md_path = Path(item.get("transcript_path") or transcript_path_for_audio(audio_path))
        if md_path.exists() and not args.force_transcribe:
            continue

        podcast = str(item.get("podcast") or audio_path.parent.name)
        episode = str(item.get("episode") or audio_path.stem)
        if wanted and podcast.casefold() not in wanted:
            continue

        log(f"Transcribing: {podcast} - {episode}")
        try:
            transcript = transcribe_with_whisper(audio_path, args)
        except TranscriptionError as exc:
            log(f"  failed to transcribe: {exc}")
            item["transcription_error"] = str(exc)
            item["transcription_last_attempt_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            save_state(args.state, state)
            continue

        write_markdown(md_path, podcast, episode, audio_path, transcript, args.transcribe_model)
        item["transcript_path"] = str(md_path)
        item["transcribed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        item.pop("transcription_error", None)
        item.pop("transcription_last_attempt_at", None)
        save_state(args.state, state)
        log(f"  wrote transcript: {md_path}")
        total += 1

    return total


def transcribe_audio_files(args: argparse.Namespace) -> int:
    total = 0
    wanted = {item.casefold() for item in args.only} if args.only else None

    for audio_path in find_audio_files(args.output):
        podcast = audio_path.parent.name
        episode = audio_path.stem
        if wanted and podcast.casefold() not in wanted:
            continue

        md_path = transcript_path_for_audio(audio_path)
        if md_path.exists() and not args.force_transcribe:
            continue

        log(f"Transcribing: {podcast} - {episode}")
        try:
            transcript = transcribe_with_whisper(audio_path, args)
        except TranscriptionError as exc:
            log(f"  failed to transcribe: {exc}")
            continue

        write_markdown(md_path, podcast, episode, audio_path, transcript, args.transcribe_model)
        log(f"  wrote transcript: {md_path}")
        total += 1

    return total


def run(args: argparse.Namespace) -> int:
    podcasts = get_podcasts(args)
    if args.only:
        wanted = {item.casefold() for item in args.only}
        podcasts = [p for p in podcasts if p.title.casefold() in wanted]

    if args.list:
        for podcast in podcasts:
            log(f"{podcast.title} - {podcast.feed_url}")
        log(f"Total subscribed podcasts: {len(podcasts)}")
        return 0

    state = load_state(args.state)
    downloaded: dict[str, Any] = state["downloaded"]
    total_downloaded = 0
    total_would_download = 0

    if not args.skip_download:
        for podcast in podcasts:
            log(f"Checking: {podcast.title}")
            try:
                feed_xml = fetch_bytes(podcast.feed_url, args.timeout)
                episodes = parse_feed(feed_xml)
            except (ET.ParseError, urllib.error.URLError, TimeoutError, OSError) as exc:
                log(f"  failed to read feed: {exc}")
                continue

            episodes = episodes[: args.max_per_feed]
            feed_downloaded = 0
            show_dir = args.output / sanitize_name(podcast.title)

            attempts = 0
            for episode in episodes:
                key = episode_key(podcast, episode)
                if key in downloaded:
                    log(f"  already recorded: {episode.title}")
                    continue

                if attempts >= args.max_attempts_per_feed:
                    log(f"  stopping after {attempts} failed/unrecorded attempt(s)")
                    break
                attempts += 1

                try:
                    prefix = date_prefix(episode.published)
                except Exception:
                    prefix = time.strftime("%Y-%m-%d")

                ext = extension_from_url(episode.enclosure_url)
                filename = sanitize_name(f"{prefix} {episode.title}") + ext
                expected_destination = show_dir / filename

                if args.dry_run:
                    log(f"  would download: {episode.title}")
                    destination = expected_destination
                    total_would_download += 1
                elif expected_destination.exists():
                    destination = expected_destination
                    log(f"  already present: {episode.title}")
                else:
                    destination = unique_path(show_dir, filename)
                    try:
                        log(f"  downloading: {episode.title}")
                        download_episode(
                            episode.enclosure_url,
                            destination,
                            args.timeout,
                            args.progress_interval,
                            args.min_rate_kbps,
                            args.slow_grace_seconds,
                        )
                    except DownloadError as exc:
                        log(f"  failed to download: {exc}")
                        continue

                if not args.dry_run:
                    downloaded[key] = {
                        "podcast": podcast.title,
                        "episode": episode.title,
                        "url": episode.enclosure_url,
                        "path": str(destination),
                        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                feed_downloaded += 1
                if not args.dry_run:
                    total_downloaded += 1

                if not args.dry_run:
                    save_state(args.state, state)

                if feed_downloaded >= args.max_per_feed:
                    break

            state["feeds"][podcast.feed_url] = {
                "title": podcast.title,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }

    if not args.dry_run:
        save_state(args.state, state)

    if args.dry_run:
        log(f"Would download {total_would_download} new episode(s).")
    else:
        log(f"Downloaded {total_downloaded} new episode(s).")
    if args.transcribe and not args.dry_run:
        if args.skip_download:
            total_transcribed = transcribe_audio_files(args)
        else:
            total_transcribed = transcribe_downloaded_items(args, state)
        log(f"Transcribed {total_transcribed} episode(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Apple Podcasts subscriptions.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Apple Podcasts DB path. Default: {DEFAULT_DB}")
    parser.add_argument("--feeds-file", type=Path, default=DEFAULT_FEEDS_FILE, help=f"Fallback JSON feed list. Default: {DEFAULT_FEEDS_FILE}")
    parser.add_argument("--feeds-only", action="store_true", help="Use --feeds-file instead of Apple Podcasts subscriptions.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help=f"Download directory. Default: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE, help=f"State JSON path. Default: {DEFAULT_STATE_FILE}")
    parser.add_argument("--max-per-feed", type=int, default=3, help="Maximum new episodes per podcast per run.")
    parser.add_argument("--max-attempts-per-feed", type=int, default=8, help="Maximum unrecorded episodes to try per podcast per run.")
    parser.add_argument("--timeout", type=int, default=60, help="Network timeout in seconds.")
    parser.add_argument("--progress-interval", type=int, default=15, help="Seconds between transfer progress logs.")
    parser.add_argument("--min-rate-kbps", type=float, default=10.0, help="Abort transfer if average speed stays below this rate.")
    parser.add_argument("--slow-grace-seconds", type=int, default=60, help="Seconds before enforcing --min-rate-kbps.")
    parser.add_argument("--skip-download", action="store_true", help="Do not fetch feeds or download audio; only run later stages.")
    parser.add_argument("--transcribe", action="store_true", help="Transcribe downloaded audio to Markdown with Whisper.")
    parser.add_argument("--force-transcribe", action="store_true", help="Recreate Markdown transcripts even if they already exist.")
    parser.add_argument("--transcribe-model", default=DEFAULT_TRANSCRIBE_MODEL, help=f"MLX Whisper model repo or local path. Default: {DEFAULT_TRANSCRIBE_MODEL}.")
    parser.add_argument("--transcribe-verbose", action="store_true", help="Show mlx-whisper decoding progress.")
    parser.add_argument("--only", action="append", help="Only download a podcast with this exact title. Can be repeated.")
    parser.add_argument("--list", action="store_true", help="List subscribed podcasts and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded without writing files.")
    return parser


def main() -> int:
    ensure_tool_path()
    parser = build_parser()
    args = parser.parse_args()
    args.output = args.output.expanduser()
    args.state = args.state.expanduser()
    args.db = args.db.expanduser()
    args.feeds_file = args.feeds_file.expanduser()

    if args.max_per_feed < 1:
        parser.error("--max-per-feed must be >= 1")
    if args.max_attempts_per_feed < args.max_per_feed:
        parser.error("--max-attempts-per-feed must be >= --max-per-feed")
    if args.progress_interval < 1:
        parser.error("--progress-interval must be >= 1")
    if args.min_rate_kbps < 0:
        parser.error("--min-rate-kbps must be >= 0")
    if args.slow_grace_seconds < 1:
        parser.error("--slow-grace-seconds must be >= 1")

    with contextlib.suppress(BrokenPipeError):
        return run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
