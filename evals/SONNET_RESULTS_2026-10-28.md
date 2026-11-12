# Sonnet 기능별 평가 결과 (2026-10-28)

**기존 기능별 Sonnet 평가는 완료됐다.** 아래에서 각 기능의 입력 실행 결과, Sonnet 판정 이유, 보정 지표와 확정 라벨을 확인할 수 있다.

- 기존 평가 대상: 16개 기능, calibration 출력 **191건**, 실패 유형별 Judge 판정 **477건**.
- 실제 저장된 Judge 모델: `claude-sonnet-5-5` (실행 별칭 `sonnet`). f02·f07·f14는 규칙 검사만 사용했다.
- 기존 앱 실행 버전: `cb53097-dirty`. 파일명은 최초 실행 기준이며 일부 판정은 이후 재채점으로 갱신됐다. 실행 당시 전체 상태가 커밋만으로 복원되는 것은 아니다.
- Judge `ERROR`: **0건**. 앱 실행 오류: **f12의 5건**. 두 종류의 오류는 별개다.
- 현재 남은 작업: 미달 Judge 5개 보정·재판정, 표본 부족 9개 유형 검증, 회귀·최종 시험 기준선 측정. 기존 평가 결과와 구분해 진행한다.

| 기능 | 기존 결과 JSON | Judge 판정·비고 | 보정 지표 | 정답 라벨 |
| --- | --- | --- | --- | --- |
| f01 · 온보딩 질문 생성 | [출력 12건](f01-persona-conversation/results/20261026-2248_cb53097-dirty_calibration.json) | 72건 | [5-3 지표](f01-persona-conversation/EVAL.md) | [확정 라벨](f01-persona-conversation/labels/calibration_final.csv) |
| f02 · 온보딩 답변 태깅 | [출력 12건](f02-persona-tagging/results/20261026-2248_cb53097-dirty_calibration.json) | 규칙 검사만 | [5-3 지표](f02-persona-tagging/EVAL.md) | [확정 라벨](f02-persona-tagging/labels/calibration_final.csv) |
| f03 · 온보딩 페르소나 추출 | [출력 12건](f03-persona-extraction/results/20261026-2248_cb53097-dirty_calibration.json) | 60건 | [5-3 지표](f03-persona-extraction/EVAL.md) | [확정 라벨](f03-persona-extraction/labels/calibration_final.csv) |
| f04 · 카톡 말투 추출 v1 | [출력 12건](f04-persona-extraction-style/results/20261026-2248_cb53097-dirty_calibration.json) | 12건 | [5-3 지표](f04-persona-extraction-style/EVAL.md) | [확정 라벨](f04-persona-extraction-style/labels/calibration_final.csv) |
| f05 · 카톡 말투·점수 추출 v2 | [출력 12건](f05-v2-persona-extraction/results/20261026-2248_cb53097-dirty_calibration.json) | 12건 | [5-3 지표](f05-v2-persona-extraction/EVAL.md) | [확정 라벨](f05-v2-persona-extraction/labels/calibration_final.csv) |
| f06 · 연습대화 답변 | [출력 12건](f06-practice-reply/results/20261026-2248_cb53097-dirty_calibration.json) | 48건 | [5-3 지표](f06-practice-reply/EVAL.md) | [확정 라벨](f06-practice-reply/labels/calibration_final.csv) |
| f07 · 말 놓기 허락 판정 | [출력 12건](f07-v2-practice-speech-permission/results/20261026-2248_cb53097-dirty_calibration.json) | 규칙 검사만 · 현재 코드에서 삭제 | [5-3 지표](f07-v2-practice-speech-permission/EVAL.md) | [확정 라벨](f07-v2-practice-speech-permission/labels/calibration_final.csv) |
| f08 · 메모리 사실 추출 | [출력 12건](f08-v2-memory-extract/results/20261026-2248_cb53097-dirty_calibration.json) | 36건 | [5-3 지표](f08-v2-memory-extract/EVAL.md) | [확정 라벨](f08-v2-memory-extract/labels/calibration_final.csv) |
| f09 · 메모리 충돌 판정 | [출력 12건](f09-v2-memory-reconcile/results/20261026-2248_cb53097-dirty_calibration.json) | 36건 | [5-3 지표](f09-v2-memory-reconcile/EVAL.md) | [확정 라벨](f09-v2-memory-reconcile/labels/calibration_final.csv) |
| f12 · 시뮬레이션 v2 대본 생성 | [출력 12건](f12-simulation-v2-generate/results/20261026-2351_cb53097-dirty_calibration.json) | 60건 · 앱 오류 5건 | [5-3 지표](f12-simulation-v2-generate/EVAL.md) | [확정 라벨](f12-simulation-v2-generate/labels/calibration_final.csv) |
| f13 · 시뮬레이션 v2 대본 재생성 | [출력 12건](f13-simulation-v2-regenerate/results/20261026-2351_cb53097-dirty_calibration.json) | 12건 | [5-3 지표](f13-simulation-v2-regenerate/EVAL.md) | [확정 라벨](f13-simulation-v2-regenerate/labels/calibration_final.csv) |
| f14 · 시뮬레이션 v2 대본 심사 | [출력 12건](f14-simulation-v2-judge/results/20261026-2351_cb53097-dirty_calibration.json) | 규칙 검사만 | [5-3 지표](f14-simulation-v2-judge/EVAL.md) | [확정 라벨](f14-simulation-v2-judge/labels/calibration_final.csv) |
| f15 · 시뮬레이션 v2 결함 줄 수정 | [출력 12건](f15-simulation-v2-repair/results/20261026-2351_cb53097-dirty_calibration.json) | 12건 | [5-3 지표](f15-simulation-v2-repair/EVAL.md) | [확정 라벨](f15-simulation-v2-repair/labels/calibration_final.csv) |
| f16 · 시뮬레이션 v2 리포트 서술 | [출력 12건](f16-simulation-v2-report/results/20261026-2351_cb53097-dirty_calibration.json) | 24건 | [5-3 지표](f16-simulation-v2-report/EVAL.md) | [확정 라벨](f16-simulation-v2-report/labels/calibration_final.csv) |
| f17 · 마무리 답변 초안 생성 | [출력 11건](f17-chat-end-drafts/results/20261026-2351_cb53097-dirty_calibration.json) | 33건 | [5-3 지표](f17-chat-end-drafts/EVAL.md) | [확정 라벨](f17-chat-end-drafts/labels/calibration_final.csv) |
| f18 · 종료 메시지 생성 | [출력 12건](f18-chat-end-message/results/20261026-2351_cb53097-dirty_calibration.json) | 60건 | [5-3 지표](f18-chat-end-message/EVAL.md) | [확정 라벨](f18-chat-end-message/labels/calibration_final.csv) |

