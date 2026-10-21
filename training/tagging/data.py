"""온보딩 태깅 파인튜닝 학습 데이터 형식과 검증.

한 줄 = 사람이 정답을 확인한 (질문, 답변, 태그) 한 건. 출처를 정하지 않았어도 형식은 고정해 둬서
팀이 직접 쓰든, 합성하든, 동의받은 운영 데이터를 쓰든 같은 검증과 변환을 거치게 한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.features.persona.schemas import ALL_DIMENSIONS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_DATASET = PROJECT_ROOT / "evals/quality_datasets/persona_onboarding_tagging.jsonl"
# 사람이 라벨을 확정한 골드셋(scripts/gold_drafts.py 출력). 없을 수도 있다
HUMAN_EVAL_DATASET = PROJECT_ROOT / "evals/quality_datasets/human/persona_onboarding_tagging.jsonl"

Source = Literal["team_written", "synthetic", "production_consented"]


class TagExample(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    off_topic: bool = False
    source: Source
    # 정답 라벨을 확인한 사람. 모델이 만든 라벨을 검수 없이 학습하면 모델의 실수까지 배운다
    reviewed_by: str | None = None
    # production_consented 일 때 학습 활용 동의 근거(약관 버전 등)
    consent_basis: str | None = None

    def labels(self) -> dict:
        """모델이 출력해야 할 JSON. TAG_PROMPT 의 출력 예시와 같은 키 순서를 쓴다."""
        return {"primary": self.primary, "secondary": self.secondary, "off_topic": self.off_topic}


def normalize(text: str) -> str:
    """공백·문장부호 차이로 평가 문장이 학습에 새는 것을 놓치지 않도록 비교용으로 정규화한다."""
    return re.sub(r"[\s\W_]+", "", text).lower()


def eval_answers(*paths: Path) -> set[str]:
    """평가 데이터셋(합성 초안 + 사람 골드셋)의 모든 split 답변. 학습에 섞이면 평가 점수가 부풀려진다."""
    answers: set[str] = set()
    for path in paths or (EVAL_DATASET, HUMAN_EVAL_DATASET):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            answers |= {normalize(json.loads(line)["input"]["answer"]) for line in handle if line.strip()}
    return answers


def load(path: Path) -> tuple[list[TagExample], list[str]]:
    examples: list[TagExample] = []
    errors: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                examples.append(TagExample.model_validate_json(line))
            except ValidationError as exc:
                errors.append(f"{path.name}:{number}: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}")
    return examples, errors


def validate(
    examples: Iterable[TagExample],
    *,
    leaked: set[str] | None = None,
    allow_unreviewed: bool = False,
) -> list[str]:
    """학습에 쓰면 안 되는 이유를 모두 모아 반환한다. 빈 목록이면 통과."""
    valid = set(ALL_DIMENSIONS)
    leaked = eval_answers() if leaked is None else leaked
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()

    for ex in examples:
        where = f"[{ex.id}]"
        unknown = sorted((set(ex.primary) | set(ex.secondary)) - valid)
        if unknown:
            errors.append(f"{where} 등록되지 않은 차원: {', '.join(unknown)}")
        overlap = sorted(set(ex.primary) & set(ex.secondary))
        if overlap:
            errors.append(f"{where} primary 와 secondary 에 같은 차원: {', '.join(overlap)}")
        if len(set(ex.primary)) != len(ex.primary) or len(set(ex.secondary)) != len(ex.secondary):
            errors.append(f"{where} 같은 차원이 중복됨")
        if ex.off_topic and (ex.primary or ex.secondary):
            errors.append(f"{where} off_topic 이면 primary·secondary 는 비어 있어야 함")
        if not allow_unreviewed and not ex.reviewed_by:
            errors.append(f"{where} reviewed_by 없음 (사람이 정답을 확인해야 함)")
        if ex.source == "production_consented" and not ex.consent_basis:
            errors.append(f"{where} 운영 데이터는 consent_basis(학습 활용 동의 근거)가 필요함")
        if normalize(ex.answer) in leaked:
            errors.append(f"{where} 평가 데이터셋과 같은 답변 (평가 누수)")
        if ex.id in seen_ids:
            errors.append(f"{where} id 중복")
        pair = (normalize(ex.question), normalize(ex.answer))
        if pair in seen_pairs:
            errors.append(f"{where} 같은 질문·답변이 이미 있음")
        seen_ids.add(ex.id)
        seen_pairs.add(pair)
    return errors
