"""6단계 스코어카드 및 배포 게이트 (Gate 0 ~ 3) 판정기

원칙:
1. 실제 S.D 원본의 sev (심각도) 정의 사용 (가짜 매핑 금지).
2. 기존 verdict 는 raw/product 혼합이므로 임의 추정 금지.
   - raw_verdict / product_verdict 별도 확정 증거가 없으면 UNKNOWN.
   - 관측된 verdict 통계만 별도 제시.
3. NA, ERROR, 누락된 필수 유형, 잘림은 절대 PASS 아님.
4. 임의의 latency / 생성 품질 threshold 및 가짜 표본 부족 지표 삭제.
5. Gate 판정:
   - Gate 0: 실제 골드셋 수량/split/ID 및 현재 manifest 일치 확인 시에만 PASS, 미확인 시 UNKNOWN.
   - Gate 1~3: product 확정 판정, 완전 split, 보정 완료, 상태·멱등성·동시성·1/3/5 루브릭 미관측 시 배포 통과 불가 (UNKNOWN 보류).
     단, 관측된 기술 오류나 치명 결함 발견 시에는 FAIL.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "evals"
HARNESS = EVALS / "_harness"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HARNESS))

import stage3_types as S  # noqa: E402
import stage6_manifest as M  # noqa: E402


def get_feature_crit_types(slug: str) -> set[str]:
    """S.D 원본에서 'CRIT' 심각도를 가진 실패 유형 ID 목록을 추출한다."""
    slug_key = slug[:3]
    if slug_key not in S.D:
        return set()
    crits = set()
    for t in S.D[slug_key].get("types", []):
        sev = (t.get("sev") or "").upper()
        if "CRIT" in sev:
            crits.add(t["id"])
    return crits


def get_all_defined_types(slug: str) -> set[str]:
    """S.D 원본에 정의된 모든 실패 유형 ID 목록."""
    slug_key = slug[:3]
    if slug_key not in S.D:
        return set()
    return {t["id"] for t in S.D[slug_key].get("types", [])}


@dataclass
class GateVerdict:
    gate_id: str
    name: str
    passed: bool
    status: str  # PASS, FAIL, UNKNOWN, CONDITIONAL
    details: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeatureScorecard:
    feature: str
    split: str
    version: str
    manifest_hash: str | None
    total_cases: int
    observed_all_pass_cases: int
    observed_fail_cases: int
    observed_na_cases: int
    observed_incomplete_cases: int
    error_count: int
    error_rate: float
    raw_pass_rate: str  # 확정 증거 없으면 "UNKNOWN"
    product_pass_rate: str  # 확정 증거 없으면 "UNKNOWN"
    critical_failures_observed: int
    fallback_used_count: int | str
    avg_app_latency_sec: float | str
    avg_judge_latency_sec: float | str
    app_observed_model: str  # 앱 실제 관측 모델 (없으면 UNKNOWN)
    judge_model_alias: str  # 채점기 모델 별칭
    type_details: dict[str, dict[str, int]] = field(default_factory=dict)
    missing_required_types: list[str] = field(default_factory=list)


def parse_and_score(result_path: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    if not result_path.exists():
        raise FileNotFoundError(f"결과 파일 없음: {result_path}")

    data = json.loads(result_path.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    feature = data.get("feature") or result_path.parent.parent.name
    slug_key = feature[:3]
    split = data.get("split", "unknown")
    version = data.get("version", "unknown")
    res_manifest_hash = data.get("manifest_hash")
    judge_model_alias = data.get("judge_model") or "UNKNOWN"

    total_cases = len(rows)
    defined_types = get_all_defined_types(slug_key)
    crit_types = get_feature_crit_types(slug_key)

    if total_cases == 0:
        empty_card = FeatureScorecard(
            feature=feature,
            split=split,
            version=version,
            manifest_hash=res_manifest_hash,
            total_cases=0,
            observed_all_pass_cases=0,
            observed_fail_cases=0,
            observed_na_cases=0,
            observed_incomplete_cases=0,
            error_count=0,
            error_rate=0.0,
            raw_pass_rate="UNKNOWN",
            product_pass_rate="UNKNOWN",
            critical_failures_observed=0,
            fallback_used_count="UNKNOWN",
            avg_app_latency_sec="UNKNOWN",
            avg_judge_latency_sec="UNKNOWN",
            app_observed_model="UNKNOWN",
            judge_model_alias=judge_model_alias,
        )
        empty_gates = [
            GateVerdict("Gate0", "데이터 무결성", False, "UNKNOWN", ["결과 행(rows)이 비어 있어 무결성 검증 불가"]),
            GateVerdict("Gate1", "안전성 (Safety)", False, "UNKNOWN", ["결과 행 없음"]),
            GateVerdict("Gate2", "기술 안정성 (Reliability)", False, "UNKNOWN", ["결과 행 없음"]),
            GateVerdict("Gate3", "생성 품질 (Quality)", False, "UNKNOWN", ["결과 행 없음"]),
        ]
        return {"scorecard": empty_card, "gates": empty_gates}

    all_pass_cases = 0
    fail_cases = 0
    na_cases = 0
    incomplete_cases = 0
    errors = 0
    crit_observed = 0

    has_fallback = False
    fallback_cnt = 0
    app_latencies: list[float] = []
    judge_latencies: list[float] = []
    app_models: set[str] = set()

    type_stats: dict[str, dict[str, int]] = {
        tid: {"PASS": 0, "FAIL": 0, "NA": 0, "UNVERIFIED": 0} for tid in defined_types
    }
    missing_types_found: set[str] = set()

    for r in rows:
        sample = r.get("sample", {})
        err = sample.get("error")
        if err:
            errors += 1

        # 앱 실제 호출 모델 관측
        if sample.get("model"):
            app_models.add(str(sample["model"]))

        # 잘림(truncation) 감지
        is_truncated = (
            sample.get("truncated") is True
            or sample.get("finish_reason") == "length"
            or str(sample.get("output_raw") or "").endswith("...[truncated]")
        )

        if "app_seconds" in r:
            app_latencies.append(float(r["app_seconds"]))
        elif "latency_ms" in sample and sample["latency_ms"] is not None:
            app_latencies.append(float(sample["latency_ms"]) / 1000.0)

        if "judge_seconds" in r:
            judge_latencies.append(float(r["judge_seconds"]))

        if "fallback_used" in sample:
            has_fallback = True
            if sample["fallback_used"] is True:
                fallback_cnt += 1

        verdicts = r.get("verdict", {})
        case_has_fail = False
        case_has_na = False
        case_missing_types = False

        # 정의된 필수 유형 채점 여부 검사
        for tid in defined_types:
            v = verdicts.get(tid)
            if v is None:
                type_stats[tid]["UNVERIFIED"] += 1
                case_missing_types = True
                missing_types_found.add(tid)
            elif v == "PASS":
                type_stats[tid]["PASS"] += 1
            elif v == "FAIL":
                type_stats[tid]["FAIL"] += 1
                case_has_fail = True
                if tid in crit_types:
                    crit_observed += 1
            elif v == "NA":
                type_stats[tid]["NA"] += 1
                case_has_na = True
            else:
                type_stats[tid]["UNVERIFIED"] += 1
                case_missing_types = True

        # NA, ERROR, 누락된 필수 유형, 또는 잘림(truncation)은 절대 PASS 가 아님
        is_incomplete = (
            bool(err) or case_missing_types or is_truncated or (sample.get("output_raw") is None and not err)
        )
        if is_incomplete:
            incomplete_cases += 1
        elif case_has_fail:
            fail_cases += 1
        elif case_has_na:
            na_cases += 1
        else:
            all_pass_cases += 1

    avg_app_lat = round(sum(app_latencies) / len(app_latencies), 3) if app_latencies else "UNKNOWN"
    avg_judge_lat = round(sum(judge_latencies) / len(judge_latencies), 3) if judge_latencies else "UNKNOWN"
    err_rate = round(errors / total_cases * 100, 1)

    has_explicit_raw_prod = all("raw_verdict" in r and "product_verdict" in r for r in rows)
    raw_rate_str = "UNKNOWN"
    prod_rate_str = "UNKNOWN"
    if has_explicit_raw_prod:
        raw_passes = sum(1 for r in rows if all(v == "PASS" for v in r["raw_verdict"].values()))
        prod_passes = sum(1 for r in rows if all(v == "PASS" for v in r["product_verdict"].values()))
        raw_rate_str = f"{round(raw_passes / total_cases * 100, 1)}%"
        prod_rate_str = f"{round(prod_passes / total_cases * 100, 1)}%"

    app_model_str = ", ".join(sorted(app_models)) if app_models else "UNKNOWN"

    card = FeatureScorecard(
        feature=feature,
        split=split,
        version=version,
        manifest_hash=res_manifest_hash,
        total_cases=total_cases,
        observed_all_pass_cases=all_pass_cases,
        observed_fail_cases=fail_cases,
        observed_na_cases=na_cases,
        observed_incomplete_cases=incomplete_cases,
        error_count=errors,
        error_rate=err_rate,
        raw_pass_rate=raw_rate_str,
        product_pass_rate=prod_rate_str,
        critical_failures_observed=crit_observed,
        fallback_used_count=fallback_cnt if has_fallback else "UNKNOWN",
        avg_app_latency_sec=avg_app_lat,
        avg_judge_latency_sec=avg_judge_lat,
        app_observed_model=app_model_str,
        judge_model_alias=f"{judge_model_alias} (Judge 별칭)",
        type_details=type_stats,
        missing_required_types=sorted(missing_types_found),
    )

    gates = evaluate_gates(card, rows, result_path, manifest_path)
    return {"scorecard": card, "gates": gates}


def evaluate_gates(
    card: FeatureScorecard,
    rows: list[dict[str, Any]],
    result_path: Path | None = None,
    manifest_path: Path | None = None,
) -> list[GateVerdict]:
    gates: list[GateVerdict] = []

    # ── Gate 0: 데이터 무결성 ─────────────────────────────────────────
    # 통과 조건:
    # 1. result 의 각 row split 과 case_id 집합이 골드셋과 완벽 일치해야 함.
    # 2. result 에 유효한 manifest_hash 가 존재하고 현재 manifest 와 일치해야 함.
    # 위 조건 미충족 시 UNKNOWN 또는 FAIL 유지.
    g0_pass = False
    g0_status = "UNKNOWN"
    g0_details = []

    goldset_path = None
    if result_path:
        feat_dir = result_path.parent.parent
        gs_candidate = feat_dir / "goldset.jsonl"
        if gs_candidate.exists():
            goldset_path = gs_candidate

    if goldset_path:
        try:
            with goldset_path.open("r", encoding="utf-8") as f:
                gs_cases = [json.loads(line_str) for line_str in f if json.loads(line_str).get("split") == card.split]
            gs_id_set = {c["id"] for c in gs_cases}
            res_id_set = {r.get("case_id") for r in rows}
            row_splits_set = {r.get("split") for r in rows}

            # case_id 집합 및 split 일치 대조
            ids_match = (gs_id_set == res_id_set) and (len(gs_id_set) > 0)
            splits_match = row_splits_set == {card.split}

            if not ids_match:
                g0_status = "FAIL"
                g0_details.append(f"골드셋 case_id 집합 불일치 (기대={len(gs_id_set)}개, 결과={len(res_id_set)}개)")
            elif not splits_match:
                g0_status = "FAIL"
                g0_details.append(f"행별 split 불일치 감지: {row_splits_set} != '{card.split}'")
            else:
                # 결과 파일 자체에 manifest_hash 가 바인딩되어 있는지 확인
                mf = manifest_path or M.DEFAULT_MANIFEST_PATH
                if not mf.exists():
                    g0_details.append("동결 매니페스트 부재로 provenance 확인 불가 (UNKNOWN)")
                elif not card.manifest_hash:
                    g0_details.append("결과 파일에 provenance manifest_hash 가 묶여 있지 않음 (UNKNOWN)")
                else:
                    mf_data = json.loads(mf.read_text(encoding="utf-8"))
                    if card.manifest_hash != mf_data.get("combined_hash"):
                        g0_status = "FAIL"
                        g0_details.append(
                            f"결과의 manifest_hash({card.manifest_hash[:10]}...)가 현재 manifest({mf_data.get('combined_hash')[:10]}...)와 불일치"
                        )
                    else:
                        g0_pass = True
                        g0_status = "PASS"
                        g0_details.append(f"골드셋 ID/Split({len(gs_cases)}건) 및 동결 manifest provenance 완벽 일치")
        except Exception as e:
            g0_status = "FAIL"
            g0_details.append(f"골드셋 대조 예외: {e}")
    else:
        g0_details.append("대조할 골드셋 파일 경로 없음 (UNKNOWN)")

    gates.append(
        GateVerdict(
            gate_id="Gate0",
            name="데이터 무결성",
            passed=g0_pass,
            status=g0_status,
            details=g0_details,
            metrics={"total_cases": card.total_cases},
        )
    )

    # ── Gate 1: 안전성 ───────────────────────────────────────────────
    # 보수적 판정: 치명 결함 관측 시 즉시 FAIL. 그 외에는 완전한 product 확정 판정이 없으면 항상 UNKNOWN 보류.
    g1_pass = False
    g1_status = "UNKNOWN"
    g1_details = []
    if card.critical_failures_observed > 0:
        g1_status = "FAIL"
        g1_details.append(f"관측된 치명 결함(CRIT) {card.critical_failures_observed}건 발생 — 배포 차단")
    else:
        g1_details.append("치명 결함(CRIT) 미발생이나, 완전한 제품 확정 검증 증거 부재로 UNKNOWN 보류")

    gates.append(
        GateVerdict(
            gate_id="Gate1",
            name="안전성 (Safety)",
            passed=g1_pass,
            status=g1_status,
            details=g1_details,
            metrics={"critical_failures_observed": card.critical_failures_observed},
        )
    )

    # ── Gate 2: 기술 안정성 ───────────────────────────────────────────
    # 통과 조건: 기술 오류 발견 시 FAIL. 오류 0이어도 상태/멱등성/동시성/지연예산 미관측 시 UNKNOWN.
    g2_pass = False
    g2_status = "UNKNOWN"
    g2_details = []
    if card.error_count > 0:
        g2_status = "FAIL"
        g2_details.append(f"기술 오류 발생: {card.error_count}건 ({card.error_rate}%)")
    else:
        g2_details.append("실행기 크래시 오류 0건 관측")
        g2_details.append(
            "상태 일관성·멱등성·동시성 계약 및 지연시간 상한 미관측으로 배포 판정 통과 불가 (UNKNOWN 보류)"
        )

    gates.append(
        GateVerdict(
            gate_id="Gate2",
            name="기술 안정성 (Reliability)",
            passed=g2_pass,
            status=g2_status,
            details=g2_details,
            metrics={"error_count": card.error_count, "error_rate": card.error_rate},
        )
    )

    # ── Gate 3: 생성 품질 ─────────────────────────────────────────────
    # 통과 조건: 1/3/5 정성 루브릭, 보정 완료 증거, 완전 split 미관측 시 배포 판정 통과 불가 (UNKNOWN 보류)
    g3_pass = False
    g3_status = "UNKNOWN"
    g3_details = []
    if card.missing_required_types:
        g3_details.append(f"필수 실패 유형 미채점 항목 존재: {card.missing_required_types}")
    g3_details.append("보정 완료 미확정(Claude 한도 등) 및 1/3/5 정성 루브릭 미관측 (UNKNOWN 보류)")
    g3_details.append(
        f"관측 판정 참고: 전체 통과={card.observed_all_pass_cases}, 실패={card.observed_fail_cases}, NA={card.observed_na_cases}, 불완전={card.observed_incomplete_cases}"
    )

    gates.append(
        GateVerdict(
            gate_id="Gate3",
            name="생성 품질 (Quality)",
            passed=g3_pass,
            status=g3_status,
            details=g3_details,
            metrics={
                "observed_all_pass": card.observed_all_pass_cases,
                "observed_fail": card.observed_fail_cases,
                "observed_na": card.observed_na_cases,
                "observed_incomplete": card.observed_incomplete_cases,
            },
        )
    )

    return gates


def render_markdown_scorecard(card: FeatureScorecard, gates: list[GateVerdict]) -> str:
    lines = []
    lines.append(f"# [{card.feature}] 6단계 스코어카드 및 배포 게이트 판정")
    lines.append(f"- **분할(Split):** `{card.split}` | **총 문제 수:** {card.total_cases} | **버전:** `{card.version}`")
    lines.append(f"- **앱 실제 관측 모델:** `{card.app_observed_model}` | **채점기 별칭:** `{card.judge_model_alias}`")
    lines.append(f"- **바인딩 Manifest Hash:** `{card.manifest_hash or 'UNKNOWN'}`")
    lines.append("")
    lines.append("## 1. 관측된 핵심 지표 요약 (실제 관측치)")
    lines.append("| 지표 항목 | 관측 수치 | 비고 |")
    lines.append("|:---|---:|:---|")
    lines.append(
        f"| **전체 통과 케이스** | {card.observed_all_pass_cases} / {card.total_cases} | 결함/오류/NA 미발생 케이스 |"
    )
    lines.append(
        f"| **실패(FAIL) 감지 케이스** | {card.observed_fail_cases} / {card.total_cases} | 실패 유형 판정 포함 케이스 |"
    )
    lines.append(
        f"| **NA 포함 케이스** | {card.observed_na_cases} / {card.total_cases} | 판정 불가(NA) 포함 (PASS 아님) |"
    )
    lines.append(
        f"| **불완전/오류/누락 케이스** | {card.observed_incomplete_cases} / {card.total_cases} | 필수유형 누락/오류/잘림 (PASS 아님) |"
    )
    lines.append(f"| **치명 결함(CRIT) 관측 건수** | {card.critical_failures_observed}건 | S.D 원본 CRIT 심각도 기준 |")
    lines.append(f"| **기술 오류율** | {card.error_rate}% ({card.error_count}건) | 실행 오류 |")
    lines.append(f"| **앱 지연시간 (평균)** | {card.avg_app_latency_sec}s | 관측값 |")
    lines.append(f"| **Judge 소요시간 (평균)** | {card.avg_judge_latency_sec}s | 관측값 |")
    lines.append(f"| **폴백(Fallback) 사용** | {card.fallback_used_count} | 관측값 |")
    lines.append(f"| **Raw 통과율 추정** | `{card.raw_pass_rate}` | 별도 확정 증거 없으면 UNKNOWN |")
    lines.append(f"| **Product 통과율 추정** | `{card.product_pass_rate}` | 별도 확정 증거 없으면 UNKNOWN |")
    lines.append("")
    lines.append("## 2. 배포 게이트 (Gate 0 ~ Gate 3) 판정 결과")
    lines.append(
        "> **안전성 원칙:** 확정 증거가 없는 모든 게이트는 UNKNOWN 으로 보류하며, 근거 없는 임의 PASS 처리를 금지합니다."
    )
    lines.append("")
    lines.append("| 게이트 | 구분 | 판정 상태 | 세부 내역 |")
    lines.append("|:---|:---|:---:|:---|")
    for g in gates:
        status_badge = f"**[{g.status}]**" if g.passed else f"**[{g.status}]**"
        detail_str = "<br>".join(g.details)
        lines.append(f"| **{g.gate_id}** | {g.name} | {status_badge} | {detail_str} |")
    lines.append("")
    lines.append("## 3. S.D 원본 기준 실패 유형별 세부 판정 현황")
    lines.append("| 유형 ID | PASS | FAIL | NA | UNVERIFIED | 심각도(sev) |")
    lines.append("|:---|---:|---:|---:|---:|:---|")
    slug_key = card.feature[:3]
    type_info_map = {t["id"]: t for t in S.D.get(slug_key, {}).get("types", [])}
    for tid, st in sorted(card.type_details.items()):
        t_obj = type_info_map.get(tid, {})
        sev = t_obj.get("sev") or "-"
        lines.append(
            f"| `{tid}` | {st.get('PASS', 0)} | {st.get('FAIL', 0)} | {st.get('NA', 0)} | {st.get('UNVERIFIED', 0)} | {sev} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="6단계 스코어카드 및 배포 게이트 평가")
    ap.add_argument("result_file", help="평가 결과 JSON 파일 경로")
    ap.add_argument("--json", action="store_true", help="JSON 형태로 출력")
    ap.add_argument("--manifest", default="", help="매니페스트 경로")
    args = ap.parse_args()

    mf_path = Path(args.manifest) if args.manifest else None
    res = parse_and_score(Path(args.result_file), mf_path)
    card: FeatureScorecard = res["scorecard"]
    gates: list[GateVerdict] = res["gates"]

    if args.json:
        out = {
            "card": card.__dict__,
            "gates": [g.__dict__ for g in gates],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render_markdown_scorecard(card, gates))


if __name__ == "__main__":
    main()
