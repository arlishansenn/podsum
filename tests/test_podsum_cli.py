import argparse
import json
import os
import shlex
import re
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
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
EMAIL_SUMMARY_PROMPT = ROOT / "outputs" / "email_summary_prompt.md"
OUTPUTS_README = ROOT / "outputs" / "README.md"
PODSUM = ROOT / "outputs" / "podsum.py"
sys.path.insert(0, str(ROOT / "outputs"))
import podsum  # noqa: E402
import podsum_email_summary as email_summary  # noqa: E402
import podsum_email_workbench as email_workbench  # noqa: E402
import podsum_runtime  # noqa: E402
import podsum_send_to_feishu as sender  # noqa: E402
from email import brief_agent, evidence_agent, graph as email_graph, need_store, object_harness  # noqa: E402
from email.providers import FakeLinkClassifier, LinkClassification  # noqa: E402
from email.schemas import EmailEvidencePack, EmailIntelBrief, EvidenceNeed, EvidenceNeedEvent, transition_need  # noqa: E402


def run_podsum(
    *args: str,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    # 把部署根指向一次性目录：否则子进程会读到这台机器上真实的 .env，
    # 那里的开关（PODSUM_EMAIL_SUMMARY 之类）会改变被测行为。
    if env is None:
        env = dict(os.environ, PODSUM_HOME=tempfile.mkdtemp(prefix="podsum-test-home-"))
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


def email_summary_prompt_text() -> str:
    return EMAIL_SUMMARY_PROMPT.read_text(encoding="utf-8")


class EmailSummaryPromptTest(unittest.TestCase):
    """email_summary_prompt.md 是 Hermes 的契约文本：改一句就改了 brief 的形状。

    断言的是逐字短语而不是行为，所以改 prompt 时必须连着这里一起改；分散在 CLI
    测试里的时候，改 prompt 的人找不到它们。
    """

    def test_prompt_asks_for_an_analyst_voice_without_internal_jargon(self) -> None:
        """既有契约：brief 是读完邮件后的判断，正文不许出现处理流程和对象名。"""
        prompt = email_summary_prompt_text()

        self.assertIn("邮件情报助理", prompt)
        self.assertIn("不要复述字段名或数据结构", prompt)
        self.assertIn("正文不要出现 JSON、字段", prompt)
        self.assertNotIn("对象: EmailIntelBrief", prompt)
        self.assertNotIn("来源对象: EmailEvidencePack", prompt)
        self.assertNotIn("处理方式: EmailTopicMap -> EmailEvidencePack -> EmailIntelBrief", prompt)
        self.assertNotIn("EmailEvidencePack", prompt)

    def test_prompt_prefers_omission_over_filler(self) -> None:
        """没有实质发现就整段省略，不许用「今天没有……」凑段落。"""
        prompt = email_summary_prompt_text()

        self.assertIn("只写有实质证据的发现", prompt)
        self.assertIn("整段省略", prompt)
        self.assertIn("直接省略", prompt)
        self.assertIn("连小节标题一起省略", prompt)
        self.assertNotIn("可以忽略什么", prompt)

    def test_prompt_requires_sources_inline_not_in_a_trailing_index(self) -> None:
        """来源集中到末尾，读者就得在正文和索引之间来回跳。"""
        prompt = email_summary_prompt_text()

        self.assertIn("必须把来源嵌在正文对应内容里", prompt)
        self.assertIn("[UID 1001](email://2026-07-05/1001)", prompt)
        self.assertIn("不要把来源集中放到末尾", prompt)
        self.assertNotIn("## 来源索引", prompt)

    def test_prompt_pins_the_brief_section_skeleton(self) -> None:
        """review_checklist 按 `## 今天先看` 判 brief 是否成形，标题不能随手改。"""
        prompt = email_summary_prompt_text()

        self.assertIn("# Morning Brief - {date}", prompt)
        for heading in ("## 今天先看", "## 需要处理", "## 情报线索", "## 证据边界"):
            with self.subTest(heading=heading):
                self.assertIn(heading, prompt)

    def test_prompt_states_how_evidence_may_be_used(self) -> None:
        """邮件片段不是正文，扫描可能触达上限——这些边界必须写在 prompt 里。"""
        prompt = email_summary_prompt_text()

        self.assertIn("邮件片段不是完整正文", prompt)
        self.assertIn("已抓取公开网页证据优先", prompt)
        self.assertIn("证据边界可以自然写成", prompt)
        self.assertIn("触达上限，可能有遗漏", prompt)
        self.assertIn("跟踪话题提示", prompt)
        self.assertIn("合并重复、营销、低信号邮件", prompt)
        self.assertIn("digest 类邮件例外", prompt)

    def test_email_summary_prompt_forbids_packing_items_onto_one_line(self) -> None:
        """真实运行里 LLM 把 19 条快讯写进了同一行：链接还能点，列表结构没了。

        「每个子条目一行」被读成了排版建议，所以这里要一条硬约束，明写禁止什么。
        """
        prompt = email_summary_prompt_text()

        self.assertIn("独占一行", prompt)
        self.assertIn("换行分隔", prompt)
        self.assertIn("禁止把多个条目写进同一行", prompt)

    def test_email_summary_prompt_requires_digest_items_to_be_expanded(self) -> None:
        """digest 类邮件本身没有内容，它的内容就是那张链接列表——压成一行等于丢掉全部信息。"""
        prompt = email_summary_prompt_text()

        # 三类必须点名，否则模型只会认字面上的 "digest"
        for email_type in ("google_alert", "digest", "newsletter_article"):
            with self.subTest(email_type=email_type):
                self.assertIn(email_type, prompt)

        self.assertIn("逐条展开", prompt)
        self.assertIn("不允许压成一行", prompt)
        self.assertIn("每个子条目一行", prompt)
        self.assertIn("normalized_url", prompt)
        self.assertIn("不超过一行", prompt)

    def test_email_summary_prompt_names_the_real_digest_email_types(self) -> None:
        """prompt 的 digest 规则靠类型名点名：策略里改了名字而 prompt 不动，规则会静默失效。"""
        prompt = email_summary_prompt_text()
        policy_types = {entry["name"] for entry in email_summary.DEFAULT_POLICY["email_types"]}

        for email_type in email_summary.DIGEST_EMAIL_TYPES:
            with self.subTest(email_type=email_type):
                self.assertIn(email_type, policy_types, "digest 家族必须是真实存在的策略类型")
                self.assertIn(email_type, prompt, "prompt 不点名这个类型，这类邮件仍会被压成一行")

    def test_email_summary_prompt_merges_same_subject_digests_by_url(self) -> None:
        """按 subject 判重会整封丢弃：8/19 的 RWA 快讯就是这样整个消失的。"""
        prompt = email_summary_prompt_text()

        self.assertIn("按链接地址去重", prompt)
        self.assertIn("不要按邮件主题判重", prompt)
        self.assertIn("整封丢弃", prompt)

    def test_email_summary_prompt_keeps_unenriched_links_clickable(self) -> None:
        """pending 的链接仍是有效点击目标：指向来源本身就是它的用途，不是未验证论断。"""
        prompt = email_summary_prompt_text()

        self.assertIn("尚未抓取正文的链接", prompt)
        self.assertIn("不构成未验证论断", prompt)


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
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
                topic_file=ROOT / "outputs" / "topic.md.example",
            )
            commands = email_workbench.commands_payload(config)["commands"]
        finally:
            if old_value is None:
                os.environ.pop(podsum_runtime.PODSUM_PYTHON_ENV, None)
            else:
                os.environ[podsum_runtime.PODSUM_PYTHON_ENV] = old_value

        self.assertIn("/opt/podsum/.venv/bin/python", commands["regenerate_summary_no_send"])
        self.assertNotIn("/usr/bin/python3", commands["regenerate_summary_no_send"])

    def test_email_object_harness_catalog_uses_current_workbench_artifacts_only(self) -> None:
        catalog = object_harness.list_catalog()

        self.assertEqual(catalog["renderer_contract"], object_harness.RENDERER_CONTRACT)
        self.assertFalse(catalog["safe_defaults"]["reads_imap"])
        self.assertFalse(catalog["safe_defaults"]["fetches_links"])
        self.assertFalse(catalog["safe_defaults"]["calls_hermes"])
        self.assertFalse(catalog["safe_defaults"]["sends"])
        self.assertEqual(catalog["scenarios"], [object_harness.CURRENT_SCENARIO])
        for object_type in object_harness.OBJECT_TYPES:
            group = catalog["groups"][object_type]
            self.assertEqual(group["scenarios"], [object_harness.CURRENT_SCENARIO])
            self.assertEqual(group["fixtures"], [])
            self.assertEqual(group["current_source"]["scenario"], object_harness.CURRENT_SCENARIO)
            self.assertEqual(group["current_source"]["fixture_source"], "workbench_artifact")
            self.assertFalse(group["current_source"]["privacy_safe"])

    def test_email_object_harness_session_events_and_import_validation(self) -> None:
        current_object = {
            "object_type": "email_evidence_pack",
            "status": "ready_for_summary",
            "date": "2026-07-05",
            "raw_count": 1,
            "items": [{"uid": "77", "flags": [], "_review": {}}],
        }
        session = object_harness.new_session_from_object(
            "email_evidence_pack",
            current_object,
            ("current_workbench_artifact",),
            (),
        )
        before_version = session.to_dict()["version_history"][-1]["version"]

        marked = object_harness.apply_event(session, "mock_evidence_agent", {}, "test")
        marked_payload = marked.to_dict()
        self.assertEqual(marked_payload["lifecycle_status"], "edited")
        self.assertIn("harness_mock_evidence_agent", marked_payload["current_object"]["items"][0]["flags"])
        self.assertEqual(marked_payload["event_log"][-1]["before_version"], before_version)
        self.assertNotEqual(marked_payload["event_log"][-1]["after_version"], before_version)
        self.assertEqual(marked_payload["renderer"]["contract"], object_harness.RENDERER_CONTRACT)
        self.assertFalse(marked_payload["privacy_safe"])

        validated = object_harness.apply_event(marked, "validate_object", {}, "test")
        self.assertEqual(validated.to_dict()["lifecycle_status"], "validated")
        exported = object_harness.export_session_fixture(validated)
        self.assertEqual(exported["selected_object_type"], "email_evidence_pack")
        self.assertFalse(exported["privacy_safe"])
        imported = object_harness.import_session_fixture("email_evidence_pack", "current", exported["fixture"])
        self.assertEqual(imported.to_dict()["current_object"]["object_type"], "email_evidence_pack")
        with self.assertRaisesRegex(ValueError, "email_evidence_pack"):
            object_harness.import_session_fixture("email_evidence_pack", "current", {"object_type": "wrong"})

    def test_email_object_harness_http_api_uses_shared_renderer_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reports = tmp_path / "downloads" / "EmailReports"
            reports.mkdir(parents=True)
            need_store.save_need_store(
                reports,
                {
                    "object_type": "email_need_store",
                    "object_version": "0.1",
                    "needs": [
                        {
                            "need_id": "need-http",
                            "status": "open",
                            "urgency": "high",
                            "topic_id": "topic-http",
                            "source_brief_id": "brief-2026-07-05",
                            "claim_or_question": "HTTP harness question",
                            "why_needed": "Need current artifact coverage.",
                            "known_source_refs": ["email://2026-07-05/77"],
                            "needed_evidence": ["public_link"],
                            "created_at": "2026-07-05T08:00:00+0800",
                            "last_checked_at": "2026-07-05T08:00:00+0800",
                            "resolved_by": [],
                            "response_policy": "emit_need_reference_only",
                            "audit_trail": [],
                        }
                    ],
                },
            )
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
                topic_file=ROOT / "outputs" / "topic.md.example",
            )
            server, thread, base_url = start_workbench(config)
            try:
                shell = get_text(base_url, "/harness")
                context = get_json(base_url, "/api/context")
                catalog = get_json(base_url, "/api/harness/catalog")
                loaded = get_json(base_url, "/api/harness/session?object_type=evidence_need_queue&scenario=current")
                session = loaded["session"]
                closed = post_json(
                    base_url,
                    "/api/harness/event",
                    {"session": session, "event_type": "close_need", "payload": {}, "actor": "test"},
                )
            finally:
                stop_workbench(server, thread)

            self.assertIn("Podsum Email Object Harness", shell)
            self.assertIn("Load current artifact", shell)
            self.assertNotIn("low_quality", shell)
            self.assertEqual(context["server"]["renderer_contract"], object_harness.RENDERER_CONTRACT)
            self.assertEqual(catalog["catalog"]["renderer_contract"], object_harness.RENDERER_CONTRACT)
            self.assertEqual(catalog["catalog"]["scenarios"], ["current"])
            self.assertEqual(session["renderer"]["contract"], object_harness.RENDERER_CONTRACT)
            self.assertEqual(session["fixture_source"], "workbench_artifact")
            self.assertEqual(closed["session"]["current_object"]["needs"][0]["status"], "closed")
            self.assertEqual(closed["session"]["event_log"][-1]["event_type"], "close_need")

    def test_email_object_harness_current_source_loads_formal_workbench_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reports = tmp_path / "downloads" / "EmailReports"
            reports.mkdir(parents=True)
            scan = {
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
                        "subject": "Current artifact mail",
                        "snippet": "A current artifact email for harness loading.",
                        "has_attachments": False,
                        "email_type": "personal",
                        "links": [],
                        "evidence": [],
                        "risks": ["snippet_only"],
                        "flags": [],
                    }
                ],
            }
            summary = (
                "# Morning Brief - 2026-07-05\n\n"
                "## 今天先看\n\n"
                "- 需要人工确认：[UID 77](email://2026-07-05/77) Current artifact mail；Current artifact summary.\n\n"
                "## 证据边界\n\n"
                "- 部分条目仅基于邮件摘要。\n"
            )
            (reports / "email-scan-2026-07-05.json").write_text(json.dumps(scan), encoding="utf-8")
            (reports / "email-summary-2026-07-05.md").write_text(summary, encoding="utf-8")
            need_store.save_need_store(
                reports,
                {
                    "object_type": "email_need_store",
                    "object_version": "0.1",
                    "needs": [
                        {
                            "need_id": "need-current",
                            "status": "open",
                            "urgency": "high",
                            "topic_id": "topic-current",
                            "source_brief_id": "brief-2026-07-05",
                            "claim_or_question": "Current harness question",
                            "why_needed": "Need current artifact coverage.",
                            "known_source_refs": ["email://2026-07-05/77"],
                            "needed_evidence": ["public_link"],
                            "created_at": "2026-07-05T08:00:00+0800",
                            "last_checked_at": "2026-07-05T08:00:00+0800",
                            "resolved_by": [],
                            "response_policy": "emit_need_reference_only",
                            "audit_trail": [],
                        }
                    ],
                },
            )
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
                topic_file=ROOT / "outputs" / "topic.md.example",
            )
            server, thread, base_url = start_workbench(config)
            try:
                topics = get_json(base_url, "/api/topics")["topic_map"]
                policy = get_json(base_url, "/api/policy")["policy"]
                evidence = get_json(base_url, "/api/evidence-pack")["scan"]
                brief = get_json(base_url, "/api/intel-brief")
                brief.pop("ok")
                needs = get_json(base_url, "/api/needs")["store"]
                current_topics = get_json(base_url, "/api/harness/session?object_type=email_topic_map&scenario=current")["session"]
                current_policy = get_json(base_url, "/api/harness/session?object_type=email_evidence_policy&scenario=current")["session"]
                current_evidence = get_json(base_url, "/api/harness/session?object_type=email_evidence_pack&scenario=current")["session"]
                current_brief = get_json(base_url, "/api/harness/session?object_type=email_intel_brief&scenario=current")["session"]
                current_needs = get_json(base_url, "/api/harness/session?object_type=evidence_need_queue&scenario=current")["session"]
            finally:
                stop_workbench(server, thread)

            self.assertEqual(current_topics["current_object"], topics)
            self.assertEqual(current_policy["current_object"], policy)
            self.assertEqual(current_evidence["current_object"], evidence)
            self.assertEqual(current_brief["current_object"], brief)
            self.assertEqual(current_needs["current_object"], needs)
            self.assertEqual(current_evidence["selected_fixture"], object_harness.CURRENT_SCENARIO)
            self.assertEqual(current_evidence["fixture_source"], "workbench_artifact")
            self.assertIn("current_workbench_artifact", current_evidence["risks"])
            self.assertEqual(current_evidence["version_history"][0]["reason"], "load_current_workbench_artifact")

    def test_email_workbench_help_exists(self) -> None:
        result = run_podsum("email-workbench", "--help")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("--root", result.stdout)
        self.assertIn("--policy-file", result.stdout)
        self.assertIn("--topic-file", result.stdout)

    def test_email_intel_brief_prunes_empty_signal_sections(self) -> None:
        markdown = textwrap.dedent(
            """
            # Podsum Email Summary 2026-07-06

            ## 今天先看

            今天没有明确需要立即回复或决策的邮件。

            今天没有必须立刻处理的个人事务邮件。

            唯一值得人工看一眼的是 RWA 快讯。[UID 1020](email://2026-07-06/1020)

            ## 跟踪话题

            ### VIS / NB / 可视化工作对象

            今天没有可用信息。Nikkei 邮件里只看到关键词命中，没有具体标题、链接或正文。

            ### RWA / Credit / 金融基础设施

            RWA 方向继续有行业密集变化的迹象。[UID 1020](email://2026-07-06/1020)

            ## 值得知道

            今天不形成可用判断。

            两封 Nikkei Asia 每日新闻邮件只显示“有文章匹配订阅关键词”，但没有具体标题、链接或正文。

            同一封快讯还包含一个 Binance Square 链接，但当前摘要没有可读标题或正文，不能提炼观点。

            Nikkei Asia 有两封每日新闻匹配提醒。邮件摘要没有露出具体文章标题或正文内容，不能据此形成 AI 行业、VIS/NB、教育 PBL 或写作交付方面的实质判断。

            SMTP 测试邮件说明发送链路至少完成过一次测试投递。它没有业务内容，但对本地邮件摘要链路有意义。[UID 1018](email://2026-07-06/1018)

            这条快讯涉及 RWA、合规和稳定币，不能从这两封邮件里提炼 AI 行业、VIS/NB 或 PBL 的可靠判断。
            """
        )

        pruned = email_summary.prune_empty_signal_sections(markdown)

        self.assertIn("唯一值得人工看一眼的是 RWA 快讯", pruned)
        self.assertIn("RWA / Credit / 金融基础设施", pruned)
        self.assertIn("SMTP 测试邮件说明发送链路", pruned)
        self.assertNotIn("今天没有明确需要", pruned)
        self.assertNotIn("没有必须立刻", pruned)
        self.assertNotIn("VIS / NB / 可视化工作对象", pruned)
        self.assertNotIn("不形成可用判断", pruned)
        self.assertNotIn("没有可用信息", pruned)
        self.assertNotIn("只显示“有文章匹配", pruned)
        self.assertNotIn("Binance Square", pruned)
        self.assertNotIn("没有露出具体文章标题", pruned)
        self.assertNotIn("不能据此形成", pruned)
        self.assertNotIn("不能提炼观点", pruned)
        self.assertIn("SMTP 测试邮件说明发送链路", pruned)
        self.assertNotIn("没有业务内容", pruned)
        self.assertNotIn("不能从", pruned)
        self.assertNotIn("可靠判断", pruned)

    def test_email_link_policy_parses_from_markdown(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")

        self.assertEqual(policy["object_type"], "email_policy")
        self.assertEqual(policy["limits"]["max_links_per_email"], 2)
        self.assertTrue(any(item["name"] == "newsletter_article" for item in policy["email_types"]))

    def test_email_topic_map_parses_from_markdown(self) -> None:
        topic_map = email_summary.load_topic_map(ROOT / "outputs" / "topic.md.example")

        self.assertEqual(topic_map["object_type"], "email_topic_map")
        self.assertGreaterEqual(len(topic_map["topics"]), 3)
        self.assertTrue(any(item["id"] == "ai_industry_agent_strategy" for item in topic_map["topics"]))
        self.assertTrue(all(item.get("description") for item in topic_map["topics"]))
        self.assertTrue(all(item.get("examples") for item in topic_map["topics"]))

    def test_email_evidence_pack_applies_topic_matches(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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

        original = email_summary.mailparser_cleaned_message

        def fake_cleaned_message(raw_message: bytes) -> dict:
            self.assertIn(b"Fixture Newsletter", raw_message)
            return {
                "snippet": "Plain lead before https://example.invalid/plain-article and plain tail after the link.",
                "links": [
                    {
                        "url": "https://example.invalid/plain-article",
                        "anchor_text": "",
                        "context": "Plain lead before https://example.invalid/plain-article and plain tail after the link.",
                        "source_content_type": "text/plain",
                        "position": "0",
                    },
                    {
                        "url": "https://example.invalid/html-article",
                        "anchor_text": "Read HTML article",
                        "context": "Read HTML article",
                        "source_content_type": "text/html",
                        "position": "1",
                    },
                ],
                "body_part_count": 2,
                "body_part_types": ["text/plain", "text/html"],
                "attachment_count": 0,
                "attachment_shapes": [],
            }

        email_summary.mailparser_cleaned_message = fake_cleaned_message
        try:
            item = email_summary.message_item("55", message.as_bytes(), policy)
        finally:
            email_summary.mailparser_cleaned_message = original
        snippet_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "email_snippet"]

        self.assertEqual(item["body_part_count"], 2)
        self.assertEqual(set(item["body_part_types"]), {"text/plain", "text/html"})
        self.assertEqual(len(item["links"]), 2)
        self.assertTrue(any("Plain lead before" in link["context"] for link in item["links"]))
        self.assertTrue(any(link["anchor_text"] == "Read HTML article" for link in item["links"]))
        self.assertEqual(snippet_evidence[0]["uid"], "55")
        self.assertEqual(snippet_evidence[0]["link_count"], 2)
        self.assertEqual(snippet_evidence[0]["body_part_count"], 2)

    def test_email_message_item_uses_informative_body_block_not_preview_padding(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
        message = EmailMessage()
        message["From"] = "Daily Digest <digest@example.invalid>"
        message["To"] = "Fixture Receiver <receiver@example.invalid>"
        message["Subject"] = "Daily keyword digest"
        message["Date"] = "Sun, 05 Jul 2026 08:00:00 +0800"
        message.set_content(
            "Here are new articles that match your following keywords.\n"
            + ("\u200c \u200d\u200e\u200f\ufeff " * 24)
            + "\nFrontier AI teams are moving agent workbenches into regulated enterprise deployments."
        )

        original = email_summary.mailparser_cleaned_message

        def fake_cleaned_message(raw_message: bytes) -> dict:
            self.assertIn(b"Daily keyword digest", raw_message)
            return {
                "snippet": "Frontier AI teams are moving agent workbenches into regulated enterprise deployments.",
                "links": [],
                "body_part_count": 1,
                "body_part_types": ["text/plain"],
                "attachment_count": 0,
                "attachment_shapes": [],
            }

        email_summary.mailparser_cleaned_message = fake_cleaned_message
        try:
            item = email_summary.message_item("preview-1", message.as_bytes(), policy)
        finally:
            email_summary.mailparser_cleaned_message = original
        snippet_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "email_snippet"]

        self.assertIn("Frontier AI teams", item["snippet"])
        self.assertNotIn("Here are new articles", item["snippet"])
        self.assertIn("Frontier AI teams", snippet_evidence[0]["excerpt"])
        self.assertNotIn("Here are new articles", snippet_evidence[0]["excerpt"])
        self.assertEqual(snippet_evidence[0]["reason"], "snippet_only")

    def test_email_message_item_prefers_mailparser_cleaned_payload(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
        message = EmailMessage()
        message["From"] = "Daily Digest <digest@example.invalid>"
        message["To"] = "Fixture Receiver <receiver@example.invalid>"
        message["Subject"] = "Daily keyword digest"
        message["Date"] = "Sun, 05 Jul 2026 08:00:00 +0800"
        message.set_content(
            "Here are new articles that match your following keywords.\n"
            "unsubscribe\n"
            "https://tracker.example.invalid/noisy"
        )

        original = email_summary.mailparser_cleaned_message

        def fake_cleaned_message(raw_message: bytes) -> dict:
            self.assertIn(b"Daily keyword digest", raw_message)
            return {
                "snippet": "Clean article title - Clean useful excerpt",
                "links": [
                    {
                        "url": "https://example.invalid/article",
                        "anchor_text": "Clean article title",
                        "context": "Clean useful excerpt",
                        "source_content_type": "text/html",
                        "position": "0",
                    }
                ],
                "body_part_count": 2,
                "body_part_types": ["text/plain", "text/html"],
                "attachment_count": 0,
                "attachment_shapes": [],
            }

        email_summary.mailparser_cleaned_message = fake_cleaned_message
        try:
            item = email_summary.message_item("preview-2", message.as_bytes(), policy)
        finally:
            email_summary.mailparser_cleaned_message = original

        self.assertEqual(item["snippet"], "Clean article title - Clean useful excerpt")
        self.assertEqual(len(item["links"]), 1)
        self.assertEqual(item["links"][0]["url"], "https://example.invalid/article")
        self.assertEqual(item["links"][0]["anchor_text"], "Clean article title")
        self.assertNotIn("Here are new articles", item["snippet"])
        self.assertNotIn("unsubscribe", item["snippet"])
        self.assertEqual(item["body_part_count"], 2)
        self.assertEqual(set(item["body_part_types"]), {"text/plain", "text/html"})

    def test_email_message_item_fails_when_mailparser_helper_is_unavailable(self) -> None:
        original_paths = email_summary.mailparser_node_module_paths
        original_node = os.environ.get("PODSUM_NODE")
        message = EmailMessage()
        message["From"] = "Daily Digest <digest@example.invalid>"
        message["To"] = "Fixture Receiver <receiver@example.invalid>"
        message["Subject"] = "Daily keyword digest"
        message.set_content("Useful body")

        email_summary.mailparser_node_module_paths = lambda: []
        os.environ["PODSUM_NODE"] = "node"
        try:
            with self.assertRaisesRegex(RuntimeError, "mailparser helper dependencies are missing"):
                email_summary.message_item("missing-helper", message.as_bytes(), email_summary.DEFAULT_POLICY)
        finally:
            email_summary.mailparser_node_module_paths = original_paths
            if original_node is None:
                os.environ.pop("PODSUM_NODE", None)
            else:
                os.environ["PODSUM_NODE"] = original_node

    def test_normalize_evidence_pack_recomputes_low_signal_legacy_snippet_evidence(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
        scan = {
            "date": "2026-07-05",
            "items": [
                {
                    "uid": "legacy-preview",
                    "from": "Daily Digest <digest@example.invalid>",
                    "subject": "Daily keyword digest",
                    "snippet": "Here are new articles that match your following keywords. \u200c \u200d\u200e\u200f\ufeff",
                    "evidence": [
                        {
                            "type": "email_snippet",
                            "status": "available",
                            "reason": "snippet_only",
                            "excerpt": "Here are new articles that match your following keywords. \u200c \u200d\u200e\u200f\ufeff",
                        }
                    ],
                    "risks": ["snippet_only"],
                }
            ],
        }

        normalized = email_summary.normalize_evidence_pack(scan, policy)
        item = normalized["items"][0]
        snippet_evidence = [evidence for evidence in item["evidence"] if evidence.get("type") == "email_snippet"]

        self.assertEqual(item["snippet"], "")
        self.assertNotIn("Here are new articles", snippet_evidence[0]["excerpt"])
        self.assertIn("Subject=Daily keyword digest", snippet_evidence[0]["excerpt"])
        self.assertEqual(snippet_evidence[0]["reason"], "metadata_only")
        self.assertNotIn("snippet_only", item["risks"])
        self.assertIn("metadata_only", item["risks"])

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

        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        self.assertNotIn(need["need_id"], composition.email_intel_brief.markdown)
        self.assertEqual(composition.email_intel_brief.source_coverage["need_ids"], [need["need_id"]])
        self.assertNotIn("待外部验证", composition.email_intel_brief.markdown)
        self.assertNotIn("claim_or_question", composition.email_intel_brief.markdown)

    def test_email_brief_keeps_need_ids_out_of_delivery_markdown(self) -> None:
        scan = {
            "object_type": "email_evidence_pack",
            "object_version": email_summary.EVIDENCE_PACK_VERSION,
            "status": "ready_for_summary",
            "date": "2026-07-05",
            "account": "fixture@example.invalid",
            "window": "1d",
            "scan_limit": 10,
            "raw_count": 2,
            "possibly_truncated": False,
            "items": [
                {
                    "uid": "77",
                    "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                    "from": "Fixture Sender <sender@example.invalid>",
                    "subject": "Decision one",
                    "snippet": "First snippet-only signal.",
                    "has_attachments": False,
                    "email_type": "personal",
                    "links": [],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "First snippet-only signal."}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [{"id": "decision", "name": "Decision", "priority": "high"}],
                },
                {
                    "uid": "78",
                    "date": "Sun, 05 Jul 2026 08:05:00 +0800",
                    "from": "Fixture Sender <sender@example.invalid>",
                    "subject": "Decision two",
                    "snippet": "Second snippet-only signal.",
                    "has_attachments": False,
                    "email_type": "personal",
                    "links": [],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "Second snippet-only signal."}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [{"id": "decision", "name": "Decision", "priority": "high"}],
                },
            ],
            "topic_map": {"object_type": "email_topic_map", "version": 1, "topics": [], "topic_count": 1},
            "topic_hits": [],
        }

        composition = brief_agent.compose_with_need_store(
            EmailEvidencePack.from_dict(scan),
            scan["topic_map"],
            need_store.empty_need_store(),
            "",
            {},
            "",
        )
        markdown = composition.email_intel_brief.markdown

        self.assertNotIn("need-2026-07-05-decision-77-snippet-only", markdown)
        self.assertNotIn("need-2026-07-05-decision-78-snippet-only", markdown)
        self.assertNotIn("证据需求", markdown)
        self.assertEqual(
            composition.email_intel_brief.source_coverage["need_ids"],
            [
                "need-2026-07-05-decision-77-snippet-only",
                "need-2026-07-05-decision-78-snippet-only",
            ],
        )

    def test_email_intel_brief_delivery_markdown_is_user_facing(self) -> None:
        scan = {
            "object_type": "email_evidence_pack",
            "object_version": email_summary.EVIDENCE_PACK_VERSION,
            "status": "ready_for_summary",
            "date": "2026-07-05",
            "account": "fixture@example.invalid",
            "window": "7d",
            "scan_limit": 10,
            "raw_count": 5,
            "possibly_truncated": True,
            "items": [
                {
                    "uid": "983",
                    "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                    "from": "OpenAI <noreply@tm.openai.com>",
                    "subject": "New sign-in to your OpenAI account",
                    "snippet": "We noticed a new sign-in to your OpenAI account from Los Angeles.",
                    "has_attachments": False,
                    "email_type": "unknown",
                    "links": [],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "New sign-in to your OpenAI account."}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [
                        {"id": "ai", "name": "AI 行业 / Agent 战略", "priority": "high", "matched_keywords": ["openai"]},
                        {"id": "vis", "name": "VIS / NB / 可视化工作对象", "priority": "normal", "matched_keywords": ["vis"]},
                    ],
                },
                {
                    "uid": "976",
                    "date": "Sun, 05 Jul 2026 09:00:00 +0800",
                    "from": "The Rundown AI <news@daily.therundown.ai>",
                    "subject": "OpenAI's most powerful model is here",
                    "snippet": "OpenAI launched a limited preview of its newest model for selected users.",
                    "has_attachments": False,
                    "email_type": "newsletter_article",
                    "links": [{"url": "https://example.invalid/openai", "context": "OpenAI model preview"}],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "OpenAI launched a limited preview of its newest model."}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [
                        {"id": "ai", "name": "AI 行业 / Agent 战略", "priority": "high", "matched_keywords": ["openai"]},
                        {"id": "vis", "name": "VIS / NB / 可视化工作对象", "priority": "normal", "matched_keywords": ["vis"]},
                    ],
                },
                {
                    "uid": "989",
                    "date": "Sun, 05 Jul 2026 10:00:00 +0800",
                    "from": "Google Alerts <googlealerts-noreply@google.com>",
                    "subject": "Google快讯 - 酒鬼酒",
                    "snippet": "酒鬼酒发布新品，包含若干财经媒体报道。",
                    "has_attachments": False,
                    "email_type": "google_alert",
                    "links": [],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "酒鬼酒发布新品。"}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [{"id": "vis", "name": "VIS / NB / 可视化工作对象", "priority": "normal", "matched_keywords": ["vis"]}],
                },
                {
                    "uid": "1018",
                    "date": "Sun, 05 Jul 2026 11:00:00 +0800",
                    "from": "fixture@example.invalid",
                    "subject": "SMTP Connection Test",
                    "snippet": "This is a test email from the IMAP/SMTP email skill.",
                    "has_attachments": False,
                    "email_type": "unknown",
                    "links": [],
                    "evidence": [{"type": "email_snippet", "status": "available", "excerpt": "This is a test email from the IMAP/SMTP email skill."}],
                    "risks": ["snippet_only"],
                    "flags": [],
                    "topics": [{"id": "ai", "name": "AI 行业 / Agent 战略", "priority": "high", "matched_keywords": ["ai"]}],
                },
            ],
            "topic_map": {"object_type": "email_topic_map", "version": 1, "topic_count": 2},
            "topic_hits": [
                {"id": "ai", "name": "AI 行业 / Agent 战略", "priority": "high", "item_uids": ["983", "976", "1018"], "matched_keywords": ["openai", "ai"]},
                {"id": "vis", "name": "VIS / NB / 可视化工作对象", "priority": "normal", "item_uids": ["983", "976", "989"], "matched_keywords": ["vis"]},
            ],
        }

        composition = brief_agent.compose_with_need_store(
            EmailEvidencePack.from_dict(scan),
            scan["topic_map"],
            need_store.empty_need_store(),
            "",
            {},
            "",
        )
        markdown = composition.email_intel_brief.markdown

        self.assertIn("# Morning Brief - 2026-07-05", markdown)
        self.assertIn("## 今天先看", markdown)
        self.assertIn("## 证据边界", markdown)
        self.assertIn("[UID 983](email://2026-07-05/983)", markdown)
        self.assertIn("[UID 976](email://2026-07-05/976)", markdown)
        self.assertLess(markdown.find("UID 983"), markdown.find("UID 976"))
        self.assertEqual(markdown.count("email://2026-07-05/983"), 1)
        self.assertEqual(markdown.count("email://2026-07-05/976"), 1)
        self.assertNotIn("酒鬼酒", markdown)
        self.assertNotIn("SMTP Connection Test", markdown)
        for forbidden in (
            "对象:",
            "EmailEvidencePack",
            "EmailTopicMap",
            "Review Checklist",
            "need_id",
            "snippet_only",
            "topic.md",
            "这封邮件命中",
            "skip",
            "link_triage",
        ):
            self.assertNotIn(forbidden, markdown)

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
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
                topic_file=ROOT / "outputs" / "topic.md.example",
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
                "# Morning Brief - 2026-07-05\n\n"
                "## 今天先看\n\n"
                "- 需要人工确认：[UID 77](email://2026-07-05/77) Fixture actionable mail；fixture item should be reviewed.\n\n"
                "## 证据边界\n\n"
                "- 部分条目仅基于邮件摘要。\n"
            )
            scan_path.write_text(scan_text, encoding="utf-8")
            summary_path.write_text(summary_text, encoding="utf-8")
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
                topic_file=ROOT / "outputs" / "topic.md.example",
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
            self.assertTrue(any(section["title"] == "今天先看" for section in brief["sections"]))
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
                "# Morning Brief - 2026-07-05\n\n"
                "## 今天先看\n\n"
                "- 需要人工确认：[UID 77](email://2026-07-05/77) Fixture actionable mail；fixture item should be reviewed.\n\n"
                "## 证据边界\n\n"
                "- 部分条目仅基于邮件摘要。\n"
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
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
                topic_file=ROOT / "outputs" / "topic.md.example",
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
            original = (ROOT / "outputs" / "email_link_policy.md.example").read_text(encoding="utf-8")
            policy_file.write_text(original, encoding="utf-8")
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=policy_file,
                topic_file=ROOT / "outputs" / "topic.md.example",
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
            original = (ROOT / "outputs" / "topic.md.example").read_text(encoding="utf-8")
            topic_file.write_text(original, encoding="utf-8")
            config = email_workbench.WorkbenchConfig(
                root=tmp_path / "downloads",
                date="2026-07-05",
                host="127.0.0.1",
                port=0,
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
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
                policy_file=ROOT / "outputs" / "email_link_policy.md.example",
                topic_file=ROOT / "outputs" / "topic.md.example",
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
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        self.assertEqual(link_evidence, [])
        self.assertIn("tracking_skipped", item["risks"])

    def test_normalize_evidence_pack_prunes_legacy_skipped_public_link_evidence(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
        scan = email_summary.normalize_evidence_pack(
            {
                "date": "2026-07-05",
                "items": [
                    {
                        "uid": "1009",
                        "from": "Google Alerts <googlealerts-noreply@google.com>",
                        "subject": "Google Alert - 巴菲特",
                        "snippet": "巴菲特 alert",
                        "links": [{"url": "https://www.google.com.hk/alerts/share?x=1"}],
                        "evidence": [
                            {
                                "type": "public_link",
                                "uid": "1009",
                                "url": "https://www.google.com.hk/alerts/share?x=1",
                                "status": "skipped",
                                "reason": "hard_skip:share",
                            },
                            {
                                "type": "public_link",
                                "uid": "1009",
                                "url": "https://finance.sina.com.cn/stock/usstock/c/2025-06-30/doc-ineycazv5737211.shtml",
                                "final_url": "https://finance.sina.com.cn/stock/usstock/c/2025-06-30/doc-ineycazv5737211.shtml?oid=WA+0859+3970+0884+RAB+Bangun+Plafon+Gypsum+Buat+Kamar+Murah+Banyumanik+Semarang&vt=4",
                                "title": "巴菲特相关报道",
                                "excerpt": "伯克希尔哈撒韦相关公开报道。",
                                "status": "fetched",
                                "content_type": "text/html",
                            },
                        ],
                    }
                ],
            },
            policy,
        )
        evidence = scan["items"][0]["evidence"]
        public_links = [entry for entry in evidence if entry.get("type") == "public_link"]

        self.assertTrue(any(entry.get("type") == "email_snippet" for entry in evidence))
        self.assertEqual(len(public_links), 1)
        self.assertEqual(public_links[0]["status"], "fetched")
        self.assertEqual(public_links[0]["final_url"], "https://finance.sina.com.cn/stock/usstock/c/2025-06-30/doc-ineycazv5737211.shtml")
        self.assertNotIn("hard_skip:share", json.dumps(evidence, ensure_ascii=False))
        self.assertNotIn("Bangun", json.dumps(evidence, ensure_ascii=False))

    def test_topic_guided_link_triage_selects_only_matching_canonical_targets(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
        policy["limits"]["max_links_total"] = 3
        policy["limits"]["max_links_per_email"] = 2
        calls: list[str] = []
        links = [
            {"url": "https://alerts.google.com/unsubscribe?token=1", "anchor_text": "unsubscribe", "context": "unsubscribe"},
            {"url": "https://www.google.com/url?url=https%3A%2F%2Fsource.example%2Fai-agents%3Futm_source%3Dalert%26gclid%3D1", "anchor_text": "AI agents", "context": "New report about agentic systems"},
            {"url": "https://source.example/ai-agents?utm_medium=email", "anchor_text": "Duplicate", "context": "Same source"},
            {"url": "https://early.example/market", "anchor_text": "Market", "context": "Market update"},
            {"url": "https://social.example/share?url=https%3A%2F%2Fsource.example%2Fai-agents", "anchor_text": "share", "context": "Share"},
        ]
        for index in range(100):
            links.append({"url": f"https://irrelevant.example/item-{index}", "anchor_text": f"Other {index}", "context": "sports and coupons"})
        links.append({"url": "https://late.example/frontier-models", "anchor_text": "Frontier models", "context": "AI agents benchmark"})
        scan = {
            "date": "2026-07-05",
            "items": [
                {
                    "uid": "UID-1003",
                    "from": "Google Alerts <googlealerts-noreply@google.com>",
                    "subject": "Google Alert - AI agents",
                    "snippet": "AI agents and frontier models updates.",
                    "email_type": "google_alert",
                    "links": links,
                    "evidence": [],
                    "risks": ["snippet_only"],
                }
            ],
        }
        topic_map = {
            "object_type": "email_topic_map",
            "version": 1,
            "topics": [
                {
                    "id": "ai_agents",
                    "name": "AI Agents",
                    "priority": "high",
                    "keywords": ["ai agents", "frontier models"],
                    "aliases": ["agentic systems"],
                    "description": "AI agent research",
                    "examples": ["benchmark"],
                    "non_examples": ["coupons"],
                }
            ],
        }

        def fake_fetcher(url: str, timeout: int, excerpt_chars: int) -> dict[str, str]:
            calls.append(url)
            return {
                "url": url,
                "final_url": url,
                "title": f"Fetched {url}",
                "excerpt": "Fetched public article excerpt.",
                "status": "fetched",
                "reason": "",
                "content_type": "text/html",
            }

        enriched = email_summary.enrich_scan_links(email_summary.normalize_evidence_pack(scan, policy), policy, fetcher=fake_fetcher, topic_map=topic_map)
        item = enriched["items"][0]
        triage = item["link_triage"]
        decisions = [group["decision"] for group in triage["groups"]]
        reasons = [group["reason"] for group in triage["groups"]]

        self.assertEqual(triage["total_links"], 106)
        self.assertEqual(triage["selected_fetch_count"], 2)
        self.assertGreaterEqual(triage["hard_skipped_count"], 2)
        self.assertGreaterEqual(triage["deduped_count"], 1)
        self.assertIn("dedupe", decisions)
        self.assertIn("defer:unmapped_topic", reasons)
        self.assertIn("https://source.example/ai-agents", calls)
        self.assertIn("https://late.example/frontier-models", calls)
        self.assertEqual(calls, ["https://source.example/ai-agents", "https://late.example/frontier-models"])
        self.assertNotIn("https://alerts.google.com/unsubscribe?token=1", calls)
        self.assertNotIn("https://early.example/market", calls)
        self.assertIn("unmapped_alert_topic", item["risks"])
        fetched_urls = [evidence["url"] for evidence in item["evidence"] if evidence.get("type") == "public_link" and evidence.get("status") == "fetched"]
        self.assertEqual(fetched_urls, calls)

    def test_topic_guided_link_triage_blocks_unmapped_alert_until_topic_matches(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
        policy["limits"]["max_links_total"] = 2
        policy["limits"]["max_links_per_email"] = 2
        scan = {
            "date": "2026-07-05",
            "items": [
                {
                    "uid": "UID-1003",
                    "from": "Google Alerts <googlealerts-noreply@google.com>",
                    "subject": "Google Alert - Synthetic biology",
                    "snippet": "A new result is available.",
                    "email_type": "google_alert",
                    "links": [{"url": "https://science.example/synbio-breakthrough", "anchor_text": "Synbio", "context": "Synthetic biology breakthrough"}],
                    "evidence": [],
                    "risks": ["snippet_only"],
                }
            ],
        }
        unmatched_topic_map = {"object_type": "email_topic_map", "version": 1, "topics": [{"id": "ai", "name": "AI", "priority": "high", "keywords": ["frontier model"]}]}
        matched_topic_map = {"object_type": "email_topic_map", "version": 1, "topics": [{"id": "bio", "name": "Bio", "priority": "high", "keywords": ["synthetic biology"]}]}
        calls: list[str] = []

        def fake_fetcher(url: str, timeout: int, excerpt_chars: int) -> dict[str, str]:
            calls.append(url)
            return {"url": url, "final_url": url, "title": "Fetched", "excerpt": "Fetched", "status": "fetched", "reason": "", "content_type": "text/html"}

        unmapped = email_summary.enrich_scan_links(email_summary.normalize_evidence_pack(json.loads(json.dumps(scan)), policy), policy, fetcher=fake_fetcher, topic_map=unmatched_topic_map)
        self.assertEqual(calls, [])
        self.assertEqual(unmapped["items"][0]["link_triage"]["selected_fetch_count"], 0)
        self.assertEqual(unmapped["items"][0]["link_triage"]["groups"][0]["reason"], "defer:unmapped_topic")
        self.assertIn("unmapped_alert_topic", unmapped["items"][0]["risks"])

        matched = email_summary.enrich_scan_links(email_summary.normalize_evidence_pack(json.loads(json.dumps(scan)), policy), policy, fetcher=fake_fetcher, topic_map=matched_topic_map)
        self.assertEqual(calls, ["https://science.example/synbio-breakthrough"])
        self.assertEqual(matched["items"][0]["link_triage"]["selected_fetch_count"], 1)
        self.assertEqual(matched["items"][0]["link_triage"]["groups"][0]["decision"], "fetch")

    def test_email_link_budget_exhaustion_marks_links_without_skipped_evidence(self) -> None:
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        self.assertEqual(second_link_evidence, [])
        self.assertEqual(enriched["items"][1]["links"][0]["policy_decision"], "skip")
        self.assertIn("link_budget_exhausted", enriched["items"][1]["risks"])

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
        policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
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
        sources = email_workbench.parse_source_index(markdown, scan)

        self.assertIn("# Morning Brief - 2026-07-05", markdown)
        self.assertIn("## 今天先看", markdown)
        self.assertIn("## 证据边界", markdown)
        self.assertIn("Decision Follow-up", markdown)
        self.assertNotIn("topic.md", markdown)
        self.assertNotIn("对象: EmailIntelBrief", markdown)
        self.assertNotIn("Review Checklist", markdown)
        self.assertNotIn("## 来源索引", markdown)
        self.assertIn("触达上限，可能有遗漏", markdown)
        self.assertIn("仅基于邮件摘要", markdown)
        self.assertIn("[UID personal-1](email://2026-07-05/personal-1)", markdown)
        # alert-1 是 google_alert：以前被整体排除、整封消失，现在进订阅摘要区并保留溯源。
        self.assertEqual([source["source_uid"] for source in sources], ["personal-1", "alert-1"])
        self.assertEqual(sources[0]["subject"], "Fixture Follow-up")
        self.assertIn("## 订阅摘要", markdown)
        self.assertIn("](https://example.invalid/source)", markdown)
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
                "--target",
                "discord:test-target",
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
            self.assertEqual(send_args[send_args.index("--to") + 1], "discord:test-target")

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
                "--target",
                "discord:test-target",
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
                "--email-topic-file",
                str(ROOT / "outputs" / "topic.md.example"),
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
            self.assertIn("# Morning Brief - 2026-07-05", report_text)
            self.assertIn("[UID 42](email://2026-07-05/42)", report_text)
            self.assertNotIn("dry-run: Podsum local summary engine", report_text)
            self.assertNotIn(needs["needs"][0]["need_id"], report_text)

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
            policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
            topic_map = email_summary.load_topic_map(ROOT / "outputs" / "topic.md.example")
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

    def test_email_run_graph_reconciles_existing_need_from_future_scan(self) -> None:
        if not email_graph.langgraph_available():
            self.skipTest("langgraph is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_dir = tmp_path / "downloads" / "EmailReports"
            artifact_dir.mkdir(parents=True)
            open_need = EvidenceNeed.from_dict(
                {
                    "need_id": "need-2026-07-05-decision-77-snippet-only",
                    "status": "open",
                    "urgency": "high",
                    "topic_id": "decision",
                    "source_brief_id": "brief-2026-07-05",
                    "claim_or_question": "UID=77 是否有邮件摘要之外的可验证证据？",
                    "why_needed": "当前 EvidencePack 将该邮件标记为 snippet_only。",
                    "known_source_refs": ["email:77"],
                    "needed_evidence": ["public_link_or_full_body"],
                    "created_at": "2026-07-05T08:00:00Z",
                    "last_checked_at": "2026-07-05T08:00:00Z",
                    "resolved_by": [],
                    "response_policy": "emit_need_reference_only",
                    "audit_trail": [],
                }
            )
            need_store.save_need_store(artifact_dir, need_store.replace_need(need_store.empty_need_store(), open_need))
            scan_file = tmp_path / "email-scan-day2.json"
            scan_file.write_text(
                json.dumps(
                    {
                        "date": "2026-07-06",
                        "account": "fixture@example.invalid",
                        "window": "1d",
                        "scan_limit": 300,
                        "raw_count": 1,
                        "possibly_truncated": False,
                        "items": [
                            {
                                "uid": "77",
                                "date": "Mon, 06 Jul 2026 08:00:00 +0800",
                                "from": "Fixture Sender <sender@example.invalid>",
                                "subject": "Decision source found",
                                "snippet": "A verified source is now available.",
                                "has_attachments": False,
                                "links": [],
                                "evidence": [
                                    {
                                        "type": "public_link",
                                        "uid": "77",
                                        "url": "https://example.invalid/source",
                                        "status": "fetched",
                                        "title": "Source",
                                        "excerpt": "Verified source text.",
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
            policy = email_summary.load_link_policy(ROOT / "outputs" / "email_link_policy.md.example")
            topic_map = {"object_type": "email_topic_map", "version": 1, "topics": []}
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
                "graph-reconcile-fixture",
                "fixture@example.invalid",
                "2026-07-06",
                artifact_dir,
                scan_file,
            )
            app = email_graph.build_in_memory_email_run_graph()
            config = {"configurable": {"thread_id": "graph-reconcile-fixture", "email_run_context": context}}

            app.invoke(initial_state, config)

            reconciled = need_store.load_need_store(artifact_dir)["needs"][0]
            self.assertEqual(reconciled["status"], "fulfilled_now")
            self.assertEqual(reconciled["resolved_by"], ["pack-2026-07-06"])
            self.assertNotEqual(reconciled["audit_trail"][-1]["added_evidence_refs"], [])

    def test_email_summary_cli_graph_reconciles_existing_need_from_future_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reports = tmp_path / "downloads" / "EmailReports"
            reports.mkdir(parents=True)
            open_need = EvidenceNeed.from_dict(
                {
                    "need_id": "need-2026-07-05-decision-77-snippet-only",
                    "status": "open",
                    "urgency": "high",
                    "topic_id": "decision",
                    "source_brief_id": "brief-2026-07-05",
                    "claim_or_question": "UID=77 是否有邮件摘要之外的可验证证据？",
                    "why_needed": "当前 EvidencePack 将该邮件标记为 snippet_only。",
                    "known_source_refs": ["email:77"],
                    "needed_evidence": ["public_link_or_full_body"],
                    "created_at": "2026-07-05T08:00:00Z",
                    "last_checked_at": "2026-07-05T08:00:00Z",
                    "resolved_by": [],
                    "response_policy": "emit_need_reference_only",
                    "audit_trail": [],
                }
            )
            need_store.save_need_store(reports, need_store.replace_need(need_store.empty_need_store(), open_need))
            scan_file = tmp_path / "email-scan-day2.json"
            scan_file.write_text(
                json.dumps(
                    {
                        "date": "2026-07-06",
                        "account": "fixture@example.invalid",
                        "window": "1d",
                        "scan_limit": 300,
                        "raw_count": 1,
                        "possibly_truncated": False,
                        "items": [
                            {
                                "uid": "77",
                                "date": "Mon, 06 Jul 2026 08:00:00 +0800",
                                "from": "Fixture Sender <sender@example.invalid>",
                                "subject": "Decision source found",
                                "snippet": "A verified source is now available.",
                                "has_attachments": False,
                                "links": [],
                                "evidence": [
                                    {
                                        "type": "public_link",
                                        "uid": "77",
                                        "url": "https://example.invalid/source",
                                        "status": "fetched",
                                        "title": "Source",
                                        "excerpt": "Verified source text.",
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

            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(scan_file),
                "--output",
                str(tmp_path / "downloads"),
                "--summary-engine",
                "podsum",
                "--no-send",
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            reconciled = need_store.load_need_store(reports)["needs"][0]
            self.assertEqual(reconciled["status"], "fulfilled_now")
            self.assertEqual(reconciled["resolved_by"], ["pack-2026-07-06"])
            self.assertNotEqual(reconciled["audit_trail"][-1]["added_evidence_refs"], [])
            self.assertTrue((reports / "email-scan-2026-07-06.json").exists())
            self.assertTrue((reports / "email-summary-2026-07-06.md").exists())

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
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("# Morning Brief - 2026-07-05", report_text)
            self.assertNotIn("对象: EmailIntelBrief", report_text)

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
            self.assertIn("email_evidence_digest", prompt)
            self.assertIn("source_object_type", prompt)
            self.assertIn("topic_hits", prompt)
            self.assertNotIn("对象: EmailIntelBrief", prompt)
            self.assertIn("# Morning Brief - 2026-07-05", prompt)
            self.assertIn("newsletter_article", prompt)
            self.assertIn("Public article excerpt for evidence-aware summary.", prompt)
            self.assertIn("https://example.invalid/article", prompt)

    def test_email_summary_uses_llm_evidence_preprocess_digest(self) -> None:
        prompts: list[str] = []
        scan = {
            "object_type": "email_evidence_pack",
            "object_version": "0.1",
            "status": "enriched",
            "date": "2026-07-05",
            "account": "fixture@example.invalid",
            "window": "1d",
            "scan_limit": 10,
            "raw_count": 1,
            "possibly_truncated": False,
            "topic_hits": [{"id": "markets", "name": "Markets", "priority": "normal", "item_uids": ["1009"]}],
            "items": [
                {
                    "uid": "1009",
                    "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                    "from": "Google Alerts <googlealerts-noreply@google.com>",
                    "subject": "Google Alert - 巴菲特",
                    "snippet": "巴菲特 <https://www.google.com.hk/alerts/share?x=1> hard_skip:share Bangun Plafon tracking noise",
                    "email_type": "google_alert",
                    "links": [{"url": "https://www.google.com.hk/alerts/share?x=1", "policy_decision": "skip"}],
                    "link_triage": {
                        "hard_skipped_count": 40,
                        "groups": [{"decision": "skip", "reason": "hard_skip:share", "url": "https://www.google.com.hk/alerts/share?x=1"}],
                    },
                    "evidence": [
                        {
                            "type": "email_snippet",
                            "status": "available",
                            "excerpt": "巴菲特 alert snippet with tracking noise",
                        },
                        {
                            "type": "public_link",
                            "url": "https://www.google.com.hk/alerts/share?x=1",
                            "status": "skipped",
                            "reason": "hard_skip:share",
                            "classification": "navigation",
                            "decision_reason": "hard_skip:share",
                        },
                        {
                            "type": "public_link",
                            "url": "https://finance.sina.com.cn/stock/usstock/c/2025-06-30/doc-ineycazv5737211.shtml",
                            "final_url": "https://finance.sina.com.cn/stock/usstock/c/2025-06-30/doc-ineycazv5737211.shtml",
                            "title": "巴菲特相关报道",
                            "excerpt": "伯克希尔哈撒韦相关公开报道。",
                            "status": "fetched",
                            "content_type": "text/html",
                            "classification": "content",
                            "decision_reason": "eligible_public_link",
                        },
                    ],
                    "risks": ["link_skipped"],
                    "flags": [],
                    "topics": [{"id": "markets", "name": "Markets", "priority": "normal"}],
                }
            ],
        }

        def fake_run_hermes_prompt(hermes: str, prompt: str, cwd: str, timeout: int) -> tuple[bool, str]:
            prompts.append(prompt)
            if len(prompts) == 1:
                return True, json.dumps(
                    {
                        "object_type": "email_evidence_digest",
                        "items": [
                            {
                                "uid": "1009",
                                "source_ref": "email://2026-07-05/1009",
                                "clean_summary": "巴菲特相关公开报道需要人工复核。",
                                "key_facts": ["伯克希尔相关报道来自新浪财经。"],
                                "public_sources": [
                                    {
                                        "title": "新浪财经报道",
                                        "url": "https://finance.sina.com.cn/stock/usstock/c/2025-06-30/doc-ineycazv5737211.shtml?oid=WA+0859+3970+0884+RAB+Bangun+Plafon+Gypsum+Buat+Kamar+Murah+Banyumanik+Semarang&vt=4",
                                        "claim": "公开报道提到伯克希尔相关信息。",
                                        "evidence_excerpt": "伯克希尔哈撒韦相关公开报道。",
                                        "reason": "should_not_leak",
                                    }
                                ],
                                "evidence_limits": ["Google Alerts 只提供摘要。"],
                                "link_triage": {"reason": "hard_skip:share"},
                                "reason": "hard_skip:share",
                                "decision": "skip",
                                "classification": "content",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            return True, "# Podsum Email Summary 2026-07-05\n\n巴菲特相关报道 [UID 1009](email://2026-07-05/1009)\n"

        args = argparse.Namespace(
            summary_engine="hermes",
            dry_run=False,
            email_summary_prompt=EMAIL_SUMMARY_PROMPT,
            email_evidence_preprocess_prompt=ROOT / "outputs" / "email_evidence_preprocess_prompt.md",
            hermes=Path("/bin/echo"),
            project_dir=ROOT,
            hermes_timeout=180,
            no_llm_evidence_preprocess=False,
        )
        original = email_summary.run_hermes_prompt
        try:
            email_summary.run_hermes_prompt = fake_run_hermes_prompt
            rendered = email_summary.render_report(args, scan)
        finally:
            email_summary.run_hermes_prompt = original

        self.assertIn("[UID 1009](email://2026-07-05/1009)", rendered)
        self.assertEqual(len(prompts), 2)
        self.assertIn("email_evidence_pack_llm_brief_input", prompts[0])
        self.assertIn("google.com.hk/alerts/share", prompts[0])
        final_prompt = prompts[1]
        self.assertIn("email_evidence_digest", final_prompt)
        self.assertIn("巴菲特相关公开报道需要人工复核", final_prompt)
        self.assertIn("https://finance.sina.com.cn/stock/usstock/c/2025-06-30/doc-ineycazv5737211.shtml", final_prompt)
        self.assertNotIn("google.com.hk/alerts/share", final_prompt)
        self.assertNotIn("Bangun", final_prompt)
        self.assertNotIn("oid=", final_prompt)
        self.assertNotIn("hard_skip", final_prompt)
        self.assertNotIn("should_not_leak", final_prompt)
        self.assertNotIn("\"link_triage\"", final_prompt)
        self.assertNotIn("\"reason\"", final_prompt)
        self.assertNotIn("\"decision\"", final_prompt)
        self.assertNotIn("\"classification\"", final_prompt)

    def test_email_summary_llm_input_filters_raw_link_noise(self) -> None:
        noisy_context = "tracking pixel hidden html " * 200
        scan = {
            "object_type": "email_evidence_pack",
            "object_version": "0.1",
            "status": "enriched",
            "date": "2026-07-05",
            "account": "fixture@example.invalid",
            "window": "1d",
            "scan_limit": 10,
            "raw_count": 1,
            "possibly_truncated": False,
            "topic_map": {"object_type": "email_topic_map", "version": 2, "topic_count": 1},
            "topic_hits": [
                {
                    "id": "ai",
                    "name": "AI",
                    "priority": "high",
                    "matched_keywords": ["agent"],
                    "summary_focus": "Agent workflow",
                    "item_uids": ["88"],
                    "description": "drop this verbose topic description",
                }
            ],
            "items": [
                {
                    "uid": "88",
                    "date": "Sun, 05 Jul 2026 08:00:00 +0800",
                    "from": "Fixture Newsletter <sender@example.invalid>",
                    "subject": "Agent workflow update",
                    "snippet": "A useful agent workflow update.",
                    "email_type": "newsletter_article",
                    "links": [
                        {
                            "url": f"https://noise.example/{index}",
                            "context": noisy_context,
                            "anchor_text": "noise",
                        }
                        for index in range(60)
                    ],
                    "link_triage": {
                        "total_links": 60,
                        "hard_skipped_count": 50,
                        "candidate_group_count": 2,
                        "selected_fetch_count": 1,
                        "deferred_count": 1,
                        "deduped_count": 7,
                        "unmapped_topic_count": 1,
                        "groups": [
                            {
                                "decision": "fetch",
                                "reason": "fetch:topic_budget",
                                "canonical_url": "https://source.example/agent-workflow",
                                "score": 400,
                                "topics": [{"id": "ai", "name": "AI", "priority": "high"}],
                            },
                            {
                                "decision": "defer",
                                "reason": "defer:unmapped_topic",
                                "canonical_url": "https://noise.example/0",
                                "score": 0,
                                "topics": [],
                            },
                        ],
                    },
                    "evidence": [
                        {
                            "type": "public_link",
                            "url": "https://source.example/agent-workflow",
                            "final_url": "https://source.example/agent-workflow",
                            "title": "Agent workflow source",
                            "excerpt": "Evidence excerpt that the LLM should see.",
                            "status": "fetched",
                            "content_type": "text/html",
                        },
                        {
                            "type": "public_link",
                            "url": "https://noise.example/0",
                            "status": "skipped",
                            "reason": "defer:unmapped_topic",
                        },
                    ],
                    "risks": ["unmapped_alert_topic"],
                    "flags": [],
                    "topics": [{"id": "ai", "name": "AI", "priority": "high", "matched_keywords": ["agent"]}],
                }
            ],
        }

        raw_json = json.dumps(scan, ensure_ascii=False)
        compact = email_summary.llm_brief_input(scan)
        compact_json = json.dumps(compact, ensure_ascii=False)

        self.assertLess(len(compact_json), len(raw_json) // 3)
        self.assertIn("email_evidence_pack_llm_brief_input", compact_json)
        self.assertIn("Agent workflow source", compact_json)
        self.assertIn("Evidence excerpt that the LLM should see.", compact_json)
        self.assertNotIn(noisy_context, compact_json)
        self.assertNotIn("\"links\"", compact_json)
        self.assertNotIn("\"link_triage\"", compact_json)
        self.assertNotIn("\"public_link_coverage\"", compact_json)
        self.assertNotIn("\"risks\"", compact_json)
        self.assertNotIn("\"decision\"", compact_json)
        self.assertNotIn("\"reason\"", compact_json)
        self.assertNotIn("\"classification\"", compact_json)
        self.assertNotIn("\"decision_reason\"", compact_json)
        self.assertNotIn("defer:unmapped_topic", compact_json)
        self.assertNotIn("skipped", compact_json)

    def test_empty_scan_day_is_a_quiet_no_op_not_a_failure(self) -> None:
        """收件箱被清空的那天：不报错、不发送、不覆盖当天已有的 summary。

        review_checklist 无条件要求 key takeaway 与来源溯源，空日必假，
        send_report 会当场抛 RuntimeError。判空必须在写文件之前，不能等 checklist 兜。
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reports = tmp_path / "downloads" / "EmailReports"
            reports.mkdir(parents=True)
            existing = reports / "email-summary-2026-07-05.md"
            existing.write_text("# 昨天写好的 brief\n", encoding="utf-8")

            # 刻意不带 --no-send / --dry-run：真实那天走的就是这条路
            result = run_podsum(
                "email-summary",
                "--scan-file",
                str(ROOT / "tests" / "fixtures" / "email_summary_scans" / "email-scan-empty.json"),
                "--output",
                str(tmp_path / "downloads"),
            )

            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("RuntimeError", output)
            self.assertNotIn("failed Review Checklist", output)
            self.assertIn("no mail in window", output)
            self.assertEqual(existing.read_text(encoding="utf-8"), "# 昨天写好的 brief\n")
            # 没写就不许说写了：日志里出现一个不存在的路径，正是这类静默谎报
            self.assertNotIn("Wrote email summary", output)

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
            # 空日不写 brief：写了就覆盖当天那份好的，而空 brief 还过不了 review checklist
            self.assertFalse(report.exists())
            self.assertIn("no mail in window", result.stdout + result.stderr)

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
                "# Morning Brief - 2026-07-05\n\n"
                "## 今天先看\n\n"
                "- 需要人工确认：[UID 77](email://2026-07-05/77) Fixture actionable mail；fixture item should be reviewed.\n\n"
                "## 证据边界\n\n"
                "- 部分条目仅基于邮件摘要。\n"
                "EOF\n"
                "exit 0; fi\n"
                f"if [ \"$1\" = \"send\" ]; then printf '%s\\n' \"$@\" > {hermes_args}; echo 'sent'; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            hermes.chmod(0o755)

            result = run_podsum(
                "email-summary",
                "--target",
                "discord:test-target",
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
            self.assertTrue(
                any(email_summary.is_self_mail({"subject": line}, "") for line in send_args.splitlines()),
                "发出去的主题必须被扫描端的自发邮件判据认出来，否则 brief 会把自己读回来",
            )
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

    def capture_interpretation_prompt(
        self,
        tmp_path: Path,
        *,
        rules_text: Optional[str],
        template: Optional[str] = None,
    ) -> str:
        """Run one interpretation through a fake Hermes and return the prompt it received."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        prompt_file = tmp_path / "prompt.txt"
        hermes = tmp_path / "hermes"
        hermes.write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = \"-z\" ]; then printf '%s' \"$2\" > {prompt_file}; echo '解读正文'; exit 0; fi\n"
            "exit 2\n",
            encoding="utf-8",
        )
        hermes.chmod(0o755)

        prompt_path = tmp_path / "interpretation_prompt.md"
        prompt_path.write_text(
            sender.DEFAULT_INTERPRETATION_PROMPT.read_text(encoding="utf-8") if template is None else template,
            encoding="utf-8",
        )
        rules_path = tmp_path / "interpretation_rules.md"
        if rules_text is not None:
            rules_path.write_text(rules_text, encoding="utf-8")

        args = argparse.Namespace(
            memory_file=tmp_path / "missing-memory.md",
            interpretation_prompt=prompt_path,
            interpretation_rules=rules_path,
            hermes=hermes,
            project_dir=tmp_path,
            hermes_timeout=30,
        )
        info = {"podcast": "Fixture Show", "episode": "New Episode", "body": "Transcript body."}

        self.assertEqual(sender.hermes_interpretation(args, info), "解读正文")
        return prompt_file.read_text(encoding="utf-8")

    def test_interpretation_rules_file_is_injected_after_builtin_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompt = self.capture_interpretation_prompt(
                Path(tmp),
                rules_text="<!-- 一行一条，中文自然语言 -->\n- 这一集偏技术，多留代码细节。\n- 长度压到 600 字以内。\n",
            )

            self.assertIn(sender.INTERPRETATION_RULES_HEADER, prompt)
            self.assertIn("这一集偏技术，多留代码细节。", prompt)
            self.assertIn("长度压到 600 字以内。", prompt)
            self.assertNotIn("一行一条，中文自然语言", prompt)
            self.assertLess(
                prompt.index("不要编造文字稿里没有的信息"),
                prompt.index(sender.INTERPRETATION_RULES_HEADER),
            )
            self.assertLess(prompt.index(sender.INTERPRETATION_RULES_HEADER), prompt.index("Podcast: Fixture Show"))

    def test_missing_interpretation_rules_file_renders_prompt_without_rules_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompt = self.capture_interpretation_prompt(Path(tmp), rules_text=None)

            self.assertNotIn(sender.INTERPRETATION_RULES_HEADER, prompt)
            self.assertIn("Podcast: Fixture Show", prompt)
            self.assertIn("Transcript body.", prompt)

    def write_rules(self, tmp: str, text: str) -> Path:
        path = Path(tmp) / "interpretation_rules.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_comment_only_interpretation_rules_file_yields_no_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_rules(tmp, "<!--\n在下面写你的解读规则，一行一条。\n-->\n\n")

            self.assertEqual(sender.interpretation_rules_block(path), "")

    def test_interpretation_rules_block_strips_every_comment_and_keeps_rules_between_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_rules(tmp, "<!-- 说明头 -->\n- 保留这一条。\n<!-- 说明尾 -->\n")

            block = sender.interpretation_rules_block(path)

            self.assertEqual(block, f"{sender.INTERPRETATION_RULES_HEADER}\n- 保留这一条。")

    def test_overlong_interpretation_rules_are_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_rules(tmp, "规则" * sender.INTERPRETATION_RULES_EXCERPT_CHARS)

            block = sender.interpretation_rules_block(path)
            rules = block[len(sender.INTERPRETATION_RULES_HEADER) + 1 :]

            self.assertEqual(len(rules), sender.INTERPRETATION_RULES_EXCERPT_CHARS)

    def test_legacy_prompt_without_rules_placeholder_still_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompt = self.capture_interpretation_prompt(
                Path(tmp),
                rules_text="- 长度压到 600 字以内。\n",
                template="旧模板\n\nPodcast: {podcast}\nEpisode: {episode}\n\n{transcript}\n",
            )

            self.assertIn("Podcast: Fixture Show", prompt)
            self.assertNotIn("长度压到 600 字以内。", prompt)

    def test_shipped_interpretation_rules_file_is_empty_so_default_output_is_unchanged(self) -> None:
        self.assertIn("{rules}", sender.DEFAULT_INTERPRETATION_PROMPT.read_text(encoding="utf-8"))
        template = sender.DEFAULT_INTERPRETATION_RULES.with_suffix(".md.example")
        self.assertTrue(template.exists(), "规则模板必须随仓库发布")
        self.assertEqual(sender.interpretation_rules_block(template), "")
        # 真实文件不入库；缺席时必须与模板同样产出空串，否则新机器上的解读会变样。
        self.assertEqual(sender.interpretation_rules_block(Path("/nowhere/interpretation_rules.md")), "")

    def test_interpretation_rules_flag_defaults_to_the_shipped_file_in_both_entrypoints(self) -> None:
        self.assertEqual(
            podsum.build_parser().parse_args(["send"]).interpretation_rules,
            sender.DEFAULT_INTERPRETATION_RULES,
        )
        self.assertEqual(
            sender.build_parser().parse_args([]).interpretation_rules,
            sender.DEFAULT_INTERPRETATION_RULES,
        )

    def path_dests(self, parser: argparse.ArgumentParser, skip: frozenset = frozenset()) -> set:
        """收集一个 parser（含子命令）里所有 type=Path 的 dest。"""
        dests = set()
        for action in parser._actions:
            if action.type is Path:
                dests.add(action.dest)
            if isinstance(action, argparse._SubParsersAction):
                for name, subparser in action.choices.items():
                    if name not in skip:
                        dests |= self.path_dests(subparser, skip)
        return dests

    def test_path_args_table_covers_every_path_argument_the_entrypoints_own(self) -> None:
        # email-summary 自带 normalize_args，它的路径参数由该模块自己展开。
        delegated = frozenset({"email-summary"})

        self.assertEqual(self.path_dests(podsum.build_parser(), delegated), set(podsum.PATH_ARGS))
        self.assertEqual(self.path_dests(sender.build_parser()), set(sender.PATH_ARGS))

    def test_normalize_args_expands_tilde_on_every_path_argument(self) -> None:
        args = argparse.Namespace(**{name: Path("~/x") / name for name in podsum.PATH_ARGS})

        podsum.normalize_args(args)

        for name in podsum.PATH_ARGS:
            value = getattr(args, name)
            self.assertEqual(value, Path.home() / "x" / name, name)

    def test_normalize_args_skips_absent_and_optional_none_path_arguments(self) -> None:
        args = argparse.Namespace(interpretation_rules=Path("~/rules.md"), email_eml_dir=None)

        podsum.normalize_args(args)

        self.assertEqual(args.interpretation_rules, Path.home() / "rules.md")
        self.assertIsNone(args.email_eml_dir)
        self.assertFalse(hasattr(args, "state"))

    def test_send_ready_forwards_the_interpretation_rules_path_to_the_sender(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "show" / "episode.md"
            transcript.parent.mkdir(parents=True)
            transcript.write_text("body", encoding="utf-8")
            rules = Path(tmp) / "rules.md"
            args = argparse.Namespace(
                output=Path(tmp),
                state=Path(tmp) / "state.json",
                memory_file=Path(tmp) / "memory.md",
                interpretation_prompt=Path(tmp) / "prompt.md",
                interpretation_rules=rules,
                project_dir=Path(tmp),
                target="discord:1",
                hermes=Path(tmp) / "hermes",
                hermes_timeout=30,
            )
            state = {
                "episodes": {
                    "key": {
                        "status": "transcribed",
                        "transcript_path": str(transcript),
                        "transcript_sha256": podsum.sha256_file(transcript),
                        "attempts": {},
                    }
                }
            }
            seen = {}

            def fake_build_bundle(compatible, pending):
                seen["interpretation_rules"] = compatible.interpretation_rules
                raise RuntimeError("stop after wiring check")

            real_build_bundle, real_log = sender.build_bundle, podsum.log
            sender.build_bundle, podsum.log = fake_build_bundle, lambda message: None
            try:
                podsum.send_ready(args, state)
            finally:
                sender.build_bundle, podsum.log = real_build_bundle, real_log

            self.assertEqual(seen["interpretation_rules"], rules)

    def test_run_once_still_cleans_up_when_email_summary_fails(self) -> None:
        """邮件摘要失败不该吃掉 cleanup：播客链路已经跑完，保留策略必须照常执行。"""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(json.dumps({"episodes": {}, "feeds": {}}), encoding="utf-8")
            args = argparse.Namespace(
                state=state_path,
                skip_download=True,
                skip_transcribe=True,
                skip_send=True,
                email_summary=True,
                cleanup=True,
            )
            calls = []

            real_email, real_cleanup, real_log, real_from_podsum = (
                podsum.run_email_summary,
                podsum.cleanup_if_requested,
                podsum.log,
                podsum.email_summary_args_from_podsum,
            )
            podsum.run_email_summary = lambda _args: 1
            podsum.email_summary_args_from_podsum = lambda _args: _args
            podsum.cleanup_if_requested = lambda *_: calls.append("cleanup")
            podsum.log = lambda message: None
            try:
                result = podsum.run_once(args)
            finally:
                (
                    podsum.run_email_summary,
                    podsum.cleanup_if_requested,
                    podsum.log,
                    podsum.email_summary_args_from_podsum,
                ) = (real_email, real_cleanup, real_log, real_from_podsum)

            self.assertEqual(calls, ["cleanup"])
            self.assertNotEqual(result, 0)

    def test_resolve_target_prefers_cli_then_env_then_env_file(self) -> None:
        """投递目标的优先级链：CLI > 进程环境变量 > .env 文件。"""
        env_file = {"PODSUM_TARGET": "discord:from-file"}
        env_path = Path("/nowhere/.env")

        self.assertEqual(
            podsum_runtime.resolve_target("discord:from-cli", env_file, env_path),
            "discord:from-cli",
        )

        real = os.environ.get("PODSUM_TARGET")
        os.environ["PODSUM_TARGET"] = "discord:from-env"
        try:
            self.assertEqual(
                podsum_runtime.resolve_target("", env_file, env_path),
                "discord:from-env",
            )
            self.assertEqual(
                podsum_runtime.resolve_target("discord:from-cli", env_file, env_path),
                "discord:from-cli",
            )
        finally:
            if real is None:
                del os.environ["PODSUM_TARGET"]
            else:
                os.environ["PODSUM_TARGET"] = real

        self.assertEqual(
            podsum_runtime.resolve_target("", env_file, env_path),
            "discord:from-file",
        )

    def test_resolve_target_fails_loudly_when_unconfigured(self) -> None:
        """未配置投递目标必须报错，而不是回落到某个写死的默认频道。"""
        real = os.environ.pop("PODSUM_TARGET", None)
        try:
            with self.assertRaises(RuntimeError) as caught:
                podsum_runtime.resolve_target("", {}, Path("/nowhere/.env"))
        finally:
            if real is not None:
                os.environ["PODSUM_TARGET"] = real
        message = str(caught.exception)
        self.assertIn("PODSUM_TARGET", message)
        self.assertIn("/nowhere/.env", message)

    def test_target_cli_defaults_are_empty_on_every_entrypoint(self) -> None:
        """三个 entrypoint 的 --target 默认值都必须为空，否则 config 那一档永远读不到。"""
        def target_defaults(parser: argparse.ArgumentParser) -> list[Any]:
            found = [a.default for a in parser._actions if "--target" in a.option_strings]
            for action in parser._actions:
                for sub in getattr(action, "choices", {}).values() if isinstance(getattr(action, "choices", None), dict) else []:
                    found.extend(target_defaults(sub))
            return found

        for parser in (podsum.build_parser(), sender.build_parser(), email_summary.build_parser()):
            defaults = target_defaults(parser)
            self.assertTrue(defaults, "entrypoint is missing --target")
            for default in defaults:
                self.assertEqual(default, "")

    def test_runtime_dependencies_are_installed_and_epub_is_not_degraded(self) -> None:
        """冒烟断言：runtime 组的依赖真的可用，且 EPUB 没有走降级实现。

        不做「扫 import 和 manifest 比对」的守卫测试：代码里的第三方 import 全部
        包在 try/except 里，漏装不报错而是静默降级，比对清单测的是错的对象；而且
        import 名与发行名多数对不上，outputs/email 又和 stdlib email 同名。
        直接测真正关心的那个属性最短也最准。
        """
        import tomllib

        declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("runtime", declared["dependency-groups"])

        for module in ("langgraph", "langchain_core", "ebooklib", "pygments"):
            with self.subTest(module=module):
                __import__(module)

        from podsum_core.epub_converter import epub_generator

        self.assertIsNotNone(
            epub_generator.epub,
            "ebooklib 未装：EPUB 会静默走 _write_minimal_epub 降级路径。跑 pip install --group runtime",
        )

    def test_deployment_root_defaults_follow_podsum_home(self) -> None:
        """部署根只能有一处解析。各模块各写死一份，PODSUM_HOME 就会装出半残部署：
        装在 A、却从 B 读配置往 B 写状态，而且零报错。

        必须在子进程里验证：这些默认值是 import 期算出来的模块常量。
        """
        probe = textwrap.dedent(
            """
            import json, sys
            sys.path.insert(0, sys.argv[1])
            import podsum, podsum_runtime
            import podsum_email_summary as email_summary
            import podsum_send_to_feishu as sender
            print(json.dumps({
                "home": str(podsum_runtime.podsum_home()),
                "podsum_state": str(podsum.DEFAULT_STATE_FILE),
                "email_state": str(email_summary.DEFAULT_STATE_FILE),
                "email_env": str(email_summary.DEFAULT_ENV_FILE),
                "sender_state": str(sender.DEFAULT_STATE_FILE),
            }))
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "elsewhere"
            env = dict(os.environ, PODSUM_HOME=str(home))
            result = subprocess.run(
                [sys.executable, "-c", probe, str(ROOT / "outputs")],
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            paths = json.loads(result.stdout)

            self.assertEqual(paths["home"], str(home))
            self.assertEqual(paths["podsum_state"], str(home / "state.json"))
            self.assertEqual(paths["email_state"], str(home / "state.json"))
            self.assertEqual(paths["email_env"], str(home / ".env"))
            self.assertEqual(paths["sender_state"], str(home / "feishu_sent.json"))

    @staticmethod
    def _smtp_args(env_file: Path, **overrides: Any) -> argparse.Namespace:
        base = dict(
            env_file=env_file,
            imap_host="", imap_user="", imap_pass="",
            smtp_host="", smtp_port=0, smtp_user="", smtp_pass="",
            smtp_from="", smtp_to="", smtp_starttls=False,
            smtp_no_ssl=False, smtp_no_tls_verify=False, smtp_timeout=0,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_split_recipients_accepts_comma_semicolon_and_whitespace(self) -> None:
        self.assertEqual(email_summary.split_recipients("a@x.com, b@x.com"), ["a@x.com", "b@x.com"])
        self.assertEqual(email_summary.split_recipients("a@x.com;b@x.com"), ["a@x.com", "b@x.com"])
        self.assertEqual(email_summary.split_recipients("a@x.com b@x.com"), ["a@x.com", "b@x.com"])
        self.assertEqual(email_summary.split_recipients(" a@x.com ,; b@x.com\n"), ["a@x.com", "b@x.com"])
        self.assertEqual(email_summary.split_recipients(""), [])

    def test_infer_smtp_host_falls_back_through_imap_host_then_user_domain(self) -> None:
        self.assertEqual(email_summary.infer_smtp_host("imap.example.com"), "smtp.example.com")
        self.assertEqual(email_summary.infer_smtp_host("mail-imap.example.com"), "mail-smtp.example.com")
        self.assertEqual(email_summary.infer_smtp_host("", "me@example.com"), "smtp.example.com")
        self.assertEqual(email_summary.infer_smtp_host("", "no-at-sign"), "")

    def test_smtp_config_prefers_cli_then_env_then_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "PODSUM_EMAIL_SMTP_HOST=smtp.from-file\nPODSUM_EMAIL_SMTP_TO=file@x.com\n",
                encoding="utf-8",
            )

            config = email_summary.smtp_config(self._smtp_args(env_file))
            self.assertEqual(config["host"], "smtp.from-file")
            self.assertEqual(config["recipients"], ["file@x.com"])

            real = os.environ.get("PODSUM_EMAIL_SMTP_HOST")
            os.environ["PODSUM_EMAIL_SMTP_HOST"] = "smtp.from-env"
            try:
                self.assertEqual(email_summary.smtp_config(self._smtp_args(env_file))["host"], "smtp.from-env")
                self.assertEqual(
                    email_summary.smtp_config(self._smtp_args(env_file, smtp_host="smtp.from-cli"))["host"],
                    "smtp.from-cli",
                )
            finally:
                if real is None:
                    del os.environ["PODSUM_EMAIL_SMTP_HOST"]
                else:
                    os.environ["PODSUM_EMAIL_SMTP_HOST"] = real

    def test_send_report_with_email_delivery_uses_smtp_and_never_hermes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_file = tmp_path / ".env"
            env_file.write_text("PODSUM_EMAIL_SMTP_TO=to@x.com\n", encoding="utf-8")
            report = tmp_path / "brief.md"
            report.write_text("# Brief\n\n要点一。\n", encoding="utf-8")
            args = self._smtp_args(
                env_file,
                delivery="email",
                dry_run=False,
                smtp_host="smtp.example.com",
                smtp_from="from@x.com",
            )
            scan = {"date": "2026-08-21", "account": "me@x.com", "window": "1d", "raw_count": 3}
            sent = {}

            def fake_smtp(**kwargs: Any) -> str:
                sent.update(kwargs)
                return "sent email to 1 recipient(s)"

            def exploding_hermes(*_args: Any, **_kwargs: Any) -> str:
                raise AssertionError("delivery=email 不该走 Hermes")

            real = (
                email_summary.send_smtp_email,
                email_summary.send_hermes_file,
                email_summary.review_checklist,
                email_summary.log,
            )
            email_summary.send_smtp_email = fake_smtp
            email_summary.send_hermes_file = exploding_hermes
            email_summary.review_checklist = lambda *_: {"ready_to_send": True, "risks": []}
            email_summary.log = lambda message: None
            try:
                result = email_summary.send_report(args, report, scan)
            finally:
                (
                    email_summary.send_smtp_email,
                    email_summary.send_hermes_file,
                    email_summary.review_checklist,
                    email_summary.log,
                ) = real

            self.assertIsNone(result, "email 投递不产出 EPUB")
            self.assertEqual(sent["recipients"], ["to@x.com"])
            self.assertEqual(sent["host"], "smtp.example.com")
            self.assertIn("2026-08-21", sent["subject"])
            self.assertTrue(
                email_summary.is_self_mail({"subject": sent["subject"]}, ""),
                "投递目标就是被扫描的邮箱，主题必须被扫描端认成自发邮件，否则 brief 会把自己读回来",
            )
            self.assertIn("<html>", sent["html_body"])

    def test_delivery_cli_defaults_are_empty_on_every_entrypoint(self) -> None:
        """--delivery 默认值非空会踩和 --target 同一个坑：config 那一档永远读不到。"""
        def delivery_defaults(parser: argparse.ArgumentParser) -> list[Any]:
            found = [
                a.default
                for a in parser._actions
                if {"--delivery", "--email-delivery"} & set(a.option_strings)
            ]
            for action in parser._actions:
                choices = getattr(action, "choices", None)
                if isinstance(choices, dict):
                    for sub in choices.values():
                        found.extend(delivery_defaults(sub))
            return found

        for parser in (podsum.build_parser(), email_summary.build_parser()):
            defaults = delivery_defaults(parser)
            self.assertTrue(defaults, "entrypoint is missing --delivery")
            for default in defaults:
                self.assertEqual(default, "")

    def test_resolve_delivery_prefers_cli_then_env_then_env_file_then_hermes(self) -> None:
        env_file = {"PODSUM_EMAIL_DELIVERY": "email"}
        self.assertEqual(podsum_runtime.resolve_delivery("hermes", env_file), "hermes")
        self.assertEqual(podsum_runtime.resolve_delivery("", env_file), "email")
        self.assertEqual(podsum_runtime.resolve_delivery("", {}), "hermes")

        real = os.environ.get("PODSUM_EMAIL_DELIVERY")
        os.environ["PODSUM_EMAIL_DELIVERY"] = "email"
        try:
            self.assertEqual(podsum_runtime.resolve_delivery("", {}), "email")
            self.assertEqual(podsum_runtime.resolve_delivery("hermes", {}), "hermes")
        finally:
            if real is None:
                del os.environ["PODSUM_EMAIL_DELIVERY"]
            else:
                os.environ["PODSUM_EMAIL_DELIVERY"] = real

    def test_email_summary_switch_can_be_turned_on_from_env_file(self) -> None:
        """邮件摘要开关移出 plist：.env 里打开也算数，CLI 给了就无条件生效。"""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("PODSUM_EMAIL_SUMMARY=true\n", encoding="utf-8")
            args = argparse.Namespace(email_summary=False, email_env_file=env_file)
            self.assertTrue(podsum.email_summary_requested(args))

            env_file.write_text("PODSUM_EMAIL_SUMMARY=false\n", encoding="utf-8")
            self.assertFalse(podsum.email_summary_requested(args))
            self.assertTrue(
                podsum.email_summary_requested(
                    argparse.Namespace(email_summary=True, email_env_file=env_file)
                )
            )

            missing = Path(tmp) / "nope.env"
            self.assertFalse(
                podsum.email_summary_requested(
                    argparse.Namespace(email_summary=False, email_env_file=missing)
                )
            )

    def test_scan_payload_drops_podsum_own_mail(self) -> None:
        """投递目标和扫描邮箱是同一个地址时，brief 会把自己读回来，逐日放大。"""
        items = [
            {"uid": "1", "from": "hdhopenclaw@17ms.com", "subject": "SMTP Connection Test"},
            {"uid": "2", "from": "Podsum <hdhopenclaw@17ms.com>", "subject": "[Podsum] 2026-08-21 Email Brief"},
            {"uid": "3", "from": "relay@example.com", "subject": "[Podsum] 2026-07-05 Email Brief"},
            {"uid": "4", "from": "The Rundown AI <news@daily.therundown.ai>", "subject": "Slack turns coding into a group project"},
        ]
        payload = email_summary.scan_payload("hdhopenclaw@17ms.com", 1, 20, len(items), list(items))

        self.assertEqual([item["uid"] for item in payload["items"]], ["4"])
        self.assertEqual(payload["raw_count"], 4, "raw_count 是扫到的原始数量，不该被过滤改写")
        self.assertEqual(payload["dropped_self_count"], 3)

    def test_scan_payload_keeps_external_mail_from_a_lookalike_address(self) -> None:
        """只按域名或前缀猜会误伤：地址不同、主题不带前缀的外部邮件必须留下。"""
        items = [
            {"uid": "1", "from": "notifications@17ms.com", "subject": "月度账单"},
            {"uid": "2", "from": "hdhopenclaw@17ms.com.evil.test", "subject": "你好"},
        ]
        payload = email_summary.scan_payload("hdhopenclaw@17ms.com", 1, 20, len(items), list(items))
        self.assertEqual([item["uid"] for item in payload["items"]], ["1", "2"])
        self.assertEqual(payload["dropped_self_count"], 0)

    def test_scan_payload_without_account_drops_nothing_by_address(self) -> None:
        """离线 fixture 扫描没有真实账号，只能靠主题前缀，不能把所有邮件都当成自己的。"""
        items = [
            {"uid": "1", "from": "someone@example.com", "subject": "普通邮件"},
            {"uid": "2", "from": "someone@example.com", "subject": "[Podsum] 2026-07-05 Email Brief"},
        ]
        payload = email_summary.scan_payload("", 1, 20, len(items), list(items))
        self.assertEqual([item["uid"] for item in payload["items"]], ["1"])


class DigestRenderingTest(unittest.TestCase):
    """digest 类邮件自己没有内容，内容就是那张链接列表。压成一行等于全部丢掉。"""

    @staticmethod
    def _alert(
        uid: str,
        date: str,
        links: list[tuple[str, str, str]],
        subject: str = "Google快讯 - RWA",
    ) -> dict[str, object]:
        return {
            "uid": uid,
            "date": date,
            "from": "Google Alerts <googlealerts-noreply@google.com>",
            "subject": subject,
            "snippet": "RWA 相关的新报道",
            "email_type": "google_alert",
            "has_attachments": False,
            "links": [
                {
                    "anchor_text": anchor_text,
                    "context": context,
                    "url": url,
                    "normalized_url": url,
                    "policy_decision": "pending",
                }
                for anchor_text, url, context in links
            ],
            "evidence": [],
            "risks": [],
            "flags": [],
            "topics": [],
        }

    @staticmethod
    def _render_scan(*items: dict[str, object]) -> str:
        return email_summary.build_intel_brief_draft(
            {
                "date": "2026-08-21",
                "account": "fixture@example.invalid",
                "window": "2d",
                "scan_limit": 20,
                "raw_count": len(items),
                "possibly_truncated": False,
                "items": list(items),
            }
        )

    def _render(self) -> str:
        shared = ("RWA 在 DeFi 活跃资金逼近 40 亿美元", "https://example.invalid/rwa-defi", "据 DefiLlama 数据，规模升至约 39.8 亿美元。")
        return self._render_scan(
            self._alert("1329", "Wed, 19 Aug 2026 08:00:00 +0800", [
                shared,
                ("OneLife 链上全球购落地", "https://example.invalid/onelife", "RWA 从金融资产代币化迈向实体经济。"),
            ]),
            self._alert("1335", "Thu, 20 Aug 2026 08:00:00 +0800", [
                shared,
                ("X Layer 推 RWA 流动性激励", "https://example.invalid/xlayer", "总激励规模达 500 万美元。"),
            ]),
        )

    def test_same_subject_digests_merge_into_one_deduped_list(self) -> None:
        markdown = self._render()

        # 三个唯一 URL 全在，共享的那个只出现一次——按 subject 判重会整封丢掉一整批条目
        for url in ("https://example.invalid/rwa-defi", "https://example.invalid/onelife", "https://example.invalid/xlayer"):
            with self.subTest(url=url):
                self.assertEqual(markdown.count(f"]({url})"), 1, markdown)

    def test_each_link_is_one_line_with_anchor_and_context(self) -> None:
        markdown = self._render()

        line = next((l for l in markdown.splitlines() if "https://example.invalid/xlayer" in l), "")
        self.assertTrue(line.startswith("- "), markdown)
        self.assertIn("[X Layer 推 RWA 流动性激励](https://example.invalid/xlayer)", line)
        self.assertIn("总激励规模达 500 万美元", line)

    def test_digest_gets_its_own_section_instead_of_competing_for_top_five(self) -> None:
        markdown = self._render()

        self.assertIn("Google快讯 - RWA", markdown)
        top = markdown.split("## ")[1] if "## " in markdown else markdown
        self.assertNotIn("example.invalid", top, "digest 链接不该挤进今天先看")

    def test_merged_digest_keeps_every_contributing_email_traceable(self) -> None:
        """合并去重之后仍要追得回是哪几封邮件带来的，否则 digest 区没有溯源。"""
        markdown = self._render()

        self.assertIn("[UID 1329](email://2026-08-21/1329)", markdown)
        self.assertIn("[UID 1335](email://2026-08-21/1335)", markdown)

    def test_pending_links_are_still_rendered_as_click_targets(self) -> None:
        markdown = self._render()

        # policy_decision 全是 pending：指向来源本身就是它的用途，不是未验证论断
        self.assertIn("](https://example.invalid/rwa-defi)", markdown)

    def test_different_subjects_stay_in_separate_lists(self) -> None:
        """不同快讯倒进同一张列表，读者就分不清哪条来自哪份订阅。"""
        markdown = self._render_scan(
            self._alert("1400", "Thu, 20 Aug 2026 08:00:00 +0800", [
                ("RWA 条目", "https://example.invalid/rwa", "一"),
            ]),
            self._alert("1401", "Thu, 20 Aug 2026 09:00:00 +0800", [
                ("稳定币条目", "https://example.invalid/stable", "二"),
            ], subject="Google快讯 - 稳定币"),
        )

        section = markdown.split("## 订阅摘要")[1]
        self.assertIn("### Google快讯 - RWA", section)
        self.assertIn("### Google快讯 - 稳定币", section)

    def test_bare_links_render_without_empty_titles_or_dangling_dashes(self) -> None:
        """anchor_text 缺失时标题退回 url；没有 context 时不留一条空破折号尾巴。"""
        markdown = self._render_scan(
            self._alert("1402", "Thu, 20 Aug 2026 08:00:00 +0800", [
                ("", "https://example.invalid/bare", ""),
            ]),
        )

        section = markdown.split("## 订阅摘要")[1]
        self.assertIn("- [https://example.invalid/bare](https://example.invalid/bare)", section)
        self.assertNotIn("—", section)

    def test_malformed_links_are_skipped_instead_of_rendered(self) -> None:
        """扫描产物不保证每个 link 都是带地址的 dict，渲染不能因此崩掉或输出空链接。"""
        item = self._alert("1403", "Thu, 20 Aug 2026 08:00:00 +0800", [
            ("有效条目", "https://example.invalid/ok", "正常"),
        ])
        item["links"] = ["not-a-dict", {"anchor_text": "空地址", "normalized_url": "   "}] + list(item["links"])

        section = self._render_scan(item).split("## 订阅摘要")[1]
        self.assertIn("](https://example.invalid/ok)", section)
        self.assertNotIn("空地址", section)


class LlmBriefTest(unittest.TestCase):
    """podsum 引擎的 brief 必须由 LLM 写；模板是失败时的兜底，不是常态。"""

    SCAN = {
        "object_type": "email_evidence_pack",
        "object_version": "1",
        "status": "ready_for_summary",
        "date": "2026-08-21",
        "account": "fixture@example.invalid",
        "window": "1d",
        "scan_limit": 10,
        "raw_count": 1,
        "possibly_truncated": False,
        "items": [
            {
                "uid": "77",
                "date": "Fri, 21 Aug 2026 08:00:00 +0800",
                "from": "Someone <someone@example.invalid>",
                "subject": "Fixture Follow-up",
                "snippet": "需要回复的邮件",
                "email_type": "personal",
                "links": [],
                "evidence": [],
                "risks": [],
                "flags": [],
            }
        ],
    }

    def _writer(self, outcome):
        calls = []

        def fake_hermes(_binary, prompt, cwd=None, timeout=None):
            calls.append(prompt)
            return outcome

        return fake_hermes, calls

    def _render(self, outcome, reason=""):
        fake, calls = self._writer(outcome)
        real = email_summary.run_hermes_prompt
        email_summary.run_hermes_prompt = fake
        try:
            markdown = email_summary.llm_brief_markdown(
                self.SCAN,
                hermes="/nonexistent/hermes",
                prompt_path=EMAIL_SUMMARY_PROMPT,
                project_dir=ROOT,
                timeout=5,
                reason=reason,
            )
        finally:
            email_summary.run_hermes_prompt = real
        return markdown, calls

    def _compose(self, writer):
        pack = EmailEvidencePack.from_dict(self.SCAN)
        return brief_agent.compose_with_need_store(
            pack, {}, email_summary.empty_need_store(), "", {}, "dry-run: skipped", writer
        ).email_intel_brief.markdown

    def test_no_writer_means_deterministic_template(self) -> None:
        """writer 为空就是 dry-run 那条路：不调 LLM，输出确定。"""
        markdown = self._compose(None)

        self.assertIn("Fixture Follow-up", markdown)
        self.assertNotIn("dry-run", markdown, "内部处理用语不进正文")

    def test_writer_is_what_produces_the_brief_when_present(self) -> None:
        """装了 writer 就必须由它写，模板退到兜底位置。"""
        seen = []

        def writer(scan_dict, reason):
            seen.append(reason)
            return "# Morning Brief - 2026-08-21\n\n只有 LLM 会这么写。\n"

        markdown = self._compose(writer)

        self.assertEqual(seen, ["dry-run: skipped"])
        self.assertIn("只有 LLM 会这么写", markdown)
        self.assertNotIn("Fixture Follow-up", markdown, "不该再走模板")

    def test_llm_output_becomes_the_brief(self) -> None:
        markdown, calls = self._render((True, "# Morning Brief - 2026-08-21\n\n## 今天先看\n\n- LLM 写的判断。[UID 77](email://2026-08-21/77)\n"))

        self.assertEqual(len(calls), 1, "必须真的调用 LLM")
        self.assertIn("LLM 写的判断", markdown)
        self.assertIn("Fixture Follow-up", calls[0], "EvidencePack JSON 要进 prompt")
        self.assertIn("逐条展开", calls[0], "prompt 文件里的规则要进去")

    def test_llm_failure_falls_back_to_template_and_says_so(self) -> None:
        markdown, calls = self._render((False, "hermes exited 1"))

        self.assertEqual(len(calls), 1)
        self.assertIn("# Morning Brief - 2026-08-21", markdown)
        self.assertIn("Fixture Follow-up", markdown, "回落到确定性模板")
        self.assertIn("降级", markdown, "降级必须写进 brief，否则没人知道今天这份是模板")

    def test_empty_llm_output_also_falls_back(self) -> None:
        markdown, _ = self._render((True, "   "))

        self.assertIn("Fixture Follow-up", markdown)
        self.assertIn("降级", markdown)

    @staticmethod
    def _args(dry_run: bool) -> argparse.Namespace:
        return argparse.Namespace(
            dry_run=dry_run,
            hermes=Path("/nonexistent/hermes"),
            email_summary_prompt=EMAIL_SUMMARY_PROMPT,
            project_dir=ROOT,
            hermes_timeout=5,
        )

    def test_dry_run_gets_no_writer_so_it_never_reaches_the_llm(self) -> None:
        """dry-run 一旦拿到 writer 就会真去调 Hermes，输出也不再确定。"""
        self.assertIsNone(email_summary.make_brief_writer(self._args(dry_run=True)))

    def test_real_run_gets_a_writer_that_routes_to_the_llm(self) -> None:
        writer = email_summary.make_brief_writer(self._args(dry_run=False))
        seen: dict[str, object] = {}
        real = email_summary.llm_brief_markdown

        def fake(scan, **kwargs):
            seen.update(kwargs)
            return "写好了"

        email_summary.llm_brief_markdown = fake
        try:
            self.assertEqual(writer(self.SCAN, "内部原因"), "写好了")
        finally:
            email_summary.llm_brief_markdown = real

        self.assertEqual(seen["prompt_path"], EMAIL_SUMMARY_PROMPT)
        self.assertEqual(seen["reason"], "内部原因", "内部原因要一路带到兜底模板")

    def test_notice_is_user_facing_while_reason_stays_internal(self) -> None:
        """降级要让用户看见，但内部处理用语不能进正文——既有契约禁止暴露处理流程。"""
        internal = email_summary.build_intel_brief_draft(self.SCAN, "dry-run: skipped Hermes summary")
        self.assertNotIn("dry-run", internal)

        surfaced = email_summary.build_intel_brief_draft(self.SCAN, "dry-run: skipped", notice="降级：本篇由确定性模板产出。")
        self.assertIn("降级：本篇由确定性模板产出。", surfaced)
        self.assertNotIn("dry-run", surfaced)


class EditableFilesDocTest(unittest.TestCase):
    """文档写死的链接上限必须跟代码一致，否则读者按文档调完发现没生效。"""

    HEADING = "## Editable Email Summary Files"

    def _section(self) -> str:
        readme = OUTPUTS_README.read_text(encoding="utf-8")
        self.assertIn(self.HEADING, readme)
        return readme.split(self.HEADING, 1)[1].split("\n## ", 1)[0]

    @staticmethod
    def _tracked(path: str) -> str:
        return subprocess.run(
            ["git", "ls-files", path], cwd=str(ROOT), text=True, capture_output=True
        ).stdout

    def test_documented_link_limits_match_the_default_policy(self) -> None:
        section = self._section()
        limits = email_summary.DEFAULT_POLICY["limits"]

        self.assertIn(f"{limits['max_links_per_email']} links per email", section)
        self.assertIn(f"{limits['max_links_total']} links in total", section)

    def test_the_three_editable_files_are_all_named(self) -> None:
        section = self._section()

        for name in ("email_summary_prompt.md", "topic.md", "email_link_policy.md"):
            with self.subTest(name=name):
                self.assertIn(name, section)

        # 三个文件的归属不同，文档说反了读者就会去改错的那份
        self.assertTrue((ROOT / "outputs" / "email_summary_prompt.md").exists(), "prompt 直接入库，安装时会被覆盖")
        for user_file in ("topic.md", "email_link_policy.md"):
            with self.subTest(user_file=user_file):
                self.assertTrue((ROOT / "outputs" / f"{user_file}.example").exists())
                self.assertEqual(self._tracked(f"outputs/{user_file}"), "", "用户文件不该入库，否则同步会盖掉界面上的编辑")

    def test_every_flag_the_section_documents_really_exists(self) -> None:
        """flag 改名而 README 不动，读者照着敲只会得到 argparse 报错。"""
        documented = sorted(set(re.findall(r"--[a-z][a-z0-9-]+", self._section())))
        help_text = run_podsum("email-summary", "--help").stdout

        self.assertTrue(documented, "这一节本来就该给出可照抄的命令")
        for flag in documented:
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)


if __name__ == "__main__":
    unittest.main()
