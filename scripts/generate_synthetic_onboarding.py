"""합성 온보딩 세션 생성기 — 태깅·추출 증류용 학습 데이터의 입력을 만든다.

시드 프로필(잠재 성향 + 답변 스타일)을 뽑고, 그 사람을 연기하는 사용자 시뮬레이터 LLM 이
실제 온보딩 흐름에 답하게 한다. 질문은 운영과 같은 ConversationAgent 가 만들고, 주제 순서·되묻기는
PersonaService 와 같은 규칙(next_topic, Coverage, REASK_PREFIX)을 DB 없이 따른다.

  - 태깅·추출(teacher 라벨링)은 하지 않는다. 그건 다음 단계가 이 파일의 출력을 읽어서 한다.
  - 태깅이 없으므로 커버리지는 운영의 "태깅 실패" 경로처럼 topic.covers/also_touches 로 채운다.
  - 시드 프로필은 세션의 잠재 정답이다. 추출 점수의 약한 정답으로 teacher 오류를 거르는 데 쓴다.
  - 골드셋(evals/quality_datasets) 발화와 겹치는 답이 나온 세션은 버린다 (평가 누수 방지).

    .venv/bin/python scripts/generate_synthetic_onboarding.py --n 5
    .venv/bin/python scripts/generate_synthetic_onboarding.py --n 300 --seed 7 --concurrency 8
    .venv/bin/python scripts/generate_synthetic_onboarding.py --n 3 --dry-run   # LLM 없이 흐름만

시뮬레이터 모델은 SIM_LLM_MODEL, 비어 있으면 DEFAULT_SIM_MODEL(충실도 비교 결과, 문서 10의 7.6~7.7절)을 쓴다.
SIM_LLM_BASE_URL / SIM_LLM_API_KEY 가 비어 있으면 LLM_* 를 쓴다(기본 모델 ID 는 OpenRouter 기준).
Langfuse 트레이스는 운영 지표를 오염시키지 않도록 기본으로 끈다 (--trace 로 켠다).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import random
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_VERSION = "synthetic-onboarding/2.1"
SCHEMA_VERSION = 1
DEFAULT_OUT = ROOT / "data" / "synthetic" / "onboarding_sessions.jsonl"
DEFAULT_SIM_MODEL = "google/gemini-3.8-flash"
GOLD_DIR = ROOT / "evals" / "quality_datasets"

# Langfuse 운영 지표(evals/runs/langfuse_quantitative_summary.json) 의 온보딩 질문 생성 평균 토큰.
# ConversationAgent 는 사용량을 돌려주지 않아서 호출 수 × 평균으로 추정한다.
CONVERSATION_AVG_TOKENS = {"prompt": 1213, "completion": 83}

Level = Literal["low", "mid", "high", "unanswered"]
Mode = Literal["normal", "short", "joke", "off_topic"]

# ══ 시드 프로필 ════════════════════════════════════════════

# 점수 구간은 profile.py 의 서술 경계(35 / 65)와 맞춘다 — 학생 모델의 구간 일치율을 같은 경계로 잰다
LEVEL_RANGES: dict[str, tuple[int, int]] = {"low": (5, 34), "mid": (36, 64), "high": (65, 95)}
LEVEL_WEIGHTS: dict[str, float] = {"low": 0.3, "mid": 0.3, "high": 0.3, "unanswered": 0.1}

NICKNAMES = (
    "민트", "도토리", "라떼", "봄비", "솜사탕", "하늘", "구름", "별빛", "새벽", "단풍",
    "모카", "체리", "바다", "자두", "호두", "보리", "누룽지", "망고", "은하", "소금빵",
)  # fmt: skip
INTERESTS = (
    "러닝", "클라이밍", "필라테스", "보드게임", "필름카메라", "베이킹", "캠핑", "재즈 바", "웹툰", "식물 키우기",
    "전시회", "뜨개질", "자전거", "테니스", "요리", "독서 모임", "드라마 정주행", "콘서트", "등산", "향수 모으기",
    "레고", "수영", "골프", "와인", "LP 수집", "축구 보기", "야구 직관", "그림 그리기", "사진", "게임",
)  # fmt: skip
ROUTINES = (
    "주말 아침 늦잠 자고 브런치", "퇴근 후 헬스장", "주말엔 집에서 넷플릭스", "아침 일찍 산책",
    "주말마다 본가 방문", "평일 저녁 영어 공부", "주말에 카페에서 작업", "밤에 한강 산책",
    "토요일엔 친구들이랑 약속", "일요일엔 밀린 집안일",
)  # fmt: skip
DATE_PREFER = (
    "맛집 탐방", "한강 피크닉", "전시 보기", "드라이브", "집에서 영화", "동네 산책",
    "방탈출", "근교 당일치기", "공방 체험", "야구장",
)  # fmt: skip
DATE_AVOID = (
    "사람 많은 번화가", "너무 비싼 레스토랑", "공포 영화", "놀이공원", "술자리 위주 데이트",
    "새벽까지 이어지는 약속", "웨이팅 긴 맛집", "등산", "PC방", "시끄러운 클럽",
)  # fmt: skip

STYLE_LENGTH = ("단답", "보통", "장문")
LENGTH_GUIDE = {"단답": "한 문장, 30자 안팎", "보통": "두세 문장, 50~100자", "장문": "서너 문장, 100~170자"}
STYLE_SPEECH = ("존댓말", "반말", "혼용")
STYLE_LAUGHTER = ("", "ㅎㅎ", "ㅋㅋ")


@dataclass
class AnswerStyle:
    length: str  # 단답 | 보통 | 장문
    speech: str  # 존댓말 | 반말 | 혼용
    laughter: str  # "" | ㅎㅎ | ㅋㅋ
    typos: bool
    # 턴마다 이 확률로 답하는 방식이 바뀐다. 코드가 정하므로 "의도한 무관 답변" 같은 정답을 남길 수 있다
    off_topic_rate: float
    joke_rate: float
    short_rate: float


@dataclass
class SeedProfile:
    seed_id: str
    nickname: str
    levels: dict[str, Level]  # 점수형 15차원
    scores: dict[str, int | None]  # 잠재 점수. unanswered 면 None
    interests: list[str]
    routine: list[str]
    date_prefer: list[str]
    date_avoid: list[str]
    style: AnswerStyle


def sample_profile(rng: random.Random, seed_id: str) -> SeedProfile:
    """같은 rng 상태면 같은 프로필 — 재생성이 결정적이다."""
    from app.features.persona.schemas import SCORED

    levels: dict[str, Level] = {}
    scores: dict[str, int | None] = {}
    names, weights = list(LEVEL_WEIGHTS), list(LEVEL_WEIGHTS.values())
    for key in SCORED:
        level: Level = rng.choices(names, weights)[0]  # type: ignore[assignment]
        levels[key] = level
        scores[key] = rng.randint(*LEVEL_RANGES[level]) if level != "unanswered" else None
    style = AnswerStyle(
        length=rng.choices(STYLE_LENGTH, (0.3, 0.5, 0.2))[0],
        speech=rng.choices(STYLE_SPEECH, (0.6, 0.15, 0.25))[0],
        laughter=rng.choice(STYLE_LAUGHTER),
        typos=rng.random() < 0.25,
        off_topic_rate=rng.choice((0.0, 0.0, 0.05, 0.1)),
        joke_rate=rng.choice((0.0, 0.05, 0.1)),
        short_rate=rng.choice((0.0, 0.1, 0.2)),
    )
    return SeedProfile(
        seed_id=seed_id,
        nickname=rng.choice(NICKNAMES),
        levels=levels,
        scores=scores,
        interests=rng.sample(INTERESTS, rng.randint(1, 3)),
        routine=rng.sample(ROUTINES, rng.randint(1, 2)),
        date_prefer=rng.sample(DATE_PREFER, rng.randint(0, 2)),
        date_avoid=rng.sample(DATE_AVOID, rng.randint(0, 2)),
        style=style,
    )


def pick_mode(rng: random.Random, style: AnswerStyle, *, closing: bool) -> Mode:
    # 마지막 주제(선택지 질문)는 정상으로 답한다 — 운영에서도 선택지 버튼이 있어 딴소리가 드물다
    if closing:
        return "normal"
    r = rng.random()
    if r < style.off_topic_rate:
        return "off_topic"
    r -= style.off_topic_rate
    if r < style.joke_rate:
        return "joke"
    r -= style.joke_rate
    if r < style.short_rate:
        return "short"
    return "normal"


# ══ 사용자 시뮬레이터 ═══════════════════════════════════════

SIMULATOR_SYSTEM = """\
당신은 소개팅 앱 온보딩에서 AI "하루"와 처음 대화하는 20~30대 사용자를 연기합니다.
아래 [나라는 사람]이 당신의 실제 성향입니다. 질문에 이 사람으로서 메신저에 치듯 답하세요.

