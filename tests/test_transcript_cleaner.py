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

    def test_removes_prd_minimum_short_gap_restart(self) -> None:
        result = transcript_cleaner.clean_text("这个模型……这个模型其实很强大。")
        self.assertEqual(result.text, "这个模型其实很强大。\n")
        self.assertGreater(result.stats.intra_sentence_gap_chars_removed, 0)
        self.assertTrue(any(edit.type == "short_gap_repeat" for edit in result.edits))

    def test_removes_short_gap_restart_with_oral_connector(self) -> None:
        result = transcript_cleaner.clean_text("人工智能，嗯那个，人工智能正在改变世界。")
        self.assertEqual(result.text, "人工智能正在改变世界。\n")
        self.assertGreater(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_does_not_remove_short_gap_restart_when_unit_has_less_than_three_chinese_chars(self) -> None:
        text = "我说……我说完了。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")
        self.assertEqual(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_does_not_remove_short_gap_restart_when_gap_reaches_fifteen_chars(self) -> None:
        text = "这个模型，嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯，这个模型其实很强大。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")
        self.assertEqual(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_does_not_remove_short_gap_restart_with_semantic_protection_signal(self) -> None:
        cases = [
            "这个模型，2024年，这个模型其实很强大。",
            "这个模型，嗯，这个模型不是最终版本。",
            "这个模型，因此，这个模型需要重新评估。",
            "这个模型，但是，这个模型仍然值得关注。",
        ]
        for text in cases:
            with self.subTest(text=text):
                result = transcript_cleaner.clean_text(text)
                self.assertEqual(result.text, text + "\n")
                self.assertEqual(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_does_not_remove_emphasis_short_repetition(self) -> None:
        text = "很重要，很重要，真的很重要。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")
        self.assertEqual(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_does_not_remove_short_emphasis_triplet(self) -> None:
        text = "很重要，很重要，很重要。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")
        self.assertEqual(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_removes_single_character_word_stutter(self) -> None:
        cases = [
            ("我我我觉得这件事很重要。", "我觉得这件事很重要。\n"),
            ("他他他说的对。", "他说的对。\n"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                result = transcript_cleaner.clean_text(text)
                self.assertEqual(result.text, expected)
                self.assertGreater(result.stats.word_stutter_chars_removed, 0)
                self.assertTrue(any(edit.type == "word_stutter" for edit in result.edits))

    def test_removes_short_word_stutter(self) -> None:
        result = transcript_cleaner.clean_text("真的真的真的太好了。")
        self.assertEqual(result.text, "真的太好了。\n")
        self.assertEqual(result.stats.word_stutter_chars_removed, 4)
        self.assertTrue(any(edit.type == "word_stutter" for edit in result.edits))

    def test_collapses_long_phrase_stutter_to_one_copy(self) -> None:
        phrase = "仅仅作为顶层的封装层"
        result = transcript_cleaner.clean_text(phrase * 3 + "。")
        self.assertEqual(result.text, phrase + "。\n")
        self.assertEqual(result.text.count(phrase), 1)
        self.assertGreater(result.stats.word_stutter_chars_removed, 0)
        self.assertEqual(result.stats.rule_hits["word_stutter"], 1)
        self.assertEqual(result.stats.rule_char_changes["word_stutter"], len(phrase) * 2)

    def test_collapses_repeated_long_clause_sequence_to_one_copy(self) -> None:
        phrase = "这说得通，太有意思了"
        result = transcript_cleaner.clean_text("，".join([phrase, phrase, phrase]) + "。")
        self.assertEqual(result.text, phrase + "。\n")
        self.assertEqual(result.text.count(phrase), 1)
        self.assertGreater(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_collapses_repeated_long_phrase_with_trailing_separator_to_one_copy(self) -> None:
        phrase = "仅仅作为顶层的封装层"
        result = transcript_cleaner.clean_text(f"{phrase}，{phrase}，{phrase}， 所以继续。")
        self.assertEqual(result.text, f"{phrase}， 所以继续。\n")
        self.assertEqual(result.text.count(phrase), 1)
        self.assertGreater(result.stats.intra_sentence_gap_chars_removed, 0)

    def test_collapses_repeated_sentence_prefix_before_continuation_to_one_copy(self) -> None:
        prefix = "那你有没有动力去等待"
        text = f"{prefix}？{prefix}？{prefix}下一代模型，还是应该使用 Applied Compute 2？"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, f"{prefix}下一代模型，还是应该使用 Applied Compute 2？\n")
        self.assertEqual(result.text.count(prefix), 1)

    def test_keeps_normal_reduplicated_words(self) -> None:
        text = "刚刚下课，星星渐渐亮了，妈妈回来了。"
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")
        self.assertEqual(result.stats.word_stutter_chars_removed, 0)
        self.assertFalse(any(edit.type == "word_stutter" for edit in result.edits))

    def test_scans_no_residual_short_gap_repeats_after_cleaning(self) -> None:
        result = transcript_cleaner.clean_text("这个模型……这个模型其实很强大。")
        self.assertEqual(transcript_cleaner.scan_residual_short_gap_repeats(result.text), [])
        self.assertEqual(result.residual_patterns, [])

    def test_removes_real_short_gap_residual_after_many_prior_collapses(self) -> None:
        labels = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸", "子"]
        text = "".join(f"重复片段{label}……重复片段{label}完成。" for label in labels)
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.residual_patterns, [])
        for label in labels:
            self.assertEqual(result.text.count(f"重复片段{label}"), 1)

    def test_removes_residual_unit_with_internal_sentence_punctuation(self) -> None:
        result = transcript_cleaner.clean_text("如果你拿到是。拿到是。拿到结果。")
        self.assertEqual(result.text, "如果你拿到是。拿到结果。\n")
        self.assertEqual(result.residual_patterns, [])

    def test_removes_short_gap_residual_created_by_later_sentence_collapses(self) -> None:
        text = (
            "那么，我们该如何确保它…… 那么，我们该如何确保它…… 那么，我们该如何确保它在实际运行中切实可用。"
            "所以这才是真正的任务。所以这才是真正的任务。所以这才是真正的任务。"
            "困难的部分是签约之后的一切。困难的部分是签约之后的一切。困难的部分是签约之后的一切。"
            "是的。是的。是的。拿到……是的。是的。 是。拿到是。是。是。拿到签名。"
        )
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.residual_patterns, [])
        self.assertNotIn("是。拿到是。拿到", result.text)

    def test_removes_two_copy_residual_with_trailing_separator(self) -> None:
        result = transcript_cleaner.clean_text("对。对。你知道，你知道，对。")
        self.assertEqual(result.text.count("你知道，"), 1)
        self.assertEqual(result.residual_patterns, [])

    def test_scans_deliberate_residual_short_gap_repeat(self) -> None:
        residuals = transcript_cleaner.scan_residual_short_gap_repeats(
            "这个模型……这个模型其实很强大。\n"
        )
        self.assertEqual(len(residuals), 1)
        self.assertEqual(residuals[0].type, "residual_short_gap_repeat")
        self.assertEqual(residuals[0].line, 1)
        self.assertEqual(residuals[0].text, "这个模型……这个模型")

    def test_residual_scan_ignores_semantic_repetition(self) -> None:
        cases = [
            "我爱你，我爱他，我爱大家。\n",
            "很重要，很重要，真的很重要。\n",
            "这个模型，但是，这个模型仍然值得关注。\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(transcript_cleaner.scan_residual_short_gap_repeats(text), [])

    def test_cli_report_includes_residual_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            output = root / "out"
            source.write_text("# 测试文字稿\n\n这个模型……这个模型其实很强大。\n", encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "transcript_cleaner",
                    str(source),
                    "--output-dir",
                    str(output),
                ],
                cwd=str(ROOT / "outputs"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            data = json.loads((output / "source_cleaned.report.json").read_text(encoding="utf-8"))
            self.assertEqual(data["residual_patterns"], [])

    def test_removes_embedded_sentence_block_repeat(self) -> None:
        cleaned, stats = transcript_cleaner.clean_text("有人说：“A。B。C。A。B。C。”然后继续。")
        self.assertEqual(cleaned, "有人说：“A。B。C。”然后继续。\n")
        self.assertEqual(stats.removed_sentence_units, 3)

    def test_removes_adjacent_sentence_block_copies(self) -> None:
        cleaned, stats = transcript_cleaner.clean_text("A。B。C。A。B。C。A。B。C。D。")
        self.assertEqual(cleaned, "A。B。C。D。\n")
        self.assertEqual(stats.removed_sentence_units, 6)
        self.assertEqual(stats.repeated_blocks_removed, 2)

    def test_collapses_three_identical_sentences_to_one_copy(self) -> None:
        sentence = "这个模型很强大。"
        result = transcript_cleaner.clean_text(sentence * 3)
        self.assertEqual(result.text, sentence + "\n")
        self.assertEqual(result.text.count(sentence), 1)
        self.assertTrue(any(edit.auto_applied for edit in result.edits))
        self.assertGreater(sum(result.stats.rule_char_changes.values()), 0)

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

    def test_edit_report_distinguishes_auto_applied_and_report_only(self) -> None:
        text = "这个系统可以降低成本，这个系统可以提高效率。\n或者我们想改变它，或者我们想要其他定义。"
        result = transcript_cleaner.clean_text(text)
        automatic = next(edit for edit in result.edits if edit.type == "shared_prefix_coordination")
        report_only = next(edit for edit in result.edits if edit.type == "shared_prefix_candidate")
        self.assertTrue(automatic.auto_applied)
        self.assertFalse(automatic.report_only)
        self.assertFalse(report_only.auto_applied)
        self.assertTrue(report_only.report_only)
        self.assertEqual(result.stats.rule_hits["shared_prefix_coordination"], 1)
        self.assertEqual(result.stats.rule_hits["shared_prefix_candidate"], 1)
        self.assertGreater(result.stats.rule_char_changes["shared_prefix_coordination"], 0)
        self.assertEqual(result.stats.rule_char_changes["shared_prefix_candidate"], 0)

    def test_edit_report_keeps_changed_region_for_long_line_edit(self) -> None:
        text = "context-" * 40 + "真的真的真的太好了。"
        result = transcript_cleaner.clean_text(text)
        edit = next(item for item in result.edits if item.type == "word_stutter")
        data = edit.to_dict()
        self.assertNotEqual(data["before"], data["after"])
        self.assertIn("真的真的真的太好了", data["before"])
        self.assertIn("真的太好了", data["after"])

    def test_report_only_edit_stays_equal_when_no_text_changed(self) -> None:
        text = "或者我们想改变它，或者我们想要其他定义。"
        result = transcript_cleaner.clean_text(text)
        edit = next(item for item in result.edits if item.type == "shared_prefix_candidate")
        data = edit.to_dict()
        self.assertEqual(data["before"], data["after"])
        self.assertTrue(data["report_only"])

    def test_cli_report_includes_edit_action_and_rule_aggregation(self) -> None:
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
                ],
                cwd=str(ROOT / "outputs"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            data = json.loads((output / "source_cleaned.report.json").read_text(encoding="utf-8"))
            self.assertTrue(data["edits"][0]["auto_applied"])
            self.assertFalse(data["edits"][0]["report_only"])
            self.assertEqual(data["stats"]["rule_hits"]["shared_prefix_coordination"], 1)
            self.assertGreater(data["stats"]["rule_char_changes"]["shared_prefix_coordination"], 0)

    def test_reports_near_duplicate_sentences_without_modifying_text(self) -> None:
        text = "这个模型很强大。这个模型非常强大。"
        result = transcript_cleaner.clean_text(text)
        candidates = [edit for edit in result.edits if edit.type == "near_duplicate_candidate"]
        self.assertEqual(result.text, text + "\n")
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0].auto_applied)
        self.assertTrue(candidates[0].report_only)
        self.assertEqual(result.stats.rule_hits["near_duplicate_candidate"], 1)
        self.assertEqual(result.stats.rule_char_changes["near_duplicate_candidate"], 0)

    def test_exact_duplicate_sentence_is_auto_removed_not_reported_as_near_duplicate(self) -> None:
        result = transcript_cleaner.clean_text("这个模型很强大。这个模型很强大。")
        self.assertEqual(result.text, "这个模型很强大。\n")
        self.assertTrue(any(edit.auto_applied for edit in result.edits))
        self.assertFalse(any(edit.type == "near_duplicate_candidate" for edit in result.edits))

    def test_cli_report_includes_near_duplicate_as_report_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            output = root / "out"
            source.write_text(
                "# 测试文字稿\n\n这个模型很强大。这个模型非常强大。\n",
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
                ],
                cwd=str(ROOT / "outputs"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            data = json.loads((output / "source_cleaned.report.json").read_text(encoding="utf-8"))
            candidate = next(edit for edit in data["edits"] if edit["type"] == "near_duplicate_candidate")
            self.assertFalse(candidate["auto_applied"])
            self.assertTrue(candidate["report_only"])
            self.assertEqual(data["stats"]["rule_hits"]["near_duplicate_candidate"], 1)
            self.assertEqual(data["stats"]["rule_char_changes"]["near_duplicate_candidate"], 0)

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


    def test_collapses_english_intra_sentence_fragment_repeat(self) -> None:
        # PRD issue #10：英文片段级句内重复（不按句子边界对齐）必须收敛
        text = (
            "And the signature for those of you can "
            "And the signature for those of you can "
            "And the signature for those of you can tell is this one right here."
        )
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(
            result.text,
            "And the signature for those of you can tell is this one right here.\n",
        )
        # 报告应记录这次英文片段重复删除
        self.assertTrue(
            any(edit.type == "short_gap_repeat" for edit in result.edits),
            f"expected short_gap_repeat edit, got {[e.type for e in result.edits]}",
        )

    def test_english_fragment_two_copies_converge(self) -> None:
        # 两份英文片段重复也应收敛为一份
        text = "We'll uh continue this conversation and We'll uh continue this conversation and see how these bets play out."
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(
            result.text,
            "We'll uh continue this conversation and see how these bets play out.\n",
        )

    def test_english_fragment_counted_as_unit_length(self) -> None:
        # 含拉丁字母数字的单位应被计入长度，不再因 count_cjk=0 被拒
        self.assertGreaterEqual(
            transcript_cleaner.measure_unit_length("And the signature"),
            3,
        )

    def test_does_not_remove_normal_english_proper_noun_recurrence(self) -> None:
        # 正常英文专有名词自然复现不应被误删（两份中间有实质内容）
        text = "Nvidia reported earnings. Then later Nvidia announced a new chip."
        result = transcript_cleaner.clean_text(text)
        self.assertEqual(result.text, text + "\n")


if __name__ == "__main__":
    unittest.main()
