from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

try:
    from langchain_core.runnables import RunnableConfig
except ModuleNotFoundError:
    RunnableConfig = Any

from . import brief_agent, evidence_agent, need_store
from .io import atomic_write_json
from .providers import DeterministicLinkClassifier
from .schemas import EmailEvidencePack

try:
    from langgraph.graph import StateGraph as EmailRunGraph
except ModuleNotFoundError:
    EmailRunGraph = Any


class EmailRunState(TypedDict):
    run_id: str
    account_id: str
    date: str
    artifact_dir: str
    input_scan_path: NotRequired[str]
    evidence_pack_path: NotRequired[str]
    need_store_path: NotRequired[str]
    brief_path: NotRequired[str]
    transitions: list[str]
    errors: list[str]


@dataclass
class EmailRunContext:
    policy: dict[str, Any]
    topic_map: dict[str, Any]
    scan: dict[str, Any] | None
    enrich_links: bool
    fetcher: Any
    reason: str
    review: dict[str, Any]
    pack: EmailEvidencePack | None
    needs: dict[str, Any] | None
    composition: brief_agent.BriefComposition | None


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def build_email_run_context(
    policy: dict[str, Any],
    topic_map: dict[str, Any],
    scan: dict[str, Any] | None,
    enrich_links: bool,
    fetcher: Any,
    reason: str,
    review: dict[str, Any],
) -> EmailRunContext:
    return EmailRunContext(
        policy=policy,
        topic_map=topic_map,
        scan=scan,
        enrich_links=enrich_links,
        fetcher=fetcher,
        reason=reason,
        review=review,
        pack=None,
        needs=None,
        composition=None,
    )


def initial_email_run_state(
    run_id: str,
    account_id: str,
    date: str,
    artifact_dir: Path,
    input_scan_path: Path | None,
) -> EmailRunState:
    state: EmailRunState = {
        "run_id": run_id,
        "account_id": account_id,
        "date": date,
        "artifact_dir": str(artifact_dir),
        "transitions": [],
        "errors": [],
    }
    if input_scan_path is not None:
        state["input_scan_path"] = str(input_scan_path)
    return state


