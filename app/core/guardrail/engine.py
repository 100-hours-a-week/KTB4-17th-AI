import hashlib
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from .models import AppliedText, CheckResult, Domain, Grade, GuardrailContext, ValidationResult, Violation
from .rules import (
    EMOJI_REGEX,
    FORMAL_END_REGEX,
    IDENTITY_REGEX,
    PAST_MEETING_REGEX,
    PROFANITY_WORDS,
    SCRIPT_LINE_REGEX,
    SPEECH_END_REGEX,
    UNSAID_TRIGGER_REGEX,
)

AI_DEFLECTION = "그건 지금 우리 얘기랑은 좀 다른 질문 같아요. 오늘은 서로 얘기 나누는 쪽이 더 궁금해요."


def effective_mode(user_key: str | None) -> Literal["off", "shadow", "enforce"]:
    mode = os.environ.get("GUARDRAIL_MODE", "shadow")
    if mode not in ("off", "shadow", "enforce"):
        mode = "shadow"

    if mode == "enforce":
        try:
            percent_str = os.environ.get("GUARDRAIL_ENFORCE_PERCENT", "100")
            percent = float(percent_str)
        except ValueError:
            percent = 100.0
        percent = max(0.0, min(100.0, percent))

        if percent < 100.0:
            if not user_key:
                return "shadow"

            hash_hex = hashlib.sha256(user_key.encode("utf-8")).hexdigest()[:8]
            hash_val = int(hash_hex, 16)
            if (hash_val % 100) < percent:
                return "enforce"
            else:
                return "shadow"

    return mode


def cove_addon(ctx: GuardrailContext) -> str:
    return (
        "\n\n[System Guardrail Check]\n"
        "출력에 점검을 쓰지 말 것. "
        f"당신의 역할은 '{ctx.speaker_name}'입니다. "
        "상대의 속성을 당신의 속성인 것처럼 말하지 마세요. "
        "우리가 과거에 만난 적이 있다는 회상을 하지 마세요. "
        "반드시 한 턴만 답하세요. "
        f"AI인지 묻는 질문에는 오직 다음 문장만 출력하세요: {AI_DEFLECTION}"
    )


def correction_message(result: CheckResult | ValidationResult) -> str:
    parts = []
    for v in result.violations:
        parts.append(f"{v.domain.value} ({v.rule_id}): {v.description}")
    violations_text = "\n".join(parts)
    return f"\n[CORRECTION NOTICE]\n위반 사항:\n{violations_text}"


def fallback_text(ctx: GuardrailContext) -> str:
    return "아, 잠시 다른 생각을 했네요. 방금 하신 말씀 다시 들려주시겠어요?"


def split_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.?!])\s+", text)
    return [s for s in sentences if s.strip()]


def validate(text: str, ctx: GuardrailContext) -> CheckResult:
    start_time = time.perf_counter()
    violations: list[Violation] = []
    grade = Grade.PASS

    def add_violation(g: Grade, d: Domain, r: str, desc: str):
        nonlocal grade
        violations.append(Violation(domain=d, rule_id=r, description=desc, severity=g))
        priority = {Grade.PASS: 0, Grade.WARN: 1, Grade.RETRYABLE: 2, Grade.BLOCK: 3}
        if priority[g] > priority[grade]:
            grade = g

    if text.strip() == "":
        add_violation(Grade.RETRYABLE, Domain.TASK, "RULE-TASK-EMPTY", "Output is empty")
        latency = int((time.perf_counter() - start_time) * 1000)
        return CheckResult(grade=grade, violations=violations, latency_ms=latency)

    leak_keywords = ["## 절대 하지 않는 것", "[CORRECTION NOTICE]", "GUARDRAIL", "Chain of Verification"]
    if any(k in text for k in leak_keywords):
        # We use STYLE for safety rules as SAFETY is not in Domain enum
        add_violation(Grade.BLOCK, Domain.STYLE, "RULE-SAFETY-LEAK", "System prompt leaked")

    if any(w in text for w in PROFANITY_WORDS):
        add_violation(Grade.BLOCK, Domain.STYLE, "RULE-SAFETY-PROFANITY", "Profanity detected")

    if IDENTITY_REGEX.search(text):
        add_violation(Grade.RETRYABLE, Domain.IDENTITY, "RULE-IDENTITY-SELF", "Identity confession")

    sentences = split_sentences(text)
    if not sentences:
        sentences = [text]

    if ctx.partner_attributes:
        for attr in ctx.partner_attributes:
            if len(attr) >= 2 and not any(attr in sa for sa in ctx.speaker_attributes):
                attr_matched = False
                for sentence in sentences:
                    if attr in sentence:
                        match = re.search(r"(저는|나는|난|제가|저도)", sentence)
                        if match:
                            idx = match.end()
                            if attr in sentence[idx : idx + 20 + len(attr)]:
                                if not re.search(r"너|하셨|님은", sentence):
                                    add_violation(
                                        Grade.RETRYABLE,
                                        Domain.PERSPECTIVE,
                                        "RULE-PERSPECTIVE-SWAP",
                                        f"Swapped attribute: {attr}",
                                    )
                                    attr_matched = True
                                    break
                if attr_matched:
                    break  # or don't break if we want all violations

    if PAST_MEETING_REGEX.search(text):
        add_violation(Grade.RETRYABLE, Domain.FACT, "RULE-FACT-PAST-MEETING", "Past meeting reference")

    if ctx.user_texts:
        # 공백·조사 바이그램이 아니라, 사용자가 실제로 말한 2자 이상 토큰만 근거로 본다.
        tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", " ".join(ctx.user_texts))
        for sentence in sentences:
            if UNSAID_TRIGGER_REGEX.search(sentence) and not any(token in sentence for token in tokens):
                add_violation(Grade.RETRYABLE, Domain.FACT, "RULE-FACT-UNSAID", "Unsaid fact reference")
                break

    if ctx.task not in ("first_turn_json", "script_line"):
        q_count = text.count("?") + text.count("？")
        if q_count >= 2:
            add_violation(Grade.RETRYABLE, Domain.TASK, "RULE-TASK-QUESTIONS", "Too many questions")

    script_lines = SCRIPT_LINE_REGEX.findall(text)
    if len(script_lines) >= 2:
        add_violation(Grade.RETRYABLE, Domain.TASK, "RULE-TASK-SCRIPT", "Multiple script lines")

    if EMOJI_REGEX.search(text):
        add_violation(Grade.WARN, Domain.STYLE, "RULE-STYLE-EMOJI", "Emoji detected")

    if len(text) > ctx.max_chars * 2:
        add_violation(Grade.WARN, Domain.STYLE, "RULE-STYLE-LENGTH", "Length exceeded")

    if ctx.avoid_expressions:
        for expr in ctx.avoid_expressions:
            if expr in text:
                add_violation(Grade.WARN, Domain.STYLE, "RULE-STYLE-AVOID", f"Avoid expression used: {expr}")

    if ctx.style == "formal":
        for sentence in sentences:
            if not FORMAL_END_REGEX.search(sentence) and SPEECH_END_REGEX.search(sentence):
                add_violation(Grade.WARN, Domain.STYLE, "RULE-STYLE-SPEECH", "Informal speech in formal style")
                break

    latency = int((time.perf_counter() - start_time) * 1000)
    return CheckResult(grade=grade, violations=violations, latency_ms=latency)


