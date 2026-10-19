"""v2 연습대화 — 사용자 메모리 검색과 프롬프트 섹션 / 가드레일 근거 조립."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.v2.persona.embedder import EmbedderProtocol, is_trivial_reaction
from app.v2.persona.repository import PersonaMemoryRepository

CORE_LABELS = {
    "job": "직업/하는 일",
    "region": "거주 지역",
    "love_style": "연애관",
    "personality": "성격/성향",
    "marriage_plan": "결혼/관계 계획",
}

MEMORY_SECTION = """\
## {me}님에 대해 알고 있는 것 (위 "기본 정보만 압니다" 보다 우선)
### 프로필
{core_lines}

### 지금 대화와 관련된 기억 (기록일 참고)
{fact_lines}
위는 {me}님이 직접 밝힌 사실입니다. 기록일을 보고 지난 일과 최근 상태를 구분하세요.
대화 흐름에 자연스러울 때만 한 번에 하나씩 쓰고, 목록처럼 읊지 않습니다.
"저번에 말했잖아요" 처럼 이전 만남이 있었던 듯 말하지 않습니다.
"""


@dataclass
class MemoryContext:
    core_profile: dict[str, Any] = field(default_factory=dict)
    facts: list[tuple[str, str]] = field(default_factory=list)  # (YYYY-MM-DD, 문장)

    @property
    def is_empty(self) -> bool:
        return not self.core_profile and not self.facts


def _sanitize(text: str) -> str:
    """개행·마크다운 헤더 기호를 제거해 프롬프트 인젝션을 막고 200자로 자른다."""
    return " ".join(text.replace("#", "").splitlines()).strip()[:200]


async def load_memory_context(
    db: AsyncSession,
    *,
    user_id: str,
    query: str | None,
    embedder: EmbedderProtocol,
) -> MemoryContext:
    """코어 프로필은 항상, Fact Top-K 는 의미 있는 발화일 때만 벡터 검색한다 (단답·오프닝은 코어만)."""
    repo = PersonaMemoryRepository(db)
    core = await repo.get_core(user_id)
    profile = dict(core.profile) if core and core.profile else {}
    if not query or is_trivial_reaction(query):
        return MemoryContext(core_profile=profile)

    settings = get_settings()
    q_vec = await embedder.encode_query(query)
    facts = await repo.search_confirmed_facts(
        user_id,
        q_vec,
        top_k=settings.memory_top_k,
        max_distance=settings.memory_max_cosine_distance,
    )
    return MemoryContext(core_profile=profile, facts=facts)


def render_memory_section(my_name: str, ctx: MemoryContext) -> str:
    core_lines = [f"- {CORE_LABELS.get(k, k)}: {_sanitize(str(v))}" for k, v in ctx.core_profile.items() if v] or [
        "- (아직 알려진 프로필 없음)"
    ]
    fact_lines = [f"- [{d} 기록] {_sanitize(f)}" for d, f in ctx.facts] or ["- (지금 대화와 직접 연관된 기억 없음)"]
    return MEMORY_SECTION.format(
        me=_sanitize(my_name),
        core_lines="\n".join(core_lines),
        fact_lines="\n".join(fact_lines),
    )


def grounding_texts(ctx: MemoryContext) -> list[str]:
    """가드레일 `user_texts` 에 더할 허용 근거 — 코어 값과 검색된 Fact 문장."""
    return [str(v) for v in ctx.core_profile.values() if v] + [fact for _, fact in ctx.facts]