def load_inputs(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    if context.scan is None:
        input_scan_path = state.get("input_scan_path")
        if not input_scan_path:
            raise ValueError("EmailRunState requires input_scan_path when context.scan is empty")
        context.scan = _read_json(Path(input_scan_path))
    return _transition(state, "load_inputs")


def build_evidence_pack(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    scan = _scan(context)
    pack = evidence_agent.normalize_evidence_pack(scan, context.policy)
    if context.enrich_links:
        pack = evidence_agent.enrich_scan_links(pack, context.policy, context.fetcher, context.topic_map)
    context.pack = pack
    return _transition(state, "build_evidence_pack")


def classify_links(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    pack = _pack(context)
    classified = evidence_agent.classify_evidence_links(
        pack,
        context.policy,
        DeterministicLinkClassifier(),
        _classification_confidence_threshold(context.policy),
    )
    context.pack = evidence_agent.apply_topics(classified, context.topic_map)
    return _transition(state, "classify_links")


def persist_evidence_pack(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    pack = _pack(context)
    artifact_dir = Path(state["artifact_dir"])
    path = artifact_dir / f"email-scan-{pack.date}.json"
    atomic_write_json(path, pack.to_dict())
    return {**_transition(state, "persist_evidence_pack"), "evidence_pack_path": str(path)}


def load_need_queue(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    artifact_dir = Path(state["artifact_dir"])
    context.needs = need_store.load_need_store(artifact_dir)
    return {**_transition(state, "load_need_queue"), "need_store_path": str(need_store.need_store_path(artifact_dir))}


def compose_brief(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    pack = _pack(context)
    needs = _needs(context)
    brief_path = Path(state["artifact_dir"]) / f"email-summary-{pack.date}.md"
    context.composition = brief_agent.compose_with_need_store(
        pack,
        context.topic_map,
        needs,
        str(brief_path),
        context.review,
        context.reason,
    )
    return {**_transition(state, "compose_brief"), "brief_path": str(brief_path)}


def reconcile_needs(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    if context.composition is None:
        pack = _pack(context)
        needs = _needs(context)
        context.needs = brief_agent.reconcile_need_store(
            pack,
            needs,
            _now_stamp(),
            brief_agent.DEFAULT_RECONCILE_MAX_CHECKS,
        )
    return _transition(state, "reconcile_needs")


def persist_needs(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    composition = _composition(context)
    artifact_dir = Path(state["artifact_dir"])
    need_store.save_need_store(artifact_dir, composition.need_store)
    return {**_transition(state, "persist_needs"), "need_store_path": str(need_store.need_store_path(artifact_dir))}


def persist_brief(state: EmailRunState, config: RunnableConfig) -> dict[str, Any]:
    context = _context(config)
    composition = _composition(context)
    brief_path = Path(state["brief_path"])
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(composition.email_intel_brief.markdown.rstrip() + "\n", encoding="utf-8")
    return _transition(state, "persist_brief")


def build_email_run_graph(checkpointer: Any) -> Any:
    StateGraph, END = _langgraph_types()
    graph = StateGraph(EmailRunState)
    graph.add_node("load_inputs", load_inputs)
    graph.add_node("build_evidence_pack", build_evidence_pack)
    graph.add_node("classify_links", classify_links)
    graph.add_node("persist_evidence_pack", persist_evidence_pack)
    graph.add_node("load_need_queue", load_need_queue)
    graph.add_node("compose_brief", compose_brief)
    graph.add_node("reconcile_needs", reconcile_needs)
    graph.add_node("persist_needs", persist_needs)
    graph.add_node("persist_brief", persist_brief)
    graph.set_entry_point("load_inputs")
    graph.add_edge("load_inputs", "build_evidence_pack")
    graph.add_edge("build_evidence_pack", "classify_links")
    graph.add_edge("classify_links", "persist_evidence_pack")
    graph.add_edge("persist_evidence_pack", "load_need_queue")
    graph.add_edge("load_need_queue", "compose_brief")
    graph.add_edge("compose_brief", "reconcile_needs")
    graph.add_edge("reconcile_needs", "persist_needs")
    graph.add_edge("persist_needs", "persist_brief")
    graph.add_edge("persist_brief", END)
    return graph.compile(checkpointer=checkpointer)


def build_in_memory_email_run_graph() -> Any:
    MemorySaver = _memory_saver_type()
    return build_email_run_graph(MemorySaver())


def run_email_run_graph(state: EmailRunState, context: EmailRunContext) -> EmailRunState:
    graph = build_in_memory_email_run_graph()
    config = {"configurable": {"thread_id": state["run_id"], "email_run_context": context}}
    return graph.invoke(state, config)


def _transition(state: EmailRunState, name: str) -> dict[str, Any]:
    return {"transitions": [*state.get("transitions", []), name]}


def _context(config: RunnableConfig) -> EmailRunContext:
    configurable = config.get("configurable", {})
    context = configurable.get("email_run_context")
    if not isinstance(context, EmailRunContext):
        raise ValueError("LangGraph config.configurable.email_run_context must be an EmailRunContext")
    return context


def _scan(context: EmailRunContext) -> dict[str, Any]:
    if context.scan is None:
        raise ValueError("EmailRunContext.scan is not loaded")
    return context.scan


def _pack(context: EmailRunContext) -> EmailEvidencePack:
    if context.pack is None:
        raise ValueError("EmailRunContext.pack is not built")
    return context.pack


def _needs(context: EmailRunContext) -> dict[str, Any]:
    if context.needs is None:
        raise ValueError("EmailRunContext.needs is not loaded")
    return context.needs


def _composition(context: EmailRunContext) -> brief_agent.BriefComposition:
    if context.composition is None:
        raise ValueError("EmailRunContext.composition is not built")
    return context.composition


def _classification_confidence_threshold(policy: dict[str, Any]) -> float:
    classification_policy = policy.get("classification", {})
    if isinstance(classification_policy, dict) and "confidence_threshold" in classification_policy:
        return float(classification_policy["confidence_threshold"])
    return evidence_agent.DEFAULT_CLASSIFICATION_CONFIDENCE_THRESHOLD


def _read_json(path: Path) -> dict[str, Any]:
    import json

    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _now_stamp() -> str:
    import podsum_email_summary as email_summary

    return email_summary.now_stamp()


def _langgraph_types() -> tuple[Any, Any]:
    try:
        from langgraph.graph import END, StateGraph
    except ModuleNotFoundError as error:
        raise RuntimeError(_missing_langgraph_message()) from error
    return StateGraph, END


def _memory_saver_type() -> Any:
    try:
        from langgraph.checkpoint.memory import MemorySaver
    except ModuleNotFoundError as error:
        raise RuntimeError(_missing_langgraph_message()) from error
    return MemorySaver


def _missing_langgraph_message() -> str:
    return "LangGraph is required for EmailRunGraph. Install the langgraph package before running graph orchestration."
