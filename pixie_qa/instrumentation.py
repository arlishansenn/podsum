"""真实 HTTP Reactor Pixie 调用的观测边界。"""
from __future__ import annotations

from collections.abc import Callable

import pixie

from pixie_qa.json_types import JsonObject


def controlled_pack(provider: Callable[[], JsonObject]) -> JsonObject:
    """Review 输入边界；名称为既有 dataset/trace 合约。"""
    return pixie.wrap(provider, purpose="input", name="review_input", description="通过真实 ingress kernel 提交的公开官方 EmailEvidencePack 与 Review scenario。")()


def controlled_brief_pack(provider: Callable[[], JsonObject]) -> JsonObject:
    """Brief 输入边界；不得与 Review instrumentation 共用 name。"""
    return pixie.wrap(provider, purpose="input", name="evidence_pack_input", description="通过真实 ingress kernel 提交的公开官方 EmailEvidencePack 与可选 Evidence action scenario。")()


def capture_result(result: JsonObject) -> JsonObject:
    """捕获真实五 contract daemon 的 Review cycle；既有输出名称不可变。"""
    outputs: tuple[tuple[str, str, str], ...] = (
        ("evidence_via", "output", "真实 EvidencePack VIA。"),
        ("brief_via", "output", "真实 Agent Brief VIA，含当前只读 fingerprint。"),
        ("review_via", "output", "真实 Agent 加 human counter-review 后的 Review collection VIA。"),
        ("review_binding", "state", "由最终 Review VIA 提取的 proposal 语义与 kernel 稳定绑定字段。"),
        ("review_action", "state", "经真实 HTTP Gateway 动态选择相反 verdict 的 action response。"),
        ("authority_rejection", "state", "真实 Gateway 对 Agent confirm 的拒绝，或明确未执行。"),
        ("review_receipts", "state", "真实 Reactor receipt collection，含 Review render receipt。"),
        ("receipt_cost", "state", "真实 Reactor disposition、receipt chain 与 Agent cost。"),
    )
    for name, purpose, description in outputs:
        pixie.wrap(result[name], purpose=purpose, name=name, description=description)
    return result


def capture_brief_result(result: JsonObject) -> JsonObject:
    """只捕获 Brief dataset 的最终真实 Evidence/Brief/action/cost 输出。"""
    outputs: tuple[tuple[str, str, str], ...] = (
        ("evidence_via", "output", "真实 current EvidencePack VIA。"),
        ("brief_via", "output", "真实 current candidate Brief VIA。"),
        ("update_receipt", "state", "真实 Evidence action response 与绑定最新 Brief source 的 ledger receipt。"),
        ("receipt_cost", "state", "真实 Reactor disposition、receipt chain 与 Agent cost。"),
    )
    for name, purpose, description in outputs:
        pixie.wrap(result[name], purpose=purpose, name=name, description=description)
    return result
