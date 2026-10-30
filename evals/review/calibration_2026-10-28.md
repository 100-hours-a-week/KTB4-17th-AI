# 5단계 자동 채점기(Judge) 2차 보정 및 검증 인프라 개정 종합 보고서

- **작성일:** 2026-10-28
- **작성자:** Antigravity (5단계 구현 워커)
- **대상 커밋 / 기준:** 현재 HEAD `b5e158c` (기존 실행 결과: `cb53097-dirty`)
- **수행 범위:** 코드 및 검증 인프라 개선, 합성 대조 fixture 자체 그라운딩 보강, 로컬 단위 테스트 검증 (실제 LLM 대량 재판정은 주간 한도 및 호출 금지 지침에 따라 미실행)

---

## 1. 개요 및 필수 수정 반영 사항

본 개정 보고서는 이전 1차 보고서에서 지적된 결함들을 완전히 해결하고, 안전하고 비파괴적인 평가 재개 환경을 구축한 결과를 정리합니다.

### 반영된 핵심 수정 사항
1. **dry-run 무결성 확보**:
   - `stage5_rejudge.py` 및 `stage5_supplement.py`의 `--dry-run` 모드가 디스크에 가짜 PASS 결과 파일(`*_rejudged.json`)을 생성하던 현상을 완전히 제거했습니다.
   - dry-run은 오직 사전 검증 및 판정 계획만 stdout으로 출력하며, 디스크에 결과 파일을 일절 남기지 않습니다.
2. **결과 파일 구조 및 최신성 호환 (`*_calibration.json`)**:
   - 새 결과 파일은 `{timestamp}_{uuid}_{ver}_{split}.json` (예: `20261028-133500_a1b2c3d4_cb53097-dirty_calibration.json`) 구조를 유지하여, `stage5_labels.py`의 `latest_result` 탐색 규칙(`*_{split}.json`)과 완전히 호환되며 최신 실제 판정을 자연스럽게 소비하고 재개할 수 있습니다.
   - 소스 파일이나 기존 파일 경로로의 덮어쓰기 시도는 즉시 `FileExistsError` / `ValueError`로 거절됩니다.
   - `latest_result` 선택 시 `smoke`, `dryrun`, `plan`, `test` 등은 엄격히 필터링됩니다.
   - 부분 재판정(`--types`) 시 갱신되지 않은 유형의 provenance를 최신 프롬프트 해시로 오인하지 않도록 유형별 provenance(`type_provenance`)를 분리 기록합니다.
3. **스냅샷 완전성 및 결측 사전 차단**:
   - 기존의 500자 단순 preview 대신, Judge에 전달된 전체 `input_full`, `output_full`, `oracle_full`, `template_hash`, `final_prompt_hash`를 스냅샷에 기록합니다.
   - 누락된 케이스, 빈 템플릿, 모델 실행 오류(`output_raw is None`), 필수 컨텍스트 누락, 6000자 초과 잘림(truncation) 발생 시 **LLM을 호출하지 않고 즉시 ERROR로 차단**하며, 정상 PASS 처리를 엄격히 금지합니다.
   - 과거 결과(`cb53097-dirty`) 복원 시 `lf_eval_cache.json`에서 exact source match 탐색을 수행하고, 복원 불가 시 `historical_context_unobserved: True`로 결측 상태를 정직하게 기록합니다.
4. **Holdout 보호 및 안전장치**:
   - `--split blind_holdout` 실행은 `--allow-holdout` 플래그 없이 실행할 수 없으며, holdout 평가 시 특정 유형 강제 보정(`--types`)을 금지하여 holdout 오염을 방지합니다.
   - 서브프로세스 타임아웃 및 `ThreadPoolExecutor` 컨텍스트 매니저를 필수로 적용했습니다.
5. **f08 디렉토리 경로 정정 및 folder 중복 방지**:
   - 잘못 생성되었던 `evals/f08-v2-memory-extraction/` 폴더를 정리하고, 정식 경로인 `evals/f08-v2-memory-extract/`로 보강 fixture를 이관했습니다.
   - `J.folder(slug)` 함수에서 동일 prefix를 가진 디렉토리가 복수 개 감지될 경우 명확한 `ValueError`를 발생시키도록 안전장치를 강화했습니다.
