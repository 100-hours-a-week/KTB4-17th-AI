"""평가 결과 파일의 점수를 Langfuse 에 올려 실행끼리 비교한다. 평가를 다시 돌리지 않는다.

    uv run python scripts/upload_eval_scores.py evals/results/tagging-v1.json --dry-run
    uv run python scripts/upload_eval_scores.py evals/results/tagging-v1.json
    uv run python scripts/upload_eval_scores.py evals/f03-persona-extraction/results/<파일>.json --run-name f03-extraction-v1

지원하는 결과: scripts/eval_tagging.py --out, evals/_harness/stage5_run.py.

- 실행 하나가 세션 eval-<run> 이다. 요약 점수는 세션에, 케이스 점수는 케이스별 trace 에 붙는다.
- 환경은 eval-results 로 고정해 운영 trace 와 섞이지 않는다.
- trace·점수 ID 를 run 과 case 로 정하므로 다시 올리면 덮어쓴다.
- 입력·출력·Judge 근거는 올리지 않는다. 사용자 답변이 들어 있을 수 있다. 원문은 결과 파일에서 본다.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langfuse import Langfuse, propagate_attributes

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = "eval-results"


@dataclass
class Score:
    name: str
    value: float | str
    data_type: str


@dataclass
class Case:
    id: str
    metadata: dict[str, Any]
    scores: list[Score]


@dataclass
class Report:
    run: str
    kind: str
    metadata: dict[str, Any]
    cases: list[Case] = field(default_factory=list)
    summary: list[Score] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return f"eval-{self.run}"


def _flag(name: str, value: Any) -> Score:
    return Score(name, float(bool(value)), "BOOLEAN")


def _number(name: str, value: float) -> Score:
    return Score(name, float(value), "NUMERIC")


def _tagging_report(data: dict, run: str) -> Report:
    cases = [
        Case(
            id=row["case_id"],
            metadata={"category": row.get("category")},
            scores=[
                *(_flag(f"tagging.{key}", value) for key, value in row["score"].items()),
                _number("tagging.latency_s", row["latency_s"]),
            ],
        )
        for row in data["results"]
    ]
    summary = [
        _number(f"tagging.{key}", value)
        for key, value in data["summary"].items()
        if key != "cases" and isinstance(value, int | float)
    ]
    metadata = {"model": data.get("model"), "splits": data.get("splits")}
    return Report(run, "tagging", metadata, cases, summary)


def _stage5_case(row: dict) -> Case:
    sample = row.get("sample") or {}
    scores = [Score(f"verdict.{key}", str(value), "CATEGORICAL") for key, value in row["verdict"].items()]
    if "fallback_used" in sample:
        scores.append(_flag("fallback_used", sample["fallback_used"]))
    scores.append(_flag("app_error", sample.get("error")))
    metadata = {
        "split": row.get("split"),
        "model": sample.get("model"),
        "target_failures": row.get("target_failures"),
    }
    return Case(row["case_id"], metadata, scores)


def _stage5_summary(rows: list[dict]) -> list[Score]:
    """PASS·FAIL 만 분모에 넣는다. NA·ERROR 는 실패도 통과도 아니라 unjudged_count 로 따로 남긴다."""
    summary = []
    for failure in sorted({key for row in rows for key in row["verdict"]}):
        verdicts = [row["verdict"].get(failure) for row in rows]
        failed = verdicts.count("FAIL")
        judged = failed + verdicts.count("PASS")
        summary += [_number(f"fail_count.{failure}", failed), _number(f"unjudged_count.{failure}", len(rows) - judged)]
        if judged:
            summary.append(_number(f"fail_rate.{failure}", round(failed / judged, 4)))
    samples = [row.get("sample") or {} for row in rows]
    with_routing = [sample for sample in samples if "fallback_used" in sample]
    if with_routing:
        rate = sum(bool(sample["fallback_used"]) for sample in with_routing) / len(with_routing)
        summary.append(_number("fallback_rate", round(rate, 4)))
    summary.append(_number("app_error_count", sum(bool(sample.get("error")) for sample in samples)))
    return summary


def _stage5_report(data: dict, run: str) -> Report:
    metadata = {key: data.get(key) for key in ("feature", "split", "version", "judge_model", "git_commit")}
    cases = [_stage5_case(row) for row in data["rows"]]
    return Report(run, data.get("feature") or "stage5", metadata, cases, _stage5_summary(data["rows"]))


def build_report(data: dict, *, run: str) -> Report:
    if "results" in data and "summary" in data:
        return _tagging_report(data, run)
    if "rows" in data:
        return _stage5_report(data, run)
    raise SystemExit("지원하지 않는 결과 형식입니다. eval_tagging.py 또는 stage5_run.py 의 결과 파일을 넘기세요.")


def _send(client: Any, report: Report, score: Score, key: str, **target: str) -> None:
    client.create_score(
        name=score.name,
        value=score.value,
        data_type=score.data_type,
        score_id=Langfuse.create_trace_id(seed=f"{report.run}:{key}:{score.name}"),
        environment=ENVIRONMENT,
        **target,
    )


def upload(client: Any, report: Report) -> None:
    with propagate_attributes(session_id=report.session_id, tags=["eval-result", report.kind]):
        for case in report.cases:
            trace_id = Langfuse.create_trace_id(seed=f"{report.run}:{case.id}")
            with client.start_as_current_observation(
                trace_context={"trace_id": trace_id},
                name=f"eval/{report.kind}",
                metadata={"caseId": case.id, **report.metadata, **case.metadata},
            ):
                pass
            for score in case.scores:
                _send(client, report, score, case.id, trace_id=trace_id)
    for score in report.summary:
        _send(client, report, score, "summary", session_id=report.session_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("result", type=Path, help="평가 결과 JSON")
    parser.add_argument("--run-name", help="비교할 때 보일 실행 이름. 기본은 파일 이름")
    parser.add_argument("--dry-run", action="store_true", help="올릴 내용만 출력한다")
    args = parser.parse_args(argv)

    data = json.loads(args.result.read_text(encoding="utf-8"))
    report = build_report(data, run=args.run_name or args.result.stem)
    print(
        f"session={report.session_id} kind={report.kind} cases={len(report.cases)} "
        f"case_scores={sum(len(case.scores) for case in report.cases)} summary_scores={len(report.summary)}"
    )
    print(json.dumps({score.name: score.value for score in report.summary}, ensure_ascii=False))
    if args.dry_run:
        return 0

    load_dotenv(ROOT / ".env")
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        raise SystemExit("LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 가 없습니다. .env 에 넣으세요.")
    client = Langfuse(environment=ENVIRONMENT, tracing_enabled=True)
    try:
        authenticated = client.auth_check()
    except Exception as error:  # noqa: BLE001  SDK 오류 원문은 응답 헤더까지 담아 길다
        raise SystemExit(
            f"Langfuse 인증 실패 ({type(error).__name__}). 키와 LANGFUSE_BASE_URL 을 확인하세요."
        ) from None
    if not authenticated:
        raise SystemExit("Langfuse 인증 실패. 키와 LANGFUSE_BASE_URL 을 확인하세요.")
    upload(client, report)
    client.shutdown()
    print(f"전송 요청 완료: 세션 {report.session_id}, 환경 {ENVIRONMENT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
