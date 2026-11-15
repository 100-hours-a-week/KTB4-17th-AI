"""문장 판정 카드에 쓸 문장을 sentences 테이블에 적재한다 (최소 버전).

입력은 사람이 만든 JSONL. 한 줄 = 문장 1개 + 질문 1개 (data/sentences/sentences.example.jsonl).
  {"sentence": "답장은 생각날 때 몰아서 하는 편이에요", "question_type": "AXIS", "dimension": "contact_rhythm",
   "source": "team_written", "family_id": "contact-01", "is_check": false, "check_answer": null,
   "context_question": "연락은 자주 하는 편이에요?"}

  uv run python scripts/load_sentences.py data/sentences/team_v1.jsonl --dry-run   # 형식·중복·누수 검사만 (LLM 안 씀)
  uv run python scripts/load_sentences.py data/sentences/team_v1.jsonl             # 답안지 계산 + 적재

형식 오류가 한 줄이라도 있으면 아무것도 넣지 않는다 (10~80자, 등록된 차원, 검증 문항 정답).
건너뛰는 것: 파일 안 중복, DB 에 이미 있는 (문장, 질문 종류, 차원), 평가 골드셋과 같거나 거의 같은 문장 (노출되면 평가 누수).

답안지는 expected_champion 만 채운다 (설계 5.4). 운영과 같은 프롬프트로 지금 모델을 부른다.
  축 질문 : TaggingAgent → primary = direct, secondary = inferred, 그 밖 = none
            태거 전체 출력(champion_tags)과 context_question 도 저장한다. 태깅 학습 데이터는 이 전체 출력에서
            판정한 차원만 다수결로 고쳐 만든다 (scripts/export_crowd_tagging.py). 그래서 축 질문은 context_question 이 필수다
  방향 질문: ExtractionAgent 를 그 문장 한 턴으로 → 65 이상 high, 34 이하 low, 중간·근거 없음 unsure
expected_teacher 는 비워 둔다 — 다수결 집계 단계에서 채운다.
답안지 계산이 실패한 문장은 넣지 않는다. 다시 돌리면 이미 넣은 문장은 건너뛰고 실패한 것만 다시 시도한다.

메인 LLM 주소가 openrouter.ai 면 --allow-openrouter 없이는 멈춘다. SLM 을 켜 둬도 실패하면 메인 LLM 으로 넘어가기 때문이다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.features.persona.schemas import Tags  # noqa: E402
from app.features.sentence_cards.models import Sentence  # noqa: E402
from app.features.sentence_cards.schemas import ChoiceId, QuestionType  # noqa: E402
from app.features.sentence_cards.service import build_question  # noqa: E402
from scripts.gold_drafts import GOLD_DIR, LEVEL_RANGES, LeakIndex, default_leak_index  # noqa: E402

logger = logging.getLogger(__name__)

MIN_LEN, MAX_LEN = 10, 80  # 설계 5.3
HIGH_FROM = LEVEL_RANGES["high"][0]  # 65 — 골드셋·student 구간 일치 지표와 같은 경계
LOW_TO = LEVEL_RANGES["low"][1]  # 34
CHECK_ANSWERS = {
    QuestionType.DIRECTION: {ChoiceId.HIGH, ChoiceId.LOW},
    QuestionType.AXIS: {ChoiceId.DIRECT, ChoiceId.INFERRED, ChoiceId.NONE},
}


def normalize(text: str) -> str:
    return " ".join(text.split())


class SeedRow(BaseModel):
    """입력 JSONL 한 줄."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sentence: str = Field(min_length=MIN_LEN, max_length=MAX_LEN)
    question_type: QuestionType
    dimension: str
    source: Literal["synthetic", "team_written", "tagging_train"]
    family_id: str | None = Field(default=None, min_length=1, max_length=64)  # 없으면 문장마다 따로
    is_check: bool = False
    check_answer: ChoiceId | None = None  # 검증 문항의 확실한 정답. 판단 어려움(unsure)은 안 된다
    # 태깅 입력의 질문. 답안지 계산과 태깅 학습 데이터에 쓰고 사용자에게는 안 보인다. 축 질문은 필수
    context_question: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _consistent(self) -> SeedRow:
        self.sentence = normalize(self.sentence)
        # 카드를 실제로 만들 수 있는 차원인지는 카드 발급과 같은 함수로 확인한다
        try:
            build_question(self.to_model(), random.Random(0))
        except (ValueError, KeyError) as e:
            raise ValueError(str(e)) from e
        if self.is_check and self.check_answer not in CHECK_ANSWERS[self.question_type]:
            allowed = ", ".join(sorted(CHECK_ANSWERS[self.question_type]))
            raise ValueError(f"검증 문항은 check_answer 가 {allowed} 중 하나여야 합니다")
        if not self.is_check and self.check_answer is not None:
            raise ValueError("검증 문항이 아니면 check_answer 를 비워 두세요")
        if self.question_type == QuestionType.AXIS and not self.context_question:
            raise ValueError("축 질문은 context_question 이 필요합니다 (태깅 학습 입력의 질문)")
        return self

    @property
    def key(self) -> tuple[str, str, str]:
        return normalize(self.sentence), self.question_type.value, self.dimension

    def to_model(self, champion: ChampionAnswer | None = None, champion_model: str | None = None) -> Sentence:
        return Sentence(
            family_id=self.family_id or hashlib.sha256(normalize(self.sentence).encode()).hexdigest()[:16],
            sentence=self.sentence,
            source=self.source,
            question_type=self.question_type.value,
            dimension=self.dimension,
            expected_champion=champion.choice.value if champion else None,
            champion_tags=champion.tags if champion else None,
            champion_model=champion_model if champion else None,
            context_question=self.context_question,
            is_check=self.is_check,
            check_answer=self.check_answer.value if self.check_answer else None,
        )