## 지켜야 할 것
- 답변만 출력합니다. 따옴표·설명·괄호 지문·이름표 금지
- 200자 이내. 실제 사람처럼 문장이 매끄럽지 않아도 됩니다
- 성향을 설명하듯 말하지 않습니다. "저는 회피형이라" 같은 유형명, 점수, 성향 이름을 쓰지 않습니다
  대신 나만의 구체적인 습관·장면·최근 일로 드러냅니다
- [나라는 사람]에 "잘 모름"으로 적힌 성향은 물어봐도 얼버무리거나 아직 생각해 본 적 없다고 답합니다
- 묻지 않은 성향을 먼저 늘어놓지 않습니다. 이번 질문에 답하는 데 필요한 만큼만
- 하루가 AI라는 걸 알고 있지만 굳이 언급하지 않습니다
- [나라는 사람]과 어긋나는 말을 하지 않습니다. 성향이 "상황에 따라 다름"이면 그렇게 답합니다
- 하루의 말에 질문이 여럿이면 마지막 질문에 답합니다. 앞에서 이미 한 말을 되풀이하지 않습니다
- 관심사·데이트 목록을 나열하지 않습니다. 이번 질문에 맞는 것 한두 개만 자연스럽게 꺼냅니다
- 줄바꿈 없이 메시지 하나로 씁니다
"""

SIMULATOR_SYSTEM_V2 = """\
당신은 소개팅 앱 온보딩에서 AI "하루"와 처음 대화하는 20~30대 사용자를 연기합니다.
아래 [나라는 사람]과 매 턴 주어지는 [이번 질문과 관련된 내 성향]이 당신의 실제 모습입니다.
질문에 이 사람으로서 메신저에 치듯 답하세요.

