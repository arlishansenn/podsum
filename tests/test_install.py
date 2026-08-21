import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.sh"


def run_install(home: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ, PODSUM_HOME=str(home))
    merged.update(env or {})
    return subprocess.run(
        ["/bin/bash", str(INSTALL), *args],
        cwd=str(ROOT),
        env=merged,
        text=True,
        capture_output=True,
        # PATH 被剥掉后 bash 的本地化报错未必是 UTF-8，别让解码问题掩盖真正的断言
        errors="replace",
    )


class InstallTest(unittest.TestCase):
    """整脚本黑盒。只断言退出码、stdout 和跑完之后的目录状态。"""

    def setUp(self) -> None:
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = self.tmp / "home"

    def install(self, *args: str, **kw) -> subprocess.CompletedProcess[str]:
        return run_install(self.home, "--skip-venv", "--skip-node", "--skip-launchd", *args, **kw)

    def test_syncs_tracked_application_files_only(self) -> None:
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = self.home / "outputs"
        self.assertTrue((out / "podsum.py").exists())
        self.assertTrue((out / "podsum_core" / "delivery" / "smtp_adapter.py").exists())
        # 仓库里这些目录不属于部署内容
        for stray in ("tests", "plans", "docs", "swarmforge", "mse435_transcripts"):
            self.assertFalse((self.home / stray).exists(), f"{stray} 不该被同步")
        self.assertFalse((out / "node_modules").exists(), "node_modules 不在追踪集内")

    def test_generates_user_content_from_templates_and_never_overwrites_them(self) -> None:
        self.install()
        out = self.home / "outputs"
        topic = out / "topic.md"
        self.assertTrue(topic.exists(), "缺席时应从 .example 生成")

        topic.write_text("# 我改过的话题\n", encoding="utf-8")
        (out / "feeds.json").write_text('{"feeds": []}', encoding="utf-8")
        self.install()
        self.assertEqual(topic.read_text(encoding="utf-8"), "# 我改过的话题\n")
        self.assertEqual((out / "feeds.json").read_text(encoding="utf-8"), '{"feeds": []}')

    def test_creates_env_skeleton_owner_readable_and_lists_missing_values(self) -> None:
        result = self.install()
        env_path = self.home / ".env"
        self.assertTrue(env_path.exists())
        self.assertEqual(oct(env_path.stat().st_mode)[-3:], "600")
        self.assertIn("PODSUM_TARGET", result.stdout)

        env_path.write_text("PODSUM_TARGET=discord:mine\n", encoding="utf-8")
        self.install()
        self.assertEqual(env_path.read_text(encoding="utf-8"), "PODSUM_TARGET=discord:mine\n")

    def test_never_touches_runtime_state(self) -> None:
        self.home.mkdir(parents=True)
        state = self.home / "state.json"
        state.write_text('{"episodes": {}}', encoding="utf-8")
        self.install()
        self.assertEqual(state.read_text(encoding="utf-8"), '{"episodes": {}}')

    def test_receipt_deletes_only_files_it_installed(self) -> None:
        self.install()
        out = self.home / "outputs"
        zombie = out / "gone_from_repo.py"
        zombie.write_text("# 假装上一轮装过\n", encoding="utf-8")
        receipt = self.home / ".podsum-install-receipt"
        receipt.write_text(receipt.read_text(encoding="utf-8") + "outputs/gone_from_repo.py\n", encoding="utf-8")

        stranger = out / "not_ours.txt"
        stranger.write_text("别人的东西\n", encoding="utf-8")

        result = self.install()
        self.assertFalse(zombie.exists(), "回执里有、追踪集里没有的必须删掉")
        self.assertTrue(stranger.exists(), "从未进过回执的一律不碰")
        self.assertIn("not_ours.txt", result.stdout, "认不出来的要列出来")

    def test_user_content_never_enters_the_receipt(self) -> None:
        self.install()
        receipt = (self.home / ".podsum-install-receipt").read_text(encoding="utf-8")
        for name in ("outputs/topic.md", "outputs/email_link_policy.md", "outputs/interpretation_rules.md", "outputs/feeds.json"):
            self.assertNotIn(name + "\n", receipt, f"{name} 进了回执，仓库一次改名就会带走用户编辑")

    def test_preflight_failure_prints_a_copyable_command(self) -> None:
        fake_bin = self.tmp / "bin"
        fake_bin.mkdir()
        result = run_install(self.home, "--skip-venv", "--skip-node", "--skip-launchd", env={"PATH": f"{fake_bin}:/usr/bin:/bin"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("brew install", result.stdout + result.stderr)

    def test_running_twice_changes_nothing_the_second_time(self) -> None:
        self.install()
        before = sorted((p.relative_to(self.home), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file())
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        after = sorted((p.relative_to(self.home), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file())
        self.assertEqual([n for n, _ in before], [n for n, _ in after])

    def test_shipped_plist_carries_the_imap_confirmation_flag(self) -> None:
        """确认开关刻意不移进 .env，那它就必须由 plist 携带，否则定时任务永远读不了邮箱。"""
        plist = (ROOT / "outputs" / "com.local.podsum.plist").read_text(encoding="utf-8")
        self.assertIn("<string>--email-allow-imap-read</string>", plist)
        # 反过来：会变成「一次设置永久生效」的开关不该写死在 plist 里
        for externalized in ("--target", "--email-summary</string>", "--email-delivery"):
            self.assertNotIn(externalized, plist, f"{externalized} 应当由 .env 提供")


if __name__ == "__main__":
    unittest.main()
