"""v2 사용자 사실 추출(extract) 및 중복/모순 판정(judge) 에이전트.

LLM 호출은 v1 과 같은 경로다 — persona.agents._call_json (OpenAI 호환 클라이언트, llm_model, llm_retry 재시도).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langfuse import observe
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata
from app.features.persona.agents import LLMError, _call_json

from .embedder import normalize_chat_text
from .schemas import ExtractionOutput, ReconcileBatchOutput

logger = logging.getLogger(__name__)


class MemoryLLMFailed(Exception):
    """LLM 호출 실패 또는 출력 형식 오류. 작업은 failed 로 끝나고 커서는 전진하지 않는다 (다음 트리거에서 재시도)."""


EXTRACT_SYSTEM_PROMPT = """\
당신은 대화에서 사용자의 지속적인 사실만 뽑는 기록 담당자입니다.
입력: 현재 기준 날짜({current_date}), 현재 코어 프로필, AI 메시지와 사용자 메시지 쌍 (AI 메시지는 맥락용)

규칙:
1. 사용자가 자기 자신에 대해 진지하게 말한 지속성 있는 사실만 추출한다.
2. 농담("ㅋㅋ" 동반 과장), 가정("만약 ~라면", "로또 되면"), 역할극은 추출하지 않는다.
3. "오늘 점심에 김치찌개를 먹었다" 같은 일회성 일상/단기 이벤트는 추출하지 않는다.
4. AI 페르소나가 한 말은 사용자 정보로 절대 추출하지 않는다 (AI 발화는 사용자의 짧은 답 "ㅇㅇ" 등을 해석하는 맥락으로만 참조).
5. 지시어("그거", "거기")는 AI 질문을 참고해 풀어서 자기완결적인 한 문장(10~30토큰)으로 쓴다.
6. 각 사실에 confidence(0.0~1.0)를 붙인다 (명확한 직접 진술: 0.85~1.0, 완곡한 표현/추정: 0.55~0.75).
7. 직업(job), 거주지역(region), 연애관(love_style), 성격(personality), 결혼계획(marriage_plan) 변경이 확인되면 core_updates 에 기록한다.
   이 다섯 키 외의 키는 쓰지 않는다. 확인되지 않은 키는 생략한다.
8. "[이전 맥락(오버랩)]" 표시가 붙은 쌍은 이해를 돕는 용도다. 거기서만 나온 내용은 추출하지 않는다.

# 출력 — JSON 객체 하나만. 설명·코드펜스 금지. 추출할 것이 없으면 빈 값으로 둔다.
{
  "core_updates": {"job": "...", "region": "..."},
  "facts": [
    {"category": "취미 | 가치관 | 일상 | 연애관 | 과거이력 중 하나", "fact": "자기완결적인 한 문장", "confidence": 0.9, "source_message_id": 123}
  ]
}
"""

RECONCILE_SYSTEM_PROMPT = """\
당신은 사용자의 기존 기억(confirmed / tentative)과 새로 추출된 사실을 비교해 DB 반영 액션을 결정하는 심판입니다.
입력은 JSON 배열이며 각 원소는 new_fact_index, new_fact, category, existing_candidates 를 가진다.

액션 기준:
- NOOP: 기존 사실과 의미 및 수치가 완전히 같고 기존 status 가 이미 confirmed 인 경우
- PROMOTE: 기존 사실과 같은 내용인데 기존 status 가 tentative 여서 confirmed 로 승격해야 하는 경우 (target_id 지정)
- UPDATE: 취향 반전("등산을 좋아한다" -> "등산을 싫어한다"), 수치/빈도 변경("주 3회" -> "주 5회"), 최신 정보 갱신이 필요한 경우 (target_id 와 최신 문장 updated_fact 지정)
- DELETE: 기존 사실이 명시적으로 부정되어 삭제만 해야 하는 경우 (target_id 지정)
- ADD: 기존 후보들과 별개의 새로운 사실인 경우

target_id 는 반드시 existing_candidates 의 id 중에서만 고른다.

