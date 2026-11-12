"""v2 리포트 프롬프트 재료 — v1 app/features/simulation/agents.py 의 리포트 규칙·섹션 함수를 복제했다.

v1 프롬프트를 고쳐도 v2 리포트가 같이 바뀌지 않도록 v2 가 따로 들고 있는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.features.persona.schemas import SCORED, PersonaResponse
from app.features.simulation.schemas import AREAS, DIMENSIONS_BY_AREA, RULES, Fit


def _rules_section() -> str:
    lines = []
    for area, label in AREAS.items():
        lines.append(f"[{label}]")
        for d in DIMENSIONS_BY_AREA[area]:
            rule = RULES[d]
            how = "대화록으로 판정" if rule.fit == Fit.JUDGED else f"규칙: {rule.fit.value}"
            lines.append(f"- {d} ({SCORED[d].label}) — {how}. {rule.why}")
    return "\n".join(lines)


def _scores_section(area_scores: dict[str, int | None], dim_scores: dict[str, int | None]) -> str:
    lines = []
    for area, label in AREAS.items():
        s = area_scores.get(area)
        lines.append(f"- {label}: {'미정' if s is None else s}")
        for d in DIMENSIONS_BY_AREA[area]:
            ds = dim_scores.get(d)
            lines.append(f"    · {SCORED[d].label}: {'미정' if ds is None else ds}")
    return "\n".join(lines)


_REPORT_RULES = """- 점수를 다시 매기지 마세요. 주어진 점수를 "왜 그런지" 대화록의 장면으로 설명하세요.
- 예외: ideal_* 세 차원만 대화록을 보고 0~100 으로 판정하세요. 근거가 없으면 키를 빼거나 값을 null로 두세요.
- 두 사람 모두에게 보이는 글입니다. 한쪽을 깎아내리지 마세요. "OO님은 ~한 편이고 XX님은 ~한 편이라" 식으로.
- 사람을 가리킬 땐 실제 닉네임에 "님"을 붙여 부르세요. a·b·A·B 같은 기호로 사람을 가리키지 마세요.
- 근거 부족(LOW) 차원은 단정하지 말고 "아직 잘 모르겠지만" 톤으로.
- 한국어, "~해요" 체. 조언은 구체적으로 (예: "연락 빈도를 첫 주에 맞춰보세요").
- 하이라이트 quote 는 대화록 원문을 그대로, turn_index 와 함께. click(잘 통한 순간)·friction(어긋난 순간) 섞어서 3~5개."""


def _fact_attrs(facts: Sequence[Any]) -> set[str]:
    from .verifier import _JOSA, _VERBISH, _fact_keys

    attrs: set[str] = set()
    for item in facts:
        text = getattr(item, "text", item)
        if not isinstance(text, str):
            continue
        keys = _fact_keys(text)
        attrs.update(k for k in keys if len(k) >= 2)
        words = [_JOSA.sub("", w) for w in text.replace(".", " ").split() if w]
        for i in range(len(words) - 1):
            w1, w2 = words[i], words[i + 1]
            if len(w1) >= 2 and len(w2) >= 2 and not _VERBISH.search(w2):
                attrs.add(f"{w1} {w2}")
    return attrs


def _report_perspective_swap(
    text: str,
    pa: PersonaResponse | None = None,
    pb: PersonaResponse | None = None,
    name_a: str = "",
    name_b: str = "",
    *,
    facts_a: Sequence[Any] = (),
    facts_b: Sequence[Any] = (),
) -> bool:
    """Detect an exclusive memory fact phrase/keyword attributed to the other named person."""
    attrs_a = _fact_attrs(facts_a)
    attrs_b = _fact_attrs(facts_b)
    for owner_attrs, other_attrs, owner, other in (
        (attrs_a, attrs_b, name_a, name_b),
        (attrs_b, attrs_a, name_b, name_a),
    ):
        for attr in owner_attrs - other_attrs:
            if len(attr) < 2:
                continue
            for match in re.finditer(re.escape(other), text):
                after = text[match.end() : match.end() + 40]
                pos = after.find(attr)
                if pos >= 0 and owner not in after[:pos]:
                    return True
    return False
