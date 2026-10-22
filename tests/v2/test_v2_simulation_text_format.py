"""v2 시뮬레이션 대본 평문(text) 포맷 지원 테스트.

1. parse_text_lines: 정규식·화자·줄바꿈·fact 걷어내기 등 파서 동작 검증
2. generate_text: text_llm 호출 및 정규화(normalize), 빈 대본 예외 검증
3. graph 라우팅: 항상 text_llm 호출 및 기존 llm 생성 단계 미호출 검증
4. call_text: OpenAI completions 호출 시 tools/response_format 미전송 및 finish_reason 검증
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.v2.simulation import llm as llm_module
from app.v2.simulation.generator import (
    build_generate_prompt_text,
    generate_text,
    parse_text_lines,
)
from app.v2.simulation.graph import run_harness
from app.v2.simulation.llm import SimLLMError, call_text
from app.v2.simulation.schemas import TextResult, TopicFact, TopicPlan
from tests.v2.sim_fixtures import (
    FakeLLM,
    ctx,
    good_script,
    judge_pass,
    report_call,
)

TURNS = 10


# ── 1. parse_text_lines 테스트 ─────────────────────────────────────────────


def test_parse_text_lines_normal_4_lines():
    raw = (
        "민지: 안녕하세요 준호님 [A-f1]\n"
        "준호: 반가워요 [B-f2, A-f1]\n"
        "민지: 주말에 뭐 하세요? [A-f2]\n"
        "준호: 저는 영화 봐요 [B-f1]\n"
    )
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 4
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕하세요 준호님"
    assert lines[0].fact_refs == ["A-f1"]
    assert lines[1].speaker == "b"
    assert lines[1].text == "반가워요"
    assert lines[1].fact_refs == ["B-f2", "A-f1"]
    assert lines[2].speaker == "a"
    assert lines[2].text == "주말에 뭐 하세요?"
    assert lines[2].fact_refs == ["A-f2"]
    assert lines[3].speaker == "b"
    assert lines[3].text == "저는 영화 봐요"
    assert lines[3].fact_refs == ["B-f1"]


def test_parse_text_lines_colon_in_dialogue():
    raw = "민지: 시간은 3:30 어때요?"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 1
    assert lines[0].speaker == "a"
    assert lines[0].text == "시간은 3:30 어때요?"


def test_parse_text_lines_fullwidth_colon_and_bold():
    raw = "**민지**: 안녕하세요\n준호：반가워요"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 2
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕하세요"
    assert lines[1].speaker == "b"
    assert lines[1].text == "반가워요"


def test_parse_text_lines_ignore_intro_codefence_blank_lines():
    raw = "대본 생성 결과입니다.\n```text\n\n민지: 안녕하세요\n\n```\n준호: 반가워요\n"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 2
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕하세요"
    assert lines[1].speaker == "b"
    assert lines[1].text == "반가워요"


def test_parse_text_lines_continuation_line_appends_to_prev():
    raw = "민지: 안녕하세요\n반가워요 준호님\n준호: 네 저도 반가워요\n"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 2
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕하세요 반가워요 준호님"
    assert lines[1].speaker == "b"
    assert lines[1].text == "네 저도 반가워요"


def test_parse_text_lines_unknown_speaker_prefix():
    raw = "지아: 저는 첫 줄 지아입니다\n민지: 안녕하세요\n지아: 중간에 나온 지아입니다\n준호: 반갑습니다\n"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 2
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕하세요 지아: 중간에 나온 지아입니다"
    assert lines[1].speaker == "b"
    assert lines[1].text == "반갑습니다"


def test_parse_text_lines_speaker_suffix_nim_recognized():
    raw = "민지님: 안녕하세요\n민지: 저예요\n"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 2
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕하세요"
    assert lines[1].speaker == "a"
    assert lines[1].text == "저예요"


def test_parse_text_lines_numbering_and_bullet_formats():
    raw = (
        "1. 민지: 안녕하세요\n"
        "2) 준호: 반갑습니다\n"
        "- 민지: 주말에 뭐 하세요?\n"
        "* 준호: 영화 봐요\n"
        "- 5. 민지: 어떤 영화요?\n"
    )
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 5
    assert lines[0].text == "안녕하세요"
    assert lines[1].text == "반갑습니다"
    assert lines[2].text == "주말에 뭐 하세요?"
    assert lines[3].text == "영화 봐요"
    assert lines[4].text == "어떤 영화요?"


def test_parse_text_lines_known_refs_filter():
    raw = "민지: 안녕하세요 [A-f1]\n준호: 영화 좋아해요 [M-f1]\n민지: 저도요 [B-f9]\n준호: 반가워요 [B-f2]\n"
    lines = parse_text_lines(raw, "민지", "준호", known_refs={"A-f1", "B-f2"})
    assert len(lines) == 4
    assert lines[0].fact_refs == ["A-f1"]
    assert lines[1].fact_refs == []
    assert lines[1].text == "영화 좋아해요"
    assert lines[2].fact_refs == []
    assert lines[2].text == "저도요"
    assert lines[3].fact_refs == ["B-f2"]


def test_parse_text_lines_emoji_removal():
    raw = "민지: 반가워요 😊\n준호: 안녕하세요!! ㅎㅎ 👍"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 2
    assert lines[0].text == "반가워요"
    assert lines[1].text == "안녕하세요!! ㅎㅎ"


def test_parse_text_lines_trailing_explanation_and_blank_lines():
    # 1) 빈 줄 다음 비화자 줄은 버려짐
    raw1 = "민지: 안녕하세요\n\n이상으로 시뮬레이션 끝입니다.\n준호: 반가워요"
    lines1 = parse_text_lines(raw1, "민지", "준호")
    assert len(lines1) == 2
    assert lines1[0].text == "안녕하세요"
    assert lines1[1].text == "반가워요"

    # 2) 2N(turns=1 -> 2줄) 화자 줄 읽은 후 비화자 줄은 버려짐
    raw2 = "1. 민지: 안녕\n2. 준호: 잘 가\n이 대본은 테스트 대본입니다."
    lines2 = parse_text_lines(raw2, "민지", "준호", turns=1)
    assert len(lines2) == 2
    assert lines2[1].text == "잘 가"


def test_parse_text_lines_case_normalization_and_bracket_leftovers():
    raw = "민지: 안녕하세요 [a-f1]\n준호: 반가워요 (b-F2)\n민지: 주말 [ a-f1 ] [ ] -f3 찌꺼기\n"
    lines = parse_text_lines(raw, "민지", "준호", known_refs={"A-f1", "B-f2"})
    assert len(lines) == 3
    assert lines[0].fact_refs == ["A-f1"]
    assert lines[1].fact_refs == ["B-f2"]
    assert "[" not in lines[2].text
    assert "]" not in lines[2].text
    assert "-f" not in lines[2].text
    assert lines[2].text == "주말 찌꺼기"
    assert lines[2].fact_refs == ["A-f1"]


def test_parse_text_lines_speaker_prefix_tolerance():
    raw = (
        '1. "민지": 안녕\n'
        "2. “준호: 반가워”\n"
        "3. **민지:** 날씨 좋네요\n"
        "4. **준호**: 네 맞아요\n"
        "5. 민지씨: 점심 드셨어요?\n"
        "6. a: 네 먹었어요\n"
    )
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 6
    assert lines[0].speaker == "a" and lines[0].text == "안녕"
    assert lines[1].speaker == "b" and lines[1].text == "반가워"
    assert lines[2].speaker == "a" and lines[2].text == "날씨 좋네요"
    assert lines[3].speaker == "b" and lines[3].text == "네 맞아요"
    assert lines[4].speaker == "a" and lines[4].text == "점심 드셨어요?"
    assert lines[5].speaker == "a" and lines[5].text == "네 먹었어요"


def test_parse_text_lines_colon_in_nickname():
    raw = "1. 민:지: 안녕 준:호!\n2. 준:호: 안녕 민:지!"
    lines = parse_text_lines(raw, "민:지", "준:호")
    assert len(lines) == 2
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕 준:호!"
    assert lines[1].speaker == "b"
    assert lines[1].text == "안녕 민:지!"


def test_parse_text_lines_inner_speaker_prefix(caplog: pytest.LogCaptureFixture):
    raw = "1. 준호: 민지: 오늘 뭐 해?"
    with caplog.at_level("WARNING"):
        lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 1
    assert lines[0].speaker == "b"
    assert lines[0].text == "민지: 오늘 뭐 해?"
    assert "inner speaker prefix in line" in caplog.text


def test_parse_text_lines_split_inline_fact_ref():
    raw = "민지: 저도 등산 자주 가요 A-f1 주말에요"
    lines = parse_text_lines(raw, "민지", "준호")
    assert len(lines) == 1
    assert lines[0].text == "저도 등산 자주 가요 주말에요"
    assert lines[0].fact_refs == ["A-f1"]


def test_parse_text_lines_same_nickname_fallback_ab():
    raw = "a: 안녕하세요\nb: 반갑습니다"
    lines = parse_text_lines(raw, "민지", "민지")
    assert len(lines) == 2
    assert lines[0].speaker == "a"
    assert lines[0].text == "안녕하세요"
    assert lines[1].speaker == "b"
    assert lines[1].text == "반갑습니다"


def test_parse_text_lines_empty_input():
    assert parse_text_lines("", "민지", "준호") == []
    assert parse_text_lines("   \n\n\r\n  ", "민지", "준호") == []


# ── 2. generate_text 테스트 ────────────────────────────────────────────────


class FakeTextLLM:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        timeout: float,
        name: str,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TextResult:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "name": name,
                "model": model,
                "metadata": metadata,
            }
        )
        if not self.responses:
            raise RuntimeError("FakeTextLLM: no more responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return TextResult(text=item, prompt_tokens=100, completion_tokens=200)


def test_generate_text_success_and_normalization():
    async def _run():
        response = "민수: 안녕하세요\n민수: 날씨가 좋네요\n지은: 네 반갑습니다\n"
        fake_llm = FakeTextLLM([response])
        plan = TopicPlan(mode="persona_only")
        context = ctx(name_a="민수", name_b="지은")

        lines, res = await generate_text(fake_llm, context, plan, turns=2, correction=None, timeout=30.0)

        assert len(fake_llm.calls) == 1
        call = fake_llm.calls[0]
        assert call["name"] == "simulation-v2-generate-text"
        assert call["max_tokens"] == 600 + 150 * 2 * 2  # 1200
        assert res.prompt_tokens == 100
        assert res.completion_tokens == 200

        # 같은 화자(민수) 2줄 연속 -> 1줄로 합쳐짐
        assert len(lines) == 2
        assert lines[0].speaker == "a"
        assert lines[0].text == "안녕하세요 날씨가 좋네요"
        assert lines[1].speaker == "b"
        assert lines[1].text == "네 반갑습니다"

    asyncio.run(_run())


def test_generate_text_regeneration_name():
    async def _run():
        fake_llm = FakeTextLLM(["민수: 안녕하세요\n지은: 반갑습니다"])
        plan = TopicPlan(mode="persona_only")
        context = ctx(name_a="민수", name_b="지은")

        await generate_text(fake_llm, context, plan, turns=1, correction="이전 대본 오류", timeout=20.0)
        assert fake_llm.calls[0]["name"] == "simulation-v2-regenerate-text"

    asyncio.run(_run())


def test_generate_text_no_lines_raises_invalid_text():
    async def _run():
        fake_llm = FakeTextLLM(["화자 없이 설명만 있는 텍스트입니다."])
        plan = TopicPlan(mode="persona_only")
        context = ctx(name_a="민수", name_b="지은")

        with pytest.raises(SimLLMError) as exc_info:
            await generate_text(fake_llm, context, plan, turns=2, correction=None, timeout=30.0)
        assert exc_info.value.reason == "invalid_text"

    asyncio.run(_run())


# ── 3. graph 라우팅 테스트 ────────────────────────────────────────────────


def test_graph_always_uses_text_llm():
    """설정 없이 항상 text_llm 이 호출되고 기존 llm 은 생성 단계에서 호출되지 않는다."""

    async def _run():
        script_lines = good_script(TURNS)
        good_text = "\n".join(f"{'민수' if line.speaker == 'a' else '지은'}: {line.text}" for line in script_lines)
        fake_text_llm = FakeTextLLM([good_text])
        fake_llm = FakeLLM({"judge": [judge_pass()], "report": [report_call()]})

        res = await run_harness(
            ctx(),
            [],
            [],
            TURNS,
            job_id="j-text",
            llm=fake_llm,
            text_llm=fake_text_llm,
        )

        assert res.status == "succeeded"
        assert len(fake_text_llm.calls) == 1
        assert fake_text_llm.calls[0]["name"] == "simulation-v2-generate-text"
        assert fake_llm.count("generate") == 0

        gen_event = next(e for e in res.trace if e.node == "generate")
        assert gen_event.note is None

    asyncio.run(_run())


# ── 4. call_text 테스트 ────────────────────────────────────────────────────


class _DummyMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _DummyChoice:
    def __init__(self, content: str | None, finish_reason: str = "stop") -> None:
        self.message = _DummyMessage(content)
        self.finish_reason = finish_reason


class _DummyUsage:
    prompt_tokens = 12
    completion_tokens = 34


class _DummyResponse:
    def __init__(self, content: str | None = "민지: 안녕", finish_reason: str = "stop") -> None:
        self.choices = [_DummyChoice(content, finish_reason)]
        self.usage = _DummyUsage()


class _FakeCompletions:
    def __init__(self, response: _DummyResponse) -> None:
        self.response = response
        self.last_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> _DummyResponse:
        self.last_kwargs = kwargs
        return self.response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response: _DummyResponse) -> None:
        self.completions = _FakeCompletions(response)
        self.chat = _FakeChat(self.completions)

    def with_options(self, **kwargs: Any) -> _FakeClient:
        return self


def test_call_text_no_tools_no_response_format(monkeypatch: pytest.MonkeyPatch):
    async def _run():
        dummy_resp = _DummyResponse(content="민지: 안녕하세요\n준호: 반가워요")
        fake_client = _FakeClient(dummy_resp)
        monkeypatch.setattr(llm_module, "_get_client", lambda: fake_client)

        result = await call_text(
            system="당신은 시뮬레이터입니다.",
            user="대본을 작성하세요.",
            max_tokens=500,
            timeout=10.0,
            name="test-call-text",
        )

        kwargs = fake_client.completions.last_kwargs
        assert "tools" not in kwargs
        assert "response_format" not in kwargs
        assert kwargs["name"] == "test-call-text"
        assert kwargs["max_tokens"] == 500
        assert result.text == "민지: 안녕하세요\n준호: 반가워요"
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 34

    asyncio.run(_run())


def test_call_text_truncated_on_length_finish_reason(monkeypatch: pytest.MonkeyPatch):
    async def _run():
        dummy_resp = _DummyResponse(content="민지: 안녕하세요...", finish_reason="length")
        fake_client = _FakeClient(dummy_resp)
        monkeypatch.setattr(llm_module, "_get_client", lambda: fake_client)

        with pytest.raises(SimLLMError) as exc_info:
            await call_text(
                system="system",
                user="user",
                max_tokens=100,
                timeout=5.0,
                name="test-truncated",
            )
        assert exc_info.value.reason == "truncated"

    asyncio.run(_run())


# ── 5. 프롬프트 및 generate_text 추가 검증 ─────────────────────────────────


def test_build_generate_prompt_text_no_facts():
    plan = TopicPlan(mode="persona_only", facts_a=[], facts_b=[])
    system, user = build_generate_prompt_text(ctx(), plan, turns=10)

    # fact 없는 plan -> system 에 "A-f", "B-f", "[" 예시 ID 없음
    assert "A-f" not in system
    assert "B-f" not in system
    assert "[" not in system
    # "대괄호" 금지 문장 있음
    assert "대괄호" in system
    assert "줄 끝에 대괄호나 괄호 표시를 붙이지 않습니다" in system
    # user 에 "정확히 20줄" 류 문장
    assert "정확히 20줄" in user
    assert "마지막 줄 번호는 20" in user


def test_build_generate_prompt_text_with_facts():
    plan = TopicPlan(
        mode="shared",
        facts_a=[TopicFact(ref="A-custom9", fact_id=1, owner="a", text="재즈 음악 감상", category="hobby")],
        facts_b=[TopicFact(ref="B-custom3", fact_id=2, owner="b", text="피아노 연주", category="hobby")],
    )
    system, user = build_generate_prompt_text(ctx(), plan, turns=10)

    # 실제 ref가 예시로 등장, 하드코딩 A-f1 없음
    assert "A-custom9" in system
    assert "A-f1" not in system
    assert "fact 를 실제로 쓴 줄에만 붙이고" in system
    assert "아래 fact 블록에 있는 ID 만 쓴다" in system


def test_build_generate_prompt_text_same_nicknames():
    plan = TopicPlan(mode="persona_only", facts_a=[], facts_b=[])
    context = ctx(name_a="민지", name_b="민지")
    system, user = build_generate_prompt_text(context, plan, turns=10)

    # 닉네임이 같을 때 a/b 표기 사용 지시
    assert "번호. a/b: 대사" in system or "a: 대사" in system
    assert "홀수 번호는 a, 짝수 번호는 b" in user
    assert "'번호. a: 대사'" in user


def test_generate_text_warns_when_short_and_passes_known_refs(caplog: pytest.LogCaptureFixture):
    async def _run():
        response = "1. 민수: 안녕하세요 [A-f1]\n2. 지은: 반가워요 [M-f1]\n"
        fake_llm = FakeTextLLM([response])
        plan = TopicPlan(
            mode="shared",
            facts_a=[TopicFact(ref="A-f1", fact_id=1, owner="a", text="사진 찍기", category="hobby")],
            facts_b=[],
        )
        context = ctx(name_a="민수", name_b="지은")

        with caplog.at_level("WARNING"):
            lines, _ = await generate_text(fake_llm, context, plan, turns=2, correction=None, timeout=30.0)

        assert len(lines) == 2
        assert any("generated text script lines" in record.message for record in caplog.records)
        assert any("dropped 1 unknown fact refs" in record.message for record in caplog.records)

        assert lines[0].fact_refs == ["A-f1"]
        assert lines[1].fact_refs == []

    asyncio.run(_run())
