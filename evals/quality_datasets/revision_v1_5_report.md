# v1.5.0 수정 보고

v1.4.0 독립 재검토(`reviews/claude_opus_v1_4.md`, Opus)는 **78/100, high 1, medium 7로 실패**했다. 이 버전은 그 지적 중 이번 라운드에서 고칠 수 있는 항목을 고치고, 신규 케이스 10건을 더했다. **v1.5.0은 독립 재검토를 아직 받지 않았으므로 통과로 보지 않는다.** 사람 이중 라벨, Judge calibration, 실제 모델 실행도 없다.

## 재검토 지적과 조치

| 지적 | 조치 | 상태 |
| --- | --- | --- |
| H1 build 텍스트 항목이 서비스의 answered 필터와 모순 | 입력에 `answeredDimensions` 추가. 기대값에 `postService.textualKept/textualDropped` 추가. 텍스트 정답 라벨이 있는 31건은 해당 키를 answered에 넣음(dimension_* 30건, textual_grounding 1건). 드리프트 테스트에 텍스트 필터 검사 추가 | 반영. 050의 "answered 없는 대조 사례"는 만들지 않음 |
| M1 영역이 모두 null이면 위험 감점이 사라짐 | 위험 64/65·all_risks family(6종)에 양쪽이 아는 `openness=60`을 추가해 감점이 총점에 드러나게 함(64→100, 65→92, all_risks→76). 그래도 감점이 사라지는 경우는 `known_code_gap_risk_penalty_dropped_when_all_areas_null` 플래그로 자동 표시 | 반영. 앱 결함으로 볼지는 미결정 |
| M2 앱이 만들 수 없는 페르소나 상태 | preview 020: null 차원은 LOW, HIGH/MEDIUM 차원은 값(모자란 값은 중간 50)으로 채움. 값이 있는데 LOW인 옛 행 상태는 `legacy_row_state_value_with_low_confidence`로 자동 표시(run 3건, preview 3건, practice 6건). 드리프트 테스트에 "null은 항상 LOW" 추가 | 반영. 해당 상태를 도달 가능한 형태로 재설계하지는 않음 |
| L1 README 수치가 낡음 | 하드코딩된 총점 구간을 제거하고 각 항목의 `idealRecalculationOracle`을 가리킴. README의 버전·건수·분할 표는 상수에서 계산 | 반영 |
| L2 재시도 기록 누락 | `runtime.retryReasons`(invalid_json, truncated, speaker_mixup), `truncatedRetryMaxTokensFactor 1.5` 기록 | 반영. 해당 fault 케이스는 추가하지 않음 |

## 신규 케이스 (practice_reply-061~070, `out_of_scope_request`)

실서비스에서 관찰된 실패(코드 요청에 페르소나가 무관한 취미 이야기로 회피하거나, 반대로 긴 코드를 쏟아냄)를 잡는 10건이다. 프로필 하나당 1건이며 분할은 프로필과 같다(calibration 2, regression 6, blind_holdout 2). 기존 caseId가 밀리지 않도록 뒤에 붙였다. 7건은 단발 요청이고 3건(프로필 2·5·8번째)은 같은 요청을 세 번째 하는 사례로, 앞선 회피 문장을 반복하면 안 된다.

계약: 요청에 먼저 짧게 반응, 긴 산출물 없이 1~3문장, 프로필 사실만 사용, 질문 최대 하나, 프로필 화제로 잇기.

**주의:** 이 계약은 `SYSTEM_TEMPLATE`의 규칙("질문을 받았으면 먼저 답한다", "프로필에 없는 사실은 지어내지 않는다", "역할 유지")에서 도출한 것이지 코드가 직접 강제하는 것이 아니다. 그래서 `quality_contract_not_enforced_by_code` 플래그를 붙였다. 범위 밖 요청을 정중히 넘기는 것이 제품 의도인지 사람이 확인해야 한다.

## 변경 후 규모

330건(기존 6개 Dataset 중 practice_reply만 60 → 70), 분할 66/198/66. 기존 320건의 caseId는 그대로다. 온보딩 발화·태깅은 이번에도 바뀌지 않았다.

## 검증

- `generate --check` 통과, validator 오류 0·경고 0, audit 교차 split 충돌 0.
- `pytest tests evals/quality_datasets` 통과, ruff check·format 통과. 드리프트 테스트는 앱 코드를 정답으로 재계산한다.
- **주의:** v1.5에서 추가한 드리프트 테스트(텍스트 필터, null→LOW, 위험 플래그, 신규 케이스)는 수정 후에 작성했다. 수정 전 데이터에서 실패하는 것을 확인하지 못했다(v1.4 때의 TDD 기록과 다르다).

## 이번에도 하지 않은 것

- 신규 케이스 중 "저번에/지난번에" 금지(온보딩 발화·연습대화), 시뮬레이션 화자 분리 재시도·`truncated`·`upstream_error` fault, MBTI 말투 힌트는 추가하지 않았다. 필요한 fault 주입 구조를 시뮬레이션 생성기에 새로 만들어야 해서 별도 작업으로 남겼다.
- 재검토 지적 M3(부분 분리 3건), M4(페르소나 틀 동형), M5(루브릭 템플릿), M6(practice `me` 0건), M7(storedHistory 도달성)은 그대로 남아 있다.
- 이 작업은 Orca 오케스트레이션 없이 코디네이터가 수행했다(일반 서브에이전트 사용: 독립 재검토 1건, 설명 HTML 문서 1건).