def read_rows(path: Path) -> tuple[list[SeedRow], list[str]]:
    """(정상 행, 오류 메시지). 오류가 하나라도 있으면 호출부는 적재하지 않는다."""
    rows, errors = [], []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(SeedRow.model_validate(json.loads(line)))
        except json.JSONDecodeError as e:
            errors.append(f"{number}번째 줄: JSON 형식 오류 ({e.msg})")
        except ValidationError as e:
            details = "; ".join(f"{'.'.join(map(str, err['loc'])) or '행'}: {err['msg']}" for err in e.errors())
            errors.append(f"{number}번째 줄: {details}")
    return rows, errors


# ══ 답안지 ═══════════════════════════════════════════════


def axis_answer(tags: Tags, dimension: str) -> ChoiceId:
    if dimension in tags.primary:
        return ChoiceId.DIRECT
    if dimension in tags.secondary:
        return ChoiceId.INFERRED
    return ChoiceId.NONE


def direction_answer(score: int | None) -> ChoiceId:
    if score is None:  # 근거를 못 찾아 키를 생략한 경우
        return ChoiceId.UNSURE
    if score >= HIGH_FROM:
        return ChoiceId.HIGH
    if score <= LOW_TO:
        return ChoiceId.LOW
    return ChoiceId.UNSURE


@dataclass(frozen=True)
class ChampionAnswer:
    choice: ChoiceId
    tags: dict | None = None  # 축 질문만: 태거 전체 출력 {primary, secondary, off_topic}


AnswerKey = Callable[[SeedRow], Awaitable[ChampionAnswer | None]]


def champion_model_name() -> str:
    """답안지를 낸 모델. SLM 이 켜져 있어도 실패하면 메인 LLM 으로 넘어가므로 설정 기준의 기록이다."""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.slm_base_url and settings.slm_tagging_model:
        return f"slm:{settings.slm_tagging_model}"[:128]
    return f"llm:{settings.llm_model}"[:128]


async def champion_answer(row: SeedRow) -> ChampionAnswer | None:
    """운영 에이전트로 계산한 답안지. 실패면 None."""
    from app.features.persona.agents import BuildFailed, ExtractionAgent, TaggingAgent

    question = row.context_question or ""
    if row.question_type == QuestionType.AXIS:
        tags = await TaggingAgent().tag(question, row.sentence)
        return None if tags is None else ChampionAnswer(axis_answer(tags, row.dimension), tags.model_dump())
    history = [{"role": "assistant", "content": question}] if question else []
    history.append({"role": "user", "content": row.sentence})
    try:
        extracted = await ExtractionAgent().extract(history, answered={row.dimension})
    except BuildFailed as e:
        logger.warning("extraction failed: %s", e)
        return None
    return ChampionAnswer(direction_answer(getattr(extracted, row.dimension)))


# ══ 적재 ═════════════════════════════════════════════════


@dataclass
class Plan:
    to_load: list[SeedRow] = field(default_factory=list)
    skipped: list[tuple[SeedRow, str]] = field(default_factory=list)


