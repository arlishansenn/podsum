import json
import os
import shlex
import subprocess
import sys
import textwrap
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
PODSUM = ROOT / "outputs" / "podsum.py"
sys.path.insert(0, str(ROOT / "outputs"))
import podsum_email_summary as email_summary  # noqa: E402
import podsum_email_workbench as email_workbench  # noqa: E402
import podsum_runtime  # noqa: E402
from email import brief_agent, evidence_agent, graph as email_graph, need_store  # noqa: E402
from email.providers import FakeLinkClassifier, LinkClassification  # noqa: E402
from email.schemas import EmailEvidencePack, EmailIntelBrief, EvidenceNeed, EvidenceNeedEvent, transition_need  # noqa: E402


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


def get_text(base_url: str, path: str) -> str:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as response:
        return response.read().decode("utf-8")


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
    def test_podsum_runtime_env_override_wins_for_generated_commands(self) -> None:
        old_value = os.environ.get(podsum_runtime.PODSUM_PYTHON_ENV)
        os.environ[podsum_runtime.PODSUM_PYTHON_ENV] = "/opt/podsum/.venv/bin/python"
        try:
            self.assertEqual(podsum_runtime.podsum_python(), "/opt/podsum/.venv/bin/python")
            config = email_workbench.WorkbenchConfig(
                root=ROOT,
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
                topic_file=ROOT / "outputs" / "topic.md",
            )
            commands = email_workbench.commands_payload(config)["commands"]
        finally:
            if old_value is None:
                os.environ.pop(podsum_runtime.PODSUM_PYTHON_ENV, None)
            else:
                os.environ[podsum_runtime.PODSUM_PYTHON_ENV] = old_value

        self.assertIn("/opt/podsum/.venv/bin/python", commands["regenerate_summary_no_send"])
        self.assertNotIn("/usr/bin/python3", commands["regenerate_summary_no_send"])

    def test_email_workbench_help_exists(self) -> None:
        result = run_podsum("email-workbench", "--help")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("--root", result.stdout)
        self.assertIn("--policy-file", result.stdout)
        self.assertIn("--topic-file", result.stdout)

    def test_email_summary_prompt_matches_deep_interpretation_style(self) -> None:
        prompt = (ROOT / "outputs" / "email_summary_prompt.md").read_text(encoding="utf-8")

        self.assertIn("邮件情报深度解读器", prompt)
        self.assertIn("基本不需要再打开邮箱逐封确认", prompt)
        self.assertIn("## key takeaway", prompt)
        self.assertIn("## 如果只记三件事", prompt)
        self.assertIn("对象: EmailIntelBrief", prompt)
        self.assertIn("版本: 0.1", prompt)
        self.assertIn("来源对象: EmailEvidencePack", prompt)
        self.assertIn("引导对象: EmailTopicMap", prompt)
        self.assertIn("处理方式: EmailTopicMap -> EmailEvidencePack -> EmailIntelBrief", prompt)
        self.assertIn("不要把输出写成简单分类清单", prompt)
        self.assertIn("topic_hits", prompt)
        self.assertIn("items[].topics", prompt)
        self.assertIn("## 跟踪话题", prompt)
        self.assertIn("EmailEvidencePack", prompt)
        self.assertIn("snippet` 只是邮件摘要或截断片段", prompt)
        self.assertIn("type=email_snippet", prompt)
        self.assertIn("type=public_link", prompt)
        self.assertIn("status=fetched", prompt)
        self.assertIn("UID={{uid}} | From={{from}} | Subject={{subject}} | Date={{date}}", prompt)
        self.assertIn("possibly_truncated=true", prompt)

    def test_email_link_policy_parses_from_markdown(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")

        self.assertEqual(policy["object_type"], "email_policy")
        self.assertEqual(policy["limits"]["max_links_per_email"], 2)
        self.assertTrue(any(item["name"] == "newsletter_article" for item in policy["email_types"]))

    def test_email_topic_map_parses_from_markdown(self) -> None:
        topic_map = email_summary.load_topic_map(ROOT / "outputs" / "topic.md")

        self.assertEqual(topic_map["object_type"], "email_topic_map")
        self.assertGreaterEqual(len(topic_map["topics"]), 3)
        self.assertTrue(any(item["id"] == "ai_industry_agent_strategy" for item in topic_map["topics"]))
        self.assertTrue(all(item.get("description") for item in topic_map["topics"]))
        self.assertTrue(all(item.get("examples") for item in topic_map["topics"]))

    def test_email_evidence_pack_applies_topic_matches(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        topic_map = {
            "object_type": "email_topic_map",
            "version": 1,
            "topics": [
                {
                    "id": "tracked_agent",
                    "name": "Tracked Agent Work",
                    "priority": "high",
                    "keywords": ["agent workflow"],
                    "summary_focus": "Track agent workflow updates.",
                }
            ],
        }
        pack = evidence_agent.build_evidence_pack(
            {
                "date": "2026-07-05",
                "account": "fixture@example.invalid",
                "window": "1d",
                "scan_limit": 10,
                "raw_count": 1,
                "possibly_truncated": False,
                "items": [
                    {
                        "uid": "topic-1",
                        "from": "Fixture <sender@example.invalid>",
                        "subject": "Agent workflow update",
                        "snippet": "A fixture note about the agent workflow.",
                    }
                ],
            },
            policy,
            topic_map,
            False,
            email_summary.fetch_link_context,
        )
        scan = pack.to_dict()

        self.assertIsInstance(pack, EmailEvidencePack)
        self.assertEqual(scan["topic_map"]["object_type"], "email_topic_map")
        self.assertEqual(scan["topic_hits"][0]["id"], "tracked_agent")
        self.assertEqual(scan["items"][0]["topics"][0]["name"], "Tracked Agent Work")

    def test_email_message_item_fills_evidence_with_link_contexts(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        message = EmailMessage()
        message["From"] = "Fixture Sender <sender@example.invalid>"
        message["To"] = "Fixture Receiver <receiver@example.invalid>"
        message["Subject"] = "Fixture Newsletter"
        message["Date"] = "Sun, 05 Jul 2026 08:00:00 +0800"
        message.set_content(
            "Plain lead before https://example.invalid/plain-article and plain tail after the link."
        )
        message.add_alternative(
            "<html><body><p>HTML lead</p><a href=\"https://example.invalid/html-article\">Read HTML article</a></body></html>",
            subtype="html",
        )

        item = email_summary.message_item("55", message.as_bytes(), policy)
        snippet_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "email_snippet"]

        self.assertEqual(item["body_part_count"], 2)
        self.assertEqual(set(item["body_part_types"]), {"text/plain", "text/html"})
        self.assertEqual(len(item["links"]), 2)
        self.assertTrue(any("Plain lead before" in link["context"] for link in item["links"]))
        self.assertTrue(any(link["anchor_text"] == "Read HTML article" for link in item["links"]))
        self.assertEqual(snippet_evidence[0]["uid"], "55")
        self.assertEqual(snippet_evidence[0]["link_count"], 2)
        self.assertEqual(snippet_evidence[0]["body_part_count"], 2)

    def test_email_evidence_agent_fake_link_classifier_writes_audit_fields(self) -> None:
        def fixture_fetcher(url: str, timeout: int, excerpt_chars: int) -> dict:
            return {
                "url": url,
                "final_url": url,
                "title": "Fixture Article",
                "excerpt": "Fixture article body",
                "status": "fetched",
                "reason": "",
                "content_type": "text/html",
            }

        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        topic_map = {"object_type": "email_topic_map", "version": 1, "topics": []}
        classifier = FakeLinkClassifier(
            {
                "https://example.invalid/article": LinkClassification(
                    classification="content",
                    decision_source="ai_classifier",
                    decision_reason="fixture_content",
                    classifier_version="fake-v1",
                    confidence=0.9,
                ),
                "https://example.invalid/weak": LinkClassification(
                    classification="content",
                    decision_source="ai_classifier",
                    decision_reason="fixture_weak",
                    classifier_version="fake-v1",
                    confidence=0.2,
                ),
            }
        )

        pack = evidence_agent.build_evidence_pack_with_classifier(
            {
                "date": "2026-07-05",
                "account": "fixture@example.invalid",
                "window": "1d",
                "scan_limit": 10,
                "raw_count": 1,
                "possibly_truncated": False,
                "items": [
                    {
                        "uid": "classify-1",
                        "from": "Fixture <sender@example.invalid>",
                        "subject": "Fixture Newsletter",
                        "snippet": "Read https://example.invalid/article and https://example.invalid/weak",
                    }
                ],
            },
            policy,
            topic_map,
            True,
            fixture_fetcher,
            classifier,
            0.5,
        )
        links = [evidence for evidence in pack.to_dict()["items"][0]["evidence"] if evidence.get("type") == "public_link"]

        self.assertEqual(links[0]["classification"], "content")
        self.assertEqual(links[0]["decision_source"], "ai_classifier")
        self.assertEqual(links[0]["decision_reason"], "fixture_content")
        self.assertEqual(links[0]["classifier_version"], "fake-v1")
        self.assertEqual(links[0]["confidence"], 0.9)
        self.assertEqual(links[1]["classification"], "unknown")
        self.assertEqual(links[1]["decision_source"], "ai_classifier")
        self.assertEqual(links[1]["decision_reason"], "low_confidence:fixture_weak")
        self.assertEqual(links[1]["confidence"], 0.2)

    def test_email_evidence_agent_builds_pack_from_eml_message(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        topic_map = {
            "object_type": "email_topic_map",
            "version": 1,
            "topics": [{"id": "agent", "name": "Agent", "keywords": ["agent workflow"]}],
        }
        message = EmailMessage()
        message["From"] = "Newsletter <sender@example.invalid>"
        message["Subject"] = "Fixture Newsletter"
        message["Date"] = "Sun, 05 Jul 2026 08:00:00 +0800"
        message.set_content("Agent workflow story at https://example.invalid/article")

        pack = evidence_agent.build_evidence_pack_from_messages(
            "2026-07-05",
            "fixture@example.invalid",
            "1d",
            10,
            1,
            [("1", message.as_bytes())],
            policy,
            topic_map,
            False,
            email_summary.fetch_link_context,
        )
        item = pack.to_dict()["items"][0]

        self.assertEqual(item["links"][0]["normalized_url"], "https://example.invalid/article")
        self.assertEqual(item["evidence"][0]["type"], "email_snippet")
        self.assertEqual(item["topics"][0]["id"], "agent")
        self.assertIn("snippet_only", item["risks"])

    def test_scan_file_snippet_urls_become_link_candidates(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        scan = {
            "date": "2026-07-05",
            "items": [
                {
                    "uid": "scan-1",
                    "from": "Fixture <sender@example.invalid>",
                    "subject": "Fixture Newsletter",
                    "snippet": "Read the article at https://example.invalid/from-snippet before deciding.",
                    "evidence": [],
                    "risks": [],
                }
            ],
        }

        normalized = email_summary.normalize_evidence_pack(scan, policy)
        item = normalized["items"][0]
        snippet_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "email_snippet"]

        self.assertEqual(normalized["object_version"], "0.1")
        self.assertEqual(item["links"][0]["normalized_url"], "https://example.invalid/from-snippet")
        self.assertEqual(item["links"][0]["source_content_type"], "snippet")
        self.assertIn("before deciding", item["links"][0]["context"])
        self.assertEqual(snippet_evidence[0]["link_count"], 1)

    def test_email_dataclass_schemas_round_trip_scan_and_workbench_brief(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        scan = email_summary.normalize_evidence_pack(
            {
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
            policy,
        )
        scan["unexpected"] = "ignored"
        pack = EmailEvidencePack.from_dict(scan)
        expected_scan = dict(scan)
        del expected_scan["unexpected"]

        self.assertEqual(pack.to_dict(), expected_scan)

        review = email_workbench.default_review("2026-07-05")
        composition = brief_agent.compose_with_need_store(
            pack,
            {},
            need_store.empty_need_store(),
            "/tmp/email-summary-2026-07-05.md",
            review,
            "dry-run: fixture",
        )
        intel = EmailIntelBrief.from_dict({**composition.email_intel_brief.to_dict(), "unexpected": "ignored"})

        self.assertEqual(intel.to_dict(), composition.email_intel_brief.to_dict())
        self.assertEqual(intel.source_index[0]["source_uid"], "77")
        self.assertTrue(intel.source_coverage["complete"])

    def test_email_brief_agent_snippet_only_persists_need_without_external_calls(self) -> None:
        scan = {
            "object_type": "email_evidence_pack",
            "object_version": email_summary.EVIDENCE_PACK_VERSION,
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
                    "subject": "Decision needs source",
                    "snippet": "A snippet-only decision signal.",
                    "has_attachments": False,
                    "email_type": "personal",
                    "links": [],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "A snippet-only decision signal."}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [{"id": "decision", "name": "Decision", "priority": "high"}],
                }
            ],
            "topic_map": {"object_type": "email_topic_map", "version": 1, "topics": [], "topic_count": 1},
            "topic_hits": [],
        }
        called: list[str] = []

        def forbidden_hermes(hermes: str, prompt: str, cwd: str, timeout: int) -> tuple[bool, str]:
            called.append("hermes")
            raise AssertionError("Hermes must not be called")

        def forbidden_fetch(url: str, timeout: int, excerpt_chars: int) -> dict[str, str]:
            called.append("fetch")
            raise AssertionError("fetch must not be called")

        def forbidden_imap(args: object, policy: dict[str, object]) -> dict[str, object]:
            called.append("imap")
            raise AssertionError("IMAP must not be called")

        old_hermes = email_summary.run_hermes_prompt
        old_fetch = email_summary.fetch_link_context
        old_imap = email_summary.scan_imap
        email_summary.run_hermes_prompt = forbidden_hermes
        email_summary.fetch_link_context = forbidden_fetch
        email_summary.scan_imap = forbidden_imap
        try:
            with tempfile.TemporaryDirectory() as tmp:
                artifact_dir = Path(tmp)
                composition = brief_agent.compose_and_persist(
                    EmailEvidencePack.from_dict(scan),
                    scan["topic_map"],
                    artifact_dir,
                    str(artifact_dir / "email-summary-2026-07-05.md"),
                    {},
                    "dry-run: fixture",
                )
                loaded = need_store.load_need_store(artifact_dir)
        finally:
            email_summary.run_hermes_prompt = old_hermes
            email_summary.fetch_link_context = old_fetch
            email_summary.scan_imap = old_imap

        self.assertEqual(called, [])
        self.assertEqual(len(loaded["needs"]), 1)
        need = loaded["needs"][0]
        self.assertEqual(need["topic_id"], "decision")
        self.assertEqual(need["known_source_refs"], ["email:77"])
        self.assertIn(need["need_id"], composition.email_intel_brief.markdown)
        self.assertEqual(composition.email_intel_brief.source_coverage["need_ids"], [need["need_id"]])
        self.assertNotIn("待外部验证", composition.email_intel_brief.markdown)
        self.assertNotIn("claim_or_question", composition.email_intel_brief.markdown)

    def test_email_need_reconciliation_fulfills_and_stales_from_future_scans_without_external_calls(self) -> None:
        day1_scan = {
            "object_type": "email_evidence_pack",
            "object_version": email_summary.EVIDENCE_PACK_VERSION,
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
                    "subject": "Decision needs source",
                    "snippet": "A snippet-only decision signal.",
                    "has_attachments": False,
                    "email_type": "personal",
                    "links": [],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "A snippet-only decision signal."}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [{"id": "decision", "name": "Decision", "priority": "high"}],
                }
            ],
            "topic_map": {"object_type": "email_topic_map", "version": 1, "topics": [], "topic_count": 1},
            "topic_hits": [],
        }
        day2_scan = json.loads(json.dumps(day1_scan))
        day2_scan["date"] = "2026-07-06"
        day2_scan["items"][0]["risks"] = []
        day2_scan["items"][0]["evidence"] = [
            {
                "type": "public_link",
                "uid": "77",
                "url": "https://example.invalid/source",
                "status": "fetched",
                "title": "Source",
                "excerpt": "Verified source text.",
            }
        ]
        called: list[str] = []

        def forbidden_hermes(hermes: str, prompt: str, cwd: str, timeout: int) -> tuple[bool, str]:
            called.append("hermes")
            raise AssertionError("Hermes must not be called")

        def forbidden_fetch(url: str, timeout: int, excerpt_chars: int) -> dict[str, str]:
            called.append("fetch")
            raise AssertionError("fetch must not be called")

        def forbidden_imap(args: object, policy: dict[str, object]) -> dict[str, object]:
            called.append("imap")
            raise AssertionError("IMAP must not be called")

        old_hermes = email_summary.run_hermes_prompt
        old_fetch = email_summary.fetch_link_context
        old_imap = email_summary.scan_imap
        email_summary.run_hermes_prompt = forbidden_hermes
        email_summary.fetch_link_context = forbidden_fetch
        email_summary.scan_imap = forbidden_imap
        try:
            with tempfile.TemporaryDirectory() as tmp:
                artifact_dir = Path(tmp)
                brief_agent.compose_and_persist(
                    EmailEvidencePack.from_dict(day1_scan),
                    day1_scan["topic_map"],
                    artifact_dir,
                    str(artifact_dir / "email-summary-2026-07-05.md"),
                    {},
                    "dry-run: fixture",
                )
                brief_agent.compose_and_persist(
                    EmailEvidencePack.from_dict(day2_scan),
                    day2_scan["topic_map"],
                    artifact_dir,
                    str(artifact_dir / "email-summary-2026-07-06.md"),
                    {},
                    "dry-run: fixture",
                )
                loaded = need_store.load_need_store(artifact_dir)
        finally:
            email_summary.run_hermes_prompt = old_hermes
            email_summary.fetch_link_context = old_fetch
            email_summary.scan_imap = old_imap

        self.assertEqual(called, [])
        self.assertEqual(len(loaded["needs"]), 1)
        fulfilled = loaded["needs"][0]
        self.assertEqual(fulfilled["status"], "fulfilled_now")
        self.assertEqual(fulfilled["resolved_by"], ["pack-2026-07-06"])
        self.assertNotEqual(fulfilled["audit_trail"][0]["added_evidence_refs"], [])

        watching = EvidenceNeed.from_dict({**fulfilled, "status": "open", "resolved_by": [], "audit_trail": []})
        no_evidence_pack = EmailEvidencePack.from_dict(day1_scan)
        watched_store = brief_agent.reconcile_need_store(
            no_evidence_pack,
            need_store.replace_need(need_store.empty_need_store(), watching),
            "2026-07-07T08:00:00Z",
            2,
        )
        self.assertEqual(watched_store["needs"][0]["status"], "watching")
        stale_store = brief_agent.reconcile_need_store(no_evidence_pack, watched_store, "2026-07-08T08:00:00Z", 2)
        self.assertEqual(stale_store["needs"][0]["status"], "stale")

    def test_email_dataclass_schemas_validate_required_and_literal_fields(self) -> None:
        with self.assertRaises(ValueError):
            EmailEvidencePack.from_dict({"object_type": "email_evidence_pack"})
        with self.assertRaises(ValueError):
            EmailEvidencePack.from_dict(
                {
                    "object_type": "email_evidence_pack",
                    "object_version": "0.1",
                    "status": "unknown",
                    "date": "2026-07-05",
                    "account": "fixture@example.invalid",
                    "window": "1d",
                    "scan_limit": 10,
                    "raw_count": 0,
                    "possibly_truncated": False,
                    "items": [],
                }
            )

    def test_email_need_store_round_trip_transition_and_validation(self) -> None:
        need = EvidenceNeed.from_dict(
            {
                "need_id": "need-1",
                "status": "open",
                "urgency": "high",
                "topic_id": "ai_industry_agent_strategy",
                "source_brief_id": "brief-2026-07-05",
                "claim_or_question": "Does the claim have primary-source evidence?",
                "why_needed": "The current brief only has snippet evidence.",
                "known_source_refs": ["email:77"],
                "needed_evidence": ["public_link"],
                "created_at": "2026-07-05T08:00:00Z",
                "last_checked_at": "2026-07-05T08:00:00Z",
                "resolved_by": [],
                "response_policy": "check_future_scans",
                "audit_trail": [],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = need_store.replace_need(need_store.empty_need_store(), need)
            need_store.save_need_store(Path(tmp), store)
            loaded = need_store.load_need_store(Path(tmp))
            loaded_need = EvidenceNeed.from_dict(loaded["needs"][0])
            event = EvidenceNeedEvent.from_dict(
                {
                    "event_type": "fulfill",
                    "at": "2026-07-05T09:00:00Z",
                    "actor": "EmailIntelBriefAgent",
                    "reason": "Fetched link evidence is enough.",
                    "added_evidence_refs": ["link:77:1"],
                    "resolved_by": ["pack-2026-07-05"],
                }
            )
            transitioned = transition_need(loaded_need, event)

            self.assertEqual(transitioned.status, "fulfilled_now")
            self.assertEqual(transitioned.resolved_by, ("pack-2026-07-05",))
            self.assertEqual(transitioned.audit_trail[0].old_status, "open")
            self.assertEqual(transitioned.audit_trail[0].new_status, "fulfilled_now")

        invalid = need.to_dict()
        invalid["status"] = "unknown"
        with self.assertRaises(ValueError):
            EvidenceNeed.from_dict(invalid)
        with self.assertRaises(ValueError):
            transition_need(
                need,
                EvidenceNeedEvent.from_dict(
                    {
                        "event_type": "fulfill",
                        "at": "2026-07-05T10:00:00Z",
                        "actor": "EmailIntelBriefAgent",
                        "reason": "No evidence was added.",
                        "added_evidence_refs": [],
                        "resolved_by": [],
                    }
                ),
            )

    def test_email_workbench_reports_missing_artifacts_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
                topic_file=ROOT / "outputs" / "topic.md",
            )
            server, thread, base_url = start_workbench(config)
            try:
                home = get_text(base_url, "/")
                context = get_json(base_url, "/api/context")
                evidence = get_json(base_url, "/api/evidence-pack")
                topics = get_json(base_url, "/api/topics")
                commands = get_json(base_url, "/api/commands")
            finally:
                stop_workbench(server, thread)

            self.assertIn('data-view="topics"', home)
            self.assertIn("EmailTopicMap", home)
            self.assertIn('data-view="policy"', home)
            self.assertIn("EmailEvidencePolicy", home)
            workbench_source = (ROOT / "outputs" / "podsum_email_workbench.py").read_text(encoding="utf-8")
            self.assertIn("1. Tracked Topics", workbench_source)
            self.assertIn("Examples", workbench_source)
            self.assertIn("Not this", workbench_source)
            self.assertIn("1. Type Rules", workbench_source)
            self.assertIn("1. Topic Match", workbench_source)
            self.assertIn("3. Link Decision", workbench_source)
            self.assertEqual(context["server"]["mode"], "manual-local-workbench")
            self.assertFalse(context["server"]["safe_defaults"]["reads_imap"])
            self.assertIn("scan", context["missing"])
            self.assertIn("summary", context["missing"])
            self.assertTrue(evidence["missing"])
            self.assertEqual(topics["topic_map"]["object_type"], "email_topic_map")
            self.assertIn("generate_scan_manual_imap", commands["commands"])
            self.assertIn("--email-topic-file", commands["commands"]["regenerate_summary_no_send"])
            self.assertNotIn("/usr/bin/python3", commands["commands"]["regenerate_summary_no_send"])
            self.assertIn(shlex.quote(podsum_runtime.podsum_python()), commands["commands"]["regenerate_summary_no_send"])
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
                "## 跟踪话题\n\n"
                "本次没有命中 topic.md 中的跟踪话题。\n\n"
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
                topic_file=ROOT / "outputs" / "topic.md",
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
            self.assertEqual(brief["object_type"], "email_intel_brief")
            self.assertEqual(brief["object_version"], "0.1")
            self.assertTrue(any(section["title"] == "key takeaway" for section in brief["sections"]))
            self.assertTrue(brief["source_coverage"]["complete"])
            self.assertEqual(brief["source_index"][0]["source_uid"], "77")
            self.assertEqual(review["review"]["brief_status"], "approved")
            self.assertTrue(checklist["delivery_ready"])
            self.assertTrue(evidence_after["scan"]["items"][0]["_review"]["important"])
            self.assertEqual(scan_path.read_text(encoding="utf-8"), scan_text)
            self.assertEqual(summary_path.read_text(encoding="utf-8"), summary_text)
            self.assertTrue((reports / "email-review-2026-07-05.json").exists())

    def test_email_workbench_needs_api_manual_close_and_ui_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reports = tmp_path / "downloads" / "EmailReports"
            reports.mkdir(parents=True)
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
                "## 跟踪话题\n\n"
                "本次没有命中 topic.md 中的跟踪话题。\n\n"
                "## 来源索引\n\n"
                "- UID=77 | From=Fixture Sender <sender@example.invalid> | "
                "Subject=Fixture actionable mail | Date=Sun, 05 Jul 2026 08:00:00 +0800 | "
                "`email://2026-07-05/77`\n"
            )
            (reports / "email-scan-2026-07-05.json").write_text(scan_text, encoding="utf-8")
            (reports / "email-summary-2026-07-05.md").write_text(summary_text, encoding="utf-8")
            need_store.save_need_store(
                reports,
                {
                    "object_type": "email_need_store",
                    "object_version": "0.1",
                    "needs": [
                        {
                            "need_id": "need-low",
                            "status": "open",
                            "urgency": "low",
                            "topic_id": "topic-b",
                            "source_brief_id": "brief-2026-07-05",
                            "claim_or_question": "Low urgency question",
                            "why_needed": "Need a better source.",
                            "known_source_refs": ["email://2026-07-05/77"],
                            "needed_evidence": ["public_link"],
                            "created_at": "2026-07-05T08:00:00+0800",
                            "last_checked_at": "2026-07-05T08:00:00+0800",
                            "resolved_by": [],
                            "response_policy": "emit_need_reference_only",
                            "audit_trail": [],
                        },
                        {
                            "need_id": "need-high",
                            "status": "open",
                            "urgency": "high",
                            "topic_id": "topic-a",
                            "source_brief_id": "brief-2026-07-05",
                            "claim_or_question": "High urgency question",
                            "why_needed": "Need a stronger source.",
                            "known_source_refs": ["email:77"],
                            "needed_evidence": ["full_body"],
                            "created_at": "2026-07-05T08:00:00+0800",
                            "last_checked_at": "2026-07-05T08:00:00+0800",
                            "resolved_by": [],
                            "response_policy": "emit_need_reference_only",
                            "audit_trail": [],
                        },
                    ],
                },
            )
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
                topic_file=ROOT / "outputs" / "topic.md",
            )
            server, thread, base_url = start_workbench(config)
            try:
                home = get_text(base_url, "/")
                needs = get_json(base_url, "/api/needs")
                post_json(
                    base_url,
                    "/api/review",
                    {"brief_status": "approved", "brief_override_markdown": summary_text},
                )
                checklist = get_json(base_url, "/api/checklist")
                closed = post_json(base_url, "/api/needs/need-high/action", {"action": "close"})
                reloaded = get_json(base_url, "/api/needs")
            finally:
                stop_workbench(server, thread)

            self.assertIn('data-view="needs"', home)
            self.assertIn("claim_or_question", email_workbench.APP_JS)
            self.assertIn("data-need-ref-uid", email_workbench.APP_JS)
            self.assertEqual(needs["counts"]["open"], 2)
            self.assertEqual([item["need_id"] for item in needs["needs"]], ["need-high", "need-low"])
            self.assertTrue(checklist["delivery_ready"])
            self.assertEqual(closed["counts"]["closed"], 1)
            self.assertEqual(reloaded["counts"]["closed"], 1)
            persisted = need_store.load_need_store(reports)
            closed_need = [item for item in persisted["needs"] if item["need_id"] == "need-high"][0]
            self.assertEqual(closed_need["status"], "closed")
            self.assertEqual(closed_need["audit_trail"][-1]["actor"], "Workbench")

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
                topic_file=ROOT / "outputs" / "topic.md",
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

    def test_email_workbench_rejects_invalid_topics_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            topic_file = tmp_path / "topic.md"
            original = (ROOT / "outputs" / "topic.md").read_text(encoding="utf-8")
            topic_file.write_text(original, encoding="utf-8")
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
                topic_file=topic_file,
            )
            server, thread, base_url = start_workbench(config)
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    post_json(base_url, "/api/topics", {"markdown": "```json\n{\"object_type\":\n```"})
                caught.exception.close()
            finally:
                stop_workbench(server, thread)

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(topic_file.read_text(encoding="utf-8"), original)

    def test_email_workbench_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md",
                topic_file=ROOT / "outputs" / "topic.md",
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
        link_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "public_link"]
        snippet_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "email_snippet"]

        self.assertEqual(used, 1)
        self.assertEqual(item["links"][0]["policy_decision"], "fetch")
        self.assertEqual(link_evidence[0]["status"], "fetched")
        self.assertEqual(snippet_evidence[0]["status"], "available")
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
        link_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "public_link"]

        self.assertEqual(used, 0)
        self.assertEqual(item["links"][0]["policy_decision"], "skip")
        self.assertEqual(link_evidence[0]["status"], "skipped")
        self.assertIn("track", link_evidence[0]["reason"])

    def test_email_link_budget_exhaustion_writes_skipped_evidence(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        policy["limits"]["max_links_total"] = 1
        policy["limits"]["max_links_per_email"] = 1
        scan = {
            "date": "2026-07-05",
            "items": [
                {
                    "uid": "first",
                    "from": "Newsletter <sender@example.invalid>",
                    "subject": "Fixture Newsletter",
                    "snippet": "Read first https://example.invalid/first",
                    "email_type": "newsletter_article",
                    "links": [{"url": "https://example.invalid/first", "context": "Read first"}],
                    "evidence": [],
                    "risks": ["snippet_only"],
                },
                {
                    "uid": "second",
                    "from": "Newsletter <sender@example.invalid>",
                    "subject": "Fixture Newsletter",
                    "snippet": "Read second https://example.invalid/second",
                    "email_type": "newsletter_article",
                    "links": [{"url": "https://example.invalid/second", "context": "Read second"}],
                    "evidence": [],
                    "risks": ["snippet_only"],
                },
            ],
        }

        def fake_fetcher(url: str, timeout: int, excerpt_chars: int) -> dict[str, str]:
            return {
                "url": url,
                "final_url": url,
                "title": "Fetched article",
                "excerpt": "Fetched public article excerpt.",
                "status": "fetched",
                "reason": "",
                "content_type": "text/html",
            }

        enriched = email_summary.enrich_scan_links(email_summary.normalize_evidence_pack(scan, policy), policy, fetcher=fake_fetcher)
        first_link_evidence = [
            evidence for evidence in enriched["items"][0]["evidence"] if evidence.get("type") == "public_link"
        ]
        second_link_evidence = [
            evidence for evidence in enriched["items"][1]["evidence"] if evidence.get("type") == "public_link"
        ]

        self.assertEqual(first_link_evidence[0]["status"], "fetched")
        self.assertEqual(first_link_evidence[0]["uid"], "first")
        self.assertEqual(second_link_evidence[0]["status"], "skipped")
        self.assertEqual(second_link_evidence[0]["reason"], "link_budget_exhausted")
        self.assertEqual(second_link_evidence[0]["uid"], "second")
        self.assertEqual(enriched["items"][1]["links"][0]["policy_decision"], "skip")

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

    def test_email_intel_brief_draft_from_evidence_is_reviewable(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
        scan = email_summary.normalize_evidence_pack(
            {
                "date": "2026-07-05",
                "account": "fixture@example.invalid",
                "window": "7d",
                "raw_count": 2,
                "scan_limit": 2,
                "possibly_truncated": True,
                "items": [
                    {
                        "uid": "personal-1",
                        "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                        "from": "Fixture Sender <sender@example.invalid>",
                        "subject": "Fixture Follow-up",
                        "snippet": "Please review the decision before the meeting.",
                        "email_type": "personal",
                        "links": [],
                        "evidence": [],
                        "risks": ["snippet_only"],
                    },
                    {
                        "uid": "alert-1",
                        "date": "Sun, 05 Jul 2026 09:00:00 +0800",
                        "from": "Fixture Alerts <alerts@example.invalid>",
                        "subject": "Fixture Google快讯",
                        "snippet": "Read the source at https://example.invalid/source",
                        "email_type": "google_alert",
                        "links": [{"url": "https://example.invalid/source", "context": "Read the source"}],
                        "evidence": [],
                        "risks": ["snippet_only"],
                    },
                ],
            },
            policy,
        )

        topic_map = {
            "object_type": "email_topic_map",
            "version": 1,
            "default_behavior": "未命中 topic.md 的邮件只做低优先级补充。",
            "topics": [
                {
                    "id": "decision_topic",
                    "name": "Decision Follow-up",
                    "priority": "high",
                    "keywords": ["decision"],
                    "summary_focus": "Track decisions that need follow-up.",
                }
            ],
        }
        scan = email_summary.apply_topics(scan, topic_map)
        markdown = email_summary.build_intel_brief_draft(scan, "dry-run: skipped Hermes summary")
        checklist = email_summary.review_checklist(scan, markdown)
        sources = email_workbench.parse_source_index(markdown)

        self.assertIn("对象: EmailIntelBrief", markdown)
        self.assertIn("版本: 0.1", markdown)
        self.assertIn("来源对象: EmailEvidencePack 0.1", markdown)
        self.assertIn("引导对象: EmailTopicMap v1 (1 topics)", markdown)
        self.assertIn("处理方式: EmailTopicMap -> EmailEvidencePack -> EmailIntelBrief", markdown)
        self.assertIn("## 需要处理", markdown)
        self.assertIn("## 跟踪话题", markdown)
        self.assertIn("Decision Follow-up", markdown)
        self.assertIn("topic.md 命中：Decision Follow-up", markdown)
        self.assertIn("## 值得知道", markdown)
        self.assertIn("## 来源索引", markdown)
        self.assertIn("触达上限，可能有遗漏", markdown)
        self.assertIn("仅基于邮件摘要", markdown)
        self.assertIn("UID=personal-1", markdown)
        self.assertEqual([source["source_uid"] for source in sources], ["personal-1", "alert-1"])
        self.assertTrue(checklist["ready_to_send"])

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
            needs_path = tmp_path / "downloads" / "EmailReports" / "email-needs.json"
            self.assertTrue(report.exists())
            self.assertTrue(copied_scan.exists())
            self.assertTrue(needs_path.exists())
            needs = json.loads(needs_path.read_text(encoding="utf-8"))
            self.assertEqual(len(needs["needs"]), 1)
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("dry-run: Podsum local summary engine", report_text)
            self.assertIn(needs["needs"][0]["need_id"], report_text)

    def test_email_run_graph_import_guard_is_actionable_when_langgraph_missing(self) -> None:
        if email_graph.langgraph_available():
            self.skipTest("langgraph is installed")

        with self.assertRaisesRegex(RuntimeError, "LangGraph is required for EmailRunGraph"):
            email_graph.build_in_memory_email_run_graph()

    def test_email_run_graph_fixture_only_run_produces_lightweight_state(self) -> None:
        if not email_graph.langgraph_available():
            self.skipTest("langgraph is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_dir = tmp_path / "downloads" / "EmailReports"
            scan_file = tmp_path / "email-scan.json"
            scan = {
                "date": "2026-07-05",
                "account": "fixture@example.invalid",
                "window": "1d",
                "scan_limit": 300,
                "raw_count": 1,
                "possibly_truncated": False,
                "items": [
                    {
                        "uid": "42",
                        "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                        "from": "Fixture Sender <sender@example.invalid>",
                        "subject": "Fixture actionable mail",
                        "snippet": "A fixture email that should become a graph summary.",
                        "has_attachments": False,
                        "flags": [],
                    }
                ],
            }
            scan_file.write_text(json.dumps(scan), encoding="utf-8")
            policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md")
            topic_map = email_summary.load_topic_map(ROOT / "outputs" / "topic.md")
            context = email_graph.build_email_run_context(
                policy,
                topic_map,
                None,
                False,
                email_summary.fetch_link_context,
                "dry-run: Podsum local summary engine; no external summary engine called",
                {},
            )
            initial_state = email_graph.initial_email_run_state(
                "graph-fixture",
                "fixture@example.invalid",
                "2026-07-05",
                artifact_dir,
                scan_file,
            )
            app = email_graph.build_in_memory_email_run_graph()
            config = {"configurable": {"thread_id": "graph-fixture", "email_run_context": context}}

            final_state = app.invoke(initial_state, config)

            copied_scan = artifact_dir / "email-scan-2026-07-05.json"
            report = artifact_dir / "email-summary-2026-07-05.md"
            needs_path = artifact_dir / "email-needs.json"
            self.assertTrue(copied_scan.exists())
            self.assertTrue(report.exists())
            self.assertTrue(needs_path.exists())
            self.assertEqual(final_state["evidence_pack_path"], str(copied_scan))
            self.assertEqual(final_state["brief_path"], str(report))
            self.assertEqual(final_state["need_store_path"], str(needs_path))
            self.assertNotIn("graph summary", json.dumps(final_state, ensure_ascii=False))
            checkpoint_state = app.get_state(config).values
            self.assertNotIn("graph summary", json.dumps(checkpoint_state, ensure_ascii=False))
            scan_before = copied_scan.read_text(encoding="utf-8")
            needs_before = needs_path.read_text(encoding="utf-8")
            report_before = report.read_text(encoding="utf-8")
            email_graph.persist_evidence_pack(final_state, config)
            email_graph.persist_needs(final_state, config)
            email_graph.persist_brief(final_state, config)
            self.assertEqual(scan_before, copied_scan.read_text(encoding="utf-8"))
            self.assertEqual(needs_before, needs_path.read_text(encoding="utf-8"))
            self.assertEqual(report_before, report.read_text(encoding="utf-8"))

    def test_email_summary_default_engine_does_not_call_hermes_prompt(self) -> None:
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
                                "uid": "42",
                                "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                                "from": "Fixture Sender <sender@example.invalid>",
                                "subject": "Fixture actionable mail",
                                "snippet": "A fixture email that should become a local Podsum summary.",
                                "has_attachments": False,
                                "flags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            hermes = tmp_path / "hermes"
            hermes.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-z\" ]; then echo 'Hermes prompt should not be called' >&2; exit 88; fi\n"
                "exit 0\n",
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
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = tmp_path / "downloads" / "EmailReports" / "email-summary-2026-07-05.md"
            self.assertIn("对象: EmailIntelBrief", report.read_text(encoding="utf-8"))

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
                "--summary-engine",
                "hermes",
                "--project-dir",
                str(tmp_path),
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = prompt_path.read_text(encoding="utf-8")
            self.assertIn("email_topic_map", prompt)
            self.assertIn("topic_hits", prompt)
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
            self.assertIn("dry-run: Podsum local summary engine", report.read_text(encoding="utf-8"))

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
            self.assertEqual(scan["topic_map"]["object_type"], "email_topic_map")
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
            self.assertTrue(all("topics" in item for item in scan["items"]))
            self.assertIn("topic_hits", scan)
            self.assertTrue(any(item["links"] for item in scan["items"]))
            self.assertTrue(all("evidence" in item for item in scan["items"]))
            self.assertTrue(
                all(
                    any(evidence.get("type") == "email_snippet" for evidence in item["evidence"])
                    for item in scan["items"]
                )
            )
            self.assertTrue(
                all(
                    any(evidence.get("status") == "available" for evidence in item["evidence"])
                    for item in scan["items"]
                )
            )
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
                "## 跟踪话题\n\n"
                "本次没有命中 topic.md 中的跟踪话题。\n\n"
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
                "--summary-engine",
                "hermes",
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
                "--summary-engine",
                "hermes",
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