# 출력 — JSON 객체 하나만. 설명·코드펜스 금지
{
  "decisions": [
    {"new_fact_index": 0, "action": "ADD | UPDATE | DELETE | PROMOTE | NOOP 중 하나", "target_id": null, "updated_fact": null}
  ]
}
"""


class PersonaMemoryExtractor:
    @observe(name="v2-memory-extract", capture_input=False, capture_output=False)
    async def extract(
        self,
        *,
        pairs: list[dict[str, Any]],
        current_date: str,
        current_core: dict[str, Any],
        trace_metadata: LangfuseMetadata | None = None,
    ) -> ExtractionOutput:
        lines: list[str] = []
        for p in pairs:
            tag = "[이전 맥락(오버랩)]" if p.get("is_overlap") else f"[msg_id={p.get('message_id')}]"
            ai_txt = normalize_chat_text(p.get("ai") or "")
            user_txt = normalize_chat_text(p.get("user") or "")
            lines.append(f"{tag}\nAI: {ai_txt}\nUser: {user_txt}")

        content = f"현재 코어 프로필: {json.dumps(current_core, ensure_ascii=False)}\n\n대화 쌍 목록:\n" + "\n\n".join(
            lines
        )
        with propagate_langfuse_metadata(trace_metadata):
            try:
                data = await _call_json(
                    system=EXTRACT_SYSTEM_PROMPT.replace("{current_date}", current_date),
                    messages=[{"role": "user", "content": content}],
                    max_tokens=1500,
                    timeout=get_settings().memory_extract_timeout_s,
                    name="v2-memory-extract",
                    metadata=trace_metadata,
                )
            except LLMError as e:
                raise MemoryLLMFailed(f"extract LLM call failed: {e}") from e
        try:
            return ExtractionOutput.model_validate(data)
        except ValidationError as e:
            logger.warning("v2 memory extract validation failed: %s", e)
            raise MemoryLLMFailed(f"invalid extraction: {e}") from e


class PersonaMemoryReconcileJudge:
    @observe(name="v2-memory-reconcile", capture_input=False, capture_output=False)
    async def judge_batch(
        self,
        items: list[tuple[int, dict[str, Any], list[float], list[tuple[Any, float]]]],
        *,
        trace_metadata: LangfuseMetadata | None = None,
    ) -> list[dict[str, Any]]:
        payload = []
        for idx, fact_item, _, candidates in items:
            payload.append(
                {
                    "new_fact_index": idx,
                    "new_fact": fact_item["fact"],
                    "category": fact_item["category"],
                    "existing_candidates": [
                        {
                            "id": c.id,
                            "category": c.category,
                            "fact": c.fact,
                            "status": c.status,
                            "similarity": round(sim, 3),
                        }
                        for c, sim in candidates
                    ],
                }
            )

        with propagate_langfuse_metadata(trace_metadata):
            try:
                data = await _call_json(
                    system=RECONCILE_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}],
                    max_tokens=1200,
                    timeout=get_settings().memory_reconcile_timeout_s,
                    name="v2-memory-reconcile",
                    metadata=trace_metadata,
                )
            except LLMError as e:
                raise MemoryLLMFailed(f"reconcile LLM call failed: {e}") from e
        try:
            out = ReconcileBatchOutput.model_validate(data)
        except ValidationError as e:
            logger.warning("v2 memory reconcile validation failed: %s", e)
            raise MemoryLLMFailed(f"invalid reconcile output: {e}") from e

        by_idx = {d.new_fact_index: d for d in out.decisions}
        results: list[dict[str, Any]] = []
        for idx, fact_item, _, candidates in items:
            valid_ids = {c.id for c, _ in candidates}
            dec = by_idx.get(idx)
            # 판정이 빠졌거나, 후보에 없는 target_id 를 지어냈거나, 대상이 필요한 액션인데 대상이 없으면 안전하게 ADD
            if dec is None or (dec.action in ("UPDATE", "DELETE", "PROMOTE") and dec.target_id not in valid_ids):
                results.append({"action": "ADD", "target_id": None, "updated_fact": fact_item["fact"]})
            else:
                results.append(
                    {
                        "action": dec.action,
                        "target_id": dec.target_id if dec.action != "ADD" else None,
                        "updated_fact": dec.updated_fact or fact_item["fact"],
                    }
                )
        return results
