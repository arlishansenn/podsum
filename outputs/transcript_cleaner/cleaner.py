#!/usr/bin/env python3
"""Clean transcript noise and local ASR repetition patterns."""

from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterator, List, Sequence, Tuple


NOISE_TOKENS = ["[笑声]", "[掌声]", "[嗤笑]", "[哼了一声]", "[音乐]", "[听不清]"]
FILLER_PATTERNS = [
    r"(?<![\w])你知道的[，,、]?",
    r"(?<![\w])说实话[，,、]?",
    r"(?<![\w])老实说[，,、]?",
    r"(?<![\w])我是说[，,、]?",
    r"(?<![\w])总之[，,、]?",
    r"(?<![\w])基本上[，,、]?",
    r"(?<![\w])事实上[，,、]?",
    r"(?<![\w])其实[，,、]?",
    r"(?<![\w])也就是说[，,、]?",
    r"(?<![\w])明白吗[？?]?",
    r"(?<![\w])对吧[？?]?",
    r"(?<![\w])没错[。！!，,、]?",
    r"(?<![\w])好的[。！!，,、]?",
    r"(?<![\w])好吧[。！!，,、]?",
    r"(?<![\w])请说[。！!，,、]?",
    r"(?<![\w])请继续[。！!，,、]?",
    r"(?<![\w])谢谢[。！!，,、]?",
    r"(?<![\w])抱歉[。！!，,、]?",
    r"(?<![\w])对不起[。！!，,、]?",
    r"(?<![\w])哇[，,、]?",
    r"(?<![\w])嘿[，,、]?",
    r"(?<![\w])嗨[，,、]?",
]
SENTENCE_RE = re.compile(r"[^。！？!?…\n]+(?:[。！？!?]+|…{2,})|[^。！？!?…\n]+$")
REGEX_SENTENCE_UNIT = r"[^。！？!?…\n]{1,240}(?:[。！？!?]+|…{2,})"
FILLER_RE = re.compile(
    r"^(?:"
    r"是的|对|嗯|呃|啊|好的|好吧|没错|谢谢|抱歉|对不起|"
    r"你知道的|你知道|我觉得|我认为|就是说|也就是说|其实|基本上|事实上|说实话"
    r")[，,、。！？!?:：;；\s]*"
)
EDIT_SUMMARY_LIMIT = 240

# 热路径固定正则统一预编译，避免短间隔重复扫描里反复触发 re._compile。
RESTART_SENTENCE_END_RE = re.compile(r"[。！？!?]")
RESTART_CJK_LATIN_RE = re.compile(r"[\u3400-\u9fffA-Za-z]")
RESTART_PUNCT_ONLY_RE = re.compile(r"[\s\d，。！？；：、\"“”「」『』\-,.…— ]+")
EMPHASIS_REPEAT_NORMALIZE_RE = re.compile(r"[\s，,、。！？!?；;：:]+")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
UNIT_LENGTH_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
TRAILING_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9_]+$")
DAMAGED_FRAGMENT_RE = re.compile(r"(?:……|…|—|--|\b[xX]{3,}\b|[xX]{4,}|[?？]{3,})")
FILLER_TAIL_RE = re.compile(r"(?:嗯|呃|那个|怎么说|就是)[，,、…—\s]*$")


@dataclass
class CleaningStats:
    chars_before: int = 0
    chars_after: int = 0
    chars_removed: int = 0
    paragraphs_changed: int = 0
    removed_sentence_units: int = 0
    repeated_blocks_removed: int = 0
    intra_sentence_gap_chars_removed: int = 0
    word_stutter_chars_removed: int = 0
    shared_prefix_merges: int = 0
    prefix_restarts_removed: int = 0
    adjacent_clause_repeats: int = 0
    rule_hits: dict[str, int] = field(default_factory=dict)
    rule_char_changes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Edit:
    type: str
    before: str
    after: str
    confidence: float
    line: int
    auto_applied: bool
    report_only: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["before"], value["after"] = summarize_edit_pair(self.before, self.after, EDIT_SUMMARY_LIMIT)
        return value


@dataclass
class ResidualPattern:
    type: str
    text: str
    unit: str
    gap: str
    confidence: float
    line: int
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["text"] = summarize_edit_text(self.text, EDIT_SUMMARY_LIMIT)
        return value


