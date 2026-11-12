# 모델별 성능평가 비교

> 이 문서는 `evals/_harness/stage6_compare.py` 가 결과 파일에서 자동으로 만든다. 직접 고치지 말고 스크립트를 다시 돌린다.

## 새 평가를 추가하는 방법

1. 실행 태그를 붙여 회귀용을 돌린다. `.env` 는 그대로 두고 실행할 때만 모델을 덮어쓴다.
   ```bash
   LLM_MODEL_V2=<v2 모델> python3 evals/_harness/stage6_cli.py run-regression --only f05,f06 --version b5e158c-<이름>
   ```
2. `evals/model_runs.json` 에 그 태그의 이름·모델·SLM 상태·메모를 추가한다.
3. `python3 evals/_harness/stage6_compare.py` 를 돌리면 이 문서가 갱신된다.

읽는 법:
- 같은 회귀용 문제(기능당 20문제, 마무리 답변 생성은 21문제)로 모델만 바꿔 비교한다.
- '규칙 검사 통과율'은 Claude 채점 없이 프로그램이 규칙(물음표 개수, JSON 형식, 허용 카테고리 등)으로 기계적으로 검사한 항목의 통과율이다. 내용 품질은 '치명 실패'와 유형별 실패율로 본다.
- '치명 실패'는 치명(CRIT) 유형이 하나라도 FAIL인 문제의 비율이다. 분모는 전체 문제 수이고, 실행 오류 건수는 따로 적는다.
- '게이트'는 기준선보다 나빠졌는지다. 기준선은 `b5e158c` 실행(v1 `google/gemini-2.5-flash-lite`, v2 `google/gemini-3.1-flash-lite`)이다. 차단 조건은 네 가지다.
  - 실행 오류 증가
  - 규칙 검사 통과율 하락
  - 검증된 치명 유형의 FAIL 증가
  - 검증된 주요 유형의 실패율 10%p 이상 상승
- 채점기가 '표본 부족'인 유형의 수치는 참고치다.

## 1. 평가한 실행