## 파일에서 읽는 위치

- 결과 JSON의 `rows[].sample`: 앱 출력, 원문·최종 결과 및 실행 오류. 기능에 따라 두 출력의 분리 수준이 다르다.
- `rows[].judge["F-xx"].reasoning` / `verdict` / `model`: Sonnet의 판정 이유, PASS·FAIL, 실제 모델.
- `rows[].code`: 규칙 검사 결과. `rows[].verdict`는 코드·Judge를 합친 유형별 판정이다.
- 각 `EVAL.md`의 **5-3. Judge 보정 결과**: 확정 라벨 대비 TPR·TNR. 표의 대시와 통과 표시는 [기존 집계의 한계](EVAL_PROCESS_2026-10-28.md)를 함께 읽어야 한다.
- `labels/calibration_final.csv`: 기존 최종 합의 라벨. Sonnet 판정과 비교할 정답이다.

`results/`와 Judge 캐시는 Git에서 제외해 로컬에 보관한다. 위 결과 파일은 현재 이 작업 폴더에 존재한다. 미사용 f10·f11은 이 인덱스의 합계에서 제외했으며 과거 파일은 보존했다.

## 현재 후속 실행의 상태

현재 HEAD는 `b5e158c`다. f07과 허락 상태가 삭제돼 현재 평가 계약을 선택하기 전까지 격리했다. 기존 f07의 규칙 검사 기록은 위에 남겼다.

Sonnet 신규 진단과 Opus 워커는 주간 사용 한도 오류를 반환했다. CLI의 재개 시점은 **2026-10-30 19:00 (Asia/Seoul)**이다. 평가 DB는 Postgres.app 인증 오류로 연결되지 않았다. 새로운 Sonnet 판정이나 기준선 점수는 이 인덱스에 추가하지 않았다.

[전체 진행 기록](EVAL_PROCESS_2026-10-28.md) · [기능별 현황](FEATURE_EVAL_STATUS.md) · [후속 실행 도구와 제한](BASELINE_EXECUTION_2026-10-28.md) · [Grok 독립 검토](review/grok_remaining_work_2026-10-28.md)
