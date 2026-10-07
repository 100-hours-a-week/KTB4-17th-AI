from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from app.core.guardrail import CheckResult, Grade, GuardrailContext, ValidationResult, effective_mode, validate
from app.features.persona.schemas import PersonaResponse
from app.features.simulation_migration.llm import LLMError, complete_text
from app.features.simulation_migration.order import (
    contains_farewell,
    repeats_previous,
    self_addressed,
    strip_name_prefix,
)
from app.features.simulation_migration.prompts import parse_speaker_json, speaker_messages


@dataclass
class SpeakResult:
    """화자 도구의 실행 결과."""

    text: str | None
    validation: ValidationResult | None
    llm_calls: int
    messages: list[dict[str, str]]
    reason: str | None = None

    def __iter__(self):
        return iter((self.text, self.validation, self.llm_calls, self.messages, self.reason))


def to_validation_result(
    chk: CheckResult | ValidationResult | None,
    *,
    mode: str,
    content_regens: int,
    initial_grade: Grade | None,
) -> ValidationResult | None:
    """CheckResult를 ValidationResult로 변환한다.

    이미 ValidationResult면 그대로 두고, off 모드이거나 chk가 없으면 None을 반환한다.
    변환 규칙:
    - shadow이면서 grade가 RETRYABLE 또는 BLOCK이면 status는 SHADOW_FAIL
    - 내용 재생성이 있었고 그 대사가 확정되면 status는 REGENERATED, regenerated는 True, initial_grade는 첫 검사의 grade
    - 그 외 WARN은 WARN, 나머지는 PASS
    - violations와 latency_ms는 확정된 검사 결과를 사용
    """
    if chk is None or mode == "off":
        return None
    if isinstance(chk, ValidationResult):
        return chk

    grade = chk.grade
    init_grade = initial_grade if initial_grade is not None else grade

    if mode == "shadow" and grade in (Grade.RETRYABLE, Grade.BLOCK):
        status = "SHADOW_FAIL"
        regenerated = False
    elif content_regens > 0:
        status = "REGENERATED"
        regenerated = True
    elif grade == Grade.WARN:
        status = "WARN"
        regenerated = False
    else:
        status = "PASS"
        regenerated = False

    return ValidationResult(
        status=status,
        grade=grade,
        initial_grade=init_grade,
        regenerated=regenerated,
        violations=chk.violations,
        latency_ms=chk.latency_ms,
    )


