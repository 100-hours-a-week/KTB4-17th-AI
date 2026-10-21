#!/usr/bin/env python3
"""chat_end (F7) 성능평가 러너 및 종합 품질 분석 스크립트.

- 대상 기능:
  - S: DraftAgent (1·2·3단계 스크립트 초안 생성)
  - M: EndMessageAgent (턴별 방향 기반 최종 종료 메시지 생성)
- 점수 이원화:
  - 모델 점수 (Model Score): 1차 LLM 원본 생성물
  - 제품 점수 (Product Score): 폴백/재작성/접미사 보정 후 최종 반환물
- 자동 검사: 하드 어설션, 치명 결함(E7-CRIT-01~07), 4대 루브릭(A, B, C, D), 블라인드 톤 식별.
- 배포 게이트: Gate 1 (치명 0건), Gate 2 (기술안정성), Gate 3 (루브릭 >= 3.5, 톤 식별 >= 80%).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# 로깅 및 환경 설정
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.guardrail.rules import EMOJI_REGEX  # noqa: E402
from app.features.chat_end.agents import (  # noqa: E402
    CLOSING_SUFFIX,
    DRAFT_SYSTEM,
    FALLBACK_SCRIPTS,
    TONE,
    TURN_GUIDE,
    DraftAgent,
    EndMessageAgent,
    _call_json,
    _transcript,
    fill_script,
    is_closing_message,
    is_daily_greeting,
    normalize,
)
from app.features.chat_end.schemas import EndType, RecentMessage, Speaker  # noqa: E402

DATASET_FILE = ROOT / "evals" / "quality_datasets" / "chat_end.jsonl"

AI_SELF_TERMS = re.compile(r"인공지능|챗봇|언어\s*모델|\bAI\b|어시스턴트|대신\s*(?:작성|써)")
ATTACK_TERMS = re.compile(r"장난치|어이없|따위|짜증|한심|답답|최악|예의가\s*없")


def check_hard_assertions(text: str, turn: int, is_draft: bool = False) -> list[str]:
    """텍스트 한 문장에 대한 하드 어설션 위반 목록 반환."""
    violations = []
    norm = normalize(text)

    if not norm:
        violations.append("빈 문자열")
        return violations
    if len(norm) > 200:
        violations.append(f"200자 초과 ({len(norm)}자)")
    if EMOJI_REGEX.search(text):
        violations.append("이모지 포함")
    if "?" in text or "？" in text:
        violations.append("질문형 기호(?) 포함")
    if is_daily_greeting(text):
        violations.append("일상 인사/재개 여지 포함")

    if is_draft:
        if turn < 3 and re.search(r"종료|더\s*이상\s*답장", text):
            violations.append("1·2턴 조기 종료 선언")
        if turn == 3 and not is_closing_message(text):
            violations.append("3턴 종료 선언 누락")
    else:
        if turn == 3 and not is_closing_message(text):
            violations.append("3턴 종료 선언 누락")

    return violations


def detect_critical_failures(text: str, turn: int, is_final_turn: bool = False) -> list[str]:
    """치명 결함(E7-CRIT-01 ~ 07) 탐지."""
    crits = []
    if is_daily_greeting(text):
        crits.append("E7-CRIT-01(재개 여지)")
    if is_final_turn and not is_closing_message(text):
        crits.append("E7-CRIT-02(마무리 의사 부재)")
    if AI_SELF_TERMS.search(text):
        crits.append("E7-CRIT-06(정체 자백)")
    if ATTACK_TERMS.search(text):
        crits.append("E7-CRIT-07(공격·비하)")
    return crits


def score_rubric(
    text: str,
    turn: int,
    end_type: str,
    crits: list[str],
    violations: list[str],
) -> dict[str, float]:
    """4대 루브릭 1/3/5 척도 점수 산출."""
    # 축 A: 단절 (Closure)
    if crits or "3턴 종료 선언 누락" in violations:
        score_a = 1.0
    elif violations:
        score_a = 3.0
    else:
        score_a = 5.0

    # 축 B: 배려 (Politeness)
    if "E7-CRIT-07(공격·비하)" in crits or any("탓" in v for v in violations):
        score_b = 1.0
    elif any(k in text for k in ("감사", "고마", "마음", "잘 지내", "좋은 분")):
        score_b = 5.0
    else:
        score_b = 3.5

    # 축 C: 맞춤 대응 (Relevance)
    if "빈 문자열" in violations:
        score_c = 1.0
    elif len(text) < 15:
        score_c = 3.0
    else:
        score_c = 4.5

    # 축 D: 톤 적합성 (Tone)
    t = end_type.lower()
    if t == "gentle":
        # 부드럽고 다정한 어미 (~요, 감사, 마음, 고마웠어요)
        score_d = 5.0 if any(k in text for k in ("고마", "감사", "마음", "좋은 분", "미안")) else 3.0
    elif t == "direct":
        # 분명하고 단호한 어미 (~합니다, 맞지 않는, 변함이 없, 어렵습니다)
        score_d = 5.0 if any(k in text for k in ("합니다", "않습니다", "어렵습니다", "맞지", "변함")) else 3.5
    else:  # casual
        # 가볍고 담백한 어미 (할게요, 안 할게요, 얘기 나눠 줘서, 잘 지내)
        score_d = 5.0 if any(k in text for k in ("할게요", "여기까지", "얘기", "잘 지내", "안 할게")) else 3.5

    return {
        "A_closure": score_a,
        "B_politeness": score_b,
        "C_relevance": score_c,
        "D_tone": score_d,
    }


def predict_tone(text: str) -> str:
    """텍스트 어휘/어미 특성 기반 블라인드 톤 분류 예측."""
    # Gentle: 부드러운 해요체, 감사/고마움/마음/인연/좋은 분
    gentle_pts = 0
    if re.search(r"고마웠어요|감사해요|마음\s*써|좋은\s*분|인연|바라요|감사했습니다|고마웠습니다", text):
        gentle_pts += 3
    if re.search(r"~?해요|~?게요|것\s*같아요|드려요", text):
        gentle_pts += 1

    # Direct: 단호한 하십시오/합니다체, 맞지 않음, 변함없음, 어렵습니다
    direct_pts = 0
    if re.search(r"맞지\s*않|어렵습니다|변함이\s*없|종료하겠습니다|결정은|않겠습니다", text):
        direct_pts += 3
    if re.search(r"합니다|바랍니다|드리지\s*않", text):
        direct_pts += 1

    # Casual: 담백하고 가벼운 구어체, 여기까지, 얘기, 잘 지내, 이제 답장 안 해
    casual_pts = 0
    if re.search(r"여기까지만|얘기\s*나눠|잘\s*지내|답장\s*안\s*할|생각은\s*그대로|정리할게요", text):
        casual_pts += 3
    if re.search(r"할게요|안\s*할게|고마웠어|그대로예요", text):
        casual_pts += 1

    scores = {"gentle": gentle_pts, "direct": direct_pts, "casual": casual_pts}
    sorted_tones = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if sorted_tones[0][1] == 0:
        return "gentle"  # 기본값
    return sorted_tones[0][0]


async def eval_draft_case(
    agent: DraftAgent,
    record: dict[str, Any],
    live: bool,
) -> dict[str, Any]:
    """DraftAgent 케이스 평가."""
    inp = record["input"]
    end_type_str = inp["end_type"].upper()
    end_type = EndType(end_type_str)
    recent = [
        RecentMessage(speaker=Speaker(m["speaker"].upper()), content=m["content"]) for m in inp["recent_messages"]
    ]

    t0 = time.perf_counter()
    raw_turns: list[str] = []
    product_turns: list[str] = []
    source = "mock"
    latency = 0.0

    if live:
        try:
            # 1. 모델 원본 추출
            content = f"[톤] {TONE[end_type]}\n\n{_transcript(recent)}"
            data = await _call_json(
                system=DRAFT_SYSTEM,
                messages=[{"role": "user", "content": content}],
                max_tokens=700,
                name="chat-end-drafts-eval",
            )
            raw = data.get("turns")
            if isinstance(raw, list):
                raw_turns = [normalize(str(x)) for x in raw]
            product_turns, source = fill_script(raw, end_type)
        except Exception:
            product_turns, source = FALLBACK_SCRIPTS[end_type], "fallback_error"
            raw_turns = []
        latency = time.perf_counter() - t0
    else:
        # Dry-run mock: fallback 스크립트 기반
        product_turns = list(FALLBACK_SCRIPTS[end_type])
        raw_turns = list(product_turns)
        source = "mock"
        latency = 0.05

    # 검사 및 채점 (제품 점수 기준 & 모델 점수 기준)
    model_crits = []
    prod_crits = []
    model_violations = []
    prod_violations = []

    # 모델 점수 검사
    if raw_turns:
        for idx, t in enumerate(raw_turns[:3]):
            v = check_hard_assertions(t, turn=idx + 1, is_draft=True)
            c = detect_critical_failures(t, turn=idx + 1, is_final_turn=(idx == 2))
            model_violations.extend(v)
            model_crits.extend(c)
    else:
        model_violations.append("모델 응답 부재 / JSON 파싱 실패")
        model_crits.append("E7-CRIT-02(응답 실패)")

    # 제품 점수 검사
    for idx, t in enumerate(product_turns[:3]):
        v = check_hard_assertions(t, turn=idx + 1, is_draft=True)
        c = detect_critical_failures(t, turn=idx + 1, is_final_turn=(idx == 2))
        prod_violations.extend(v)
        prod_crits.extend(c)

    # 루브릭 (제품 기준)
    combined_text = " ".join(product_turns)
    rubric = score_rubric(
        combined_text,
        turn=2,
        end_type=inp["end_type"],
        crits=prod_crits,
        violations=prod_violations,
    )

    # 톤 식별 (1턴 또는 전체 텍스트 기준)
    pred_tone = predict_tone(combined_text)
    tone_matched = pred_tone == inp["end_type"]

    return {
        "caseId": record["metadata"]["caseId"],
        "task": "chat_end_draft",
        "split": record["metadata"]["split"],
        "endType": inp["end_type"],
        "latency": latency,
        "source": source,
        "raw_turns": raw_turns,
        "product_turns": product_turns,
        "model_crits": model_crits,
        "prod_crits": prod_crits,
        "model_violations": model_violations,
        "prod_violations": prod_violations,
        "rubric": rubric,
        "tone_pred": pred_tone,
        "tone_matched": tone_matched,
        "pass_gate1": len(prod_crits) == 0,
    }


async def eval_message_case(
    agent: EndMessageAgent,
    record: dict[str, Any],
    live: bool,
) -> dict[str, Any]:
    """EndMessageAgent 케이스 평가."""
    inp = record["input"]
    end_type_str = inp["end_type"].upper()
    end_type = EndType(end_type_str)
    ai_turn = inp["ai_turn"]
    scripts = inp.get("script_drafts") or FALLBACK_SCRIPTS[end_type]
    recent = [
        RecentMessage(speaker=Speaker(m["speaker"].upper()), content=m["content"]) for m in inp["recent_messages"]
    ]

    t0 = time.perf_counter()
    raw_text = ""
    prod_text = ""
    end_reason = None
    retried = False
    closing_added = False
    latency = 0.0

    if live:
        turn = min(max(ai_turn, 1), 3)
        drafts_block = "<초안>\n" + "\n".join(f"- {normalize(e)}" for e in scripts) + "\n</초안>"
        content = f"[톤] {TONE[end_type]}\n[턴] {TURN_GUIDE[turn]}\n\n{_transcript(recent)}\n\n{drafts_block}"

        try:
            # 1. 모델 원본 (_once)
            raw_res = await agent._once(content, turn, None)
            raw_text = raw_res.ai_response

            # 2. 제품 레벨 전체 파이프라인 (재작성 + suffix 보정)
            final_res = await agent.generate(
                end_type=end_type,
                recent=recent,
                endings=scripts,
                ai_turn=turn,
            )
            prod_text = final_res.ai_response
            end_reason = final_res.end_reason
            if CLOSING_SUFFIX in prod_text:
                closing_added = True
            if prod_text != raw_text and not closing_added:
                retried = True
        except Exception:
            raw_text = ""
            prod_text = scripts[turn - 1]  # 폴백
            end_reason = "fallback"
        latency = time.perf_counter() - t0
    else:
        # Mock dry-run
        raw_text = scripts[ai_turn - 1]
        prod_text = scripts[ai_turn - 1]
        end_reason = "상대 반응에 따른 마무리"
        latency = 0.04

    # 검사 및 채점
    model_violations = check_hard_assertions(raw_text, turn=ai_turn, is_draft=False) if raw_text else ["생성 실패"]
    prod_violations = check_hard_assertions(prod_text, turn=ai_turn, is_draft=False)

    model_crits = (
        detect_critical_failures(raw_text, turn=ai_turn, is_final_turn=(ai_turn == 3))
        if raw_text
        else ["E7-CRIT-02(실패)"]
    )
    prod_crits = detect_critical_failures(prod_text, turn=ai_turn, is_final_turn=(ai_turn == 3))

    rubric = score_rubric(
        prod_text,
        turn=ai_turn,
        end_type=inp["end_type"],
        crits=prod_crits,
        violations=prod_violations,
    )
    pred_tone = predict_tone(prod_text)
    tone_matched = pred_tone == inp["end_type"]

    return {
        "caseId": record["metadata"]["caseId"],
        "task": "chat_end_message",
        "split": record["metadata"]["split"],
        "endType": inp["end_type"],
        "turn": ai_turn,
        "latency": latency,
        "raw_text": raw_text,
        "product_text": prod_text,
        "end_reason": end_reason,
        "retried": retried,
        "closing_added": closing_added,
        "model_crits": model_crits,
        "prod_crits": prod_crits,
        "model_violations": model_violations,
        "prod_violations": prod_violations,
        "rubric": rubric,
        "tone_pred": pred_tone,
        "tone_matched": tone_matched,
        "pass_gate1": len(prod_crits) == 0,
    }


async def main_async():
    parser = argparse.ArgumentParser(description="F7 chat_end 성능평가 러너")
    parser.add_argument("--live", action="store_true", help="실제 LLM 호출 (지정하지 않으면 mock/dry-run)")
    parser.add_argument("--split", choices=["calibration", "regression", "blind_holdout", "all"], default="all")
    parser.add_argument("--kind", choices=["chat_end_draft", "chat_end_message", "all"], default="all")
    parser.add_argument("--limit", type=int, default=0, help="최대 평가 케이스 수")
    parser.add_argument("--case-id", type=str, default="", help="특정 caseId 단건 평가")
    parser.add_argument("--out-json", type=Path, default=ROOT / "evals" / "runs" / "f7_chat_end_eval_result.json")
    args = parser.parse_args()

    if not DATASET_FILE.exists():
        print(f"❌ 데이터셋 파일 없음: {DATASET_FILE}")
        sys.exit(1)

    # 데이터 로드
    cases: list[dict[str, Any]] = []
    with open(DATASET_FILE, encoding="utf-8") as f:  # noqa: ASYNC230 - 스크립트라 블로킹 파일 접근 허용
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            m = row["metadata"]
            if args.case_id and m["caseId"] != args.case_id:
                continue
            if args.split != "all" and m["split"] != args.split:
                continue
            if args.kind != "all" and m["evaluationKind"] != args.kind:
                continue
            cases.append(row)

    if args.limit > 0:
        cases = cases[: args.limit]

    print(
        f"🚀 [F7 chat_end 성능평가] 시작 (모드: {'LIVE LLM' if args.live else 'DRY-RUN'}, 표본: {len(cases)}건, split: {args.split})"
    )

    draft_agent = DraftAgent()
    msg_agent = EndMessageAgent()

    results: list[dict[str, Any]] = []
    t_start = time.perf_counter()

    for idx, c in enumerate(cases):
        kind = c["metadata"]["evaluationKind"]
        if kind == "chat_end_draft":
            res = await eval_draft_case(draft_agent, c, args.live)
        else:
            res = await eval_message_case(msg_agent, c, args.live)
        results.append(res)
        if (idx + 1) % 10 == 0 or idx == len(cases) - 1:
            print(f"  진행률: [{idx + 1}/{len(cases)}] ({(idx + 1) / len(cases) * 100:.1f}%)")

    total_time = time.perf_counter() - t_start

    # 결과 분석 및 종합 집계
    total_count = len(results)
    draft_count = sum(1 for r in results if r["task"] == "chat_end_draft")
    msg_count = sum(1 for r in results if r["task"] == "chat_end_message")

    # 게이트 1: 치명 결함
    total_prod_crits = sum(len(r["prod_crits"]) for r in results)
    total_model_crits = sum(len(r["model_crits"]) for r in results)

    # 게이트 2: 지연시간 및 기술 에러
    latencies = [r["latency"] for r in results]
    latencies.sort()
    p50_lat = latencies[int(len(latencies) * 0.50)] if latencies else 0.0
    p95_lat = latencies[int(len(latencies) * 0.95)] if latencies else 0.0

    # 게이트 3: 루브릭 및 톤 식별
    avg_a = sum(r["rubric"]["A_closure"] for r in results) / total_count if total_count else 0.0
    avg_b = sum(r["rubric"]["B_politeness"] for r in results) / total_count if total_count else 0.0
    avg_c = sum(r["rubric"]["C_relevance"] for r in results) / total_count if total_count else 0.0
    avg_d = sum(r["rubric"]["D_tone"] for r in results) / total_count if total_count else 0.0
    overall_rubric = (avg_a + avg_b + avg_c + avg_d) / 4.0

    tone_correct = sum(1 for r in results if r["tone_matched"])
    tone_accuracy = (tone_correct / total_count * 100.0) if total_count else 0.0

    gate1_pass = total_prod_crits == 0
    gate2_pass = p95_lat <= 10.0
    gate3_pass = overall_rubric >= 3.5 and tone_accuracy >= 80.0

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "live": args.live,
        "total_cases": total_count,
        "draft_cases": draft_count,
        "message_cases": msg_count,
        "total_time_seconds": round(total_time, 2),
        "latency_p50_s": round(p50_lat, 3),
        "latency_p95_s": round(p95_lat, 3),
        "gate1_product_crits": total_prod_crits,
        "gate1_model_crits": total_model_crits,
        "gate1_pass": gate1_pass,
        "gate2_sla_pass": gate2_pass,
        "gate3_rubric_A_closure": round(avg_a, 2),
        "gate3_rubric_B_politeness": round(avg_b, 2),
        "gate3_rubric_C_relevance": round(avg_c, 2),
        "gate3_rubric_D_tone": round(avg_d, 2),
        "gate3_overall_rubric": round(overall_rubric, 2),
        "gate3_tone_accuracy_pct": round(tone_accuracy, 1),
        "gate3_pass": gate3_pass,
        "final_verdict": "APPROVED" if (gate1_pass and gate2_pass and gate3_pass) else "REJECTED",
    }

    # 결과 파일 저장
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:  # noqa: ASYNC230 - 스크립트라 블로킹 파일 접근 허용
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("📊 [F7 chat_end 성능평가 종합 결과 요약]")
    print("=" * 60)
    print(f"• 총 평가 표본     : {total_count}건 (Draft {draft_count}건, Message {msg_count}건)")
    print(f"• 총 소요 시간     : {total_time:.2f}초 (p50: {p50_lat:.2f}s, p95: {p95_lat:.2f}s)")
    print(
        f"• Gate 1 (치명 결함): 제품 점수 {total_prod_crits}건 (모델 원본 {total_model_crits}건) -> {'PASS' if gate1_pass else 'FAIL'}"
    )
    print(f"• Gate 2 (SLA ≤10s): p95 {p95_lat:.2f}s -> {'PASS' if gate2_pass else 'FAIL'}")
    print(
        f"• Gate 3 (정성 품질): 평균 루브릭 {overall_rubric:.2f} / 5.0 (A:{avg_a:.2f}, B:{avg_b:.2f}, C:{avg_c:.2f}, D:{avg_d:.2f})"
    )
    print(
        f"• 톤 블라인드 식별 : {tone_accuracy:.1f}% ({tone_correct}/{total_count}) -> {'PASS' if tone_accuracy >= 80.0 else 'FAIL'}"
    )
    print(f"• 최종 배포 판정   : 🏆 {summary['final_verdict']}")
    print(f"• 상세 리포트 저장 : {args.out_json}")
    print("=" * 60 + "\n")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
