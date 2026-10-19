"""태깅·추출 품질 지표 — 학습 모델(LoRA/QLoRA)과 비교군을 같은 기준으로 비교한다.

run_langfuse_calibration.py 가 만든 문항별 output 을 받아 계산한다.
Langfuse 를 import 하지 않는 순수 함수만 두어 테스트에서 바로 부를 수 있게 한다.

공통 규칙
* ``mode == "model"`` 인 문항만 센다. fixture(모델 미호출) 문항은 제외한다.
* ``autoMetrics.applies`` 가 false 인 문항은 제외한다.
* 모델 실패(예외, JSON 파싱 실패로 결과 없음)는 빼지 않고 오답으로 센다.
  빼면 자주 실패하는 모델일수록 점수가 좋아 보이기 때문이다.

태깅 (quality/persona/onboarding-tagging)
* primary/secondary 각각 micro precision·recall·F1. 문항마다 허용 정답(accepted)
  중 예측과 F1 이 가장 높은 것을 그 문항의 정답으로 본다.
* exact match: primary·secondary·off_topic 이 모두 허용 정답 중 하나와 같은 비율.
* off_topic 정확도와 양성(true) 건수.

추출 (quality/persona/build)
* 차원 판정 = 문항이 정한 차원마다 "점수를 내야 하는가 / 생략해야 하는가".
  - scoreInRange 차원: 점수가 있고 허용 범위 안이면 정답.
  - mustOmitFromRawModel 차원: 모델 원출력에 점수가 없으면 정답.
* 대표 지표는 dimension_judgment_accuracy(위 두 조건을 모두 반영).
  보조로 존재 여부만 본 presence 정확도와 범위 적중률을 같이 낸다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

TAGGING_TASK = "persona_tagging"
BUILD_TASK = "persona_build"
TAGGING_ITEM_METRICS = ("tagging_primary_f1", "tagging_secondary_f1", "tagging_exact_match")


@dataclass(frozen=True)
class Metric:
    name: str
    value: float
    comment: str | None = None


def _counted(task_input: dict[str, Any], output: Any, expected: dict[str, Any], task: str) -> bool:
    if task_input.get("task") != task:
        return False
    if not (expected.get("autoMetrics") or {}).get("applies"):
        return False
    return isinstance(output, dict) and output.get("mode") == "model"


def _model_result(output: dict[str, Any]) -> Any:
    """성공한 모델 결과. 실패(예외·결과 없음)면 None."""

    if output.get("status") != "ok":
        return None
    return output.get("result")


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(tp: int, fp: int, fn: int) -> float:
    return _ratio(2 * tp, 2 * tp + fp + fn) if tp + fp + fn else 1.0


# ══ 태깅 ════════════════════════════════════════════════


def _best_counts(predicted: set[str], accepted: Iterable[Iterable[str]]) -> tuple[int, int, int, bool]:
    """허용 정답 중 예측과 가장 잘 맞는 것 기준의 (tp, fp, fn, 완전일치)."""

    best: tuple[int, int, int, bool] | None = None
    for reference in accepted:
        gold = set(reference)
        tp, fp, fn = len(predicted & gold), len(predicted - gold), len(gold - predicted)
        candidate = (tp, fp, fn, predicted == gold)
        if best is None or _f1(*candidate[:3]) > _f1(*best[:3]):
            best = candidate
    return best if best is not None else (0, len(predicted), 0, not predicted)


def _failed_counts(accepted: list[list[str]]) -> tuple[int, int, int, bool]:
    """실패한 문항: 첫 번째 정답의 라벨을 모두 놓친 것으로 센다."""

    gold = set(accepted[0]) if accepted else set()
    return 0, 0, len(gold), False


def tagging_item(output: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    metrics = expected["autoMetrics"]
    result = _model_result(output)
    item: dict[str, Any] = {"failed": result is None}
    exact = result is not None
    for field in ("primary", "secondary"):
        accepted = metrics[field]["accepted"]
        if result is None:
            counts = _failed_counts(accepted)
        else:
            counts = _best_counts(set(result.get(field, [])), accepted)
        item[field] = counts[:3]
        exact = exact and counts[3]
    accepted_off_topic = metrics["off_topic"]["accepted"]
    off_topic_ok = result is not None and bool(result.get("off_topic")) in accepted_off_topic
    item["offTopicCorrect"] = off_topic_ok
    item["offTopicPositive"] = True in accepted_off_topic and False not in accepted_off_topic
    item["exact"] = exact and off_topic_ok
    return item


def tagging_item_metrics(output: dict[str, Any], expected: dict[str, Any]) -> list[Metric]:
    item = tagging_item(output, expected)
    if item["failed"]:
        return [Metric(name, 0.0, "model failed") for name in TAGGING_ITEM_METRICS]
    return [
        Metric("tagging_primary_f1", _f1(*item["primary"])),
        Metric("tagging_secondary_f1", _f1(*item["secondary"])),
        Metric("tagging_exact_match", float(item["exact"])),
    ]


def tagging_run_metrics(rows: Iterable[tuple[dict[str, Any], Any, dict[str, Any]]]) -> list[Metric]:
    """rows: (input, output, expected_output). 해당하지 않는 문항은 알아서 건너뛴다."""

    items = [tagging_item(out, exp) for inp, out, exp in rows if _counted(inp, out, exp, TAGGING_TASK)]
    if not items:
        return []
    metrics: list[Metric] = []
    for field in ("primary", "secondary"):
        tp = sum(item[field][0] for item in items)
        fp = sum(item[field][1] for item in items)
        fn = sum(item[field][2] for item in items)
        counts = f"tp={tp} fp={fp} fn={fn}"
        # 예측·정답이 모두 비어 분모가 0이면 틀린 게 없으므로 F1 과 같이 1.0 으로 둔다
        precision = _ratio(tp, tp + fp) if tp + fp else float(fn == 0)
        recall = _ratio(tp, tp + fn) if tp + fn else float(fp == 0)
        metrics += [
            Metric(f"tagging_{field}_precision", precision, counts),
            Metric(f"tagging_{field}_recall", recall, counts),
            Metric(f"tagging_{field}_f1", _f1(tp, fp, fn), counts),
        ]
    total = len(items)
    failed = sum(item["failed"] for item in items)
    exact = sum(item["exact"] for item in items)
    off_topic_ok = sum(item["offTopicCorrect"] for item in items)
    positives = [item for item in items if item["offTopicPositive"]]
    positive_ok = sum(item["offTopicCorrect"] for item in positives)
    metrics += [
        Metric("tagging_exact_match", _ratio(exact, total), f"{exact}/{total}"),
        Metric(
            "tagging_off_topic_accuracy",
            _ratio(off_topic_ok, total),
            f"{off_topic_ok}/{total}, off_topic=true 정답 {positive_ok}/{len(positives)}",
        ),
        Metric("tagging_failure_rate", _ratio(failed, total), f"{failed}/{total} (model 문항만)"),
    ]
    return metrics


# ══ 추출 ════════════════════════════════════════════════


def build_item(output: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    metrics = expected["autoMetrics"]
    ranges: dict[str, list[int]] = metrics.get("scoreInRange", {})
    omitted: list[str] = metrics.get("mustOmitFromRawModel", [])
    result = _model_result(output)
    raw: dict[str, Any] | None = result.get("raw") if result is not None else None

    judgments: list[bool] = []
    presence: list[bool] = []
    in_range: list[bool] = []
    for key, (low, high) in ranges.items():
        value = None if raw is None else raw.get(key)
        has_value = isinstance(value, int | float)
        hit = has_value and low <= value <= high
        presence.append(has_value)
        in_range.append(hit)
        judgments.append(hit)
    for key in omitted:
        absent = raw is not None and raw.get(key) is None
        presence.append(absent)
        judgments.append(absent)
    return {
        "failed": result is None,
        "judgments": judgments,
        "presence": presence,
        "inRange": in_range,
    }


def build_item_metrics(output: dict[str, Any], expected: dict[str, Any]) -> list[Metric]:
    item = build_item(output, expected)
    judgments = item["judgments"]
    return [Metric("build_dimension_judgment_accuracy", _ratio(sum(judgments), len(judgments)))]


def build_run_metrics(rows: Iterable[tuple[dict[str, Any], Any, dict[str, Any]]]) -> list[Metric]:
    items = [build_item(out, exp) for inp, out, exp in rows if _counted(inp, out, exp, BUILD_TASK)]
    if not items:
        return []

    def pooled(key: str) -> tuple[int, int]:
        values = [value for item in items for value in item[key]]
        return sum(values), len(values)

    judged_ok, judged = pooled("judgments")
    present_ok, present = pooled("presence")
    range_ok, ranged = pooled("inRange")
    total = len(items)
    failed = sum(item["failed"] for item in items)
    return [
        Metric("build_dimension_judgment_accuracy", _ratio(judged_ok, judged), f"{judged_ok}/{judged} 차원"),
        Metric("build_dimension_presence_accuracy", _ratio(present_ok, present), f"{present_ok}/{present} 차원"),
        Metric("build_score_in_range_rate", _ratio(range_ok, ranged), f"{range_ok}/{ranged} 차원"),
        Metric("build_failure_rate", _ratio(failed, total), f"{failed}/{total} (model 문항만)"),
    ]


# ══ 진입점 ══════════════════════════════════════════════


def item_metrics(task_input: dict[str, Any], output: Any, expected: dict[str, Any]) -> list[Metric]:
    if _counted(task_input, output, expected, TAGGING_TASK):
        return tagging_item_metrics(output, expected)
    if _counted(task_input, output, expected, BUILD_TASK):
        return build_item_metrics(output, expected)
    return []


def run_metrics(rows: list[tuple[dict[str, Any], Any, dict[str, Any]]]) -> list[Metric]:
    return [*tagging_run_metrics(rows), *build_run_metrics(rows)]
