"""백그라운드 메모리 저장 LangGraph (extract -> embed -> reconcile -> save)."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import get_settings

from .embedder import EmbedderProtocol, extract_numbers, is_batch_all_trivial
from .repository import PersonaMemoryRepository, build_demoted_core_sentences
from .schemas import CORE_SLOTS


class MemState(TypedDict, total=False):
    user_id: str
    current_date: str
    source_type: str  # "practice" | "onboarding"
    dialogue_pairs: list[dict[str, Any]]
    latest_message_id: int
    existing_core: dict[str, Any]
    core_updates: dict[str, str]
    extracted_facts: list[dict[str, Any]]
    demoted_core_facts: list[str]
    fact_embeddings: list[list[float]]
    demoted_embeddings: list[list[float]]
    reconcile_plan: list[dict[str, Any]]


def _resolve_target_conflicts(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """한 배치에서 같은 기존 사실을 두 번 이상 UPDATE/DELETE 하지 않게 정리한다.

    새 사실마다 따로 판정하므로 두 사실이 같은 target_id 를 고를 수 있다. 그대로 저장하면 뒤의 UPDATE 가
    앞의 UPDATE 를 덮어쓰거나, DELETE 뒤의 UPDATE 가 대상 없음으로 버려져 정보가 사라진다.
    먼저 온 액션이 대상을 차지하고, 뒤에 온 UPDATE 는 새 행(ADD)으로, 뒤에 온 DELETE 는 NOOP 으로 바꾼다.
    """
    claimed: set[int] = set()
    resolved: list[dict[str, Any]] = []
    for item in plan:
        action, target_id = item.get("action"), item.get("target_id")
        if action in ("UPDATE", "DELETE") and target_id is not None:
            if target_id in claimed:
                item = {**item, "action": "ADD" if action == "UPDATE" else "NOOP", "target_id": None}
            else:
                claimed.add(target_id)
        resolved.append(item)
    return resolved


def create_memory_graph(
    *,
    repo: PersonaMemoryRepository,
    embedder: EmbedderProtocol,
    extractor: Any,
    judge: Any,
) -> CompiledStateGraph:
    settings = get_settings()
    workflow = StateGraph(MemState)

    async def extract_node(state: MemState) -> dict[str, Any]:
        pairs = state.get("dialogue_pairs") or []
        new_user_msgs = [p["user"] for p in pairs if not p.get("is_overlap") and p.get("user")]
        if not new_user_msgs or is_batch_all_trivial(new_user_msgs):
            return {"core_updates": {}, "extracted_facts": [], "demoted_core_facts": []}

        raw = await extractor.extract(
            pairs=pairs,
            current_date=state["current_date"],
            current_core=state.get("existing_core") or {},
        )
        valid_facts: list[dict[str, Any]] = []
        seen_texts: set[str] = set()
        for item in raw.facts:
            if item.confidence < settings.memory_confidence_tentative:  # < 0.5 버림
                continue
            clean_text = " ".join(item.fact.split())
            if not clean_text or clean_text in seen_texts:
                continue
            seen_texts.add(clean_text)
            status = "confirmed" if item.confidence >= settings.memory_confidence_confirmed else "tentative"
            valid_facts.append(
                {
                    "category": item.category,
                    "fact": clean_text,
                    "confidence": item.confidence,
                    "status": status,
                    "source_message_id": item.source_message_id or state.get("latest_message_id"),
                }
            )

        core_updates = {
            k: " ".join(v.split()) for k, v in (raw.core_updates or {}).items() if k in CORE_SLOTS and v and v.strip()
        }
        demoted = build_demoted_core_sentences(state.get("existing_core") or {}, core_updates)
        return {
            "core_updates": core_updates,
            "extracted_facts": valid_facts,
            "demoted_core_facts": demoted,
        }

    def route_after_extract(state: MemState) -> str:
        if state.get("extracted_facts") or state.get("core_updates"):
            return "embed"
        return "save"  # 단답 배치 스킵 시에도 save 노드를 거쳐 커서(last_processed_message_id)를 전진시킨다

    async def embed_node(state: MemState) -> dict[str, Any]:
        facts = state.get("extracted_facts") or []
        demoted = state.get("demoted_core_facts") or []
        fact_vecs = await embedder.encode_batch([f["fact"] for f in facts]) if facts else []
        demoted_vecs = await embedder.encode_batch(demoted) if demoted else []
        return {"fact_embeddings": fact_vecs, "demoted_embeddings": demoted_vecs}

    async def reconcile_node(state: MemState) -> dict[str, Any]:
        user_id = state["user_id"]
        facts = state.get("extracted_facts") or []
        vecs = state.get("fact_embeddings") or []

        plan: list[dict[str, Any]] = []
        needs_judge: list[tuple[int, dict[str, Any], list[float], list[tuple[Any, float]]]] = []

        for idx, (fact_item, vec) in enumerate(zip(facts, vecs, strict=False)):
            candidates = await repo.get_reconcile_candidates(user_id, vec, category=fact_item["category"], k=3)
            nearest, top_sim = candidates[0] if candidates else (None, 0.0)
            same_numbers = nearest is not None and extract_numbers(fact_item["fact"]) == extract_numbers(nearest.fact)

            if nearest is not None and top_sim >= settings.memory_reconcile_noop_sim and same_numbers:
                action = "PROMOTE" if nearest.status == "tentative" else "NOOP"
                plan.append({**fact_item, "embedding": vec, "action": action, "target_id": nearest.id})
            elif top_sim >= settings.memory_reconcile_judge_sim or len(candidates) > 0:
                needs_judge.append((idx, fact_item, vec, candidates))
            else:
                plan.append({**fact_item, "embedding": vec, "action": "ADD", "target_id": None})

        if needs_judge:
            judged = await judge.judge_batch(needs_judge)
            for (_, fact_item, vec, _), decision in zip(needs_judge, judged, strict=False):
                final_text = decision.get("updated_fact") or fact_item["fact"]
                if final_text != fact_item["fact"]:
                    vec = await embedder.encode(final_text)
                plan.append(
                    {
                        **fact_item,
                        "fact": final_text,
                        "embedding": vec,
                        "action": decision["action"],
                        "target_id": decision.get("target_id"),
                    }
                )
        return {"reconcile_plan": _resolve_target_conflicts(plan)}

    async def save_node(state: MemState) -> dict[str, Any]:
        user_id = state["user_id"]
        source_type = state.get("source_type") or "practice"
        model_name = settings.embedding_model_name

        for sentence, vec in zip(
            state.get("demoted_core_facts") or [],
            state.get("demoted_embeddings") or [],
            strict=False,
        ):
            await repo.add_fact(
                user_id=user_id,
                category="과거이력",
                fact=sentence,
                confidence=1.0,
                status="confirmed",
                embedding=vec,
                embedding_model=model_name,
                source_type="core_demote",
                source_message_id=state.get("latest_message_id"),
            )

        # 커서는 연습대화(practice_messages.id)에서만 전진시킨다. 온보딩 turn id 는 다른 번호 공간이라 건드리면 안 된다
        cursor = state.get("latest_message_id") if source_type == "practice" else None
        await repo.update_core_profile(user_id, state.get("core_updates") or {}, last_processed_message_id=cursor)

        for item in state.get("reconcile_plan") or []:
            await repo.apply_reconcile_action(
                action=item["action"],
                target_id=item.get("target_id"),
                user_id=user_id,
                category=item["category"],
                fact=item["fact"],
                confidence=item["confidence"],
                status=item["status"],
                embedding=item["embedding"],
                embedding_model=model_name,
                source_type=source_type,
                source_message_id=item.get("source_message_id"),
            )
        return {}

    workflow.add_node("extract", extract_node)
    workflow.add_node("embed", embed_node)
    workflow.add_node("reconcile", reconcile_node)
    workflow.add_node("save", save_node)

    workflow.set_entry_point("extract")
    workflow.add_conditional_edges("extract", route_after_extract, {"embed": "embed", "save": "save"})
    workflow.add_edge("embed", "reconcile")
    workflow.add_edge("reconcile", "save")
    workflow.add_edge("save", END)

    return workflow.compile()