async def _speak(
    *,
    speaker: Literal["a", "b"],
    persona: PersonaResponse,
    nickname_self: str,
    nickname_other: str,
    transcript: list[tuple[Literal["a", "b"], str]],
    closing_hint: Literal["none", "no_new_question", "close"],
    llm: Any = None,
    partner_attributes: list[str] | None = None,
    before_request: Any = None,
    index: int | None = None,
    turns: int | None = None,
) -> SpeakResult:
    """화자 단일 발화 생성 및 가드레일/형식 검증 내부 헬퍼.

    한 노드의 LLM 호출은 최대 3번(최초 1회, 형식 재시도 1회, 내용 재생성 1회)으로 제한된다.
    """
    llm_calls = 0
    format_retries = 0
    content_regens = 0
    regen_rule_id: str | None = None
    last_messages: list[dict[str, str]] = []
    last_validation: CheckResult | ValidationResult | None = None
    first_validation_grade: Grade | None = None

    while llm_calls < 3:
        messages = speaker_messages(
            speaker=speaker,
            persona=persona,
            nickname_self=nickname_self,
            nickname_other=nickname_other,
            transcript=transcript,
            closing_hint=closing_hint,
            regen_rule_id=regen_rule_id,
            index=index,
            turns=turns,
        )
        last_messages = [dict(m) for m in messages]
        llm_calls += 1

        try:
            if callable(llm):
                res = llm(messages)
                if asyncio.iscoroutine(res) or asyncio.isfuture(res):
                    raw_text = await res
                else:
                    raw_text = res
            elif hasattr(llm, "complete_text"):
                res = llm.complete_text(messages)
                if asyncio.iscoroutine(res) or asyncio.isfuture(res):
                    raw_text = await res
                else:
                    raw_text = res
            else:
                raw_text = await complete_text(messages, client=llm, before_request=before_request)
        except LLMError as e:
            if e.retryable and format_retries == 0 and llm_calls < 3:
                format_retries += 1
                continue
            return SpeakResult(
                text=None,
                validation=last_validation,
                llm_calls=llm_calls,
                messages=last_messages,
                reason=e.reason,
            )
        except Exception:
            if format_retries == 0 and llm_calls < 3:
                format_retries += 1
                continue
            return SpeakResult(
                text=None,
                validation=last_validation,
                llm_calls=llm_calls,
                messages=last_messages,
                reason="upstream_error",
            )

        # ── 1. 형식 검사 (빈 값, 300자 초과, JSON 오류, 이름표 잔여) ──
        format_failed = False
        parsed_text = ""
        try:
            parsed_text = parse_speaker_json(raw_text)
            stripped = strip_name_prefix(parsed_text, nickname_self)
            # 한 번 벗긴 뒤에도 여전히 이름표가 남아있는지 검사
            if strip_name_prefix(stripped, nickname_self) != stripped:
                format_failed = True
            else:
                clean_text = stripped
        except (ValueError, TypeError):
            format_failed = True

        if format_failed:
            if format_retries == 0 and llm_calls < 3:
                format_retries += 1
                continue
            return SpeakResult(
                text=None,
                validation=last_validation,
                llm_calls=llm_calls,
                messages=last_messages,
                reason="invalid_json",
            )

        # ── 2. 내용 검사 (가드레일, self_addressed, 마지막 b의 물음표) ──
        mode = effective_mode(None)
        if mode == "off":
            last_validation = None
            is_guardrail_violation = False
        else:
            speaker_attrs = [
                *getattr(persona, "interests", []),
                *getattr(persona, "routine", []),
                *getattr(persona, "date_prefer", []),
            ]
            ctx = GuardrailContext(
                surface="simulation_line",
                task="script_line",
                max_chars=300,
                speaker_name=nickname_self,
                partner_name=nickname_other,
                speaker_attributes=speaker_attrs,
                partner_attributes=partner_attributes or [],
            )
            last_validation = validate(clean_text, ctx)
            if first_validation_grade is None and last_validation is not None:
                first_validation_grade = last_validation.grade
            if mode == "enforce":
                is_guardrail_violation = last_validation.grade in (Grade.RETRYABLE, Grade.BLOCK)
            else:
                # shadow 모드: validate는 수행하지만 재생성하거나 가드레일로 실패시키지 않음
                is_guardrail_violation = False

        is_self_addressed = self_addressed(clean_text, nickname_self)
        is_closing_question = speaker == "b" and closing_hint == "close" and ("?" in clean_text or "？" in clean_text)
        own_previous = next((text for spk, text in reversed(transcript) if spk == speaker), None)
        is_repeat = own_previous is not None and repeats_previous(clean_text, own_previous)
        is_early_farewell = closing_hint == "none" and contains_farewell(clean_text)
        # 작별·반복은 한 번만 다시 쓴다. 그래도 남으면 그 줄을 저장하고 실행을 끊지 않는다.
        if (
            (is_repeat or is_early_farewell)
            and not is_guardrail_violation
            and not is_self_addressed
            and not is_closing_question
            and content_regens == 0
            and llm_calls < 3
        ):
            content_regens += 1
            regen_rule_id = "REPEAT" if is_repeat else "EARLY-FAREWELL"
            continue

        has_content_violation = is_guardrail_violation or is_self_addressed or is_closing_question
        if not has_content_violation:
            final_val = to_validation_result(
                last_validation,
                mode=mode,
                content_regens=content_regens,
                initial_grade=first_validation_grade,
            )
            return SpeakResult(
                text=clean_text,
                validation=final_val,
                llm_calls=llm_calls,
                messages=last_messages,
                reason=None,
            )

        # 위반이 발생한 경우 내용 재생성 여부 결정
        if content_regens == 0 and llm_calls < 3:
            content_regens += 1
            if is_self_addressed:
                regen_rule_id = "SELF-ADDRESS"
            elif is_closing_question:
                regen_rule_id = "CLOSING-QUESTION"
            elif last_validation.violations:
                regen_rule_id = last_validation.violations[0].rule_id
            else:
                regen_rule_id = "GUARDRAIL"
            continue

        # 내용 재생성을 이미 소진했거나 호출 기회가 없는 경우 실패 반환
        if is_closing_question:
            return SpeakResult(
                text=None,
                validation=last_validation,
                llm_calls=llm_calls,
                messages=last_messages,
                reason="closing_failed",
            )
        else:
            return SpeakResult(
                text=None,
                validation=last_validation,
                llm_calls=llm_calls,
                messages=last_messages,
                reason="guardrail",
            )

    return SpeakResult(
        text=None,
        validation=last_validation,
        llm_calls=llm_calls,
        messages=last_messages,
        reason="invalid_json",
    )