@dataclass
class CleaningResult:
    text: str
    stats: CleaningStats
    edits: List[Edit]
    residual_patterns: List[ResidualPattern]

    def __iter__(self) -> Iterator[Any]:
        yield self.text
        yield self.stats


def summarize_edit_text(text: str, limit: int) -> str:
    value = text.replace("\n", "\\n")
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def find_changed_region(before: str, after: str) -> Tuple[int, int, int, int]:
    prefix = 0
    max_prefix = min(len(before), len(after))
    while prefix < max_prefix and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    before_limit = len(before) - prefix
    after_limit = len(after) - prefix
    while suffix < before_limit and suffix < after_limit and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    before_end = len(before) - suffix
    after_end = len(after) - suffix
    return prefix, before_end, prefix, after_end


def summarize_changed_text(text: str, start: int, end: int, limit: int) -> str:
    if len(text) <= limit:
        return text.replace("\n", "\\n")
    bounded_start = max(0, min(start, len(text)))
    bounded_end = max(bounded_start, min(end, len(text)))
    changed_length = bounded_end - bounded_start
    if changed_length >= limit:
        window_start = bounded_start
        window_end = min(len(text), bounded_start + limit)
    else:
        context = (limit - changed_length) // 2
        window_start = max(0, bounded_start - context)
        window_end = min(len(text), bounded_end + context)
        missing = limit - (window_end - window_start)
        if missing > 0 and window_start > 0:
            window_start = max(0, window_start - missing)
        missing = limit - (window_end - window_start)
        if missing > 0 and window_end < len(text):
            window_end = min(len(text), window_end + missing)
    prefix = "...[truncated]" if window_start > 0 else ""
    suffix = "...[truncated]" if window_end < len(text) else ""
    return prefix + text[window_start:window_end].replace("\n", "\\n") + suffix


def summarize_edit_pair(before: str, after: str, limit: int) -> Tuple[str, str]:
    if before == after:
        summarized = summarize_edit_text(before, limit)
        return summarized, summarized
    before_start, before_end, after_start, after_end = find_changed_region(before, after)
    return (
        summarize_changed_text(before, before_start, before_end, limit),
        summarize_changed_text(after, after_start, after_end, limit),
    )


def count_rule_hits(edits: Sequence[Edit]) -> dict[str, int]:
    hits: dict[str, int] = {}
    for edit in edits:
        hits[edit.type] = hits.get(edit.type, 0) + 1
    return hits


def count_rule_char_changes(edits: Sequence[Edit]) -> dict[str, int]:
    changes: dict[str, int] = {}
    for edit in edits:
        current = changes.get(edit.type, 0)
        changes[edit.type] = current + abs(len(edit.before) - len(edit.after))
    return changes


