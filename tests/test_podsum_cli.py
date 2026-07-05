import json
import os
import subprocess
import sys
import textwrap
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
PODSUM = ROOT / "outputs" / "podsum.py"
sys.path.insert(0, str(ROOT / "outputs"))
import podsum_email_summary as email_summary  # noqa: E402
import podsum_email_workbench as email_workbench  # noqa: E402


def run_podsum(
    *args: str,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PODSUM), *args],
        cwd=str(cwd or ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def start_workbench(config: email_workbench.WorkbenchConfig):
    server = email_workbench.create_server(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, thread, f"http://{host}:{port}"


def stop_workbench(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get_json(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(base_url: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def write_feed(path: Path, old_audio: Path, new_audio: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            <?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0">
              <channel>
                <title>Fixture Show</title>
                <item>
                  <title>Old Episode</title>
                  <guid>old-guid</guid>
                  <pubDate>Mon, 01 Jun 2026 08:00:00 +0000</pubDate>
                  <enclosure url="{old_audio.as_uri()}" type="audio/mpeg" length="9" />
                </item>
                <item>
                  <title>New Episode</title>
                  <guid>new-guid</guid>
                  <pubDate>Fri, 12 Jun 2026 08:00:00 +0000</pubDate>
                  <enclosure url="{new_audio.as_uri()}" type="audio/mpeg" length="9" />
                </item>
              </channel>
            </rss>
            """
        ).strip(),
        encoding="utf-8",
    )


class PodsumCliTest(unittest.TestCase):
    def test_email_workbench_help_exists(self) -> None:
        result = run_podsum("email-workbench", "--help")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("--root", result.stdout)
        self.assertIn("--policy-file", result.stdout)

    def test_email_summary_prompt_matches_deep_interpretation_style(self) -> None:
        prompt = (ROOT / "outputs" / "email_summary_prompt.md").read_text(encoding="utf-8")

        self.assertIn("邮件情报深度解读器", prompt)
        self.assertIn("基本不需要再打开邮箱逐封确认", prompt)
        self.assertIn("## key takeaway", prompt)
        self.assertIn("## 如果只记三件事", prompt)
        self.assertIn("不要把输出写成简单分类清单", prompt)
        self.assertIn("EmailEvidencePack", prompt)
        self.assertIn("snippet` 只是邮件摘要或截断片段", prompt)
        self.assertIn("evidence.status=fetched", prompt)
        self.assertIn("UID={{uid}} | From={{from}} | Subject={{subject}} | Date={{date}}", prompt)
        self.assertIn("possibly_truncated=true", prompt)

    def test_email_link_policy_parses_from_markdown(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")

        self.assertEqual(policy["object_type"], "email_policy")
        self.assertEqual(policy["limits"]["max_links_per_email"], 2)
        self.assertTrue(any(item["name"] == "newsletter_article" for item in policy["email_types"]))

    def test_email_workbench_reports_missing_artifacts_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
            )
            server, thread, base_url = start_workbench(config)
            try:
                context = get_json(base_url, "/api/context")
                evidence = get_json(base_url, "/api/evidence-pack")
                commands = get_json(base_url, "/api/commands")
            finally:
                stop_workbench(server, thread)

            self.assertEqual(context["server"]["mode"], "manual-local-workbench")
            self.assertFalse(context["server"]["safe_defaults"]["reads_imap"])
            self.assertIn("scan", context["missing"])
            self.assertIn("summary", context["missing"])
            self.assertTrue(evidence["missing"])
            self.assertIn("generate_scan_manual_imap", commands["commands"])
            self.assertFalse((tmp_path / "downloads" / "EmailReports").exists())

    def test_email_workbench_review_sidecar_preserves_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reports = tmp_path / "downloads" / "EmailReports"
            reports.mkdir(parents=True)
            scan_path = reports / "email-scan-2026-07-05.json"
            summary_path = reports / "email-summary-2026-07-05.md"
            scan_text = json.dumps(
                {
                    "object_type": "email_evidence_pack",
                    "status": "ready_for_summary",
                    "date": "2026-07-05",
                    "account": "fixture@example.invalid",
                    "window": "1d",
                    "scan_limit": 10,
                    "raw_count": 1,
                    "possibly_truncated": False,
                    "items": [
                        {
                            "uid": "77",
                            "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                            "from": "Fixture Sender <sender@example.invalid>",
                            "subject": "Fixture actionable mail",
                            "snippet": "A fixture email that should become a summary.",
                            "has_attachments": False,
                            "email_type": "personal",
                            "links": [],
                            "evidence": [],
                            "risks": ["snippet_only"],
                            "flags": [],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            summary_text = (
                "# Podsum Email Summary 2026-07-05\n\n"
                "## key takeaway\n\n"
                "仅基于邮件摘要：fixture item should be reviewed.\n\n"
                "## 来源索引\n\n"
                "- UID=77 | From=Fixture Sender <sender@example.invalid> | "
                "Subject=Fixture actionable mail | Date=Sun, 05 Jul 2026 08:00:00 +0800 | "
                "`email://2026-07-05/77`\n"
            )
            scan_path.write_text(scan_text, encoding="utf-8")
            summary_path.write_text(summary_text, encoding="utf-8")
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
            )
            server, thread, base_url = start_workbench(config)
            try:
                evidence = get_json(base_url, "/api/evidence-pack")
                brief = get_json(base_url, "/api/intel-brief")
                review = post_json(
                    base_url,
                    "/api/review",
                    {
                        "email_marks": {"77": {"important": True, "needs_link_review": True}},
                        "brief_status": "approved",
                        "brief_override_markdown": summary_text,
                    },
                )
                checklist = get_json(base_url, "/api/checklist")
                evidence_after = get_json(base_url, "/api/evidence-pack")
            finally:
                stop_workbench(server, thread)

            self.assertFalse(evidence["missing"])
            self.assertEqual(evidence["scan"]["items"][0]["uid"], "77")
            self.assertEqual(brief["source_index"][0]["source_uid"], "77")
            self.assertEqual(review["review"]["brief_status"], "approved")
            self.assertTrue(checklist["delivery_ready"])
            self.assertTrue(evidence_after["scan"]["items"][0]["_review"]["important"])
            self.assertEqual(scan_path.read_text(encoding="utf-8"), scan_text)
            self.assertEqual(summary_path.read_text(encoding="utf-8"), summary_text)
            self.assertTrue((reports / "email-review-2026-07-05.json").exists())

    def test_email_workbench_rejects_invalid_policy_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            policy_file = tmp_path / "email_link_policy.md"
            original = (ROOT / "outputs" / "email_link_policy.md").read_text(encoding="utf-8")
            policy_file.write_text(original, encoding="utf-8")
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=policy_file,
            )
            server, thread, base_url = start_workbench(config)
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    post_json(base_url, "/api/policy", {"markdown": "```json\n{\"object_type\":\n```"})
                caught.exception.close()
            finally:
                stop_workbench(server, thread)

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(policy_file.read_text(encoding="utf-8"), original)

    def test_email_workbench_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
            )
            server, thread, base_url = start_workbench(config)
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    get_json(base_url, "/%2e%2e/secret")
                caught.exception.close()
            finally:
                stop_workbench(server, thread)

            self.assertEqual(caught.exception.code, 403)

    def test_email_link_enrichment_uses_fake_fetcher(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        item = {
            "uid": "fixture",
            "from": "Newsletter <sender@example.invalid>",
            "subject": "Fixture Newsletter",
            "snippet": "Read more at https://example.invalid/article",
            "email_type": "newsletter_article",
            "links": [
                {
                    "url": "https://example.invalid/article",
                    "normalized_url": "https://example.invalid/article",
                    "anchor_text": "Read more",
                    "policy_decision": "pending",
                }
            ],
            "evidence": [],
            "risks": ["snippet_only"],
        }

        def fake_fetcher(url: str, timeout: int, excerpt_chars: int) -> dict[str, str]:
            return {
                "url": url,
                "final_url": url,
                "title": "Fixture article title",
                "excerpt": "Fixture article excerpt with enough public evidence.",
                "status": "fetched",
                "reason": "",
                "content_type": "text/html",
            }

        used = email_summary.enrich_item_links(item, policy, remaining_budget=10, fetcher=fake_fetcher)

        self.assertEqual(used, 1)
        self.assertEqual(item["links"][0]["policy_decision"], "fetch")
        self.assertEqual(item["evidence"][0]["status"], "fetched")
        self.assertNotIn("snippet_only", item["risks"])

    def test_email_link_enrichment_skips_tracking_urls(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        item = {
            "uid": "fixture",
            "from": "Newsletter <sender@example.invalid>",
            "subject": "Fixture Newsletter",
            "snippet": "Track at https://example.invalid/track/item",
            "email_type": "newsletter_article",
            "links": [
                {
                    "url": "https://example.invalid/track/item",
                    "normalized_url": "https://example.invalid/track/item",
                    "anchor_text": "",
                    "policy_decision": "pending",
                }
            ],
            "evidence": [],
            "risks": ["snippet_only"],
        }

        used = email_summary.enrich_item_links(item, policy, remaining_budget=10)

        self.assertEqual(used, 0)
        self.assertEqual(item["links"][0]["policy_decision"], "skip")
        self.assertEqual(item["evidence"][0]["status"], "skipped")
        self.assertIn("track", item["evidence"][0]["reason"])

    def test_email_review_checklist_flags_missing_traceability(self) -> None:
        scan = {
            "date": "2026-07-05",
            "possibly_truncated": True,
            "items": [
                {
                    "uid": "1",
                    "evidence": [],
                    "risks": ["snippet_only"],
                }
            ],
        }

        checklist = email_summary.review_checklist(scan, "# Summary\n\nNo trace here.")

        self.assertFalse(checklist["has_key_takeaway"])
        self.assertFalse(checklist["has_source_index"])
        self.assertFalse(checklist["has_uid_trace"])
        self.assertFalse(checklist["has_truncated_warning"])
        self.assertFalse(checklist["marks_snippet_only_claims"])
        self.assertFalse(checklist["ready_to_send"])

    def test_run_once_downloads_only_latest_episode_per_feed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_audio = tmp_path / "old.mp3"
            new_audio = tmp_path / "new.mp3"
            old_audio.write_bytes(b"old audio")
            new_audio.write_bytes(b"new audio")
            feed = tmp_path / "feed.xml"
            write_feed(feed, old_audio, new_audio)

            feeds_file = tmp_path / "feeds.json"
            feeds_file.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "title": "Fixture Show",
                                "author": "Fixture",
                                "feed_url": feed.as_uri(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            state = tmp_path / "state.json"
            output = tmp_path / "downloads"
            result = run_podsum(
                "run-once",
                "--feeds-file",
                str(feeds_file),
                "--state",
                str(state),
                "--output",
                str(output),
                "--skip-transcribe",
                "--skip-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("New Episode", result.stdout)
            self.assertNotIn("Old Episode", result.stdout)

            data = json.loads(state.read_text(encoding="utf-8"))
            episodes = data["episodes"]
            self.assertEqual(len(episodes), 1)
            episode = next(iter(episodes.values()))
            self.assertEqual(episode["episode"], "New Episode")
            self.assertEqual(episode["status"], "downloaded")
            self.assertTrue(Path(episode["audio_path"]).exists())
            self.assertFalse(any(path.name.startswith("2026-06-01 Old Episode") for path in output.rglob("*.mp3")))

    def test_failed_download_is_retried_from_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            missing_audio = tmp_path / "missing.mp3"
            feed = tmp_path / "feed.xml"
            write_feed(feed, tmp_path / "old-missing.mp3", missing_audio)

            feeds_file = tmp_path / "feeds.json"
            feeds_file.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "title": "Fixture Show",
                                "author": "Fixture",
                                "feed_url": feed.as_uri(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            state = tmp_path / "state.json"
            output = tmp_path / "downloads"
            first = run_podsum(
                "run-once",
                "--feeds-file",
                str(feeds_file),
                "--state",
                str(state),
                "--output",
                str(output),
                "--skip-transcribe",
                "--skip-send",
            )
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            first_state = json.loads(state.read_text(encoding="utf-8"))
            first_episode = next(iter(first_state["episodes"].values()))
            self.assertEqual(first_episode["status"], "failed_download")
            self.assertEqual(first_episode["attempts"]["download"], 1)

            missing_audio.write_bytes(b"new audio")
            second = run_podsum(
                "run-once",
                "--feeds-file",
                str(feeds_file),
                "--state",
                str(state),
                "--output",
                str(output),
                "--skip-transcribe",
                "--skip-send",
            )
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            second_state = json.loads(state.read_text(encoding="utf-8"))
            second_episode = next(iter(second_state["episodes"].values()))
            self.assertEqual(second_episode["status"], "downloaded")
            self.assertEqual(second_episode["attempts"]["download"], 2)
            self.assertTrue(Path(second_episode["audio_path"]).exists())

    def test_transcribe_reuses_existing_markdown_and_advances_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "new.mp3"
            audio.write_bytes(b"new audio")
            feed = tmp_path / "feed.xml"
            write_feed(feed, tmp_path / "old.mp3", audio)
            feeds_file = tmp_path / "feeds.json"
            feeds_file.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "title": "Fixture Show",
                                "author": "Fixture",
                                "feed_url": feed.as_uri(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state = tmp_path / "state.json"
            output = tmp_path / "downloads"
            download = run_podsum(
                "download",
                "--feeds-file",
                str(feeds_file),
                "--state",
                str(state),
                "--output",
                str(output),
            )
            self.assertEqual(download.returncode, 0, download.stderr + download.stdout)
            data = json.loads(state.read_text(encoding="utf-8"))
            episode = next(iter(data["episodes"].values()))
            transcript_path = Path(episode["transcript_path"])
            transcript_path.parent.mkdir(parents=True)
            transcript_path.write_text("---\n---\n\n# New Episode\n\ntranscript", encoding="utf-8")

            transcribe = run_podsum(
                "transcribe",
                "--feeds-file",
                str(feeds_file),
                "--state",
                str(state),
                "--output",
                str(output),
            )
            self.assertEqual(transcribe.returncode, 0, transcribe.stderr + transcribe.stdout)
            updated = json.loads(state.read_text(encoding="utf-8"))
            updated_episode = next(iter(updated["episodes"].values()))
            self.assertEqual(updated_episode["status"], "transcribed")
            self.assertEqual(updated_episode["attempts"]["transcribe"], 0)

    def test_send_builds_epub_and_marks_transcript_sent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "downloads"
            transcript = root / "Fixture Show" / "Transcripts" / "2026-06-12 New Episode.md"
            transcript.parent.mkdir(parents=True)
            audio = root / "Fixture Show" / "2026-06-12 New Episode.mp3"
            audio.write_bytes(b"audio")
            transcript.write_text(
                "\n".join(
                    [
                        "---",
                        json.dumps("podcast")[1:-1] + ": " + json.dumps("Fixture Show"),
                        json.dumps("episode")[1:-1] + ": " + json.dumps("New Episode"),
                        json.dumps("audio")[1:-1] + ": " + json.dumps(str(audio)),
                        "---",
                        "",
                        "# New Episode",
                        "",
                        "Transcript body.",
                    ]
                ),
                encoding="utf-8",
            )
            state = tmp_path / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "feeds": {},
                        "episodes": {
                            "episode-key": {
                                "podcast": "Fixture Show",
                                "episode": "New Episode",
                                "audio_path": str(audio),
                                "transcript_path": str(transcript),
                                "status": "transcribed",
                                "attempts": {"download": 1, "transcribe": 0, "interpret": 0, "send": 0},
                                "error": None,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            hermes = tmp_path / "hermes"
            hermes_args = tmp_path / "hermes-send-args.txt"
            hermes.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-z\" ]; then echo 'key takeaway'; echo; echo 'summary'; exit 0; fi\n"
                f"if [ \"$1\" = \"send\" ]; then printf '%s\\n' \"$@\" > {hermes_args}; echo 'sent'; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            hermes.chmod(0o755)

            result = run_podsum(
                "send",
                "--state",
                str(state),
                "--output",
                str(root),
                "--hermes",
                str(hermes),
                "--memory-file",
                str(tmp_path / "missing-memory.md"),
                "--project-dir",
                str(tmp_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(state.read_text(encoding="utf-8"))
            episode = data["episodes"]["episode-key"]
            self.assertEqual(episode["status"], "sent")
            self.assertTrue(Path(episode["bundle_path"]).exists())
            self.assertTrue(Path(episode["epub_path"]).exists())
            send_args = hermes_args.read_text(encoding="utf-8").splitlines()
            self.assertEqual(send_args[send_args.index("--to") + 1], "discord:1518857496788467832")

    def test_failed_send_is_retried_from_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "downloads"
            transcript = root / "Fixture Show" / "Transcripts" / "2026-06-12 New Episode.md"
            transcript.parent.mkdir(parents=True)
            audio = root / "Fixture Show" / "2026-06-12 New Episode.mp3"
            audio.write_bytes(b"audio")
            transcript.write_text("---\npodcast: \"Fixture Show\"\nepisode: \"New Episode\"\n---\n\nbody", encoding="utf-8")
            state = tmp_path / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "feeds": {},
                        "episodes": {
                            "episode-key": {
                                "podcast": "Fixture Show",
                                "episode": "New Episode",
                                "audio_path": str(audio),
                                "transcript_path": str(transcript),
                                "status": "transcribed",
                                "attempts": {"download": 1, "transcribe": 0, "interpret": 0, "send": 0},
                                "error": None,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            hermes = tmp_path / "hermes"
            hermes.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-z\" ]; then echo 'summary'; exit 0; fi\n"
                "if [ \"$1\" = \"send\" ]; then echo 'send failed' >&2; exit 1; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            hermes.chmod(0o755)
            common_args = [
                "send",
                "--state",
                str(state),
                "--output",
                str(root),
                "--hermes",
                str(hermes),
                "--memory-file",
                str(tmp_path / "missing-memory.md"),
                "--project-dir",
                str(tmp_path),
            ]

            failed = run_podsum(*common_args)
            self.assertEqual(failed.returncode, 0, failed.stderr + failed.stdout)
            failed_state = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(failed_state["episodes"]["episode-key"]["status"], "failed_send")
            self.assertEqual(failed_state["episodes"]["episode-key"]["attempts"]["send"], 1)

            hermes.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-z\" ]; then echo 'summary'; exit 0; fi\n"
                "if [ \"$1\" = \"send\" ]; then echo 'sent'; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            retried = run_podsum(*common_args)
            self.assertEqual(retried.returncode, 0, retried.stderr + retried.stdout)
            retried_state = json.loads(state.read_text(encoding="utf-8"))
            retried_episode = retried_state["episodes"]["episode-key"]
            self.assertEqual(retried_episode["status"], "sent")
            self.assertEqual(retried_episode["attempts"]["send"], 2)

    def test_email_summary_uses_scan_file_without_real_imap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scan_file = tmp_path / "email-scan.json"
            scan_file.write_text(
                json.dumps(
                    {
                        "date": "2026-07-05",
                        "account": "fixture@example.com",
                        "window": "1d",
                        "scan_limit": 300,
                        "raw_count": 1,
                        "possibly_truncated": False,
                        "items": [
                            {
                                "uid": "42",
                                "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                                "from": "Newsletter <news@example.com>",
                                "subject": "AI update",
                                "snippet": "A concise update about agents.",
                                "has_attachments": False,
                                "flags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(scan_file),
                "--output",
                str(tmp_path / "downloads"),
                "--dry-run",
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = tmp_path / "downloads" / "EmailReports" / "email-summary-2026-07-05.md"
            copied_scan = tmp_path / "downloads" / "EmailReports" / "email-scan-2026-07-05.json"
            self.assertTrue(report.exists())
            self.assertTrue(copied_scan.exists())
            self.assertIn("dry-run: skipped Hermes summary", report.read_text(encoding="utf-8"))

    def test_email_summary_prompt_receives_evidence_pack_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scan_file = tmp_path / "email-scan.json"
            scan_file.write_text(
                json.dumps(
                    {
                        "date": "2026-07-05",
                        "account": "fixture@example.invalid",
                        "window": "1d",
                        "scan_limit": 10,
                        "raw_count": 1,
                        "possibly_truncated": False,
                        "items": [
                            {
                                "uid": "88",
                                "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                                "from": "Fixture Newsletter <sender@example.invalid>",
                                "subject": "Fixture Newsletter",
                                "snippet": "Read the public article.",
                                "has_attachments": False,
                                "email_type": "newsletter_article",
                                "links": [
                                    {
                                        "url": "https://example.invalid/article",
                                        "normalized_url": "https://example.invalid/article",
                                        "anchor_text": "Read article",
                                        "policy_decision": "fetch",
                                    }
                                ],
                                "evidence": [
                                    {
                                        "url": "https://example.invalid/article",
                                        "final_url": "https://example.invalid/article",
                                        "title": "Fixture public article",
                                        "excerpt": "Public article excerpt for evidence-aware summary.",
                                        "status": "fetched",
                                        "reason": "",
                                        "content_type": "text/html",
                                    }
                                ],
                                "risks": [],
                                "flags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            hermes = tmp_path / "hermes"
            prompt_path = tmp_path / "prompt.txt"
            hermes.write_text(
                "#!/bin/sh\n"
                f"if [ \"$1\" = \"-z\" ]; then printf '%s' \"$2\" > {prompt_path}; echo '# Email summary'; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            hermes.chmod(0o755)

            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(scan_file),
                "--output",
                str(tmp_path / "downloads"),
                "--hermes",
                str(hermes),
                "--project-dir",
                str(tmp_path),
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = prompt_path.read_text(encoding="utf-8")
            self.assertIn("EmailEvidencePack", prompt)
            self.assertIn("newsletter_article", prompt)
            self.assertIn("Public article excerpt for evidence-aware summary.", prompt)
            self.assertIn("https://example.invalid/article", prompt)

    def test_email_summary_uses_empty_scan_file_without_real_imap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(ROOT / "tests" / "fixtures" / "email_summary_scans" / "email-scan-empty.json"),
                "--output",
                str(tmp_path / "downloads"),
                "--dry-run",
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = tmp_path / "downloads" / "EmailReports" / "email-summary-2026-07-05.md"
            copied_scan = tmp_path / "downloads" / "EmailReports" / "email-scan-2026-07-05.json"
            scan = json.loads(copied_scan.read_text(encoding="utf-8"))
            self.assertEqual(scan["raw_count"], 0)
            self.assertEqual(scan["items"], [])
            self.assertIn("dry-run: skipped Hermes summary", report.read_text(encoding="utf-8"))

    def test_email_summary_uses_truncated_scan_file_without_real_imap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(ROOT / "tests" / "fixtures" / "email_summary_scans" / "email-scan-truncated.json"),
                "--output",
                str(tmp_path / "downloads"),
                "--dry-run",
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = tmp_path / "downloads" / "EmailReports" / "email-summary-2026-07-05.md"
            copied_scan = tmp_path / "downloads" / "EmailReports" / "email-scan-2026-07-05.json"
            scan = json.loads(copied_scan.read_text(encoding="utf-8"))
            self.assertTrue(scan["possibly_truncated"])
            self.assertIn("触达上限，可能有遗漏", report.read_text(encoding="utf-8"))

    def test_email_summary_uses_eml_dir_without_real_imap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = run_podsum(
                "email-summary",
                "--eml-dir",
                str(ROOT / "tests" / "fixtures" / "email_summary"),
                "--output",
                str(tmp_path / "downloads"),
                "--dry-run",
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            scan_files = list((tmp_path / "downloads" / "EmailReports").glob("email-scan-*.json"))
            self.assertEqual(len(scan_files), 1)
            scan = json.loads(scan_files[0].read_text(encoding="utf-8"))
            self.assertEqual(scan["object_type"], "email_evidence_pack")
            self.assertEqual(scan["account"], "fixture@example.invalid")
            self.assertEqual(scan["raw_count"], 6)
            self.assertEqual({item["uid"] for item in scan["items"]}, {"101", "102", "103", "201", "202", "203"})
            self.assertTrue(any(item["has_attachments"] for item in scan["items"]))
            self.assertTrue(any("Google快讯" in item["subject"] for item in scan["items"]))
            self.assertTrue(any("Exmail Enterprise HTML" in item["subject"] for item in scan["items"]))
            self.assertTrue(any("Exmail Multipart Digest" in item["subject"] for item in scan["items"]))
            self.assertTrue(any("Unknown 8bit Header" in item["subject"] for item in scan["items"]))
            self.assertTrue(any("Fixture newsletter" in item["snippet"] for item in scan["items"]))
            self.assertTrue(any(item["email_type"] == "google_alert" for item in scan["items"]))
            self.assertTrue(any(item["links"] for item in scan["items"]))
            self.assertTrue(all("evidence" in item for item in scan["items"]))
            self.assertTrue(all("risks" in item for item in scan["items"]))

    def test_email_summary_tolerates_unknown_8bit_charset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = run_podsum(
                "email-summary",
                "--eml-dir",
                str(ROOT / "tests" / "fixtures" / "email_summary"),
                "--output",
                str(tmp_path / "downloads"),
                "--dry-run",
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            scan_files = list((tmp_path / "downloads" / "EmailReports").glob("email-scan-*.json"))
            scan = json.loads(scan_files[0].read_text(encoding="utf-8"))
            item = next(item for item in scan["items"] if item["uid"] == "203")
            self.assertEqual(item["subject"], "Fixture Unknown 8bit Header")
            self.assertIn("Fixture body", item["snippet"])

    def test_email_summary_sends_email_specific_epub_with_fake_hermes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scan_file = tmp_path / "email-scan.json"
            scan_file.write_text(
                json.dumps(
                    {
                        "date": "2026-07-05",
                        "account": "fixture@example.invalid",
                        "window": "1d",
                        "scan_limit": 300,
                        "raw_count": 1,
                        "possibly_truncated": False,
                        "items": [
                            {
                                "uid": "77",
                                "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                                "from": "Fixture Sender <sender@example.invalid>",
                                "subject": "Fixture actionable mail",
                                "snippet": "A fixture email that should become a summary.",
                                "has_attachments": False,
                                "flags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            hermes = tmp_path / "hermes"
            hermes_args = tmp_path / "hermes-email-send-args.txt"
            hermes.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-z\" ]; then cat <<'EOF'\n"
                "# Podsum Email Summary 2026-07-05\n\n"
                "## key takeaway\n\n"
                "仅基于邮件摘要：fixture item should be reviewed.\n\n"
                "## 来源索引\n\n"
                "- UID=77 | From=Fixture Sender <sender@example.invalid> | Subject=Fixture actionable mail | Date=Sun, 05 Jul 2026 08:00:00 +0800 | `email://2026-07-05/77`\n"
                "EOF\n"
                "exit 0; fi\n"
                f"if [ \"$1\" = \"send\" ]; then printf '%s\\n' \"$@\" > {hermes_args}; echo 'sent'; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            hermes.chmod(0o755)

            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(scan_file),
                "--output",
                str(tmp_path / "downloads"),
                "--hermes",
                str(hermes),
                "--project-dir",
                str(tmp_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            send_args = hermes_args.read_text(encoding="utf-8")
            self.assertIn("[Podsum] 2026-07-05 Email Summary", send_args)
            self.assertIn("Podsum Email Summary 2026-07-05", send_args)
            self.assertNotIn("文字稿", send_args)

    def test_email_summary_review_checklist_blocks_bad_real_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scan_file = tmp_path / "email-scan.json"
            scan_file.write_text(
                json.dumps(
                    {
                        "date": "2026-07-05",
                        "account": "fixture@example.invalid",
                        "window": "1d",
                        "scan_limit": 300,
                        "raw_count": 1,
                        "possibly_truncated": False,
                        "items": [
                            {
                                "uid": "77",
                                "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                                "from": "Fixture Sender <sender@example.invalid>",
                                "subject": "Fixture actionable mail",
                                "snippet": "A fixture email that should become a summary.",
                                "has_attachments": False,
                                "flags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            hermes = tmp_path / "hermes"
            hermes_args = tmp_path / "send-args.txt"
            hermes.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-z\" ]; then echo '# Bad summary'; exit 0; fi\n"
                f"if [ \"$1\" = \"send\" ]; then printf '%s\\n' \"$@\" > {hermes_args}; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            hermes.chmod(0o755)

            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(scan_file),
                "--output",
                str(tmp_path / "downloads"),
                "--hermes",
                str(hermes),
                "--project-dir",
                str(tmp_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed Review Checklist", result.stdout + result.stderr)
            self.assertFalse(hermes_args.exists())

    def test_email_summary_requires_explicit_imap_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = run_podsum(
                "email-summary",
                "--output",
                str(tmp_path / "downloads"),
                "--env-file",
                str(tmp_path / "missing.env"),
                "--no-send",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Reading Gmail/IMAP requires explicit confirmation", result.stderr + result.stdout)
            self.assertFalse((tmp_path / "downloads" / "EmailReports").exists())

    def test_email_summary_missing_credentials_fails_after_imap_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legacy_env_file = tmp_path / "legacy.env"
            legacy_env_file.write_text(
                "IMAP_USER=legacy@example.invalid\n"
                "IMAP_PASS=legacy-password\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            for key in [
                "PODSUM_EMAIL_IMAP_HOST",
                "PODSUM_EMAIL_IMAP_PORT",
                "PODSUM_EMAIL_IMAP_USER",
                "PODSUM_EMAIL_IMAP_PASS",
                "PODSUM_EMAIL_IMAP_MAILBOX",
                "IMAP_HOST",
                "IMAP_PORT",
                "IMAP_USER",
                "IMAP_PASS",
                "IMAP_MAILBOX",
                "GMAIL_USER",
                "GMAIL_APP_PASSWORD",
            ]:
                env.pop(key, None)

            result = run_podsum(
                "email-summary",
                "--output",
                str(tmp_path / "downloads"),
                "--env-file",
                str(legacy_env_file),
                "--allow-imap-read",
                "--no-send",
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            output = result.stderr + result.stdout
            self.assertIn("missing IMAP credentials", output)
            self.assertIn("PODSUM_EMAIL_IMAP_USER/PODSUM_EMAIL_IMAP_PASS", output)
            self.assertFalse((tmp_path / "downloads" / "EmailReports").exists())

    def test_run_once_can_exercise_email_summary_from_eml_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = run_podsum(
                "run-once",
                "--state",
                str(tmp_path / "state.json"),
                "--output",
                str(tmp_path / "downloads"),
                "--skip-download",
                "--skip-transcribe",
                "--skip-send",
                "--email-summary",
                "--email-eml-dir",
                str(ROOT / "tests" / "fixtures" / "email_summary"),
                "--email-dry-run",
                "--email-no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Wrote email summary:", result.stdout)
            scan_files = list((tmp_path / "downloads" / "EmailReports").glob("email-scan-*.json"))
            self.assertEqual(len(scan_files), 1)

    def test_migrate_state_preserves_sent_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "downloads"
            transcript = root / "Fixture Show" / "Transcripts" / "New Episode.md"
            transcript.parent.mkdir(parents=True)
            audio = root / "Fixture Show" / "New Episode.mp3"
            audio.write_bytes(b"audio")
            transcript.write_text("---\npodcast: \"Fixture Show\"\nepisode: \"New Episode\"\n---\n\nbody", encoding="utf-8")
            old_download_state = tmp_path / "old-download.json"
            old_download_state.write_text(
                json.dumps(
                    {
                        "downloaded": {
                            "old-key": {
                                "podcast": "Fixture Show",
                                "episode": "New Episode",
                                "url": "https://example.com/new.mp3",
                                "path": str(audio),
                                "downloaded_at": "2026-06-12T10:00:00+0800",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            digest = __import__("hashlib").sha256(transcript.read_bytes()).hexdigest()
            old_sent_state = tmp_path / "old-sent.json"
            old_sent_state.write_text(
                json.dumps(
                    {
                        "sent": {
                            str(transcript): {
                                "sha256": digest,
                                "sent_at": "2026-06-12T11:00:00+0800",
                                "bundle_path": str(root / "Reports" / "bundle.md"),
                                "epub_path": str(root / "Reports" / "bundle.epub"),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            state = tmp_path / "state.json"

            result = run_podsum(
                "migrate-state",
                "--state",
                str(state),
                "--output",
                str(root),
                "--old-download-state",
                str(old_download_state),
                "--old-sent-state",
                str(old_sent_state),
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(state.read_text(encoding="utf-8"))
            episode = data["episodes"]["old-key"]
            self.assertEqual(episode["status"], "sent")
            self.assertEqual(episode["transcript_path"], str(transcript))
            self.assertEqual(episode["transcript_sha256"], digest)

    def test_cleanup_does_not_overwrite_unified_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "downloads"
            transcript = root / "Fixture Show" / "Transcripts" / "New Episode.md"
            transcript.parent.mkdir(parents=True)
            audio = root / "Fixture Show" / "New Episode.mp3"
            audio.write_bytes(b"audio")
            transcript.write_text("---\npodcast: \"Fixture Show\"\nepisode: \"New Episode\"\n---\n\nbody", encoding="utf-8")
            digest = __import__("hashlib").sha256(transcript.read_bytes()).hexdigest()
            state = tmp_path / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "feeds": {},
                        "episodes": {
                            "episode-key": {
                                "podcast": "Fixture Show",
                                "episode": "New Episode",
                                "audio_path": str(audio),
                                "transcript_path": str(transcript),
                                "transcript_sha256": digest,
                                "status": "sent",
                                "sent_at": "2026-06-12T11:00:00+0800",
                                "bundle_path": "",
                                "epub_path": "",
                                "attempts": {"download": 1, "transcribe": 0, "interpret": 0, "send": 1},
                                "error": None,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_podsum(
                "send",
                "--cleanup",
                "--audio-retention-days",
                "-1",
                "--transcript-retention-days",
                "-1",
                "--untranscribed-audio-retention-days",
                "-1",
                "--bundle-retention-days",
                "-1",
                "--state",
                str(state),
                "--output",
                str(root),
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(state.read_text(encoding="utf-8"))
            self.assertIn("episodes", data)
            self.assertIn("episode-key", data["episodes"])
            self.assertEqual(data["episodes"]["episode-key"]["status"], "sent")

    def test_status_repairs_legacy_sent_only_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "downloads"
            transcript = root / "Fixture Show" / "Transcripts" / "New Episode.md"
            transcript.parent.mkdir(parents=True)
            audio = root / "Fixture Show" / "New Episode.mp3"
            audio.write_bytes(b"audio")
            transcript.write_text(
                "---\npodcast: \"Fixture Show\"\nepisode: \"New Episode\"\naudio: "
                + json.dumps(str(audio))
                + "\n---\n\nbody",
                encoding="utf-8",
            )
            digest = __import__("hashlib").sha256(transcript.read_bytes()).hexdigest()
            state = tmp_path / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "sent": {
                            str(transcript): {
                                "sha256": digest,
                                "sent_at": "2026-06-12T11:00:00+0800",
                                "bundle_path": str(root / "Reports" / "bundle.md"),
                                "epub_path": str(root / "Reports" / "bundle.epub"),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = run_podsum("status", "--state", str(state), "--output", str(root))

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Episodes: 1", result.stdout)
            repaired = json.loads(state.read_text(encoding="utf-8"))
            episode = next(iter(repaired["episodes"].values()))
            self.assertEqual(episode["status"], "sent")
            self.assertEqual(episode["episode"], "New Episode")

    def test_send_skips_transcript_already_sent_under_another_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "downloads"
            transcript = root / "Fixture Show" / "Transcripts" / "New Episode.md"
            transcript.parent.mkdir(parents=True)
            audio = root / "Fixture Show" / "New Episode.mp3"
            audio.write_bytes(b"audio")
            transcript.write_text("---\npodcast: \"Fixture Show\"\nepisode: \"New Episode\"\n---\n\nbody", encoding="utf-8")
            digest = __import__("hashlib").sha256(transcript.read_bytes()).hexdigest()
            state = tmp_path / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "feeds": {},
                        "episodes": {
                            "legacy-key": {
                                "podcast": "Fixture Show",
                                "episode": "New Episode",
                                "audio_path": str(audio),
                                "transcript_path": str(transcript),
                                "transcript_sha256": digest,
                                "status": "sent",
                                "sent_at": "2026-06-12T11:00:00+0800",
                                "bundle_path": "old.md",
                                "epub_path": "old.epub",
                                "attempts": {"download": 1, "transcribe": 0, "interpret": 0, "send": 1},
                                "error": None,
                            },
                            "canonical-key": {
                                "podcast": "Fixture Show",
                                "episode": "New Episode",
                                "audio_path": str(audio),
                                "transcript_path": str(transcript),
                                "status": "transcribed",
                                "attempts": {"download": 1, "transcribe": 0, "interpret": 0, "send": 0},
                                "error": None,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            hermes = tmp_path / "hermes"
            hermes.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
            hermes.chmod(0o755)

            result = run_podsum("send", "--state", str(state), "--output", str(root), "--hermes", str(hermes))

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Sent 0 episode(s).", result.stdout)
            data = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(data["episodes"]["canonical-key"]["status"], "sent")
            self.assertEqual(data["episodes"]["canonical-key"]["transcript_sha256"], digest)


if __name__ == "__main__":
    unittest.main()