6. **36개 희소 유형 Fixture Self-contained Grounding 완비**:
   - 36개 모든 fixture가 DB 조회 없이도 독립적으로 판정될 수 있도록 `input` 내에 프로필, 기억 fact, 말투 문구, 대화 턴 등 필수 grounding을 완전히 내장했습니다.
   - `draft_expected`, `target_type`, `description`, `fail_if`는 Judge 프롬프트에 절대 누출되지 않도록 전송을 차단했습니다.
   - 2FAIL 2PASS가 표본 부족 문제를 완비/해결한 것이 아님을 명시하고, 독립 검토 전 가설(`draft_expected`)로 규정했습니다.
   - 실제 평가 실행 도구(`stage5_supplement.py run`)를 구현하되 판정 결과는 앱 기준선과 격리된 `evals/_harness/runs/supplement_results/`에 비파괴적으로 저장되도록 설계했습니다.
7. **프롬프트 템플릿의 DB 오류 오염 제거**:
   - 프롬프트 파일(`judges/F-*.md`) 생성 시 로컬 DB 미연결로 인한 에러 메시지(`[DB 오류: ...`)가 예시 템플릿에 구워져 환경마다 프롬프트가 달라지던 비결정성 결함을 제거했습니다.
8. **f09:F-01 라벨 불일치 감지 및 격리 (`QUARANTINE_LABEL_CONFLICT`)**:
   - `09-mem-26-b` vs `09-mem-30-b`는 둘 다 raw DELETE+ADD인데, 기존 final label에서 `26-b`는 `PASS`, `30-b`는 `FAIL`로 라벨링되어 있는 모순이 확인되었습니다 (실제 `product.output_parsed.decisions`는 DELETE를 제거해 둘 다 ADD만 남음).
   - 기존 골드셋 라벨을 지표 맞추기 위해 임의 수정하지 않고, 해당 유형을 `QUARANTINE_LABEL_CONFLICT`로 명시 격리하여 **f09:F-01에 대해 성급한 "보정 완료" 단정을 금지**했습니다.
   - 사용자 확정 기준에 맞추어 UPDATE가 정답이며, DELETE+ADD 역시 UPDATE 누락에 따른 모순 처리 실패(Fail)임을 보충 기준(`CALIB`)에 확립하되, 기존 라벨 간 충돌은 독립 검토가 완료될 때까지 보류합니다.
9. **`stage5_labels.calib`의 거짓 통과(False Pass) 왜곡 수정 및 `EVAL.md` 보존**:
   - 기존 로직이 NA/ERROR 및 한쪽 클래스 0건일 때 `TPR/TNR=None`을 `ok=True(✅)`로 판정하던 결함을 수정하여 `calib_metric_status`를 도입했습니다.
   - 한쪽 정답 클래스 0, 판정 미완료, 실행 오류, 잘림 발생 시 통과가 아닌 `⚠️ FAIL 표본 0 (재현력 미검증)` / `⚠️ PASS 표본 0 (오탐 미검증)` / `⚠️ 표본 0 (미검증)`으로 정직하게 보고합니다.
   - 새 Sonnet 재판정이 없으므로 기존 `EVAL.md` 5-3 표는 자동 덮어쓰지 않고 기본값(`write_eval_md=False`)으로 보존합니다.
10. **판정 Rubric·중립 사실과 특정 Case 결론 힌트의 엄격한 분리**:
    - 공통 확정 rubric(하루는 AI/베이킹 기준, UPDATE 정책 등) 및 case의 중립 사실 자료는 정상적인 판정 근거로 유지합니다.
    - 특정 case의 `FAIL/PASS`, `target_failures`, `판정 메모`, `(전제 OK)` 같은 결론 힌트는 전송에서 완전 제거했습니다.

---

## 2. 2차 보정 대상 5개 Judge 판정 기준 개선

