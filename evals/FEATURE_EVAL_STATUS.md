# v2 기능별 LLM 평가 진행 현황

기준: `docs/성능평가_v2/00_v2_평가체계_및_기능별_평가기준서.md`

진행 방식: 18개 기능을 단계별로 일괄 진행 (모든 기능의 N단계를 마친 뒤 N+1단계)

| # | 기능 | Langfuse 이름 | 평가 문서 | 상태 |
| --- | --- | --- | --- | --- |
| 1 | 페르소나 온보딩 — 다음 질문 생성 | `persona-conversation` | [`evals/f01-persona-conversation/EVAL.md`](f01-persona-conversation/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 2 | 페르소나 온보딩 — 답변 태깅 | `persona-tagging` | [`evals/f02-persona-tagging/EVAL.md`](f02-persona-tagging/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 3 | 페르소나 온보딩 — 페르소나 추출 | `persona-extraction` | [`evals/f03-persona-extraction/EVAL.md`](f03-persona-extraction/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 4 | 카톡 대화 — 말투 추출 v1 | `persona-extraction-style` | [`evals/f04-persona-extraction-style/EVAL.md`](f04-persona-extraction-style/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 5 | 카톡 대화 — 말투·점수 추출 v2 | `v2-persona-extraction` | [`evals/f05-v2-persona-extraction/EVAL.md`](f05-v2-persona-extraction/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 6 | AI 연습 대화 — 연습대화 답변 (v1·v2) | `practice-reply` | [`evals/f06-practice-reply/EVAL.md`](f06-practice-reply/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 7 | AI 연습 대화 — 말 놓기 허락 판정 v2 | `v2-practice-speech-permission` | [`evals/f07-v2-practice-speech-permission/EVAL.md`](f07-v2-practice-speech-permission/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 8 | v2 메모리 — 사실 추출 | `v2-memory-extract` | [`evals/f08-v2-memory-extract/EVAL.md`](f08-v2-memory-extract/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 9 | v2 메모리 — 충돌 판정 | `v2-memory-reconcile` | [`evals/f09-v2-memory-reconcile/EVAL.md`](f09-v2-memory-reconcile/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 10 | AI 시뮬레이션 v1 — 화자 한 줄 생성 | `_speak (전용 Langfuse 이름 없음)` | [`evals/f10-simulation-v1-speak/EVAL.md`](f10-simulation-v1-speak/EVAL.md) | ⛔ 평가 제외 (2026-10-28 사용자 결정: 시뮬레이션 v1 미사용) |
| 11 | AI 시뮬레이션 v1 — 리포트 서술 | `simulation-migration-report` | [`evals/f11-simulation-migration-report/EVAL.md`](f11-simulation-migration-report/EVAL.md) | ⛔ 평가 제외 (2026-10-28 사용자 결정: 시뮬레이션 v1 미사용) |
| 12 | AI 시뮬레이션 v2 — 대본 생성 | `simulation-v2-generate-text` | [`evals/f12-simulation-v2-generate/EVAL.md`](f12-simulation-v2-generate/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 13 | AI 시뮬레이션 v2 — 대본 재생성 | `simulation-v2-regenerate-text` | [`evals/f13-simulation-v2-regenerate/EVAL.md`](f13-simulation-v2-regenerate/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 14 | AI 시뮬레이션 v2 — 대본 심사 | `simulation-v2-judge` | [`evals/f14-simulation-v2-judge/EVAL.md`](f14-simulation-v2-judge/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 15 | AI 시뮬레이션 v2 — 결함 줄 수정 | `simulation-v2-repair` | [`evals/f15-simulation-v2-repair/EVAL.md`](f15-simulation-v2-repair/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 16 | AI 시뮬레이션 v2 — 리포트 서술 | `simulation-v2-report` | [`evals/f16-simulation-v2-report/EVAL.md`](f16-simulation-v2-report/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 17 | 채팅 대화 위임 — 마무리 답변 3개 생성 | `chat-end-drafts` | [`evals/f17-chat-end-drafts/EVAL.md`](f17-chat-end-drafts/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
| 18 | 채팅 대화 위임 — 이번 종료 메시지 생성 | `chat-end-message` | [`evals/f18-chat-end-message/EVAL.md`](f18-chat-end-message/EVAL.md) | 5단계 · 정답 라벨 확정, Judge 보정 대기 (evals/labels_calibration.html) |
