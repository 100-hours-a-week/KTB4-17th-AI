"""v2 시뮬레이션 테스트 공용 재료 — 페르소나·컨텍스트·fact·가짜 LLM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from app.features.persona.schemas import PersonaResponse
from app.v2.simulation.schemas import FactRow, LLMResult, ScriptLineV2, SimContext, ToolCall

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
DIM = 8


def persona(pid: str, **kw) -> PersonaResponse:
    base = dict(
        persona_id=pid,
        scores={"avoidance": 40, "contact_rhythm": 60, "seriousness": 70},
        interests=[],
        routine=[],
        date_prefer=[],
    )
    return PersonaResponse(**{**base, **kw})


def ctx(**kw) -> SimContext:
    base = dict(
        me_user_id="u-a",
        partner_user_id="u-b",
        persona_a_id="pa",
        persona_b_id="pb",
        persona_a=persona("pa", interests=["등산하기"], routine=["주말 캠핑"]),
        persona_b=persona("pb", interests=["독서 모임"], routine=["아침 요가"]),
        name_a="민수",
        name_b="지은",
    )
    return SimContext(**{**base, **kw})


def vec(*hot: float, dim: int = DIM) -> list[float]:
    """앞 칸부터 값을 채운 정규화 벡터."""
    v = np.zeros(dim, dtype=np.float64)
    v[: len(hot)] = hot
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


def fact(fid: int, text: str, embedding: list[float], *, category: str = "취미", age_days: int = 0) -> FactRow:
    return FactRow(id=fid, category=category, text=text, embedding=embedding, created_at=NOW - timedelta(days=age_days))


# 자연스럽게 서로 다른 대사 (따라 하기·작별·자기 호칭에 걸리지 않게)
_A_LINES = [
    "안녕하세요 지은님, 반가워요. 오늘 하루는 어떠셨어요?",
    "저는 주말마다 산에 가는 편이라 오늘도 다리가 좀 뻐근해요.",
    "요즘은 캠핑 장비 고르는 재미에 빠져 있어요 ㅎㅎ",
    "책은 어떤 장르를 주로 읽으세요? 추천받고 싶어요.",
    "연락은 저는 자주 하는 편인데 괜찮으실까요?",
    "데이트는 걷는 코스를 좋아해서 공원 산책도 좋아요.",
    "아침에 요가하면 하루가 개운하다는 말 들으니 궁금해지네요.",
    "다음엔 맛있는 브런치집 같이 가 보면 어떨까요?",
    "말씀 나눠 보니 생각보다 비슷한 점이 많아 신기해요.",
    "오늘 대화 즐거웠어요, 조심히 들어가세요!",
]
_B_LINES = [
    "안녕하세요 민수님! 저는 오늘 꽤 바빴는데 이제 좀 쉬어요.",
    "와 산이라니 체력이 좋으시네요, 저는 독서 모임이 취미예요.",
    "캠핑은 한 번도 안 해 봐서 어떤 느낌인지 궁금해요.",
    "요즘은 에세이를 많이 읽어요, 짧게 끊어 읽기 좋거든요.",
    "저는 연락은 하루 몇 번 정도가 편한 것 같아요.",
    "산책 좋죠, 저도 조용한 길을 걷는 걸 좋아해요.",
    "아침 요가는 몸이 풀려서 꾸준히 하고 있어요 ㅎㅎ",
    "브런치 좋아요, 제가 아는 곳도 하나 있어요.",
    "저도 그렇게 느꼈어요, 이야기가 편하게 흘러가네요.",
    "저도 즐거웠어요, 민수님도 편안한 밤 보내세요!",
]


def good_script(turns: int = 10) -> list[ScriptLineV2]:
    lines = []
    for r in range(turns):
        a = _A_LINES[r % 9] if r < turns - 1 else _A_LINES[-1]
        b = _B_LINES[r % 9] if r < turns - 1 else _B_LINES[-1]
        lines.append(ScriptLineV2(i=2 * r, speaker="a", text=a))
        lines.append(ScriptLineV2(i=2 * r + 1, speaker="b", text=b))
    return lines


def script_call(lines: list[ScriptLineV2]) -> LLMResult:
    return LLMResult(
        calls=[ToolCall(name="submit_script", arguments={"lines": [x.model_dump() for x in lines]})],
        prompt_tokens=100,
        completion_tokens=200,
    )


def report_call(headline: str = "대화가 편한 두 사람") -> LLMResult:
    return LLMResult(
        calls=[
            ToolCall(
                name="submit_report",
                arguments={
                    "headline": headline,
                    "summary": "민수님과 지은님은 편하게 대화를 이어갔어요.",
                    "area_comments": {"intimacy": "거리감이 비슷해요."},
                    "highlights": [{"kind": "click", "turn_index": 3, "quote": "인용", "why": "잘 통함"}],
                    "strengths": ["대화 리듬"],
                    "cautions": ["연락 빈도"],
                    "date_comment": "산책 데이트",
                },
            )
        ]
    )


class FakeLLM:
    """이름(name)별 응답 큐. 큐 항목이 Exception 이면 던진다. 호출 기록을 남긴다."""

    def __init__(self, responses: dict[str, list]) -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict] = []

    async def __call__(self, *, system, user, tools, max_tokens, timeout, name, model=None, metadata=None):
        self.calls.append({"name": name, "user": user, "system": system, "timeout": timeout, "model": model})
        key = name.removeprefix("simulation-v2-")
        queue = self.responses.get(key) or self.responses.get(name)
        if not queue:
            raise AssertionError(f"unexpected llm call: {name}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def count(self, key: str) -> int:
        return sum(1 for c in self.calls if c["name"].removeprefix("simulation-v2-") == key)


def judge_pass() -> LLMResult:
    return LLMResult(calls=[ToolCall(name="submit_review", arguments={"issues": [], "pass": True})])


def judge_flag(i: int, itype: str = "judge_mixup") -> LLMResult:
    issue = {"i": i, "type": itype, "evidence": "혼동"}
    return LLMResult(calls=[ToolCall(name="submit_review", arguments={"issues": [issue], "pass": False})])


def patch(i: int, text: str, refs: list[str] | None = None) -> LLMResult:
    item = {"i": i, "text": text, "fact_refs": refs or []}
    return LLMResult(calls=[ToolCall(name="submit_patches", arguments={"patches": [item]})])