async def speak_as_a(
    *,
    persona: PersonaResponse,
    nickname_self: str,
    nickname_other: str,
    transcript: list[tuple[Literal["a", "b"], str]],
    closing_hint: Literal["none", "no_new_question", "close"],
    llm: Any = None,
    partner_attributes: list[str] | None = None,
    before_request: Any = None,
    index: int | None = None,
    turns: int | None = None,
) -> SpeakResult:
    """화자 A의 발화를 생성하고 가드레일 및 형식을 검증한다.

    Args:
        persona: 화자 A의 페르소나 객체 (상대방 객체는 전달하지 않음).
        nickname_self: 화자 A의 닉네임.
        nickname_other: 화자 B의 닉네임 (가드레일 검증용).
        transcript: 이전까지 누적된 대사 목록.
        closing_hint: 종료 안내 힌트.
        llm: 호출에 사용할 LLM 클라이언트 또는 함수.
        partner_attributes: 가드레일 판정용 상대방 속성 목록.
        before_request: 호출 전 검증 함수.

    Returns:
        SpeakResult 객체.
    """
    return await _speak(
        speaker="a",
        persona=persona,
        nickname_self=nickname_self,
        nickname_other=nickname_other,
        transcript=transcript,
        closing_hint=closing_hint,
        llm=llm,
        partner_attributes=partner_attributes,
        before_request=before_request,
        index=index,
        turns=turns,
    )


async def speak_as_b(
    *,
    persona: PersonaResponse,
    nickname_self: str,
    nickname_other: str,
    transcript: list[tuple[Literal["a", "b"], str]],
    closing_hint: Literal["none", "no_new_question", "close"],
    llm: Any = None,
    partner_attributes: list[str] | None = None,
    before_request: Any = None,
    index: int | None = None,
    turns: int | None = None,
) -> SpeakResult:
    """화자 B의 발화를 생성하고 가드레일 및 형식을 검증한다.

    Args:
        persona: 화자 B의 페르소나 객체 (상대방 객체는 전달하지 않음).
        nickname_self: 화자 B의 닉네임.
        nickname_other: 화자 A의 닉네임 (가드레일 검증용).
        transcript: 이전까지 누적된 대사 목록.
        closing_hint: 종료 안내 힌트.
        llm: 호출에 사용할 LLM 클라이언트 또는 함수.
        partner_attributes: 가드레일 판정용 상대방 속성 목록.
        before_request: 호출 전 검증 함수.

    Returns:
        SpeakResult 객체.
    """
    return await _speak(
        speaker="b",
        persona=persona,
        nickname_self=nickname_self,
        nickname_other=nickname_other,
        transcript=transcript,
        closing_hint=closing_hint,
        llm=llm,
        partner_attributes=partner_attributes,
        before_request=before_request,
        index=index,
        turns=turns,
    )