| 기능 | 유형 | 잡아야 하는 실패 | 주된 불일치 원인 | 개선된 판정 기준 (`CALIB`) | 상태 |
| --- | --- | --- | --- | --- | --- |
| **f01** 온보딩 질문 생성 | `F-03` | 사용자 답의 시점·대상·내용을 잘못 읽음 | 지시문형 입력에 대한 사실 전제 맞장구(FN) vs 단순 되묻기 오독(FP) | 사용자가 실제로 말한 내용을 왜곡하거나, 답하지 않은 입력(지시문 등)에 대해 특정 경험을 답한 것처럼 전제하여 맞장구치는 경우 Fail. 단순 되묻기(`저한테서요...?`)나 단순 연결 반응은 Pass. | 기준 개선 완료 |
| **f03** 온보딩 페르소나 추출 | `F-03` | 답을 다른 질문의 답으로 읽거나 정정을 무시함 | 요약 서술의 상위 카테고리 종합을 오독으로 판정(FP) vs 유보적 답변("때에 따라 달라요") 왜곡 미감지(FN) | 요약 서술(`narrative`/`summaries`)에서 친밀감·소통 등 상위 카테고리로 종합하거나 일반화하는 것은 F-02 영역으로 배제. 사용자가 질문에 "때에 따라 달라요" 등 유보/반대로 답했음에도 질문의 유도 성향을 긍정하여 왜곡 저장한 경우 Fail. | 기준 개선 완료 |
| **f09** 메모리 충돌 판정 | `F-01` | 기존 기억을 수정하지 않고 새 사실만 추가해 모순을 남김 | UPDATE가 확정 정답인데 DELETE+ADD에 대해 Judge가 자의적 PASS 오판 및 기존 라벨 불일치 | 사용자가 기존 사실을 부정·대체할 때의 확정 정답은 UPDATE임. 기존 사실을 UPDATE하지 않고 DELETE 후 ADD하거나 단순 ADD만 하여 UPDATE를 누락한 모든 경우는 Fail (DELETE+ADD 역시 UPDATE 누락으로 Fail). | **격리 (`QUARANTINE_LABEL_CONFLICT`)** |
| **f12** 시뮬레이션 v2 대본 생성 | `F-05` | 말투 예시를 경험·사실로 사용함 | frequent_phrases의 습관/취향 문구('자기 전에 톡', '하루 종일 톡')를 사실이 아닌 단순 반응어로 간주(FN) | 카톡 문구는 말투 참조용일 뿐 사실이 아님. 과거 경험뿐 아니라 현재의 연락 습관, 상태, 취향(`하루 종일 톡하는 거 좋아`, `자기 전에 톡`, `회사 일로 힘들어` 등)을 사실이나 자기 프로필인 것처럼 말하는 것 역시 Fail. | 기준 개선 완료 |
| **f13** 시뮬레이션 v2 대본 재생성 | `F-04` | 지적한 문제가 남거나 새로 맥락이 어긋남 | CALIB이 특정 극단적 문구만 규정하여 F-07 기준(맥락 단절, 인과 불성립) 및 미해결 교정 지시를 배제(FN) | 교정 지시의 이슈가 재생성 대본에 다시 있거나, 직전 상대방 말과 맥락이 단절·무관한 문구(F-07 기준), 인과가 성립하지 않는 줄, 엉뚱한 리액션이 새로 들어가면 Fail. F-01/F-05 수준의 문제가 재생성에 남거나 새로 생겨 흐름을 깨는 경우도 Fail. | 기준 개선 완료 |

---

## 3. 전체 40개 Judge 검토 범위 및 현황 (한계와 발견 사항)

### 1) 검토 범위와 한계 명시
- **검토 범위**: `evals/_harness/stage5_judges.py`의 `oracle_text()` 정제 로직 및 40개 프롬프트 템플릿의 `<정답 기준>` 치환부에 대한 정적 텍스트 분석.
- **확인 결과**:
  - `oracle_text()`는 골드셋의 `must_not` 실패 유형 목록과 케이스별 판정 메모를 제외하고 순수 사실 기대값(`expect`)만 남기므로, 프롬프트 템플릿 차원에서의 직접적인 정답 명칭/FAIL 누출은 발견되지 않았습니다.
  - **주의 및 한계**: 이는 템플릿 치환부에 대한 정적 분석 결과일 뿐이며, 특정 케이스의 기대값 문구가 모델에게 미묘한 편향을 주는지 여부는 Claude 주간 한도로 인해 실제 LLM 판정에서 전수 실증되지 않았습니다. 향후 Grok 등 독립 AI 교차 검토가 필요합니다.

### 2) 발견된 취약점 및 개선 조치
- **프롬프트 템플릿 비결정성 제거**:
  - `render()` 함수가 실행될 때 DB 연결이 안 된 환경이면 `[DB 오류: ...]` 메시지가 예시 블록에 들어가 프롬프트 파일이 환경마다 달라지던 문제가 발견되었습니다.
  - DB 오류 문자열이 감지될 경우 기존 정상 템플릿의 예시 블록을 보존하도록 안전장치를 적용하여 비결정성을 제거했습니다.
- **컨텍스트 결측 투명화**:
  - `f03`(온보딩 대화), `f06`, `f12`(프로필/기억 fact)에서 로컬 DB가 차단되었을 때 빈 데이터로 숨기지 않고, `lf_eval_cache.json`에서 exact source match 복원을 시도하며, 복원이 불가능한 경우 판정을 시도하지 않고 `ERROR`로 즉시 차단하도록 수정했습니다.

