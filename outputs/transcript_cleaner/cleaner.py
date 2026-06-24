#!/usr/bin/env python3
"""Clean transcript noise and local ASR repetition patterns."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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


@dataclass
class CleaningStats:
    chars_before: int = 0
    chars_after: int = 0
    chars_removed: int = 0
    paragraphs_changed: int = 0
    removed_sentence_units: int = 0
    repeated_blocks_removed: int = 0
    intra_sentence_gap_chars_removed: int = 0
    shared_prefix_merges: int = 0
    prefix_restarts_removed: int = 0
    adjacent_clause_repeats: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Edit:
    type: str
    before: str
    after: str
    confidence: float
    line: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["before"] = summarize_edit_text(self.before)
        value["after"] = summarize_edit_text(self.after)
        return value


@dataclass
class CleaningResult:
    text: str
    stats: CleaningStats
    edits: List[Edit]

    def __iter__(self) -> Iterator[Any]:
        yield self.text
        yield self.stats


def summarize_edit_text(text: str, limit: int = 240) -> str:
    value = text.replace("\n", "\\n")
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


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


def collapse_intra_sentence_short_gap_repeats(
    paragraph: str,
    *,
    min_match_len: int = 20,
    max_gap: int = 10,
) -> Tuple[str, int]:
    removed = 0
    value = paragraph
    for _ in range(10):
        changed = False
        for match_len in range(min(50, len(value) // 2), min_match_len - 1, -1):
            for index in range(0, len(value) - match_len):
                prefix = value[index : index + match_len]
                if re.match(r"^[\s\d，。！？；：、\"“”「」『』\-,.…— ]+$", prefix):
                    continue
                if len(set(prefix)) <= 2:
                    continue
                rest = value[index + match_len :]
                for gap in range(0, max_gap + 1):
                    if index + match_len + gap + match_len > len(value):
                        break
                    if rest[gap : gap + match_len] != prefix:
                        continue
                    value = value[: index + match_len] + value[index + match_len + gap + match_len :]
                    removed += gap + match_len
                    changed = True
                    break
                if changed:
                    break
            if changed:
                break
        if not changed:
            break
    return value, removed


def count_cjk(text: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", text))


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
    # Never cut a Latin word in half, e.g. CoreWeave / Crusoe -> shared "C".
    prefix = re.sub(r"[A-Za-z0-9_]+$", "", prefix)
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
    return bool(
        re.search(r"(?:……|…|—|--|\b[xX]{3,}\b|[xX]{4,}|[?？]{3,})", value)
        or re.search(r"(?:嗯|呃|那个|怎么说|就是)[，,、…—\s]*$", value)
    )


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
        if left_body == right_body:
            parts[index + 1] = ""
            parts[index + 2] = ""
            edits.append(Edit("adjacent_clause_repeat", before, parts[index], 1.0, line_no))
            clause_repeats += 1
            continue
        if right_body.startswith(left_body) and len(right_body) - len(left_body) >= 2:
            parts[index] = left_leading + right_body
            parts[index + 1] = ""
            parts[index + 2] = ""
            edits.append(Edit("prefix_extension", before, parts[index], 1.0, line_no))
            clause_repeats += 1
            continue
        if left_body.startswith(right_body) and len(left_body) - len(right_body) >= 2:
            parts[index + 1] = ""
            parts[index + 2] = ""
            edits.append(Edit("prefix_extension", before, parts[index], 1.0, line_no))
            clause_repeats += 1
            continue

        raw_prefix = longest_common_prefix(left_body, right_body)
        prefix = re.sub(r"[A-Za-z0-9_]+$", "", raw_prefix).rstrip()
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
            edits.append(Edit("prefix_restart", before, after, 0.95, line_no))
            restarts += 1
            continue

        safe_prefix = safe_coordination_prefix(prefix)
        if not safe_prefix:
            edits.append(Edit("shared_prefix_candidate", before, before, 0.5, line_no))
            index += 2
            continue
        if safe_prefix != prefix:
            prefix = safe_prefix
            left_tail = left_body[len(prefix) :]
            right_tail = right_body[len(prefix) :]
        parts[index + 2] = right_leading + right_tail.lstrip()
        after = parts[index] + separator + parts[index + 2]
        edits.append(Edit("shared_prefix_coordination", before, after, 0.99, line_no))
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
        edits.append(Edit("fillers_and_noise", text, normalized, 1.0, 0))
    text = normalized
    output: List[str] = []
    paragraphs_changed = 0
    removed_units = 0
    removed_blocks = 0
    gap_removed = 0
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
        after_gap, line_gap_removed = collapse_intra_sentence_short_gap_repeats(body)
        if after_gap != body:
            edits.append(Edit("short_gap_repeat", body, after_gap, 0.99, line_no))
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
            edits.append(Edit("embedded_sentence_block", after_prefix, after_embedded, 0.99, line_no))
        units, adjacent_units, adjacent_blocks = collapse_adjacent_duplicate_blocks(split_sentence_units(after_embedded))
        body = "".join(units)
        if body != after_embedded:
            edits.append(Edit("adjacent_sentence_block", after_embedded, body, 0.99, line_no))
        output.append(body + newline)
        paragraphs_changed += int(body != before)
        gap_removed += line_gap_removed
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
        shared_prefix_merges=shared_prefix_merges,
        prefix_restarts_removed=prefix_restarts,
        adjacent_clause_repeats=adjacent_clause_repeats,
    )
    return CleaningResult(cleaned, stats, edits)
