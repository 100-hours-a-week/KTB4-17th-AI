"""LLM 호출부 — 카카오톡 대화 → 연애 성향 점수(근거 발화 번호 포함) + 말투.

점수는 온보딩 추출(persona.agents.ExtractionAgent)과 같은 차원·루브릭을 쓰고, 말투는 v1 카카오 추출의 표면 말투 필드만 뽑는다.
온보딩이 "본인이 답한 것"이라면 이쪽은 "대화에서 관찰한 것"이라 근거를 발화 번호로 받아 코드가 검증한다.
LLM 클라이언트·JSON 파싱은 persona.agents 의 것을 그대로 쓴다 — 같은 OpenRouter 설정을 공유한다.
"""

from __future__ import annotations

import json
import logging

from langfuse import observe
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata
from app.features.persona.agents import LLMError, _call_json, _scored_section
from app.features.persona.schemas import ConversationStyle

from .schemas import V2Extraction

logger = logging.getLogger(__name__)

MAX_PHRASES = 10


class ExtractionFailed(Exception):
    """LLM 호출 실패 또는 출력 형식 오류. 작업은 failed 로 끝나고 발화는 미반영으로 남는다."""


RUBRIC = f"""\
당신은 메신저 대화에서 한 사람의 연애 성향과 말투를 관찰하는 분석가입니다.
대화에는 분석 대상인 "나"와 "상대"의 말이 시간순으로 섞여 있습니다.

# 읽는 법
- 분석 대상은 "나"입니다. 점수와 말투는 "나"에 대해서만 씁니다. 상대의 성향을 "나"의 점수에 섞지 마세요.
- 근거로 인용할 수 있는 것은 번호가 붙은 "나[번호]" 발화뿐입니다. 번호가 없는 "나" 발화와 "상대" 발화는
  맥락을 이해하는 용도입니다. 예: 상대가 감정을 꺼냈을 때 "나"가 어떻게 받았는지, 질문에 어떻게 답했는지.
- 상대의 말을 근거 번호로 쓰거나 존재하지 않는 번호를 쓰지 마세요.

# 점수 원칙
- 0~100 정수. 50은 "중간"이 아니라 근거가 부족할 때의 기본값입니다.
- 대화에서 명확한 근거가 있을 때만 양 끝으로 움직이세요. 일반적인 관찰값은 보통 35~65 범위입니다.
- 이 대화의 주제가 해당 차원을 건드리지 않았다면 그 차원은 생략하세요. 추측해서 채우지 마세요.
- 농담, 역할극, 상대에게 맞춘 말투, 한 번의 감정적 순간으로 성향을 단정하지 마세요.
- 각 점수에는 실제로 근거가 된 "나" 발화 번호를 evidence 에 모두 적으세요. 근거 번호가 없으면 그 점수는 생략하세요.

## 점수형 차원
{_scored_section()}

## 상충처럼 보이지만 정상인 조합
- withdrawal 높음 + problem_solving 높음: "감정이 올라오면 잠깐 멈췄다가 그날 안에 다시 얘기한다"는 둘 다 높은 정상 패턴입니다.
- avoidance 높음 + disclosure 높음: "각자 생활은 지키되 중요한 건 공유한다"도 정상입니다.
- contact_rhythm 낮음 + anxiety 낮음: 연락이 뜸한 것과 불안한 것은 별개입니다.

# 말투 (style)
- "나"가 보낸 메시지에 실제로 나타난 것만 씁니다. 근거가 없으면 그 키를 생략합니다.
- "이전 스타일"이 주어지면 그것을 바탕으로 갱신합니다:
  * 새 메시지에서도 여전히 보이는 습관은 유지하고, 새로 쓰이기 시작한 표현은 리스트 앞쪽에 배치합니다.
  * 이전 스타일에 있었어도 새 메시지에서 보이지 않거나 빈도가 급감한 표현은 뒤로 밀거나 제거합니다. 최대 {MAX_PHRASES}개.
- frequent_phrases, endings, interjections, slang 은 메시지에서 가장 자주 쓴 순서로 정렬하고, 실제로 나온 문구를 그대로 씁니다 (30자 이내).
  사람 이름·장소·회사·학교·연락처·나이·숫자가 들어간 문구는 넣지 않습니다.
  어미나 추임새는 필요하면 괄호로 간단한 맥락 힌트를 덧붙여도 좋습니다. 단, 숫자는 넣지 않습니다.
- 아래 출력 형식의 값("...")은 자리표시일 뿐입니다. 형식 설명을 값으로 베껴 쓰지 말고, 메시지에서 확인한 것만 씁니다.
  메시지에 없는 감탄사·어미·줄임말·문장부호·웃음 표현은 쓰지 않습니다.

# 출력 — JSON 객체 하나만. 설명·코드펜스 금지
점수는 근거가 있는 차원만 키로 넣으세요. 근거가 없으면 키를 아예 생략합니다.
{{
  "scores": {{
    "disclosure": {{"value": 72, "evidence": [2, 19, 57]}}
  }},
  "style": {{
    "speech_level": "존댓말 | 반말 | 혼용",
    "frequent_phrases": ["..."],
    "endings": ["~..."],
    "interjections": ["..."],
    "slang": ["..."],
    "punctuation": "메시지에서 확인한 문장부호 습관 한 문장",
    "laughter": "메시지에서 확인한 웃음 표현과 빈도 한 문장",
    "message_length": "메시지 길이·끊어 보내는 습관 한 문장"
  }}
}}
"""


class ConversationAgent:
    # 번호 붙은 대화 흐름 + 이전 스타일 → 점수 관찰값(근거 번호 포함) + 말투
    @observe(name="v2-persona-extraction", capture_input=False, capture_output=False)
    async def analyze(
        self,
        transcript: str,
        *,
        previous: ConversationStyle | None,
        trace_metadata: LangfuseMetadata | None = None,
    ) -> V2Extraction:
        prev = json.dumps(previous.model_dump(exclude_none=True), ensure_ascii=False) if previous else "(없음)"
        content = f"# 이전 스타일\n{prev}\n\n# 대화 (오래된 순)\n{transcript}"
        with propagate_langfuse_metadata(trace_metadata):
            try:
                data = await _call_json(
                    system=RUBRIC,
                    messages=[{"role": "user", "content": content}],
                    max_tokens=2000,
                    timeout=get_settings().extraction_timeout_s,
                    name="v2-persona-extraction",
                    metadata=trace_metadata,
                )
            except LLMError as e:
                raise ExtractionFailed(f"LLM call failed: {e}") from e
        try:
            return V2Extraction.model_validate(data)
        except ValidationError as e:
            logger.warning("v2 extraction validation failed: %s", e)
            raise ExtractionFailed(f"invalid extraction: {e}") from e
