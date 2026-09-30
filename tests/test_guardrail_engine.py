import asyncio

from app.core.guardrail import Domain, Grade, GuardrailContext, apply_text, effective_mode, validate


def test_profanity_and_ai_stock():
    # style="report"로 설정하여 거든. 반말 WARN을 회피
    ctx = GuardrailContext(surface="practice_reply", speaker_name="bot", style="report")

    # AI 관련 주식을 보고 있거든 은 PASS (IDENTITY나 PROFANITY 안 걸림)
    res_pass = validate("내가 요즘 AI 관련 주식을 보고 있거든.", ctx)
    assert res_pass.grade == Grade.PASS

    # 금칙어 포함은 BLOCK
    res_block = validate("이런 씨발 진짜", ctx)
    assert res_block.grade == Grade.BLOCK
    assert any(v.domain == Domain.STYLE and v.rule_id == "RULE-SAFETY-PROFANITY" for v in res_block.violations)


def test_spec_chapter_6_sentences():
    ctx = GuardrailContext(surface="practice_reply", speaker_name="bot", partner_attributes=["수영"])

    # 정상 1
    res1 = validate("오늘 날씨가 정말 좋네요. 산책 다녀오셨어요?", ctx)
    assert res1.grade == Grade.PASS

    # 정상 2
    res2 = validate("저는 디자이너라 AI 툴을 자주 써요.", ctx)
    assert res2.grade == Grade.PASS

    # 정상 3
    res3 = validate("AI 그림 툴 써보셨어요?", ctx)
    assert res3.grade == Grade.PASS

    # 위반 1 (IDENTITY)
    res4 = validate("저는 언어 모델입니다.", ctx)
    assert res4.grade == Grade.RETRYABLE
    assert res4.violations[0].domain == Domain.IDENTITY

    # 위반 2 (PERSPECTIVE)
    res5 = validate("저는 수영을 정말 좋아해요.", ctx)
    assert res5.grade == Grade.RETRYABLE
    assert res5.violations[0].domain == Domain.PERSPECTIVE


def test_apply_text_regenerate_limit(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    monkeypatch.setenv("GUARDRAIL_ENFORCE_PERCENT", "100")
    ctx = GuardrailContext(surface="practice_reply", speaker_name="bot")

    call_count = 0

    async def mock_regenerate(msg):
        nonlocal call_count
        call_count += 1
        return "저는 챗봇입니다."  # 2차도 위반

    res = asyncio.run(apply_text("저는 인공지능 모델이에요.", ctx, user_key="user1", regenerate=mock_regenerate))
    assert res.result.status == "FALLBACK"
    assert call_count == 1
    assert res.result.regenerated is True


def test_apply_text_block_no_regenerate(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    monkeypatch.setenv("GUARDRAIL_ENFORCE_PERCENT", "100")
    ctx = GuardrailContext(surface="practice_reply", speaker_name="bot")

    call_count = 0

    async def mock_regenerate(msg):
        nonlocal call_count
        call_count += 1
        return "안전한 문장"

    res = asyncio.run(apply_text("이런 씨발 진짜", ctx, user_key="user1", regenerate=mock_regenerate))
    assert res.result.status == "FALLBACK"
    assert call_count == 0


def test_apply_text_shadow(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "shadow")
    ctx = GuardrailContext(surface="practice_reply", speaker_name="bot")

    res = asyncio.run(apply_text("이런 씨발 진짜", ctx, user_key="user1"))
    assert res.text == "이런 씨발 진짜"
    assert res.result.status == "SHADOW_FAIL"
    assert res.result.grade == Grade.BLOCK


def test_apply_text_off(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "off")
    ctx = GuardrailContext(surface="practice_reply", speaker_name="bot")

    res = asyncio.run(apply_text("이런 씨발 진짜", ctx, user_key="user1"))
    assert res.text == "이런 씨발 진짜"
    assert res.result is None


def test_effective_mode_percent(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    monkeypatch.setenv("GUARDRAIL_ENFORCE_PERCENT", "0")
    assert effective_mode("user1") == "shadow"

    monkeypatch.setenv("GUARDRAIL_ENFORCE_PERCENT", "100")
    assert effective_mode("user1") == "enforce"

    monkeypatch.setenv("GUARDRAIL_MODE", "invalid_mode")
    assert effective_mode("user1") == "shadow"


def test_unsaid_ignores_particle_bigrams():
    ctx = GuardrailContext(
        surface="persona_turn",
        speaker_name="하루",
        user_texts=["주말엔 보통 집에서 쉬어요", "연락은 자주 하는 편이 좋아요", "싸우면 바로 얘기하고 풀어요"],
    )
    claimed = validate("캠핑 좋아한다고 하셨잖아요.", ctx)
    assert claimed.grade == Grade.RETRYABLE
    assert any(v.rule_id == "RULE-FACT-UNSAID" for v in claimed.violations)

    grounded = validate("집에서 쉰다고 하셨잖아요.", ctx)
    assert all(v.rule_id != "RULE-FACT-UNSAID" for v in grounded.violations)


def test_first_turn_task_question_rule():
    ctx_single = GuardrailContext(surface="practice_reply", speaker_name="bot", task="single_turn")
    res1 = validate("밥 먹었어? 뭐 먹었어?", ctx_single)
    assert res1.grade == Grade.RETRYABLE
    assert any(v.rule_id == "RULE-TASK-QUESTIONS" for v in res1.violations)

    ctx_first = GuardrailContext(surface="practice_reply", speaker_name="bot", task="first_turn_json")
    res2 = validate("밥 먹었어? 뭐 먹었어?", ctx_first)
    assert res2.grade == Grade.PASS