---

## 4. 9개 희소 유형 합성 Judge 검증 Fixture 구축 현황

앱 기준선(`goldset.jsonl`, `results/`)과 엄격히 격리된 독립 경로(`evals/f*/calibration_supplement.jsonl`)에 9개 희소 유형별 4건(FAIL 2건, PASS 2건), 총 36건의 합성 fixture를 작성했습니다.

### 성격 및 주의사항
- **가설/초안 성격 (`draft_expected`)**: 본 fixture의 기대값 레이블은 독립 검토 전 가설(초안)입니다.
- **표본 부족 문제의 본질**: 2FAIL 2PASS 대조군을 합성으로 구비했다고 해서 "표본 부족 문제가 완전히 해결(완비)"된 것은 아니며, 향후 실제 운영 트레이스 및 독립 검토를 통한 지속적인 표본 확충이 필요합니다.
- **정답 비누출 원칙**: Judge 호출 시 `draft_expected`, `target_type`, `description`은 프롬프트에서 완전히 제외되며, 오직 self-contained grounding 입력과 output만 전달됩니다.

### 현황표 (`python3 evals/_harness/stage5_supplement.py validate`)

| 기능 | 대상 유형 | 실패 내용 | FAIL 초안 | PASS 초안 | 총계 | 내장된 Self-contained Grounding |
| --- | --- | --- | ---: | ---: | ---: | --- |
| **f01** 온보딩 질문 생성 | `F-04` | 이미 답한 질문 반복 | 2 | 2 | 4 | 온보딩 턴별 질문/답변 기록 전체 (`history`) |
| **f06** 연습대화 답변 | `F-01` | 말투 예시를 사실로 사용 | 2 | 2 | 4 | 상대역 프로필, 기억 fact, frequent_phrases 목록 |
| **f06** 연습대화 답변 | `F-07` | 프로필·기억에 없는 사실 날조 | 2 | 2 | 4 | 상대역 직업/거주지/나이 프로필 및 확정 사실 목록 |
| **f08** 메모리 사실 추출 | `F-01` | AI 발화를 사용자 사실로 저장 | 2 | 2 | 4 | AI 발화와 사용자 맞장구/응답 대화록 (`messages`) |
| **f12** 시뮬레이션 v2 대본 | `F-06` | 근거 없는 “저도/나도” | 2 | 2 | 4 | 두 사람(a, b)의 프로필, fact, frequent_phrases |
| **f12** 시뮬레이션 v2 대본 | `F-07` | 인과·맥락이 맞지 않는 문장 | 2 | 2 | 4 | 두 사람의 활동 사실 및 직전 대화 흐름 |
| **f12** 시뮬레이션 v2 대본 | `F-10` | 프로필·사실에 없는 내용 날조 | 2 | 2 | 4 | 두 사람의 등록 프로필 및 fact 전체 |
| **f17** 마무리 답변 초안 | `F-02` | 상대가 하지 않은 말에 반응 | 2 | 2 | 4 | 상대방의 실제 직전 발화 기록 (`messages`) |
| **f18** 종료 메시지 생성 | `F-02` | 요청자의 사정을 상대 사정으로 변경 | 2 | 2 | 4 | 요청자 사유(`requester_reason`) 및 상대 상태 |
| **합계** | **9개 유형** | | **18개** | **18개** | **36개** | **DB 없이 전원 자체 그라운딩 완비** |

### Grok 권고 144개 근접 대조 확장 로드맵
- **Grok 보고서 지적 사항**: 현재 36개는 스모크/초안 수준이며 통계적 유의성을 확보하기에는 표본이 부족합니다. 유형당 8쌍(FAIL 8건 / PASS 8건, 총 144건)의 근접 대조군 확장이 권고되었습니다.
- **향후 확장 원칙**:
  1. 각 fixture는 실제 도메인 사실을 담은 독립적인 grounding 자료를 필수로 내장.
  2. `draft_expected`는 작성자 가설이며, 최종 확정 전 Grok 또는 사용자/Opus의 독립 교차 검토를 거쳐야 함.
  3. `FAIL/PASS`, `전제 OK` 등 특정 케이스의 결론을 암시하는 힌트는 프롬프트에 절대 누출되지 않도록 whitelist 기반 주입 유지.

---

## 5. 단위 테스트 및 코드 품질 검증 결과

