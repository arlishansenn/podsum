import sys
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outputs"))

import transcript_cleaner


class TranscriptCleanerTest(unittest.TestCase):
    def test_removes_intra_sentence_short_gap_repeat(self) -> None:
        text = (
            "是的。你知道，这周我们看到 Mythos——嗯，"
            "是的。你知道，这周我们看到 Mythos——这是 Anthropic 尚未发布的模型。"
        )
        cleaned, stats = transcript_cleaner.clean_text(text)
        self.assertEqual(cleaned.count("是的。你知道，这周我们看到 Mythos——"), 1)
        self.assertGreater(stats.intra_sentence_gap_chars_removed, 0)

    def test_removes_embedded_sentence_block_repeat(self) -> None:
        cleaned, stats = transcript_cleaner.clean_text("有人说：“A。B。C。A。B。C。”然后继续。")
        self.assertEqual(cleaned, "有人说：“A。B。C。”然后继续。\n")
        self.assertEqual(stats.removed_sentence_units, 3)

    def test_removes_adjacent_sentence_block_copies(self) -> None:
        cleaned, stats = transcript_cleaner.clean_text("A。B。C。A。B。C。A。B。C。D。")
        self.assertEqual(cleaned, "A。B。C。D。\n")
        self.assertEqual(stats.removed_sentence_units, 6)
        self.assertEqual(stats.repeated_blocks_removed, 2)

    def test_does_not_remove_non_adjacent_repetition(self) -> None:
        text = "A。B。C。插一句。A。B。C。"
        cleaned, stats = transcript_cleaner.clean_text(text)
        self.assertEqual(cleaned, text + "\n")
        self.assertEqual(stats.removed_sentence_units, 0)

    def test_merges_adjacent_clauses_with_shared_prefix(self) -> None:
        result = transcript_cleaner.clean_text("这个系统可以降低成本，这个系统可以提高效率。")
        self.assertEqual(result.text, "这个系统可以降低成本，提高效率。\n")
        self.assertEqual(result.stats.shared_prefix_merges, 1)
        self.assertEqual(result.edits[0].type, "shared_prefix_coordination")

    def test_shared_prefix_merge_preserves_both_tails(self) -> None:
        result = transcript_cleaner.clean_text("这个系统可以降低成本，这个系统可以提高效率。")
        self.assertIn("降低成本", result.text)
        self.assertIn("提高效率", result.text)

    def test_does_not_merge_prefix_shorter_than_five_chinese_chars(self) -> None:
        text = "系统降低成本，系统提高效率。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")
        self.assertEqual(result.stats.shared_prefix_merges, 0)

    def test_removes_high_confidence_prefix_restart(self) -> None:
        result = transcript_cleaner.clean_text("因此带来了大量xxxxxx，因此带来了大量客户投诉。")
        self.assertEqual(result.text, "因此带来了大量客户投诉。\n")
        self.assertEqual(result.stats.prefix_restarts_removed, 1)
        self.assertEqual(result.edits[0].type, "prefix_restart")

    def test_uncertain_prefix_restart_uses_lossless_coordination(self) -> None:
        result = transcript_cleaner.clean_text("因此带来了大量成本，因此带来了大量客户投诉。")
        self.assertEqual(result.text, "因此带来了大量成本，客户投诉。\n")
        self.assertIn("成本", result.text)
        self.assertIn("客户投诉", result.text)

    def test_does_not_cut_latin_word_at_shared_prefix(self) -> None:
        text = "有些人在用 CoreWeave，有些人在用 Crusoe。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, "有些人在用 CoreWeave，Crusoe。\n")
        self.assertIn("Crusoe", result.text)

    def test_reports_unsafe_chinese_prefix_without_modifying(self) -> None:
        text = "或者我们想改变它，或者我们想要其他定义。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")
        self.assertTrue(any(edit.type == "shared_prefix_candidate" for edit in result.edits))

    def test_removes_identical_adjacent_clauses(self) -> None:
        result = transcript_cleaner.clean_text("如今的瓶颈是 AI，如今的瓶颈是 AI。")
        self.assertEqual(result.text, "如今的瓶颈是 AI。\n")
        self.assertEqual(result.stats.adjacent_clause_repeats, 1)

    def test_keeps_longer_prefix_extension(self) -> None:
        result = transcript_cleaner.clean_text(
            "如今的瓶颈是 AI，如今的瓶颈是 AI——考虑到执行这些操作需要很长时间。"
        )
        self.assertEqual(
            result.text,
            "如今的瓶颈是 AI——考虑到执行这些操作需要很长时间。\n",
        )
        self.assertTrue(any(edit.type == "prefix_extension" for edit in result.edits))

    def test_cli_writes_markdown_epub_and_edit_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            output = root / "out"
            source.write_text(
                "# 测试文字稿\n\n这个系统可以降低成本，这个系统可以提高效率。\n",
                encoding="utf-8",
            )
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "transcript_cleaner",
                    str(source),
                    "--output-dir",
                    str(output),
                    "--author",
                    "Test Author",
                ],
                cwd=str(ROOT / "outputs"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            markdown = output / "source_cleaned.md"
            epub = output / "source_cleaned.epub"
            report = output / "source_cleaned.report.json"
            self.assertEqual(
                markdown.read_text(encoding="utf-8"),
                "# 测试文字稿\n\n这个系统可以降低成本，提高效率。\n",
            )
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["stats"]["shared_prefix_merges"], 1)
            self.assertEqual(data["edits"][0]["type"], "shared_prefix_coordination")
            with zipfile.ZipFile(epub) as archive:
                self.assertEqual(archive.namelist()[0], "mimetype")
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                self.assertIn("OEBPS/content.xhtml", archive.namelist())


if __name__ == "__main__":
    unittest.main()