async def plan_rows(rows: list[SeedRow], db: AsyncSession, leak_index: LeakIndex) -> Plan:
    """넣을 행과 건너뛸 행(이유)을 나눈다. DB 는 읽기만 한다."""
    existing = {
        (normalize(sentence), question_type, dimension)
        for sentence, question_type, dimension in (
            await db.execute(select(Sentence.sentence, Sentence.question_type, Sentence.dimension))
        ).all()
    }
    plan, seen = Plan(), set()
    for row in rows:
        if row.key in existing:
            plan.skipped.append((row, "already_loaded"))
        elif row.key in seen:
            plan.skipped.append((row, "duplicate_in_file"))
        elif source := leak_index.find(row.sentence):
            plan.skipped.append((row, f"gold_leak:{source}"))
        else:
            seen.add(row.key)
            plan.to_load.append(row)
    return plan


async def load_rows(
    rows: list[SeedRow],
    db: AsyncSession,
    answer_key: AnswerKey,
    *,
    champion_model: str | None = None,
    concurrency: int = 4,
) -> tuple[list[Sentence], list[SeedRow]]:
    """답안지를 계산해 넣는다. (넣은 문장, 답안지 실패로 뺀 행). 한 트랜잭션으로 커밋한다."""
    gate = asyncio.Semaphore(concurrency)

    async def one(row: SeedRow) -> ChampionAnswer | None:
        async with gate:
            try:
                return await answer_key(row)
            except Exception:  # noqa: BLE001 - 한 문장 실패로 나머지를 버리지 않는다. 다음 실행에서 다시 시도
                logger.exception("answer key failed: %s", row.sentence)
                return None

    keys = await asyncio.gather(*(one(row) for row in rows))
    loaded = [row.to_model(key, champion_model) for row, key in zip(rows, keys, strict=True) if key is not None]
    failed = [row for row, key in zip(rows, keys, strict=True) if key is None]
    db.add_all(loaded)
    await db.commit()
    return loaded, failed


def summary(plan: Plan, loaded: list[Sentence] | None, failed: list[SeedRow] | None) -> dict:
    report: dict = {
        "to_load": len(plan.to_load),
        "skipped": dict(Counter(reason.split(":")[0] for _, reason in plan.skipped)),
        "skipped_rows": [{"sentence": row.sentence, "reason": reason} for row, reason in plan.skipped],
    }
    if loaded is not None and failed is not None:
        report["loaded"] = len(loaded)
        report["answer_key_failed"] = [row.sentence for row in failed]
        report["expected_champion"] = dict(Counter(s.expected_champion for s in loaded))
        # 뻔한 문장인데 모델이 다르게 답하면 champion 이 틀린 것 — 첫 배포 전에 눈으로 본다
        report["check_mismatch"] = [
            {"sentence": s.sentence, "check_answer": s.check_answer, "expected_champion": s.expected_champion}
            for s in loaded
            if s.is_check and s.check_answer != s.expected_champion
        ]
    return report


async def _run(args: argparse.Namespace, rows: list[SeedRow]) -> dict:
    from app.core.db import SessionLocal

    leak_index = default_leak_index(sorted((GOLD_DIR / "claude_gold").glob("*.jsonl")))
    async with SessionLocal() as db:
        plan = await plan_rows(rows, db, leak_index)
        if args.dry_run:
            return summary(plan, None, None)
        loaded, failed = await load_rows(
            plan.to_load, db, champion_answer, champion_model=champion_model_name(), concurrency=args.concurrency
        )
        return summary(plan, loaded, failed)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="문장 JSONL")
    parser.add_argument("--dry-run", action="store_true", help="검사만 하고 LLM 호출·DB 쓰기를 하지 않는다")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--allow-openrouter", action="store_true", help="메인 LLM 이 OpenRouter 여도 실행 (비용 발생)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    rows, errors = read_rows(args.path)
    if errors:
        print("형식 오류가 있어 적재하지 않습니다:", *errors, sep="\n  ", file=sys.stderr)
        return 1

    from app.core.config import get_settings

    settings = get_settings()
    if not args.dry_run:
        if "openrouter.ai" in settings.llm_base_url and not args.allow_openrouter:
            print(
                f"메인 LLM 이 OpenRouter 입니다 ({settings.llm_base_url}). SLM 폴백도 여기로 갑니다.\n"
                "LLM_BASE_URL 을 다른 서버로 바꾸거나, 비용을 확인했으면 --allow-openrouter 를 주세요.",
                file=sys.stderr,
            )
            return 2
        print(f"답안지 모델: main={settings.llm_model} slm={settings.slm_base_url or '(off)'}", file=sys.stderr)

    # 점수만 쓰므로 서술 가드레일(추가 LLM 호출)은 끈다
    os.environ["GUARDRAIL_MODE"] = "off"
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
    logging.basicConfig(level=logging.WARNING)
    report = asyncio.run(_run(args, rows))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
