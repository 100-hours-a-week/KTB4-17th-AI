# v2 기능별 LLM 평가 진행 현황

기준: `docs/성능평가_v2/00_v2_평가체계_및_기능별_평가기준서.md`

진행 방식: 18개 기능을 단계별로 일괄 진행 (모든 기능의 N단계를 마친 뒤 N+1단계)

**2026-10-29 평가 완료 — 최종 보고서: [FINAL_REPORT_2026-10-29.md](FINAL_REPORT_2026-10-29.md)** (아래 10-28 문단의 "대기 중"은 이후 해소됨)

2026-10-28 후속 상태: 기존 16개 기능의 Sonnet 보정 평가는 완료(출력 191건·판정 477건)했다. 아래 상태는 후속 보정·검증의 진행 상황이며, 새 기준선 결과를 뜻하지 않는다. [Sonnet 결과 인덱스](SONNET_RESULTS_2026-10-28.md), [후속 실행 기록](EVAL_PROCESS_2026-10-28.md)을 함께 확인한다. 새 재판정은 Claude 주간 한도, 기준선 앱 실행은 평가 DB 인증 오류로 대기 중이다.

| # | 기능 | Langfuse 이름 | 평가 문서 | 상태 |
| --- | --- | --- | --- | --- |
| 1 | 페르소나 온보딩 — 다음 질문 생성 | `persona-conversation` | [`evals/f01-persona-conversation/EVAL.md`](f01-persona-conversation/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 3 · 표본 부족 2 (가벼운 유형 4개 삭제 2026-10-29) · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 5/20 · 최종 시험 0/8 · 평가 완료 |
| 2 | 페르소나 온보딩 — 답변 태깅 | `persona-tagging` | [`evals/f02-persona-tagging/EVAL.md`](f02-persona-tagging/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 1 · 일부 문제 미채점 1 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 0/20 · 최종 시험 0/8 · 평가 완료 |
| 3 | 페르소나 온보딩 — 페르소나 추출 | `persona-extraction` | [`evals/f03-persona-extraction/EVAL.md`](f03-persona-extraction/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 4 · 표본 부족 3 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 20/20 · 최종 시험 7/8 · 평가 완료 |
| 4 | 카톡 대화 — 말투 추출 v1 | `persona-extraction-style` | [`evals/f04-persona-extraction-style/EVAL.md`](f04-persona-extraction-style/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 2 · 표본 부족 3 · 일부 문제 미채점 1 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 12/20 · 최종 시험 5/8 · 평가 완료 |
| 5 | 카톡 대화 — 말투·점수 추출 v2 | `v2-persona-extraction` | [`evals/f05-v2-persona-extraction/EVAL.md`](f05-v2-persona-extraction/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 2 · 표본 부족 3 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 4/20 · 최종 시험 3/8 · 평가 완료 |
| 6 | AI 연습 대화 — 연습대화 답변 (v1·v2) | `practice-reply` | [`evals/f06-practice-reply/EVAL.md`](f06-practice-reply/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 2 · 표본 부족 4 (가벼운 유형 1개 삭제 2026-10-29) · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 0/20 · 최종 시험 3/8 · 평가 완료 |
| 7 | AI 연습 대화 — 말 놓기 허락 판정 v2 | `v2-practice-speech-permission` | [`evals/f07-v2-practice-speech-permission/EVAL.md`](f07-v2-practice-speech-permission/EVAL.md) | 현재 dev에서 기능 삭제 · 5단계 기록만 보존 |
| 8 | v2 메모리 — 사실 추출 | `v2-memory-extract` | [`evals/f08-v2-memory-extract/EVAL.md`](f08-v2-memory-extract/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 5 · 표본 부족 1 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 2/20 · 최종 시험 3/8 · 평가 완료 |
| 9 | v2 메모리 — 충돌 판정 | `v2-memory-reconcile` | [`evals/f09-v2-memory-reconcile/EVAL.md`](f09-v2-memory-reconcile/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 1 · 표본 부족 3 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 4/20 · 최종 시험 0/8 · 평가 완료 |
| 10 | AI 시뮬레이션 v1 — 화자 한 줄 생성 | `_speak (전용 Langfuse 이름 없음)` | [`evals/f10-simulation-v1-speak/EVAL.md`](f10-simulation-v1-speak/EVAL.md) | ⛔ 평가 제외 (2026-10-28 사용자 결정: 시뮬레이션 v1 미사용) |
| 11 | AI 시뮬레이션 v1 — 리포트 서술 | `simulation-migration-report` | [`evals/f11-simulation-migration-report/EVAL.md`](f11-simulation-migration-report/EVAL.md) | ⛔ 평가 제외 (2026-10-28 사용자 결정: 시뮬레이션 v1 미사용) |
| 12 | AI 시뮬레이션 v2 — 대본 생성 | `simulation-v2-generate-text` | [`evals/f12-simulation-v2-generate/EVAL.md`](f12-simulation-v2-generate/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 0 · 표본 부족 7 · 앱 실행 오류로 미검증 3 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 7/20 · 최종 시험 3/8 · 평가 완료 |
| 13 | AI 시뮬레이션 v2 — 대본 재생성 | `simulation-v2-regenerate-text` | [`evals/f13-simulation-v2-regenerate/EVAL.md`](f13-simulation-v2-regenerate/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 2 · 표본 부족 2 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 5/20 · 최종 시험 1/8 · 평가 완료 |
| 14 | AI 시뮬레이션 v2 — 대본 심사 | `simulation-v2-judge` | [`evals/f14-simulation-v2-judge/EVAL.md`](f14-simulation-v2-judge/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 1 · 표본 부족 4 · 일부 문제 미채점 1 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 1/20 · 최종 시험 0/8 · 평가 완료 |
| 15 | AI 시뮬레이션 v2 — 결함 줄 수정 | `simulation-v2-repair` | [`evals/f15-simulation-v2-repair/EVAL.md`](f15-simulation-v2-repair/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 2 · 표본 부족 1 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 5/20 · 최종 시험 0/8 · 평가 완료 |
| 16 | AI 시뮬레이션 v2 — 리포트 서술 | `simulation-v2-report` | [`evals/f16-simulation-v2-report/EVAL.md`](f16-simulation-v2-report/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 3 · 표본 부족 1 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 11/20 · 최종 시험 2/8 · 평가 완료 |
| 17 | 채팅 대화 위임 — 마무리 답변 3개 생성 | `chat-end-drafts` | [`evals/f17-chat-end-drafts/EVAL.md`](f17-chat-end-drafts/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 2 · 표본 부족 2 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 3/21 · 최종 시험 1/8 · 평가 완료 |
| 18 | 채팅 대화 위임 — 이번 종료 메시지 생성 | `chat-end-message` | [`evals/f18-chat-end-message/EVAL.md`](f18-chat-end-message/EVAL.md) | 5단계 완료 (2026-10-28) · 채점기 통과 3 · 표본 부족 4 · 6단계 완료 (2026-10-29) · 치명 실패 문제 회귀 3/20 · 최종 시험 2/8 · 평가 완료 |