| 실행 | 태그 | 날짜 | v1 모델 | v2 모델 | SLM | 범위 | 메모 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | `b5e158c` | 2026-10-28 | `google/gemini-2.5-flash-lite` | `google/gemini-3.1-flash-lite` | kanana 설정 (회귀용 실행 때 실제 응답 여부는 기록 없음, 최종 시험 때는 꺼져 메인 LLM 사용) | 15개 기능 · 301문제 · Judge 채점 | 배포 게이트 기준선. Judge 기준을 고친 유형은 2026-10-29 재채점 결과를 쓴다. |
| v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | `b5e158c-v2flash25lite` | 2026-10-29 | `google/gemini-2.5-flash-lite` | `google/gemini-2.5-flash-lite` | 해당 없음 (v2 기능은 SLM 을 쓰지 않음) | 9개 기능 · 180문제 · Judge 채점 | v2 기능 9개(연습대화 답변 포함)만 실행. LLM_MODEL_V2 를 실행 때만 덮어씀. |
| Qwen3.8 27B 점검 (생각 과정 켜짐, 중단) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 | `b5e158c-qwen-smoke` | 2026-10-29 | `bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M` | `bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M` | 끔 | 2개 기능 · 4문제 · Judge 미채점 포함 | 생각 과정(reasoning)이 켜진 채 실행돼 답이 비는 오류가 나서 중단. 비교에 쓰지 않는다. |
| Qwen3.8 27B 호환성 점검 [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 | `b5e158c-qwen-nothink-smoke` | 2026-10-29 | `bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M` | `bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M` | 끔 | 15개 기능 · 30문제 · Judge 미채점 포함 | 기능당 2문제, Judge 채점 없음. llama-server --reasoning-budget 0 (생각 과정 끔). RunPod A40, Q4_K_M. |
| Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 | `b5e158c-qwen38-27b` | 2026-10-29 | `bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M` | `bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M` | 끔 (태깅·추출도 Qwen) | 15개 기능 · 301문제 · Judge 채점 | v1·v2 모두 Qwen. llama-server --reasoning-budget 0, RunPod A40 1장, Q4_K_M 양자화, 컨텍스트 196608. ⚠️ 온보딩 페르소나 추출은 프롬프트도 바뀐 상태로 실행(2026-10-29 14:40 app/features/persona/agents.py 수정: 텍스트형 항목 추출 지시 제거, 미커밋). 이 기능은 모델만 바꾼 비교가 아니다. 리포트 서술은 20건 모두 30초 시간 초과로 기본 문구가 나가 채점 무효. 상세: QWEN38_27B_EVAL_2026-10-29.md |
| 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 최종 시험 | `b5e158c` | 2026-10-28 | `google/gemini-2.5-flash-lite` | `google/gemini-3.1-flash-lite` | kanana 설정 (회귀용 실행 때 실제 응답 여부는 기록 없음, 최종 시험 때는 꺼져 메인 LLM 사용) | 15개 기능 · 120문제 · Judge 채점 | 배포 게이트 기준선. Judge 기준을 고친 유형은 2026-10-29 재채점 결과를 쓴다. |

## 2. 한눈에 비교 (회귀용 · 치명 실패 문제 비율 · 게이트)

| 기능 | 모델 구분 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- |
| 온보딩 질문 생성 | v1 | 5/20 (25%) — 기준선 | - | 4/20 (20%) — ✅ 통과 |
| 온보딩 답변 태깅 | v1 | 0/20 (0%) — 기준선 | - | 0/20 (0%) — ⛔ 규칙 검사 통과율 89% → 86% |
| 온보딩 페르소나 추출 | v1 | 20/20 (100%) — 기준선 | - | 14/20 (70%) — ✅ 통과 |
| 카톡 말투 추출 v1 | v1 | 12/20 (60%), 오류 1 — 기준선 | - | 10/20 (50%), 오류 1 — ✅ 통과 |
| 카톡 말투·점수 추출 v2 | v2 | 4/20 (20%) — 기준선 | 6/20 (30%) — ⛔ 규칙 검사 통과율 92% → 88%; F-02 치명 FAIL 4 → 6 | 8/20 (40%) — ⛔ 규칙 검사 통과율 92% → 87%; F-02 치명 FAIL 4 → 8 |
| 연습대화 답변 | v1·v2 혼합 | 0/20 (0%) — 기준선 | 2/20 (10%) — ⛔ 규칙 검사 통과율 89% → 83%; F-02 실패율 15% → 25% | 1/20 (5%) — ✅ 통과 |
| 메모리 사실 추출 | v2 | 2/20 (10%) — 기준선 | 4/20 (20%) — ⛔ 규칙 검사 통과율 88% → 75%; F-01 치명 FAIL 1 → 4; F-06 실패율 0% → 25% | 0/20 (0%) — ✅ 통과 |
| 메모리 충돌 판정 | v2 | 4/20 (20%) — 기준선 | 2/20 (10%) — ⛔ F-03 실패율 0% → 10% | 5/20 (25%) — ✅ 통과 |
| 시뮬레이션 v2 대본 생성 | v2 | 7/20 (35%), 오류 7 — 기준선 | 13/20 (65%), 오류 2 — ⛔ 규칙 검사 통과율 95% → 93% | 10/20 (50%), 오류 5 — ✅ 통과 |
| 시뮬레이션 v2 대본 재생성 | v2 | 5/20 (25%) — 기준선 | 3/20 (15%) — ✅ 통과 | 4/20 (20%) — ⛔ F-04 실패율 60% → 85% |
| 시뮬레이션 v2 대본 심사 | v2 | 1/20 (5%) — 기준선 | 10/20 (50%) — ⛔ 규칙 검사 통과율 89% → 41%; F-02 치명 FAIL 1 → 10 | 2/20 (10%), 오류 1 — ⛔ 앱 실행 오류 0 → 1; F-02 치명 FAIL 1 → 2 |
| 시뮬레이션 v2 결함 줄 수정 | v2 | 5/20 (25%) — 기준선 | 6/20 (30%), 오류 7 — ⛔ 앱 실행 오류 0 → 7; 규칙 검사 통과율 100% → 71%; F-03 치명 FAIL 0 → 4 | 9/20 (45%) — ⛔ 규칙 검사 통과율 100% → 98%; F-02 치명 FAIL 5 → 8; F-03 치명 FAIL 0 → 1 |
| 시뮬레이션 v2 리포트 서술 | v2 | 11/20 (55%) — 기준선 | 9/20 (45%), ⚠️ LLM 리포트 실패로 기본 문구 1/20 (그 문제는 채점 무효) — ✅ 통과 | 0/20 (0%), ⚠️ LLM 리포트 실패로 기본 문구 20/20 (전부라 채점 무효) — ⛔ 규칙 검사 통과율 48% → - |
| 마무리 답변 3개 생성 | v1 | 3/21 (14%) — 기준선 | - | 1/21 (5%) — ✅ 통과 |
| 종료 메시지 생성 | v1 | 3/20 (15%) — 기준선 | - | 2/20 (10%) — ⛔ 규칙 검사 통과율 95% → 81% |

## 3. 실행 오류 · 규칙 검사 · 지연시간 (회귀용)

'모델 구분'이 v1 인 기능은 v1 모델, v2 인 기능은 v2 모델로 실행됐다.

| 기능 | 모델 구분 | gemini-2.5-flash-lite (v1) + gemini-3.1-flash-lite (v2) | gemini-2.5-flash-lite | Qwen3.8-27B-Uncensored |
| --- | --- | --- | --- | --- |
| 온보딩 질문 생성 | v1 | 오류 0/20 · 규칙 검사 - · 12.1초 | - | 오류 0/20 · 규칙 검사 - · 38.5초 |
| 온보딩 답변 태깅 | v1 | 오류 0/20 · 규칙 검사 89% · 0.9초 | - | 오류 0/20 · 규칙 검사 86% · 2.2초 |
| 온보딩 페르소나 추출 | v1 | 오류 0/20 · 규칙 검사 38% · 4.7초 | - | 오류 0/20 · 규칙 검사 62% · 16.5초 |
| 카톡 말투 추출 v1 | v1 | 오류 1/20 · 규칙 검사 73% · 3.4초 | - | 오류 1/20 · 규칙 검사 85% · 19.6초 |
| 카톡 말투·점수 추출 v2 | v2 | 오류 0/20 · 규칙 검사 92% · 2.6초 | 오류 0/20 · 규칙 검사 88% · 4.0초 | 오류 0/20 · 규칙 검사 87% · 21.6초 |
| 연습대화 답변 | v1·v2 혼합 | 오류 0/20 · 규칙 검사 89% · 1.3초 | 오류 0/20 · 규칙 검사 83% · 1.1초 | 오류 0/20 · 규칙 검사 94% · 5.3초 |
| 메모리 사실 추출 | v2 | 오류 0/20 · 규칙 검사 88% · 1.5초 | 오류 0/20 · 규칙 검사 75% · 1.5초 | 오류 0/20 · 규칙 검사 95% · 9.2초 |
| 메모리 충돌 판정 | v2 | 오류 0/20 · 규칙 검사 - · 2.6초 | 오류 0/20 · 규칙 검사 - · 2.8초 | 오류 0/20 · 규칙 검사 - · 17.7초 |
| 시뮬레이션 v2 대본 생성 | v2 | 오류 7/20 · 규칙 검사 95% · 8.6초 | 오류 2/20 · 규칙 검사 93% · 10.1초 | 오류 5/20 · 규칙 검사 97% · 78.5초 |
| 시뮬레이션 v2 대본 재생성 | v2 | 오류 0/20 · 규칙 검사 92% · 1.9초 | 오류 0/20 · 규칙 검사 95% · 1.9초 | 오류 0/20 · 규칙 검사 93% · 10.2초 |
| 시뮬레이션 v2 대본 심사 | v2 | 오류 0/20 · 규칙 검사 89% · 1.2초 | 오류 0/20 · 규칙 검사 41% · 1.3초 | 오류 1/20 · 규칙 검사 89% · 6.3초 |
| 시뮬레이션 v2 결함 줄 수정 | v2 | 오류 0/20 · 규칙 검사 100% · 1.3초 | 오류 7/20 · 규칙 검사 71% · 2.0초 | 오류 0/20 · 규칙 검사 98% · 7.2초 |
| 시뮬레이션 v2 리포트 서술 | v2 | 오류 0/20 · 규칙 검사 48% · 5.5초 | 오류 0/20 · 규칙 검사 61% · 5.0초 · 기본 문구 1/20 | 오류 0/20 · 규칙 검사 - · 60.0초 · 기본 문구 20/20 |
| 마무리 답변 3개 생성 | v1 | 오류 0/21 · 규칙 검사 100% · 1.2초 | - | 오류 0/21 · 규칙 검사 100% · 4.8초 |
| 종료 메시지 생성 | v1 | 오류 0/20 · 규칙 검사 95% · 1.9초 | - | 오류 0/20 · 규칙 검사 81% · 6.8초 |

## 4. 기능별 실패 유형 비교 (회귀용 실패율)

### 온보딩 질문 생성

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- |
| F-01 haru_intro_as_user | MAJ | 검증됨 | 5% (1/20) | 0% (0/20) |
| F-03 answer_misread | MAJ | 검증됨 | 50% (10/20) | 20% (4/20) |
| F-04 repeated_question | MAJ | 표본 부족 | 40% (8/20) | 10% (2/20) |
| F-08 identity_not_disclosed | CRIT | 표본 부족 | 5% (1/20) | 0% (0/20) |
| F-09 injection_or_prompt_leak | CRIT | 검증됨 | 20% (4/20) | 20% (4/20) |

### 온보딩 답변 태깅

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- |
| F-01 wrong_dimension | MAJ | 일부 미채점 | 7% (1/15) | 7% (1/15) |
| F-02 off_topic_miss | MAJ | 검증됨 | 15% (3/20) | 20% (4/20) |

### 온보딩 페르소나 추출

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- |
| F-01 haru_fact_as_user | CRIT | 표본 부족 | 5% (1/20) | 0% (0/20) |
| F-02 unfounded_narrative | CRIT | 검증됨 | 100% (20/20) | 70% (14/20) |
| F-03 answer_misattribution | MAJ | 검증됨 | 35% (7/20) | 15% (3/20) |
| F-04 joke_as_fact | MAJ | 표본 부족 | 5% (1/20) | 0% (0/20) |
| F-05 unanswered_dimension_score | MAJ | 검증됨 | 45% (9/20) | 5% (1/20) |
| F-06 schema_rule_violation | MIN | 검증됨 | 80% (16/20) | 70% (14/20) |
| F-07 injection_followed | CRIT | 표본 부족 | 0% (0/20) | 0% (0/20) |

### 카톡 말투 추출 v1

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- |
| F-01 punctuation_fabrication | MAJ | 일부 미채점 | 90% (9/10) | 50% (1/2) |
| F-02 unobserved_expression | CRIT | 검증됨 | 32% (6/19) | 16% (3/19) |
| F-03 schema_violation | CRIT | 검증됨 | 42% (8/19) | 42% (8/19) |
| F-04 placeholder_copy | MAJ | 표본 부족 | 0% (0/19) | 0% (0/19) |
| F-05 injection_followed | CRIT | 표본 부족 | 0% (0/19) | 0% (0/19) |
| F-06 pii_exposure | CRIT | 표본 부족 | 0% (0/19) | 0% (0/19) |

### 카톡 말투·점수 추출 v2

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 partner_style_leak | CRIT | 검증됨 | 5% (1/20) | 5% (1/20) | 0% (0/20) |
| F-02 unobserved_expression | CRIT | 검증됨 | 20% (4/20) | 30% (6/20) | 40% (8/20) |
| F-03 score_without_evidence | CRIT | 표본 부족 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-04 injection_followed | CRIT | 표본 부족 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-05 pii_exposure | CRIT | 표본 부족 | - (0/0) | - (0/0) | - (0/0) |

### 연습대화 답변

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 style_phrase_as_fact | CRIT | 표본 부족 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-02 answer_misread | MAJ | 검증됨 | 15% (3/20) | 25% (5/20) | 15% (3/20) |
| F-03 casual_before_permission | MAJ | 표본 부족 | 0% (0/16) | 12% (2/16) | 6% (1/16) |
| F-04 multi_question | MAJ | 검증됨 | 20% (4/20) | 20% (4/20) | 5% (1/20) |
| F-06 injection_or_role_change | CRIT | 표본 부족 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-07 fabricated_fact | CRIT | 표본 부족 | 0% (0/20) | 10% (2/20) | 5% (1/20) |

### 메모리 사실 추출

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 ai_utterance_as_user_fact | CRIT | 검증됨 | 5% (1/20) | 20% (4/20) | 0% (0/20) |
| F-02 injection_followed | CRIT | 검증됨 | 5% (1/20) | 0% (0/20) | 0% (0/20) |
| F-03 category_out_of_set | MIN | 검증됨 | 35% (7/20) | 75% (15/20) | 15% (3/20) |
| F-04 invalid_source_id | MAJ | 표본 부족 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-05 pii_in_fact | CRIT | 검증됨 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-06 unsupported_fact | MAJ | 검증됨 | 0% (0/20) | 25% (5/20) | 0% (0/20) |

### 메모리 충돌 판정

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 contradiction_left | MAJ | 표본 부족 | 5% (1/20) | 15% (3/20) | 15% (3/20) |
| F-02 memory_loss | CRIT | 표본 부족 | 20% (4/20) | 10% (2/20) | 25% (5/20) |
| F-03 updated_fact_embellish | MAJ | 검증됨 | 0% (0/20) | 10% (2/20) | 0% (0/20) |
| F-04 deleted_on_request | CRIT | 표본 부족 | - (0/0) | - (0/0) | - (0/0) |

### 시뮬레이션 v2 대본 생성

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 label_as_name | CRIT | 표본 부족 | 0% (0/13) | 11% (2/18) | 13% (2/15) |
| F-02 self_name_call | CRIT | 표본 부족 | 0% (0/13) | 0% (0/18) | 0% (0/15) |
| F-03 premature_partner_fact | CRIT | 표본 부족 | 15% (2/13) | 17% (3/18) | 0% (0/15) |
| F-04 partner_fact_as_own | CRIT | 표본 부족 | 0% (0/13) | 28% (5/18) | 7% (1/15) |
| F-05 style_phrase_as_fact | CRIT | 실행 오류로 미검증 | 31% (4/13) | 22% (4/18) | 33% (5/15) |
| F-06 false_common_ground | MAJ | 실행 오류로 미검증 | 8% (1/13) | 39% (7/18) | 20% (3/15) |
| F-07 incoherent_line | MAJ | 표본 부족 | 31% (4/13) | 28% (5/18) | 47% (7/15) |
| F-08 turn_order_broken | MAJ | 표본 부족 | 8% (1/13) | 0% (0/18) | 0% (0/15) |
| F-09 early_farewell | MAJ | 표본 부족 | 0% (0/13) | 6% (1/18) | 0% (0/15) |
| F-10 fabricated_fact | CRIT | 실행 오류로 미검증 | 15% (2/13) | 39% (7/18) | 13% (2/15) |

### 시뮬레이션 v2 대본 재생성

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 label_as_name_persists | CRIT | 검증됨 | 20% (4/20) | 15% (3/20) | 20% (4/20) |
| F-02 self_name_call | CRIT | 표본 부족 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-03 partner_fact_as_own | CRIT | 표본 부족 | 5% (1/20) | 0% (0/20) | 0% (0/20) |
| F-04 issue_unresolved_or_incoherent | MAJ | 검증됨 | 60% (12/20) | 50% (10/20) | 85% (17/20) |

### 시뮬레이션 v2 대본 심사

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 mixup_false_positive | MAJ | 일부 미채점 | 17% (3/18) | 56% (10/18) | 18% (3/17) |
| F-02 missed_label_confusion | CRIT | 검증됨 | 33% (1/3) | 100% (10/10) | 100% (2/2) |
| F-03 unresponsive_false_positive | MAJ | 표본 부족 | 5% (1/19) | 42% (8/19) | 0% (0/18) |
| F-04 misclassified_issue | MAJ | 표본 부족 | 100% (1/1) | 0% (0/1) | 0% (0/1) |
| F-05 output_inconsistency | MAJ | 표본 부족 | 5% (1/20) | 45% (9/20) | 5% (1/19) |
| F-06 missed_fact_owner_mixup | CRIT | 표본 부족 | - (0/0) | 100% (8/8) | - (0/0) |

### 시뮬레이션 v2 결함 줄 수정

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 missing_patch | MAJ | 표본 부족 | 0% (0/20) | 23% (3/13) | 0% (0/20) |
| F-02 new_issue_introduced | CRIT | 검증됨 | 25% (5/20) | 31% (4/13) | 40% (8/20) |
| F-03 fact_id_in_text | CRIT | 검증됨 | 0% (0/20) | 36% (4/11) | 5% (1/20) |

### 시뮬레이션 v2 리포트 서술

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | v2 모델 → gemini-2.5-flash-lite [v1·v2 gemini-2.5-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- | --- |
| F-01 fabricated_statement | CRIT | 검증됨 | 50% (10/20) | 40% (8/20) | 0% (0/20) |
| F-02 same_nickname_ambiguity | MAJ | 표본 부족 | 0% (0/20) | 0% (0/20) | 0% (0/20) |
| F-03 misquoted_highlight | CRIT | 검증됨 | 10% (2/20) | 5% (1/19) | - (0/0) |
| F-04 highlight_mix_rule | MAJ | 검증됨 | 95% (19/20) | 74% (14/19) | - (0/0) |

### 마무리 답변 3개 생성

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- |
| F-01 nonexistent_conversation | CRIT | 검증됨 | 14% (3/21) | 5% (1/21) |
| F-02 false_attribution | MAJ | 표본 부족 | 10% (2/21) | 0% (0/21) |
| F-03 mood_mismatch | MAJ | 표본 부족 | 0% (0/21) | 0% (0/21) |
| F-04 fallback_replaced | MAJ | 검증됨 | 0% (0/21) | 0% (0/21) |

### 종료 메시지 생성

| 유형 | 심각도 | 채점기 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 회귀용 | Qwen3.8 27B (자체 서버) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 |
| --- | --- | --- | --- | --- |
| F-01 greeting_check_false_positive | MAJ | 표본 부족 | 100% (1/1) | 83% (5/6) |
| F-02 speaker_mixup | CRIT | 표본 부족 | 0% (0/20) | 0% (0/20) |
| F-03 partner_misread | MAJ | 검증됨 | 5% (1/20) | 0% (0/20) |
| F-04 nonexistent_conversation | CRIT | 검증됨 | 15% (3/20) | 10% (2/20) |
| F-05 false_common_ground | MAJ | 검증됨 | 10% (2/20) | 0% (0/20) |
| F-06 daily_greeting | MAJ | 표본 부족 | 0% (0/20) | 0% (0/20) |
| F-07 reunion_opening | MAJ | 표본 부족 | 0% (0/20) | 0% (0/20) |

## 5. 최종 시험 · 점검 실행

| 기능 | Qwen3.8 27B 점검 (생각 과정 켜짐, 중단) [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 | Qwen3.8 27B 호환성 점검 [v1·v2 Qwen3.8-27B-Uncensored] · 회귀용 | 기준선 (현재 운영 설정) [v1 gemini-2.5-flash-lite / v2 gemini-3.1-flash-lite] · 최종 시험 |
| --- | --- | --- | --- |
| 온보딩 질문 생성 | 미채점 · 오류 0/2 | 미채점 · 오류 0/2 | 치명 0/8 · 오류 0/8 |
| 온보딩 답변 태깅 | 치명 0/2 · 오류 2/2 | 치명 0/2 · 오류 0/2 | 치명 0/8 · 오류 0/8 |
| 온보딩 페르소나 추출 | - | 미채점 · 오류 0/2 | 치명 7/8 · 오류 0/8 |
| 카톡 말투 추출 v1 | - | 미채점 · 오류 0/2 | 치명 5/8 · 오류 2/8 |
| 카톡 말투·점수 추출 v2 | - | 미채점 · 오류 0/2 | 치명 3/8 · 오류 2/8 |
| 연습대화 답변 | - | 미채점 · 오류 0/2 | 치명 3/8 · 오류 0/8 |
| 메모리 사실 추출 | - | 미채점 · 오류 0/2 | 치명 3/8 · 오류 0/8 |
| 메모리 충돌 판정 | - | 미채점 · 오류 0/2 | 치명 0/8 · 오류 0/8 |
| 시뮬레이션 v2 대본 생성 | - | 미채점 · 오류 0/2 | 치명 3/8 · 오류 2/8 |
| 시뮬레이션 v2 대본 재생성 | - | 미채점 · 오류 0/2 | 치명 1/8 · 오류 0/8 |
| 시뮬레이션 v2 대본 심사 | - | 치명 0/2 · 오류 0/2 | 치명 0/8 · 오류 0/8 |
| 시뮬레이션 v2 결함 줄 수정 | - | 미채점 · 오류 2/2 | 치명 0/8 · 오류 0/8 |
| 시뮬레이션 v2 리포트 서술 | - | 미채점 · 오류 0/2 | 치명 2/8 · 오류 0/8 |
| 마무리 답변 3개 생성 | - | 미채점 · 오류 0/2 | 치명 1/8 · 오류 0/8 |
| 종료 메시지 생성 | - | 미채점 · 오류 0/2 | 치명 2/8 · 오류 0/8 |

최종 시험은 기능당 8문제라 1문제가 12.5%p다. 점검 실행은 호환성 확인용이라 성능 비교에 쓰지 않는다.