async def apply_text(
    text: str,
    ctx: GuardrailContext,
    *,
    user_key: str | None,
    regenerate: Callable[[str], Awaitable[str]] | None = None,
    fallback: str | None = None,
) -> AppliedText:
    mode = effective_mode(user_key)

    if mode == "off":
        return AppliedText(text=text, result=None)

    check_result = validate(text, ctx)
    initial_grade = check_result.grade

    if mode == "shadow":
        if initial_grade == Grade.PASS:
            status = "PASS"
        elif initial_grade == Grade.WARN:
            status = "WARN"
        else:
            status = "SHADOW_FAIL"

        result = ValidationResult(
            status=status,
            grade=initial_grade,
            initial_grade=initial_grade,
            regenerated=False,
            violations=check_result.violations,
            latency_ms=check_result.latency_ms,
        )
        return AppliedText(text=text, result=result)

    if initial_grade in (Grade.PASS, Grade.WARN):
        status = "PASS" if initial_grade == Grade.PASS else "WARN"
        result = ValidationResult(
            status=status,
            grade=initial_grade,
            initial_grade=initial_grade,
            regenerated=False,
            violations=check_result.violations,
            latency_ms=check_result.latency_ms,
        )
        return AppliedText(text=text, result=result)

    elif initial_grade == Grade.BLOCK:
        fb_text = fallback if fallback is not None else fallback_text(ctx)
        result = ValidationResult(
            status="FALLBACK",
            grade=initial_grade,
            initial_grade=initial_grade,
            regenerated=False,
            violations=check_result.violations,
            latency_ms=check_result.latency_ms,
        )
        return AppliedText(text=fb_text, result=result)

    elif initial_grade == Grade.RETRYABLE:
        if regenerate is None:
            fb_text = fallback if fallback is not None else fallback_text(ctx)
            result = ValidationResult(
                status="FALLBACK",
                grade=initial_grade,
                initial_grade=initial_grade,
                regenerated=False,
                violations=check_result.violations,
                latency_ms=check_result.latency_ms,
            )
            return AppliedText(text=fb_text, result=result)

        corr_msg = correction_message(check_result)
        try:
            new_text = await regenerate(corr_msg)
        except Exception:
            fb_text = fallback if fallback is not None else fallback_text(ctx)
            result = ValidationResult(
                status="FALLBACK",
                grade=initial_grade,
                initial_grade=initial_grade,
                regenerated=False,
                violations=check_result.violations,
                latency_ms=check_result.latency_ms,
            )
            return AppliedText(text=fb_text, result=result)

        new_check = validate(new_text, ctx)
        new_grade = new_check.grade
        total_latency = check_result.latency_ms + new_check.latency_ms

        if new_grade in (Grade.PASS, Grade.WARN):
            result = ValidationResult(
                status="REGENERATED",
                grade=new_grade,
                initial_grade=initial_grade,
                regenerated=True,
                violations=check_result.violations,
                latency_ms=total_latency,
            )
            return AppliedText(text=new_text, result=result)
        else:
            fb_text = fallback if fallback is not None else fallback_text(ctx)
            result = ValidationResult(
                status="FALLBACK",
                grade=new_grade,
                initial_grade=initial_grade,
                regenerated=True,
                violations=check_result.violations,
                latency_ms=total_latency,
            )
            return AppliedText(text=fb_text, result=result)