## 지켜야 할 것
- 답변만 출력합니다. 따옴표·설명·괄호 지문·이름표 금지
- 200자 이내. 실제 사람처럼 문장이 매끄럽지 않아도 됩니다
- 성향을 설명하듯 말하지 않습니다. 유형명·점수·성향 이름을 쓰지 않고 구체적인 습관·장면·최근 일로 드러냅니다
- [이번 질문과 관련된 내 성향]과 어긋나는 말을 하지 않습니다. 거기 적힌 수준(어느 쪽인지)이 드러나게 답합니다
- "잘 모름"으로 적힌 성향은 적힌 방식대로만 대답하고, 그 성향을 짐작할 수 있는 행동·장면을 말하지 않습니다
- 이번 질문과 관련 없는 다른 성향은 꺼내지 않습니다
- 하루가 AI라는 걸 알고 있지만 굳이 언급하지 않습니다
- 하루의 말에 질문이 여럿이면 마지막 질문에 답합니다. 앞에서 이미 한 말을 되풀이하지 않습니다
- 관심사·데이트 목록을 나열하지 않습니다. 이번 질문에 맞는 것 한두 개만 자연스럽게 꺼냅니다
- 줄바꿈 없이 메시지 하나로 씁니다
"""

MODE_INSTRUCTION: dict[str, str] = {
    "normal": "이번 질문에는 평소 스타일대로 성실하게 답하세요.",
    "short": "이번 질문에는 귀찮은 듯 아주 짧게(한두 마디, 15자 이내) 답하세요. 그래도 질문에 대한 답이어야 합니다.",
    "joke": "이번 질문에는 농담 섞어 가볍게 받아치되, 진짜 답도 슬쩍 한 조각 섞으세요.",
    "off_topic": "이번 질문은 흘려듣고, 질문과 전혀 상관없는 딴소리(배고프다, 방금 본 영상, 날씨 등)만 하세요. 질문에 답하지 마세요.",
}


# ── v2: 이번 질문이 겨누는 차원만, 수준별 행동 예시와 함께 준다 ──────────────
# v1 은 15개 성향을 한꺼번에 줘서, 시뮬레이터가 이번 질문과 상관없는 성향을 섞거나(교차 오염)
# "잘 모름" 차원에도 구체적인 답을 했다. 200세션 파일럿에서 시드 구간 일치율 0.55, "잘 모름" 차원 점수화 41%.
# 예시는 그대로 쓰면 모든 세션 답이 비슷해지고 student 가 문장을 외운다 — "비슷한 내 경험으로 바꿔 말하라"고 한다.

DIMENSION_CUES: dict[str, dict[str, tuple[str, ...]]] = {
    "avoidance": {
        "low": ("주말 계획은 거의 다 둘이 같이 짜는 편", "사소한 일정까지 공유하는 게 편함"),
        "high": ("연애 중에도 혼자 보내는 저녁이 꼭 있어야 함", "취미 시간은 따로 지키고 싶음"),
    },
    "anxiety": {
        "low": ("답장이 늦으면 바쁜가 보다 하고 내 할 일 함", "연락이 뜸해도 관계를 의심하진 않음"),
        "high": ("답이 늦으면 내가 뭘 잘못했나 계속 되짚어 봄", "읽고 답 없으면 대화창을 자꾸 열어 봄"),
    },
    "disclosure": {
        "low": ("힘든 일은 혼자 정리한 다음에야 겨우 꺼냄", "기분을 말로 하기보다 그냥 넘기는 편"),
        "high": ("속상한 일은 그날 바로 말하는 편", "좋거나 서운한 감정을 말로 표현함"),
    },
    "openness": {
        "low": ("'우리 어떤 사이야' 같은 얘기는 꺼내기 부담스러움", "관계 얘기는 상대가 먼저 하길 기다림"),
        "high": ("요즘 우리 괜찮은지 먼저 물어보는 편", "관계에서 불편한 점을 먼저 꺼내서 얘기함"),
    },
    "positivity": {
        "low": ("표현은 담백하게, 필요한 말 위주", "리액션이 크지 않은 편"),
        "high": ("작은 일에도 고맙다·좋다는 말을 자주 함", "다정한 말투가 기본임"),
    },
    "assurances": {
        "low": ("미래 얘기나 마음 표현은 잘 안 함", "'오래 보자' 같은 말은 낯간지러움"),
        "high": ("다음 계절 계획을 먼저 꺼냄", "마음을 말로 확인시켜 주는 편"),
    },
    "contact_rhythm": {
        "low": ("할 말 있을 때만 연락함", "하루 종일 톡 하면 피곤함"),
        "high": ("하루 종일 소소하게 톡 주고받는 게 좋음", "밥 먹은 것까지 사진 보내는 편"),
    },
    "problem_solving": {
        "low": ("다툼은 시간 지나면 흐지부지 넘어감", "굳이 원인까지 따지지 않음"),
        "high": ("뭐가 문제였는지 짚고 중간 지점을 찾음", "다음엔 어떻게 할지 같이 정함"),
    },
    "withdrawal": {
        "low": ("싸워도 그 자리에서 끝까지 얘기함", "말 안 하고 자리를 뜨는 건 싫음"),
        "high": ("감정이 올라오면 일단 입을 닫음", "잠깐 자리를 피했다가 나중에 얘기함"),
    },
    "engagement": {
        "low": ("화나도 목소리를 높이지 않음", "차분하게 말하려고 함"),
        "high": ("싸우면 목소리가 커지는 편", "욱해서 쏘아붙일 때가 있음"),
    },
    "compliance": {
        "low": ("서운하다고 해도 아닌 건 아니라고 말함", "내 생각이 다르면 분명히 말함"),
        "high": ("싸우기 싫어서 그냥 내가 맞춰 줌", "상대가 서운하다면 일단 다 내가 미안하다고 함"),
    },
    "ideal_warmth": {
        "low": ("성실함이나 배려를 크게 따지진 않음", "약속 시간 같은 건 좀 늦어도 괜찮음"),
        "high": ("약속 잘 지키는지를 제일 먼저 봄", "남한테 배려하는 모습에 끌림"),
    },
    "ideal_vitality": {
        "low": ("외모나 텐션은 거의 안 봄", "조용한 사람도 좋음"),
        "high": ("밝고 에너지 있는 사람에게 끌림", "같이 있으면 분위기 사는 사람이 좋음"),
    },
    "ideal_status": {
        "low": ("직업이나 경제력은 거의 안 봄", "조건보다 대화가 통하는지가 중요"),
        "high": ("직업이 안정적인지 꼭 봄", "경제적으로 계획 있는 사람이 좋음"),
    },
    "seriousness": {
        "low": ("지금은 가볍게 알아가고 싶음", "결혼이나 장기 계획은 아직 먼 얘기"),
        "high": ("오래 만날 사람을 찾는 중", "가벼운 만남보다 진지한 관계가 좋음"),
    },
}

# "잘 모름" 차원의 대답 방식. 세션·차원마다 하나를 고정해서 고른다 (매번 같은 핑계가 되지 않게)
# "상대마다 달라서 말하기 어렵다"는 뺐다 — 시뮬레이터가 "그때그때 달라요" + 양쪽 사례로 풀어서 "중간" 수준과 구별되지 않았다
UNKNOWN_STYLES = (
    "그런 건 생각해 본 적이 없다고 솔직하게 말하기",
    "연애를 오래 안 해서 그때 어땠는지 잘 기억이 안 난다고 하기",
    "그 부분은 아직 잘 모르겠다고 짧게 말하기",
    "어려운 질문이라며 웃고 넘기기",
)


def unknown_style(profile: SeedProfile, dim: str) -> str:
    digest = hashlib.sha256(f"{profile.seed_id}:{dim}".encode()).digest()
    return UNKNOWN_STYLES[digest[0] % len(UNKNOWN_STYLES)]


def focus_section(profile: SeedProfile, dims: list[str]) -> str:
    """이번 질문이 겨누는 차원의 내 성향 — 수준별 행동 예시와 "잘 모름"이면 대답 방식까지."""
    from app.features.persona.schemas import SCORED

    lines = ["[이번 질문과 관련된 내 성향] — 이 내용과 어긋나지 않게 답하세요"]
    for dim in dims:
        if dim not in SCORED:
            continue
        d, level = SCORED[dim], profile.levels[dim]
        cues = DIMENSION_CUES[dim]
        if level == "unanswered":
            lines.append(
                f"- {d.label}: 잘 모름. {unknown_style(profile, dim)}. "
                "이 성향에 대해서는 구체적인 행동·장면·선호·예시를 하나도 말하지 말고, "
                "'그때그때 다르다'거나 양쪽 경우를 늘어놓지도 마세요"
            )
        elif level == "mid":
            lines.append(
                f"- {d.label}: 상황에 따라 다름. 예를 들면 '{cues['low'][0]}'일 때도 있고 '{cues['high'][0]}'일 때도 있음. "
                "한쪽으로 단정하지 말고 둘 다 있다는 걸 드러내세요"
            )
        else:
            side = d.low if level == "low" else d.high
            examples = " / ".join(cues[level])
            lines.append(f"- {d.label}: {side} (이런 사람 예: {examples})")
    known = [d for d in dims if d in SCORED and profile.levels[d] != "unanswered"]
    if not known:
        lines.append("이번 질문에 관련된 성향은 모두 잘 모르는 것이니, 위 방식대로 한두 문장으로 짧게만 답하세요.")
    lines.append("예시 문장은 그대로 쓰지 말고, 비슷한 나만의 경험·장면으로 바꿔 말하세요.")
    lines.append("위에 없는 다른 성향은 이번 답에서 꺼내지 마세요.")
    return "\n".join(lines)


def describe_profile_v2(profile: SeedProfile) -> str:
    """v2 시스템 프롬프트용 — 성향 점수는 빼고 사실(관심사·일상·데이트)과 말투만. 성향은 턴마다 focus_section 으로 준다."""
    lines = [
        "[나라는 사람]",
        f"- 관심사: {', '.join(profile.interests)}",
        f"- 일상: {', '.join(profile.routine)}",
        f"- 하고 싶은 데이트: {', '.join(profile.date_prefer) or '딱히 없음'}",
        f"- 피하고 싶은 것: {', '.join(profile.date_avoid) or '딱히 없음'}",
    ]
    s = profile.style
    lines += [
        "",
        "[말투]",
        f"- 길이: {s.length} ({LENGTH_GUIDE[s.length]})",
        f"- 말씨: {s.speech}" + (" (상대가 존댓말을 써도 편하게 반말 섞기)" if s.speech != "존댓말" else ""),
        f"- 웃음: {s.laughter or '거의 안 씀'}",
        "- 가끔 맞춤법을 틀리거나 띄어쓰기를 건너뜀" if s.typos else "- 맞춤법은 대체로 지킴",
    ]
    return "\n".join(lines)


def describe_profile(profile: SeedProfile) -> str:
    """시드 프로필 → 시뮬레이터가 연기할 말로 된 성향. 점수는 숨기고 양 끝 설명만 준다."""
    from app.features.persona.schemas import SCORED

    lines = ["[나라는 사람]"]
    for key, dim in SCORED.items():
        level = profile.levels[key]
        if level == "low":
            lines.append(f"- {dim.label}: {dim.low}")
        elif level == "high":
            lines.append(f"- {dim.label}: {dim.high}")
        elif level == "mid":
            lines.append(f"- {dim.label}: 상황에 따라 다름 ('{dim.low}'와 '{dim.high}' 사이)")
        else:
            lines.append(f"- {dim.label}: 잘 모름 (생각해 본 적 없음)")
    lines.append(f"- 관심사: {', '.join(profile.interests)}")
    lines.append(f"- 일상: {', '.join(profile.routine)}")
    lines.append(f"- 하고 싶은 데이트: {', '.join(profile.date_prefer) or '딱히 없음'}")
    lines.append(f"- 피하고 싶은 것: {', '.join(profile.date_avoid) or '딱히 없음'}")

    s = profile.style
    lines += [
        "",
        "[말투]",
        f"- 길이: {s.length} ({LENGTH_GUIDE[s.length]})",
        f"- 말씨: {s.speech}" + (" (상대가 존댓말을 써도 편하게 반말 섞기)" if s.speech != "존댓말" else ""),
        f"- 웃음: {s.laughter or '거의 안 씀'}",
        "- 가끔 맞춤법을 틀리거나 띄어쓰기를 건너뜀" if s.typos else "- 맞춤법은 대체로 지킴",
    ]
    return "\n".join(lines)


@dataclass
class Usage:
    simulator_calls: int = 0
    simulator_prompt_tokens: int = 0
    simulator_completion_tokens: int = 0
    conversation_calls: int = 0

    def add(self, other: Usage) -> None:
        for name in asdict(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))


class UserSimulator:
    """OpenAI 호환 엔드포인트로 사용자 답변을 만든다. 운영 Langfuse 래퍼가 아닌 순수 클라이언트를 쓴다."""

    def __init__(
        self, *, base_url: str, api_key: str, model: str, prompt_version: str = "v2", timeout: float = 30.0
    ) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout)
        self.model = model
        self.prompt_version = prompt_version
        self.extra_body: dict[str, Any] = {}
        if "openrouter.ai" in base_url:
            # 추론 토큰이 답변 길이 예산을 다 써서 빈 응답이 오는 걸 막는다 (teacher 추론 모드 실험에서 확인)
            self.extra_body = {"reasoning": {"enabled": False, "exclude": True}}
        # 추론을 끌 수 없는 모델(gemini-3.8-flash, glm-5.3-flash 등)이면 낮은 강도로 켜고 답변 예산을 따로 늘린다
        self.reasoning_tokens = 0

    def _require_reasoning(self) -> None:
        self.extra_body = {"reasoning": {"effort": "low", "exclude": True}}
        self.reasoning_tokens = 2000

    def system_prompt(self, profile: SeedProfile) -> str:
        if self.prompt_version == "v1":
            return SIMULATOR_SYSTEM + "\n" + describe_profile(profile)
        return SIMULATOR_SYSTEM_V2 + "\n" + describe_profile_v2(profile)

    async def answer(
        self,
        profile: SeedProfile,
        history: list[dict],
        question: str,
        mode: Mode,
        usage: Usage,
        focus: list[str] | None = None,
    ) -> str:
        # 시뮬레이터 입장에서는 하루가 assistant 가 아니라 상대방이다 — 역할을 뒤집어 넣는다
        transcript = "\n".join(f"{'하루' if m['role'] == 'assistant' else '나'}: {m['content']}" for m in history)
        user = (
            f"[지금까지 대화]\n{transcript}\n\n" if transcript else ""
        ) + f"[하루의 이번 말]\n{question}\n\n[이번 답변 방식]\n{MODE_INSTRUCTION[mode]}"
        if self.prompt_version != "v1" and focus and mode != "off_topic":
            user += "\n\n" + focus_section(profile, focus)
        from openai import BadRequestError

        for _attempt in range(3):
            sent_body = self.extra_body  # 동시에 도는 다른 세션이 바꿔 놓을 수 있어 보낸 값을 기억한다
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt(profile)},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=200 + self.reasoning_tokens,
                    temperature=0.8,
                    extra_body=self.extra_body or None,
                )
            except BadRequestError as e:
                if "Reasoning is mandatory" in str(e) and sent_body.get("reasoning", {}).get("enabled") is False:
                    self._require_reasoning()  # 여러 세션이 동시에 와도 같은 값으로 바꾸므로 안전하다
                    continue
                raise
            usage.simulator_calls += 1
            if resp.usage:
                usage.simulator_prompt_tokens += resp.usage.prompt_tokens or 0
                usage.simulator_completion_tokens += resp.usage.completion_tokens or 0
            text = clean_answer(resp.choices[0].message.content if resp.choices else "")
            if text:
                return text
        raise RuntimeError("simulator returned empty answer")


def clean_answer(text: str | None) -> str:
    """시뮬레이터가 붙이는 이름표·따옴표를 떼고 운영 입력 상한(MAX_ANSWER_LEN)에 맞춘다."""
    from app.features.persona.schemas import MAX_ANSWER_LEN

    text = (text or "").strip()
    for prefix in ("나:", "나 :", "사용자:", "답변:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    text = " ".join(text.split())  # 메신저 한 줄 — 줄바꿈·연속 공백을 접는다
    if len(text) <= MAX_ANSWER_LEN:
        return text
    # 상한을 넘으면 문장 중간이 아니라 마지막 문장 경계에서 자른다 (경계가 없으면 그냥 자른다)
    cut = text[:MAX_ANSWER_LEN]
    end = max(cut.rfind(c) for c in ".?!~ㅋㅎ…")
    return (cut[: end + 1] if end >= MAX_ANSWER_LEN // 2 else cut).strip()


class FakeSimulator:
    """--dry-run·테스트용. LLM 없이 모드별 고정 답을 낸다."""

    async def answer(
        self,
        profile: SeedProfile,
        history: list[dict],
        question: str,
        mode: Mode,
        usage: Usage,
        focus: list[str] | None = None,
    ) -> str:
        usage.simulator_calls += 1
        return {
            "normal": f"{profile.interests[0]} 얘기면 할 말 많아요. 주말엔 보통 {profile.routine[0]} 해요.",
            "short": "음 글쎄요",
            "joke": "그건 비밀인데요 ㅋㅋ 사실 별거 없어요",
            "off_topic": "아 근데 갑자기 배고프네요",
        }[mode]


# ══ 세션 루프 (PersonaService 규칙을 DB 없이) ═══════════════

QuestionFn = Callable[..., Awaitable[tuple[str, str]]]


@dataclass
class TurnRecord:
    turnIndex: int
    topicId: str
    question: str
    questionSource: str  # llm | seed
    answer: str
    mode: Mode
    # 무관한 답을 한 번 되물은 경우. 원래 답은 운영처럼 저장하지 않지만 off_topic 라벨 학습용으로 남긴다
    reask: dict | None = None


@dataclass
class SessionResult:
    profile: SeedProfile
    turns: list[TurnRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    def history(self) -> list[dict]:
        """PersonaService._history 와 같은 모양 — 추출 입력으로 그대로 쓴다."""
        messages: list[dict] = []
        for t in self.turns:
            messages += [{"role": "assistant", "content": t.question}, {"role": "user", "content": t.answer}]
        return messages


def make_question_fn(agent: Any) -> QuestionFn:
    async def ask(*, history, topic, turn_index, total_turns, nickname) -> tuple[str, str]:
        utterance = await agent.generate(
            history=history, topic=topic, turn_index=turn_index, total_turns=total_turns, nickname=nickname
        )
        return utterance.text, utterance.source

    return ask


async def seed_question(*, history, topic, turn_index, total_turns, nickname) -> tuple[str, str]:
    """--dry-run·테스트용. 운영의 LLM 실패 폴백과 같은 시드 질문."""
    return topic.seed, "seed"


async def run_session(
    profile: SeedProfile,
    *,
    rng: random.Random,
    ask: QuestionFn,
    simulator: Any,
    total_turns: int,
) -> SessionResult:
    from app.features.persona.schemas import TOPICS_BY_ID
    from app.features.persona.service import REASK_PREFIX, Coverage, next_topic

    result = SessionResult(profile=profile)
    coverage = Coverage()
    used: list[str] = []
    for turn_index in range(total_turns):
        topic = next_topic(coverage, turn_index, total_turns, used)
        if topic is None:
            break
        history = result.history()
        question, source = await ask(
            history=history, topic=topic, turn_index=turn_index, total_turns=total_turns, nickname=profile.nickname
        )
        if source == "llm":
            result.usage.conversation_calls += 1
        mode = pick_mode(rng, profile.style, closing=TOPICS_BY_ID[topic.id].is_closing)
        focus = [*topic.covers, *topic.also_touches]
        answer = await simulator.answer(profile, history, question, mode, result.usage, focus)

        reask = None
        if mode == "off_topic":
            # 운영: 무관한 답이면 턴을 소모하지 않고 LLM 없이 그 주제의 시드 질문으로 한 번만 되묻는다
            reask_question = f"{REASK_PREFIX} {topic.seed}"
            reask = {"offTopicAnswer": answer, "question": reask_question}
            asked = [*history, {"role": "assistant", "content": question}, {"role": "user", "content": answer}]
            answer = await simulator.answer(profile, asked, reask_question, "normal", result.usage, focus)

        result.turns.append(
            TurnRecord(
                turnIndex=turn_index,
                topicId=topic.id,
                question=question,
                questionSource=source,
                answer=answer,
                mode=mode,
                reask=reask,
            )
        )
        # 태깅 없이 진행 — 운영의 태깅 실패 경로처럼 주제가 겨눈 차원으로 커버리지를 채운다
        coverage.apply(list(topic.covers), list(topic.also_touches))
        used.append(topic.id)
    return result


# ══ 골드셋 누수 검사 ═══════════════════════════════════════


def _load_audit_module():
    path = GOLD_DIR / "audit_quality_datasets.py"
    spec = importlib.util.spec_from_file_location("audit_quality_datasets", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class LeakChecker:
    """골드셋 발화와 같거나 거의 같은 답을 잡는다. 기준은 골드셋 감사(audit_quality_datasets)와 같다."""

    def __init__(self, gold_dir: Path = GOLD_DIR) -> None:
        audit = _load_audit_module()
        self._normalize = audit.normalize
        self._approximation = audit.approximation
        self.gold: set[str] = set()
        for path in sorted([*gold_dir.glob("*.jsonl"), *(gold_dir / "human").glob("*.jsonl")]):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                for sentence, _ in audit.stimuli(row.get("input", {})):
                    if sentence in audit.FIXED_CONTRACT_TEXT:
                        continue
                    normalized = self._normalize(sentence)
                    if len(normalized) >= 8:  # 한두 마디 관용구("네", "글쎄요")는 겹쳐도 누수가 아니다
                        self.gold.add(normalized)

    def leaks(self, text: str) -> list[str]:
        normalized = self._normalize(text)
        if len(normalized) < 8:
            return []
        if normalized in self.gold:
            return [normalized]
        return [g for g in self.gold if self._approximation(normalized, g)]

    def session_leaks(self, result: SessionResult) -> list[dict]:
        found = []
        for t in result.turns:
            texts = [t.answer] + ([t.reask["offTopicAnswer"]] if t.reask else [])
            for text in texts:
                for hit in self.leaks(text):
                    found.append({"turnIndex": t.turnIndex, "answer": text, "gold": hit})
        return found


# ══ 출력 ══════════════════════════════════════════════════


def to_record(result: SessionResult, *, index: int, run_seed: int, models: dict[str, str]) -> dict:
    p = result.profile
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sessionId": f"syn-{run_seed}-{index:05d}",
        "familyId": p.seed_id,  # 분할은 이 단위로 — 같은 시드의 세션이 학습·평가에 나뉘면 누수다
        "source": "synthetic",
        "generator": {"version": GENERATOR_VERSION, "runSeed": run_seed, **models},
        "seedProfile": {
            "nickname": p.nickname,
            "levels": p.levels,
            "scores": p.scores,
            "textual": {
                "interests": p.interests,
                "routine": p.routine,
                "date_prefer": p.date_prefer,
                "date_avoid": p.date_avoid,
            },
            "style": asdict(p.style),
        },
        "turns": [asdict(t) for t in result.turns],
        "history": result.history(),
        "usage": asdict(result.usage),
    }


def estimate_cost(usage: Usage, price_in: float | None, price_out: float | None) -> dict:
    conv_in = usage.conversation_calls * CONVERSATION_AVG_TOKENS["prompt"]
    conv_out = usage.conversation_calls * CONVERSATION_AVG_TOKENS["completion"]
    total_in = usage.simulator_prompt_tokens + conv_in
    total_out = usage.simulator_completion_tokens + conv_out
    out = {
        "simulatorTokens": {"prompt": usage.simulator_prompt_tokens, "completion": usage.simulator_completion_tokens},
        "conversationTokensEstimated": {"prompt": conv_in, "completion": conv_out},
        "totalTokens": {"prompt": total_in, "completion": total_out},
    }
    if price_in is not None and price_out is not None:
        out["estimatedCostUsd"] = round(total_in / 1e6 * price_in + total_out / 1e6 * price_out, 4)
    return out


# ══ 실행 ══════════════════════════════════════════════════


async def generate(args: argparse.Namespace) -> tuple[list[dict], dict]:
    from app.core.config import get_settings
    from app.features.persona.service import ONBOARDING_TOTAL_TURNS

    total_turns = args.total_turns or ONBOARDING_TOTAL_TURNS
    settings = get_settings()
    if args.dry_run:
        simulator: Any = FakeSimulator()
        ask: QuestionFn = seed_question
        models = {"simulatorModel": "fake", "conversationModel": "seed"}
    else:
        from app.features.persona.agents import ConversationAgent

        simulator = UserSimulator(
            base_url=os.getenv("SIM_LLM_BASE_URL") or settings.llm_base_url,
            api_key=os.getenv("SIM_LLM_API_KEY") or settings.llm_api_key,
            model=os.getenv("SIM_LLM_MODEL") or DEFAULT_SIM_MODEL,
            prompt_version=args.sim_prompt,
        )
        ask = make_question_fn(ConversationAgent())
        models = {
            "simulatorModel": simulator.model,
            "simulatorPrompt": args.sim_prompt,
            "conversationModel": settings.llm_model,
        }

    checker = LeakChecker()
    sem = asyncio.Semaphore(args.concurrency)
    results: dict[int, dict] = {}
    dropped: list[dict] = []
    failed: list[dict] = []
    total_usage = Usage()

    async def one(index: int) -> None:
        # 세션마다 독립 rng — 동시 실행 순서와 상관없이 같은 --seed 면 같은 프로필·모드가 나온다
        rng = random.Random(f"{args.seed}:{index}")
        profile = sample_profile(rng, seed_id=f"seed-{args.seed}-{index:05d}")
        async with sem:
            try:
                result = await run_session(profile, rng=rng, ask=ask, simulator=simulator, total_turns=total_turns)
            except Exception as e:  # 한 세션 실패로 전체를 버리지 않는다
                failed.append({"index": index, "error": f"{type(e).__name__}: {e}"})
                return
        total_usage.add(result.usage)
        leaks = checker.session_leaks(result)
        if leaks:
            dropped.append({"index": index, "leaks": leaks})
            return
        results[index] = to_record(result, index=index, run_seed=args.seed, models=models)
        print(f"  [{len(results) + len(dropped) + len(failed)}/{args.n}] session {index} ok", file=sys.stderr)

    started = time.monotonic()
    await asyncio.gather(*(one(i) for i in range(args.n)))
    elapsed = time.monotonic() - started

    summary = {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "out": str(args.out),
        "requested": args.n,
        "written": len(results),
        "droppedForGoldLeak": len(dropped),
        "failed": len(failed),
        "elapsedSeconds": round(elapsed, 1),
        "models": models,
        "usage": asdict(total_usage),
        "cost": estimate_cost(total_usage, args.price_in, args.price_out),
        "dropped": dropped,
        "failures": failed,
    }
    return [results[i] for i in sorted(results)], summary


def write_outputs(out: Path, records: list[dict], summary: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    out.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", type=int, default=5, help="만들 세션 수")
    parser.add_argument("--seed", type=int, default=0, help="실행 시드. 같으면 같은 프로필·답변 방식")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="출력 JSONL 경로 (옆에 .summary.json)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--total-turns", type=int, default=None, help="기본은 운영과 같은 ONBOARDING_TOTAL_TURNS")
    parser.add_argument("--dry-run", action="store_true", help="LLM 없이 시드 질문과 고정 답으로 흐름만 확인")
    parser.add_argument("--trace", action="store_true", help="Langfuse 트레이스 켜기 (기본 끔)")
    parser.add_argument(
        "--sim-prompt",
        choices=("v1", "v2"),
        default="v2",
        help="v1 = 전체 성향을 한 번에, v2 = 질문별 관련 성향 + 행동 예시 + '잘 모름' 대답 방식",
    )
    parser.add_argument("--price-in", type=float, default=None, help="입력 100만 토큰당 USD (비용 추정용)")
    parser.add_argument("--price-out", type=float, default=None, help="출력 100만 토큰당 USD (비용 추정용)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.trace:
        # 합성 세션이 운영 Langfuse 지표(호출 수·지연·비용)에 섞이지 않게 한다. app import 전에 정해야 한다
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    records, summary = asyncio.run(generate(args))
    write_outputs(Path(args.out), records, summary)
    brief = {k: v for k, v in summary.items() if k not in ("dropped", "failures")}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0 if summary["written"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