### 1) 단위 테스트 (`tests/evals/test_stage5_calibration.py`)
`poetry run pytest tests/evals/test_stage5_calibration.py -v` 실행 결과: **14개 테스트 전원 통과 (6.69s)**

- `test_folder_duplicate_prefix_detection`: 중복 prefix 디렉토리 감지 시 `ValueError` 발생 검증 완료.
- `test_folder_not_found`: 미존재 slug 시 `FileNotFoundError` 발생 검증 완료.
- `test_is_limit_error_detection`: 주간 한도(weekly limit) 등 한도 문자열 감지 검증 완료.
- `test_weekly_limit_immediate_stop_no_retry`: weekly limit 감지 시 재시도 루프 즉시 탈출(0회 재시도) 검증 완료.
- `test_rejudge_dry_run_zero_file_creation`: dry-run 실행 시 디스크 결과 파일 생성 0건 검증 완료.
- `test_supplement_dry_run_zero_file_creation`: supplement dry-run 실행 시 파일/디렉토리 미생성 검증 완료.
- `test_rejudge_refuses_overwrite`: 소스 파일 및 기실행 파일 덮어쓰기 거절 검증 완료.
- `test_rejudge_creates_unique_timestamped_file`: 새 결과 파일의 타임스탬프+UUID 및 `*_calibration.json` 명명 검증 완료.
- `test_latest_result_filters_dryrun_smoke_and_selects_latest`: dryrun/smoke 필터링 및 최신 유효 파일 선택 검증 완료.
- `test_preflight_blocks_without_calling_llm`: 케이스 결측, 모델 오류 출력 결측, 6000자 초과 잘림 시 LLM 호출 없이 ERROR 반환 검증 완료.
- `test_holdout_protection`: blind_holdout 보호 및 force_types 차단 검증 완료.
- `test_supplement_fixtures_self_contained_grounding`: 36개 fixture의 DB 없는 완결 그라운딩 보유 검증 완료.
- `test_supplement_prompt_zero_leakage`: 프롬프트 생성 시 정답/설명 비누출 검증 완료.
- `test_calib_metric_status_handles_zero_samples_and_na`: 한쪽 클래스 0건(FAIL 0 / PASS 0), NA/ERROR 시 통과(✅) 왜곡 방지 및 정직한 상태 반환 검증 완료.

### 2) Ruff 검사 및 포맷팅
- `poetry run ruff check`: **All checks passed! (0 errors)**
- `poetry run ruff format --check`: **5 files already formatted**

---

## 6. 미실행 작업 및 잔여 제한 사항

1. **Claude CLI 주간 한도(Weekly Limit)로 인한 실제 LLM 재판정 미실행**:
   - 현재 개발 환경의 Claude CLI 구독이 `Oct 30 at 7pm (Asia/Seoul)`까지 weekly limit 상태입니다.
   - 따라서 현재 대상 5개 Judge의 후속 재판정 및 수치 측정은 실행하지 않았으며, 안전한 dry-run 및 로컬 모킹 검증만 수행했습니다.
   - 실제 재판정은 주간 한도가 해제된 이후 또는 Grok 등 대체 검토 모델을 통한 승인 후 실행되어야 합니다.
2. **`f09:F-01` 골드셋 라벨 불일치 잔여 과제 (`QUARANTINE_LABEL_CONFLICT`)**:
   - `09-mem-26-b`(PASS)와 `09-mem-30-b`(FAIL) 간 기존 골드셋 라벨 충돌은 독립 검토가 필요한 상태로 격리되어 있습니다.
   - UPDATE 누락은 Fail이라는 사용자 확정 정책을 판정 기준에 수립하였으나, 기존 라벨 정리는 코디네이터/사용자 결정에 따라 일괄 정비되어야 합니다.
3. **Postgres.app Trust 인증 문제**:
   - 로컬 `ktb_eval` DB 연결 시 trust 인증 다이얼로그 오류가 지속되고 있습니다.
   - 본 작업에서는 DB 오류가 발생하더라도 프롬프트 템플릿이 오염되지 않도록 격리하고, `lf_eval_cache.json` exact match 및 투명한 결측 차단 처리를 완료했습니다. 향후 실제 앱 실행 및 신규 데이터 생성을 위해서는 사용자의 DB 재시작 또는 인증 설정 정비가 필요합니다.
4. **과거 골드셋 Oracle과의 격리**:
   - 골드셋의 일부 오래된 문제에 남아 있는 과거 기준(예: 기본 50점 허용 등)은 최신 판정 기준과 충돌할 수 있으므로, 모델 평가 시 독립적으로 격리 식별되어야 합니다.