def remove_fillers_and_normalize(text: str) -> str:
    for token in NOISE_TOKENS:
        text = text.replace(token, "")
    for pattern in FILLER_PATTERNS:
        text = re.sub(pattern, "", text)
    text = re.sub(r"…{2,}", "……", text)
    text = re.sub(r"。{2,}", "。", text)
    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"([。！？；：])[ \t]*([。！？；：])", r"\1", text)
    text = re.sub(r"[ \t]+([，。！？；：])", r"\1", text)
    text = re.sub(r"([（《“])[ \t]+", r"\1", text)
    text = re.sub(r"[ \t]+([）》”])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def normalize_sentence(sentence: str, *, loose: bool = False) -> str:
    value = re.sub(r"\s+", "", sentence.strip())
    value = value.strip("。！？!?，,、；;：:‘’'\"“”()（）[]【】")
    value = value.replace("…", "").replace("—", "-")
    if loose:
        previous = None
        while previous != value:
            previous = value
            value = FILLER_RE.sub("", value)
    return value


def split_sentence_units(paragraph: str) -> List[str]:
    if not paragraph.strip() or paragraph.lstrip().startswith("#"):
        return [paragraph]
    matches = list(SENTENCE_RE.finditer(paragraph))
    if not matches:
        return [paragraph]
    units: List[str] = []
    position = 0
    for match in matches:
        if match.start() > position:
            prefix = paragraph[position : match.start()]
            if units:
                units[-1] += prefix
            elif prefix:
                units.append(prefix)
        units.append(match.group(0))
        position = match.end()
    if position < len(paragraph):
        suffix = paragraph[position:]
        if units:
            units[-1] += suffix
        else:
            units.append(suffix)
    return units


NORMAL_REDUPLICATION_WORDS = frozenset({"刚刚", "渐渐", "星星", "妈妈"})
ORAL_GAP_TOKENS = ("嗯", "呃", "啊", "那个", "这个", "就是", "怎么说", "是吧", "对吧")
SEMANTIC_PROTECTION_RE = re.compile(
    r"(?:\d|[０-９]|因为|所以|因此|但是|但|不过|然而|可是|而是|不是|没有|不会|不能|不再|并非)"
)
EMPHASIS_CONTINUATION_RE = re.compile(r"^[，,、。！？!?；;：:\s]*(?:真的|确实|非常|特别|尤其|太|很)")
NEAR_DUPLICATE_WEAK_WORD_RE = re.compile(r"(?:非常|很|特别|确实|其实|基本上|事实上|真的|还是|比较|更加|更)")
GAP_PAUSE_CHARS = set(" \t\r\n，,、。！？!?；;：:…—-~～“”\"'‘’（）()【】[]《》")


def is_plain_clause_separator_gap(gap: str) -> bool:
    return gap in {"，", ",", "、", "；", ";"}


def is_allowed_restart_gap(gap: str) -> bool:
    if len(gap) >= 15:
        return False
    if is_plain_clause_separator_gap(gap):
        return False
    value = "".join(char for char in gap if char not in GAP_PAUSE_CHARS)
    while value:
        matched = False
        for token in sorted(ORAL_GAP_TOKENS, key=len, reverse=True):
            if value.startswith(token):
                value = value[len(token) :]
                matched = True
                break
        if not matched:
            return False
    return True


def find_plain_separator_repeat_end(text: str, unit: str, gap: str, second_end: int) -> Tuple[int, int]:
    if not is_plain_clause_separator_gap(gap) or count_cjk(unit) < 5:
        return second_end, 2
    cursor = second_end
    copies = 2
    while text.startswith(gap + unit, cursor):
        cursor += len(gap) + len(unit)
        copies += 1
    return cursor, copies


def is_repeat_separator_char(char: str) -> bool:
    return char in {"，", ",", "、", "；", ";", "。", "！", "？", "!", "?"}


def find_trailing_separator_repeat_end(text: str, unit: str, gap: str, second_end: int) -> Tuple[str, int, int]:
    if gap or len(unit) < 2:
        return unit, second_end, 2
    separator = unit[-1]
    if not is_repeat_separator_char(separator):
        return unit, second_end, 2
    base = unit[:-1]
    if count_cjk(base) < 5:
        return unit, second_end, 2
    cursor = second_end
    copies = 2
    if text.startswith(base, cursor):
        cursor += len(base)
        copies += 1
    while text.startswith(separator + base, cursor):
        cursor += len(separator) + len(base)
        copies += 1
    return base, cursor, copies


def has_semantic_protection_signal(text: str) -> bool:
    return bool(SEMANTIC_PROTECTION_RE.search(text))


@lru_cache(maxsize=100000)
def is_restart_unit(unit: str) -> bool:
    # 单位长度判定用 CJK+拉丁字母数字合并计数，使英文片段也能满足长度门槛
    if measure_unit_length(unit) < 3:
        return False
    # 跨句（含多个句末标点）的属于句子级重复，交给句块规则处理，不在此收敛
    if len(RESTART_SENTENCE_END_RE.findall(unit)) >= 2:
        return False
    if len(set(unit)) <= 2:
        return False
    if not RESTART_CJK_LATIN_RE.search(unit):
        return False
    return not RESTART_PUNCT_ONLY_RE.fullmatch(unit)


def has_emphasis_continuation(tail: str) -> bool:
    return bool(EMPHASIS_CONTINUATION_RE.match(tail))


def is_emphasis_repeat_fragment(unit: str, tail: str) -> bool:
    normalized_unit = EMPHASIS_REPEAT_NORMALIZE_RE.sub("", unit)
    normalized_tail = EMPHASIS_REPEAT_NORMALIZE_RE.sub("", tail[:8])
    context = normalized_unit + normalized_tail
    return "很重要" in context or "重要" in context


def is_auto_deletable_short_gap_repeat(unit: str, gap: str, tail: str) -> bool:
    return (
        is_restart_unit(unit)
        and is_allowed_restart_gap(gap)
        and not has_semantic_protection_signal(gap)
        and not has_semantic_protection_signal(tail[:15])
        and not has_emphasis_continuation(tail)
        and not is_emphasis_repeat_fragment(unit, tail)
    )


def collapse_word_stutters(paragraph: str) -> Tuple[str, int]:
    removed = 0
    value = paragraph
    if value.lstrip().startswith("#"):
        return value, removed

    def replace_single(match: re.Match[str]) -> str:
        nonlocal removed
        unit = match.group(1)
        if unit in ORAL_GAP_TOKENS:
            return match.group(0)
        removed += len(match.group(0)) - len(unit)
        return unit

    value = re.sub(r"([\u3400-\u9fff])\1{2,}", replace_single, value)

    max_phrase_len = min(30, len(value) // 3)
    for unit_len in range(max_phrase_len, 4, -1):
        pattern = re.compile(rf"([\u3400-\u9fff]{{{unit_len}}})(?:\1){{2,}}")

        def replace_phrase(match: re.Match[str]) -> str:
            nonlocal removed
            unit = match.group(1)
            if len(set(unit)) <= 1:
                return match.group(0)
            removed += len(match.group(0)) - len(unit)
            return unit

        value = pattern.sub(replace_phrase, value)

    for unit_len in range(4, 1, -1):
        pattern = re.compile(rf"([\u3400-\u9fff]{{{unit_len}}})(?:\1){{2,}}")

        def replace(match: re.Match[str]) -> str:
            nonlocal removed
            unit = match.group(1)
            if unit in NORMAL_REDUPLICATION_WORDS:
                return match.group(0)
            if len(set(unit)) == 1 and unit[0] in ORAL_GAP_TOKENS:
                return match.group(0)
            removed += len(match.group(0)) - len(unit)
            return unit

        value = pattern.sub(replace, value)
    return value, removed


def collapse_intra_sentence_short_gap_repeats(paragraph: str) -> Tuple[str, int]:
    removed = 0
    value = paragraph
    if value.lstrip().startswith("#"):
        return value, removed
    while True:
        changed = False
        text_len = len(value)
        max_match_len = min(80, text_len // 2)
        max_offset = min(text_len - 1, max_match_len + 14)
        candidates_by_len: dict[int, set[int]] = {}
        for offset in range(3, max_offset + 1):
            common = 0
            min_len_for_offset = max(3, offset - 14)
            max_len_for_offset = 80 if offset > 80 else offset
            for index in range(text_len - offset - 1, -1, -1):
                if value[index] == value[index + offset]:
                    common += 1
                else:
                    common = 0
                max_len_for_index = common if common < max_len_for_offset else max_len_for_offset
                if max_len_for_index < min_len_for_offset:
                    continue
                for match_len in range(min_len_for_offset, max_len_for_index + 1):
                    bucket = candidates_by_len.get(match_len)
                    if bucket is None:
                        bucket = set()
                        candidates_by_len[match_len] = bucket
                    bucket.add(index)

        for match_len in range(max_match_len, 2, -1):
            candidate_indexes = candidates_by_len.get(match_len)
            if not candidate_indexes:
                continue
            for index in sorted(candidate_indexes):
                unit = value[index : index + match_len]
                if not is_restart_unit(unit):
                    continue
                rest_start = index + match_len
                for gap_len in range(0, 15):
                    second_start = rest_start + gap_len
                    second_end = second_start + match_len
                    if second_end > text_len:
                        break
                    gap = value[rest_start:second_start]
                    if value[second_start:second_end] != unit:
                        continue
                    if is_plain_clause_separator_gap(gap):
                        repeat_end, copies = find_plain_separator_repeat_end(value, unit, gap, second_end)
                        tail = value[repeat_end:]
                        if copies < 3:
                            continue
                        if has_emphasis_continuation(tail):
                            continue
                        value = value[:rest_start] + tail
                        removed += repeat_end - rest_start
                        changed = True
                        break
                    base_unit, repeat_end, copies = find_trailing_separator_repeat_end(value, unit, gap, second_end)
                    if copies >= 3:
                        tail = value[repeat_end:]
                        if has_emphasis_continuation(tail) or "重要" in base_unit:
                            continue
                        value = value[:index] + base_unit + tail
                        removed += repeat_end - index - len(base_unit)
                        changed = True
                        break
                    tail = value[second_end:]
                    if not is_auto_deletable_short_gap_repeat(unit, gap, tail):
                        continue
                    value = value[:index] + value[second_start:]
                    removed += len(unit) + len(gap)
                    changed = True
                    break
                if changed:
                    break
            if changed:
                break
        if not changed:
            break
    return value, removed


def find_residual_short_gap_repeats_in_line(line: str, line_no: int) -> List[ResidualPattern]:
    if line.lstrip().startswith("#"):
        return []
    residuals: List[ResidualPattern] = []
    line_len = len(line)
    max_match_len = min(80, line_len // 2)
    max_offset = min(line_len - 1, max_match_len + 14)
    candidate_lengths_by_index: dict[int, set[int]] = {}
    for offset in range(3, max_offset + 1):
        common = 0
        min_len_for_offset = max(3, offset - 14)
        max_len_for_offset = 80 if offset > 80 else offset
        for index in range(line_len - offset - 1, -1, -1):
            if line[index] == line[index + offset]:
                common += 1
            else:
                common = 0
            max_len_for_index = common if common < max_len_for_offset else max_len_for_offset
            if max_len_for_index < min_len_for_offset:
                continue
            bucket = candidate_lengths_by_index.get(index)
            if bucket is None:
                bucket = set()
                candidate_lengths_by_index[index] = bucket
            for match_len in range(min_len_for_offset, max_len_for_index + 1):
                bucket.add(match_len)

    occupied_until = -1
    for index in range(0, line_len):
        if index < occupied_until:
            continue
        candidate_lengths = candidate_lengths_by_index.get(index)
        if not candidate_lengths:
            continue
        for match_len in range(max_match_len, 2, -1):
            if match_len not in candidate_lengths:
                continue
            rest_start = index + match_len
            if rest_start > line_len:
                continue
            unit = line[index:rest_start]
            if not is_restart_unit(unit):
                continue
            matched: ResidualPattern | None = None
            for gap_len in range(0, 15):
                second_start = rest_start + gap_len
                second_end = second_start + match_len
                if second_end > line_len:
                    break
                gap = line[rest_start:second_start]
                if line[second_start:second_end] != unit:
                    continue
                tail = line[second_end:]
                if not is_auto_deletable_short_gap_repeat(unit, gap, tail):
                    continue
                matched = ResidualPattern(
                    "residual_short_gap_repeat",
                    line[index:second_end],
                    unit,
                    gap,
                    0.99,
                    line_no,
                    index,
                    second_end,
                )
                break
            if matched is not None:
                residuals.append(matched)
                occupied_until = matched.end
                break
    return residuals


def scan_residual_short_gap_repeats(text: str) -> List[ResidualPattern]:
    residuals: List[ResidualPattern] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        residuals.extend(find_residual_short_gap_repeats_in_line(line, line_no))
    return residuals


def count_cjk(text: str) -> int:
    # 仅统计中日韩字符
    return len(CJK_RE.findall(text))


@lru_cache(maxsize=100000)
def measure_unit_length(text: str) -> int:
    # 单位长度判定：CJK 字符与拉丁字母数字合并计数，避免纯英文片段长度恒为 0
    return len(UNIT_LENGTH_RE.findall(text))


def normalize_near_duplicate_text(text: str) -> str:
    value = normalize_sentence(text, loose=True)
    value = NEAR_DUPLICATE_WEAK_WORD_RE.sub("", value)
    return value


def sequence_similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, first, second).ratio()


def is_near_duplicate_pair(first: str, second: str, min_cjk: int, min_similarity: float) -> bool:
    first_base = normalize_sentence(first, loose=True)
    second_base = normalize_sentence(second, loose=True)
    if not first_base or not second_base:
        return False
    if first_base == second_base:
        return False
    first_norm = normalize_near_duplicate_text(first)
    second_norm = normalize_near_duplicate_text(second)
    if count_cjk(first_norm) < min_cjk or count_cjk(second_norm) < min_cjk:
        return False
    similarity = sequence_similarity(first_norm, second_norm)
    return similarity >= min_similarity


def report_near_duplicate_sentence_candidates(paragraph: str, line_no: int) -> List[Edit]:
    if paragraph.lstrip().startswith("#"):
        return []
    units = split_sentence_units(paragraph)
    edits: List[Edit] = []
    for index in range(0, len(units) - 1):
        first = units[index]
        second = units[index + 1]
        if not first.strip() or not second.strip():
            continue
        if not is_near_duplicate_pair(first, second, 6, 0.78):
            continue
        before = first + second
        edits.append(Edit("near_duplicate_candidate", before, before, 0.6, line_no, False, True))
    return edits


def longest_common_prefix(first: str, second: str) -> str:
    limit = min(len(first), len(second))
    index = 0
    while index < limit and first[index] == second[index]:
        index += 1
    return first[:index]


SAFE_COORDINATION_PREFIX_RE = re.compile(
    r"(?:"
    r"可以|能够|可能会|会|将|正在|不断|用于|适合|支持|包括|拥有|使用|采用|"
    r"需要|应该|在用|有一个|"
    r"带来(?:了)?(?:更高的|大量)?|产生|导致|造成|提高|降低|增加|减少|"
    r"提供|实现|成为|保持|获得|面临|存在"
    r")$"
)


def safe_coordination_prefix(prefix: str) -> str:
    # 避免截断拉丁词，例如 CoreWeave / Crusoe 只共享 "C"。
    prefix = TRAILING_LATIN_WORD_RE.sub("", prefix)
    prefix = prefix.rstrip()
    if not prefix or "……" in prefix or "…" in prefix or "—" in prefix:
        return ""
    if not SAFE_COORDINATION_PREFIX_RE.search(prefix):
        return ""
    return prefix


def is_high_confidence_restart_tail(tail: str) -> bool:
    value = tail.strip()
    if not value:
        return False
    return bool(DAMAGED_FRAGMENT_RE.search(value) or FILLER_TAIL_RE.search(value))


def collapse_shared_prefix_clauses(
    paragraph: str,
    *,
    min_prefix_cjk: int = 5,
    line_no: int = 0,
) -> Tuple[str, List[Edit], int, int, int]:
    """Handle adjacent clauses sharing a Chinese prefix.

    Safe coordination:
        P+A，P+B。 -> P+A，B。

    High-confidence restart:
        P+truncated，P+complete。 -> P+complete。
    """
    if paragraph.lstrip().startswith("#"):
        return paragraph, [], 0, 0, 0

    parts = re.split(r"([，,；;。！？!?])", paragraph)
    edits: List[Edit] = []
    merges = 0
    restarts = 0
    clause_repeats = 0
    index = 0
    while index + 2 < len(parts):
        left = parts[index]
        separator = parts[index + 1]
        right = parts[index + 2]
        if separator not in {"，", ",", "；", ";"} or not left.strip() or not right.strip():
            index += 2
            continue

        left_leading = left[: len(left) - len(left.lstrip())]
        right_leading = right[: len(right) - len(right.lstrip())]
        left_body = left.strip()
        right_body = right.strip()
        before = left + separator + right
        following = "".join(parts[index + 3 : index + 5])
        if left_body == right_body:
            if has_emphasis_continuation(following):
                index += 2
                continue
            if count_cjk(left_body) < 5 and has_emphasis_continuation(separator + right_body):
                index += 2
                continue
            parts[index + 1] = ""
            parts[index + 2] = ""
            edits.append(Edit("adjacent_clause_repeat", before, parts[index], 1.0, line_no, True, False))
            clause_repeats += 1
            continue
        if right_body.startswith(left_body) and len(right_body) - len(left_body) >= 2:
            parts[index] = left_leading + right_body
            parts[index + 1] = ""
            parts[index + 2] = ""
            edits.append(Edit("prefix_extension", before, parts[index], 1.0, line_no, True, False))
            clause_repeats += 1
            continue
        if left_body.startswith(right_body) and len(left_body) - len(right_body) >= 2:
            parts[index + 1] = ""
            parts[index + 2] = ""
            edits.append(Edit("prefix_extension", before, parts[index], 1.0, line_no, True, False))
            clause_repeats += 1
            continue

        raw_prefix = longest_common_prefix(left_body, right_body)
        prefix = TRAILING_LATIN_WORD_RE.sub("", raw_prefix).rstrip()
        if count_cjk(prefix) < min_prefix_cjk:
            index += 2
            continue

        left_tail = left_body[len(prefix) :]
        right_tail = right_body[len(prefix) :]
        if len(left_tail.strip()) < 2 or len(right_tail.strip()) < 2:
            index += 2
            continue

        if is_high_confidence_restart_tail(left_tail):
            parts[index] = left_leading + right_body
            parts[index + 1] = ""
            parts[index + 2] = ""
            after = parts[index]
            edits.append(Edit("prefix_restart", before, after, 0.95, line_no, True, False))
            restarts += 1
            continue

        safe_prefix = safe_coordination_prefix(prefix)
        if not safe_prefix:
            edits.append(Edit("shared_prefix_candidate", before, before, 0.5, line_no, False, True))
            index += 2
            continue
        if safe_prefix != prefix:
            prefix = safe_prefix
            left_tail = left_body[len(prefix) :]
            right_tail = right_body[len(prefix) :]
        parts[index + 2] = right_leading + right_tail.lstrip()
        after = parts[index] + separator + parts[index + 2]
        edits.append(Edit("shared_prefix_coordination", before, after, 0.99, line_no, True, False))
        merges += 1
        index += 2

    return "".join(parts), edits, merges, restarts, clause_repeats


def collapse_exact_embedded_sentence_blocks(
    paragraph: str,
    *,
    max_block_sentences: int = 12,
    min_block_sentences: int = 1,
) -> Tuple[str, int, int]:
    removed_units = 0
    removed_blocks = 0
    value = paragraph
    for _ in range(20):
        changed = False
        for block_len in range(max_block_sentences, min_block_sentences - 1, -1):
            pattern = re.compile(rf"((?:{REGEX_SENTENCE_UNIT}){{{block_len}}})(?:[ \t]*\1)+")

            def replace(match: re.Match[str]) -> str:
                nonlocal removed_units, removed_blocks, changed
                full = match.group(0)
                block = match.group(1)
                copies = max(1, full.count(block))
                if copies > 1:
                    removed_units += (copies - 1) * block_len
                    removed_blocks += copies - 1
                    changed = True
                return block

            value = pattern.sub(replace, value)
        if not changed:
            break
    return value, removed_units, removed_blocks


def blocks_equal(norms: Sequence[str], index: int, block_len: int) -> bool:
    first = norms[index : index + block_len]
    second = norms[index + block_len : index + 2 * block_len]
    return (
        len(first) == block_len
        and len(second) == block_len
        and not any(not item for item in list(first) + list(second))
        and list(first) == list(second)
    )


def collapse_adjacent_duplicate_blocks(
    units: Sequence[str],
    *,
    max_block_sentences: int = 12,
    min_block_sentences: int = 1,
    loose: bool = True,
) -> Tuple[List[str], int, int]:
    current = list(units)
    removed_units = 0
    removed_blocks = 0
    changed = True
    while changed:
        changed = False
        norms = [normalize_sentence(unit, loose=loose) for unit in current]
        output: List[str] = []
        index = 0
        while index < len(current):
            matched_len = 0
            max_len = min(max_block_sentences, (len(current) - index) // 2)
            for block_len in range(max_len, min_block_sentences - 1, -1):
                if blocks_equal(norms, index, block_len):
                    matched_len = block_len
                    break
            if not matched_len:
                output.append(current[index])
                index += 1
                continue
            output.extend(current[index : index + matched_len])
            copies = 1
            cursor = index + matched_len
            base = norms[index : index + matched_len]
            while cursor + matched_len <= len(current) and norms[cursor : cursor + matched_len] == base:
                copies += 1
                cursor += matched_len
            removed_units += (copies - 1) * matched_len
            removed_blocks += copies - 1
            index = cursor
            changed = True
        current = output
    return current, removed_units, removed_blocks


def dedupe_paragraph(paragraph: str) -> Tuple[str, int, int, int]:
    original = paragraph
    paragraph, _word_stutter_removed = collapse_word_stutters(paragraph)
    paragraph, gap_removed = collapse_intra_sentence_short_gap_repeats(paragraph)
    paragraph, embedded_units, embedded_blocks = collapse_exact_embedded_sentence_blocks(paragraph)
    units = split_sentence_units(paragraph)
    units, adjacent_units, adjacent_blocks = collapse_adjacent_duplicate_blocks(units)
    result = "".join(units)
    changed = int(result != original)
    return result, changed, embedded_units + adjacent_units, embedded_blocks + adjacent_blocks + int(gap_removed > 0)


def clean_text(text: str, *, min_prefix_cjk: int = 5) -> CleaningResult:
    original = text
    normalized = remove_fillers_and_normalize(text)
    edits: List[Edit] = []
    if normalized != text:
        edits.append(Edit("fillers_and_noise", text, normalized, 1.0, 0, True, False))
    text = normalized
    output: List[str] = []
    paragraphs_changed = 0
    removed_units = 0
    removed_blocks = 0
    gap_removed = 0
    word_stutter_removed = 0
    shared_prefix_merges = 0
    prefix_restarts = 0
    adjacent_clause_repeats = 0
    for line_no, line in enumerate(text.splitlines(keepends=True), start=1):
        newline = ""
        body = line
        if line.endswith("\r\n"):
            body, newline = line[:-2], "\r\n"
        elif line.endswith("\n"):
            body, newline = line[:-1], "\n"
        before = body
        after_word_stutter, line_word_stutter_removed = collapse_word_stutters(body)
        if after_word_stutter != body:
            edits.append(Edit("word_stutter", body, after_word_stutter, 1.0, line_no, True, False))
        after_gap, line_gap_removed = collapse_intra_sentence_short_gap_repeats(after_word_stutter)
        if after_gap != after_word_stutter:
            edits.append(Edit("short_gap_repeat", after_word_stutter, after_gap, 0.99, line_no, True, False))
        after_prefix, prefix_edits, line_merges, line_restarts, line_clause_repeats = collapse_shared_prefix_clauses(
            after_gap,
            min_prefix_cjk=min_prefix_cjk,
            line_no=line_no,
        )
        edits.extend(prefix_edits)
        shared_prefix_merges += line_merges
        prefix_restarts += line_restarts
        adjacent_clause_repeats += line_clause_repeats
        after_embedded, embedded_units, embedded_blocks = collapse_exact_embedded_sentence_blocks(after_prefix)
        if after_embedded != after_prefix:
            edits.append(Edit("embedded_sentence_block", after_prefix, after_embedded, 0.99, line_no, True, False))
        units, adjacent_units, adjacent_blocks = collapse_adjacent_duplicate_blocks(split_sentence_units(after_embedded))
        body = "".join(units)
        if body != after_embedded:
            edits.append(Edit("adjacent_sentence_block", after_embedded, body, 0.99, line_no, True, False))
        after_final_gap, final_gap_removed = collapse_intra_sentence_short_gap_repeats(body)
        if after_final_gap != body:
            edits.append(Edit("short_gap_repeat", body, after_final_gap, 0.99, line_no, True, False))
        body = after_final_gap
        edits.extend(report_near_duplicate_sentence_candidates(body, line_no))
        output.append(body + newline)
        paragraphs_changed += int(body != before)
        gap_removed += line_gap_removed + final_gap_removed
        word_stutter_removed += line_word_stutter_removed
        removed_units += embedded_units + adjacent_units
        removed_blocks += embedded_blocks + adjacent_blocks
    cleaned = "".join(output).strip() + "\n"
    stats = CleaningStats(
        chars_before=len(original),
        chars_after=len(cleaned),
        chars_removed=len(original) - len(cleaned),
        paragraphs_changed=paragraphs_changed,
        removed_sentence_units=removed_units,
        repeated_blocks_removed=removed_blocks,
        intra_sentence_gap_chars_removed=gap_removed,
        word_stutter_chars_removed=word_stutter_removed,
        shared_prefix_merges=shared_prefix_merges,
        prefix_restarts_removed=prefix_restarts,
        adjacent_clause_repeats=adjacent_clause_repeats,
        rule_hits=count_rule_hits(edits),
        rule_char_changes=count_rule_char_changes(edits),
    )
    residual_patterns = scan_residual_short_gap_repeats(cleaned)
    return CleaningResult(cleaned, stats, edits, residual_patterns)
