# v1.4.0 수정 보고

v1.3.0은 앱 코드 커밋 `f7cc806` 시점을 기준으로 만들어졌다(manifest의 소스 SHA-256을 git 이력과 대조해 확인). 그 뒤 앱 코드가 바뀌어 일부 기대값이 낡았고, 이 버전은 그 기대값만 현재 앱 코드에 맞춰 정정한 결과다. 독립 재검토는 아직 하지 않았으므로 90점 통과로 보지 않는다. 사람 이중 라벨, Judge calibration, 실제 모델 실행도 없다.

## 바뀐 앱 동작과 반영

1. **근거 없는 점수는 50이 아니라 null(모름)이다.** `DEFAULT_SCORE` 삭제, 저장된 옛 행의 "신뢰도 LOW인 50"은 `known_scores`가 null로 읽는다. 궁합 계산은 모름 차원의 규칙 점수를 null로 두고 영역 평균과 위험 판정에서 뺀다. 단 `dimensions[].a/b` 표시값은 백엔드 계약(#63) 때문에 모름이어도 50이다.
   - 페르소나 입력의 빈 차원: 50 → null (`complete_persona`).
   - `ruleOracle`: 규칙 차원 점수·영역 점수·위험·총점을 null 기준으로 재계산했고, 표시값은 `dimensionDisplay`로 분리했다.
   - `persona_build`: `postService.defaultScores`(50) → `unknownScores`(null). 서비스는 답변에서 근거가 확인된(answered) 차원만 값을 남긴다. forbidden에 "직접 답하지 않은 차원을 다른 답에서 미루어 채움"을 추가했다.
   - `raw_null` 후처리 검사: `contactRhythm` 50 → null. preview의 `missingDimensionInputDefault: 50`은 `missingDimensionScore: null`과 `missingDimensionDisplay: 50`으로 나눴다.
2. **시뮬레이션 실행 계약.** `max_tokens = 3000 + 300 × turns`(예전 2200 + 180 × turns). 깨지거나 잘린 출력은 남은 시간이 총 timeout의 1/3을 넘으면 1회 재시도하므로 `runtime.maxLlmCalls = 2`. 계속 깨지는 `invalid_json` fault는 두 번 호출 후 실패하므로 `llmCalls = 2`이고 `retryCondition`을 기록했다. timeout은 재시도하지 않는다.

## 변경 범위 (v1.3 재생성본과 비교)

| Dataset | 바뀐 케이스 | 주요 필드 |
| --- | ---: | --- |
| persona_onboarding_conversation | 0 / 60 | 없음 |
| persona_onboarding_tagging | 0 / 80 | 없음 |
| persona_build | 60 / 60 | postService, forbidden, hardAssertions, raw_null 경계 |
| practice_reply | 60 / 60 | input.partner.scores의 빈 차원 |
| simulation_run | 36 / 36 | 입력 점수, ruleOracle, runtime, 루브릭 문구 |
| simulation_report_preview | 24 / 24 | 입력 점수, ruleOracle, idealRecalculationOracle |

시뮬레이션·preview 60건 모두 `ruleOracle.overallScore`가 바뀌었고 등급이 바뀐 케이스는 24건이다. 예: simulation_run-001 총점 81 → 97, preview-003 54 → 24(등급 OK → CAUTION). 예전에는 모름 차원을 50/50으로 보아 SIMILAR 규칙이 100점을 주던 것이 사라진 결과다. 최대 변화는 30점이다.

케이스 수(320), 분할(64/192/64), caseId는 그대로다.

## 검증 (수정 전 → 후)

- 드리프트 테스트 `tests/test_quality_datasets_drift.py`(앱 코드를 정답으로 재계산): 수정 전 253개 중 157개 실패, 수정 후 전부 통과.
- 전체 `pytest tests evals/quality_datasets`: 528개 통과.
- `generate --check` 통과, validator 오류 0·경고 0, audit 교차 split 충돌 0, ruff check·format 통과.

## 이번에 하지 않은 것과 남은 제한

- 신규 케이스 추가 없음: 온보딩·연습대화의 "저번에/지난번에" 금지, 시뮬레이션 화자 분리 규칙, MBTI 말투 힌트는 아직 평가 케이스가 없다. 추가하면 케이스 수와 분할이 바뀌므로 별도 결정이 필요하다.
- 온보딩 발화 프롬프트 변경("저번에" 금지, 기억은 대화에 실제로 있던 것만)이 기존 hardAssertions와 충돌하는지는 검토하지 못했다.
- 시뮬레이션 프롬프트에 화자 분리 규칙이 추가되었지만 fault 종류에 `truncated`·`upstream_error`·speaker_mixup 재시도 케이스는 없다.
- `llm_json_mode` 설정은 평가 실행 설정 기록에 아직 없다.
- 기대값 검증은 앱 순수 함수로 재계산한 결과이며 사람이 읽고 확인한 것이 아니다.
- 이번 작업은 Orca 오케스트레이션 없이 코디네이터가 단독으로 순차 수행했다.
