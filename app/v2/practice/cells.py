"""연습대화 애착 9칸 모델 (RunPod vLLM) — 칸 고르기, 학습 때와 같은 프롬프트, 서버 상태.

서버: persona_model 저장소 docs/모델학습/인프라/9칸_서버_실행_설명서.md. Kanana-2-1.3b + 가드레일 v5 병합 모델 위에
칸마다 LoRA 하나. 요청 본문의 model 에 칸 이름(`high_anx0.75` 등)을 넣으면 그 칸 성향으로 답한다.

라우팅 규칙
  - 세션을 시작할 때 서버가 살아 있으면 그 세션은 9칸('slm'), 아니면 메인 LLM('llm'). 세션에 저장해 고정한다.
    대화 도중 Pod 가 켜져도 메인 LLM 세션은 그대로 — 중간에 말투가 갑자기 바뀌지 않게.
  - 9칸 세션인데 서버가 내려갔으면(꺼짐·로딩 중·첫 글자 전 실패) 그 턴부터 메인 LLM 으로 내리고 되돌리지 않는다.
  - 서버 상태는 app/core/slm.Health 를 그대로 쓴다. 한 번 실패하면 '내려감', 백그라운드에서 /health 가 200 이면 복구.
    복구는 새로 시작하는 세션에만 영향을 준다.

프롬프트: 1.3B 는 persona_model 의 compact 프롬프트로 학습했다 (prompt_engine/compact_prompt.py,
training/attach_axes.py). 메인 LLM 의 긴 SYSTEM_TEMPLATE 을 넣으면 학습 때와 입력이 달라 칸 성향이 흐려진다.
그래서 여기서 학습 때와 같은 모양을 만든다. 성향 문장도 앱(schemas.SCORED)이 아니라 학습 쪽 문장을 쓴다.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import httpx
from langfuse import get_client as get_langfuse
from langfuse.openai import AsyncOpenAI

from app.core import slm
from app.core.config import get_settings
from app.features.persona.schemas import PersonaResponse

logger = logging.getLogger(__name__)

ENGINE_SLM = "slm"
ENGINE_LLM = "llm"

# 학습·평가 때 값 (설명서 3장). 0 이면 불안 α1 칸에서 끝없는 반복이 드물게 나온다 (T-23)
TEMPERATURE = 0.7
MAX_TOKENS = 160

# 점수 → 칸. 프롬프트 성향 문장과 같은 경계 (35 이하 낮음, 65 이상 높음)
LOW_TO = 35
HIGH_FROM = 65


def _avoid_level(score: int | None) -> str:
    if score is None:
        return "mid"
    if score <= LOW_TO:
        return "low"
    if score >= HIGH_FROM:
        return "high"
    return "mid"


def _anxiety_alpha(score: int | None) -> str:
    if score is None or score <= LOW_TO:
        return "0"
    if score >= HIGH_FROM:
        return "1"
    return "0.75"


def cell_for(persona: PersonaResponse) -> str:
    """회피 72·불안 50 → "high_anx0.75". 값이 없는 축은 중간 회피·불안 0 (병합 모델 그대로에 가깝게)."""
    return f"{_avoid_level(persona.scores.get('avoidance'))}_anx{_anxiety_alpha(persona.scores.get('anxiety'))}"


# ── 학습 때와 같은 compact 프롬프트 ─────────────────────────────

# prompt_engine/compact_prompt.CORE_RULES 그대로
CORE_RULES = """\
너는 아래 프로필의 인물 본인으로서 {me}와 1:1 메신저로 대화한다.
- AI·인공지능·챗봇·언어모델·페르소나·연기라는 말을 하지 않는다.
- 프로필과 대화에 없는 사실(직업·나이·사는 곳·이전 만남)은 지어내지 않는다.
- 상대를 평가·진단·조언하지 않고, 자신을 3인칭으로 부르지 않는다.
- 항상 존댓말(해요체)로 말한다."""

# training/attach_avoid.NEUTRAL_SPEECH — 9칸 학습은 말투 줄을 항상 이 값으로 고정했다
NEUTRAL_SPEECH = "존댓말, 적당한 길이, 감정 자연스러움, 대화 주도 보통"

# 9칸 학습(attach_axes.SKIP_TRAITS)에서 애착·연락·진지도 관련 성향 줄은 모두 뺐다 — 칸이 정한다.
# 남는 성향 줄은 아래뿐이다. 문장은 persona_model prompt_engine/profile.SCORED 의 것 (앱 문장과 다르다)
_MAIN_TRAITS = {
    "openness": ("새로운 장소나 색다른 활동을 먼저 찾아봄", "늘 가던 곳, 익숙한 방식을 선호함"),
}
_IDEAL_TRAITS = {
    "ideal_warmth": ("다정하고 따뜻하게 챙겨 주는 사람", "감정 표현이 과하지 않고 담백한 사람"),
    "ideal_vitality": ("밝고 유쾌하게 분위기를 띄우는 사람", "차분하고 조용한 에너지를 가진 사람"),
    "ideal_status": ("자기 일에 열정적이고 배울 점이 있는 사람", "격식 없이 소탈하고 편한 사람"),
}


def _extreme_lines(scores: dict[str, int | None], traits: dict[str, tuple[str, str]]) -> list[str]:
    """극단값(35 이하, 65 이상)만, |점수-50| 큰 순서로. 학습 쪽은 점수가 없으면 50 으로 본다 — 줄이 안 생긴다."""
    picked = []
    for key, (high, low) in traits.items():
        v = scores.get(key)
        if v is None:
            continue
        if v >= HIGH_FROM:
            picked.append((abs(v - 50), high))
        elif v <= LOW_TO:
            picked.append((abs(v - 50), low))
    return [desc for _, desc in sorted(picked, key=lambda p: -p[0])]


def system_prompt(*, partner_name: str, partner: PersonaResponse, my_name: str, my_mbti: str | None = None) -> str:
    """학습 때 학생 입력과 같은 모양: 핵심 규칙 + [프로필] + [상대].

    성별은 페르소나에 없어 뺀다 (학습 카드에는 있었지만 없으면 줄에서 빠지는 구조라 형식은 같다)."""
    head = " / ".join(x for x in (partner_name, partner.mbti) if x)
    lines = ["[프로필]", head, f"말투: {NEUTRAL_SPEECH}"]
    main = _extreme_lines(partner.scores, _MAIN_TRAITS)
    if main:
        lines.append("성향: " + " / ".join(main))
    ideal = _extreme_lines(partner.scores, _IDEAL_TRAITS)
    if ideal:
        lines.append("이상형: " + " / ".join(ideal))
    me = f"[상대] {my_name} ({my_mbti})" if my_mbti else f"[상대] {my_name}"
    return "\n\n".join([CORE_RULES.format(me=my_name), "\n".join(lines), me])


# ── 서버 ─────────────────────────────────────────────────


def enabled() -> bool:
    return bool(get_settings().practice_slm_base_url)


async def _check() -> bool:
    return await slm.check_health(get_settings().practice_slm_base_url)


health = slm.Health(check=_check, name="practice 9-cell SLM")


def available() -> bool:
    """지금 새 세션을 9칸으로 시작해도 되는가."""
    return enabled() and health.up


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.practice_slm_base_url,
        api_key=settings.practice_slm_api_key or "EMPTY",
        # 재시도 대신 메인 LLM 으로 넘긴다. 꺼진 Pod 에 붙으려고 오래 기다리지 않게 연결은 짧게
        max_retries=0,
        timeout=httpx.Timeout(30.0, connect=3.0),
    )


def record_fallback(error: BaseException) -> None:
    """9칸 실패 기록. 서버가 없다는 신호(연결 실패·5xx·404·타임아웃)면 '내려감'으로 표시한다."""
    cause = error.__cause__ or error
    if isinstance(cause, slm.DOWN_ERRORS):
        health.mark_down(f"practice-reply: {type(cause).__name__}")
    logger.warning("practice 9-cell call failed, falling back to main LLM: %s", error)
    try:
        get_langfuse().create_event(
            name="slm-fallback",
            level="WARNING",
            status_message=str(error),
            metadata={"call": "practice-reply", "error_type": type(cause).__name__},
        )
    except Exception:  # noqa: BLE001 - 관측 실패가 폴백 흐름을 깨면 안 된다
        logger.debug("failed to record practice slm fallback event", exc_info=True)


async def start() -> None:
    """앱 시작 시 한 번. 아직 모델을 올리는 중이면 내려감으로 두고 복구를 기다린다."""
    if enabled():
        health.check_in_background()


async def stop() -> None:
    await health.stop()
