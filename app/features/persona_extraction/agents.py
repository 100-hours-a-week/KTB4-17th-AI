"""LLM 호출부 — 실제 대화 발화 → 대화 스타일 + 점수 관찰값.

온보딩 추출(persona.agents.ExtractionAgent)과 따로 둔다. 온보딩은 "본인이 답한 것", 이쪽은
"실제 대화에서 관찰한 것"이라 근거의 성격이 다르고, 입력(여러 대화의 본인 발화 묶음)도 다르다.
LLM 클라이언트·JSON 파싱은 persona.agents 의 것을 그대로 쓴다 — 같은 OpenRouter 설정을 공유한다.
"""

from __future__ import annotations

import json
import logging

from langfuse import observe
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata
from app.features.persona.agents import LLMError, _call_json
from app.features.persona.schemas import REFLECTED_SCORES, SCORED, ConversationStyle, StyleExtraction

logger = logging.getLogger(__name__)

MAX_PHRASES = 10


class ExtractionFailed(Exception):
    """LLM 호출 실패 또는 출력 형식 오류. 작업은 failed 로 끝나고 발화는 미반영으로 남는다."""


# 보정 대상 점수의 0/100 양 끝 설명 — 온보딩 루브릭과 같은 SCORED 정의를 쓴다
def _score_guide() -> str:
    return "\n".join(f"- {k} ({SCORED[k].label}): 0={SCORED[k].low} / 100={SCORED[k].high}" for k in REFLECTED_SCORES)


STYLE_RUBRIC = f"""\
당신은 메신저 대화 습관 분석가입니다. 한 사람이 실제로 보낸 메시지들만 보고,
그 사람의 말투와 대화 습관을 정리합니다.

# 원칙
- 메시지에 실제로 나타난 것만 씁니다. 근거가 없으면 그 키를 생략합니다.
- "이전 스타일"이 주어지면 그것을 바탕으로 갱신합니다:
  * 새 메시지에서도 여전히 보이는 습관은 유지하고, 새롭게 쓰이기 시작한 표현은 적극적으로 반영하여 리스트 앞쪽(상위)에 배치합니다.
  * 중요 (퇴출 규칙): 이전 스타일 목록에 있었더라도 새 메시지에서 전혀 보이지 않거나 사용 빈도가 급감한 표현은 리스트 뒤쪽으로 밀어내거나 과감히 탈락(제거)시키세요.
  * 새로운 습관이 기존 습관을 대체했다면, 최대 {MAX_PHRASES}개의 자리를 새 표현에 넘겨주고 이전 표현은 제거합니다.
- frequent_phrases 에는 메시지에 실제로 나온 문구를 그대로 씁니다 (30자 이내).
  사람 이름·장소·회사·학교·연락처·나이·숫자가 들어간 문구는 넣지 않습니다.
- frequent_phrases, endings, interjections, slang 등 리스트 항목은 메시지에서 가장 자주 쓴 순서대로(빈도순·중요도순) 정렬합니다.
  어미나 추임새는 필요한 경우 괄호로 간단한 빈도·맥락 힌트를 덧붙여도 좋습니다 (예: "~요 (기본 어미)", "~구 (가끔 애교 섞인 톤)"). 단, 숫자는 넣지 않습니다.

# 점수 (0~100, 수치적으로 명확히 측정할 수 있는 것만 포함. 측정 불가 시 반드시 키 생략)
- 50점이 보통/중간 수준입니다.
- 대화 내용에서 해당 성향이 뚜렷하게 관찰되어 수치화할 수 있는 항목만 점수를 매깁니다 (일반적인 관찰값은 보통 35~65 범위).
- 중요: 대화 주제상 해당 성향이 나오지 않았거나 수치적으로 측정할 수 없는 항목은 절대로 0이나 추측값을 넣지 말고 키 자체를 생략하세요! (생략해야 기존 기본값이 유지됩니다. 0점은 그 성향을 극단적으로 거부하거나 반대되는 명백한 언행이 있을 때만 부여합니다.)
{_score_guide()}

# 출력 — JSON 객체 하나만. 설명·코드펜스 금지
# 점수 키(disclosure, positivity, openness, assurances)는 대화에서 명확히 드러나 측정 가능한 항목만 포함하세요. 측정할 수 없는 항목은 키를 아예 넣지 마세요.
{{
  "style": {{
    "speech_level": "존댓말 | 반말 | 혼용",
    "frequent_phrases": ["..."],
    "endings": ["~요", "~용"],
    "interjections": ["헐", "오"],
    "slang": ["ㄹㅇ"],
    "punctuation": "물결(~)과 느낌표를 자주 씀",
    "laughter": "ㅋㅋ를 거의 매 메시지에",
    "message_length": "짧게 여러 번 끊어 보냄",
    "question_rate": "질문을 자주 던짐",
    "reaction": "리액션이 크고 공감 표현이 많음",
    "self_talk": "자기 얘기를 먼저 꺼내는 편",
    "humor": "가벼운 농담을 자주 섞음",
    "initiative": "주제를 먼저 바꾸며 대화를 이끔",
    "summary": "1~2문장 요약"
  }}
}}
"""


class StyleAgent:
    # 발화 묶음(오래된 순) + 이전 스타일 → 대화 스타일 + 점수 관찰값
    @observe(name="persona-extraction-style", capture_input=False, capture_output=False)
    async def analyze(
        self,
        utterances: list[str],
        *,
        previous: ConversationStyle | None,
        trace_metadata: LangfuseMetadata | None = None,
    ) -> StyleExtraction:
        prev = json.dumps(previous.model_dump(exclude_none=True), ensure_ascii=False) if previous else "(없음)"
        content = f"# 이전 스타일\n{prev}\n\n# 이 사람이 보낸 메시지 (오래된 순)\n" + "\n".join(
            f"- {u}" for u in utterances
        )
        with propagate_langfuse_metadata(trace_metadata):
            try:
                data = await _call_json(
                    system=STYLE_RUBRIC,
                    messages=[{"role": "user", "content": content}],
                    max_tokens=1200,
                    timeout=get_settings().extraction_timeout_s,
                    name="persona-extraction-style",
                    metadata=trace_metadata,
                )
            except LLMError as e:
                raise ExtractionFailed(f"LLM call failed: {e}") from e
        try:
            return StyleExtraction.model_validate(data)
        except ValidationError as e:
            logger.warning("style extraction validation failed: %s", e)
            raise ExtractionFailed(f"invalid style: {e}") from e
