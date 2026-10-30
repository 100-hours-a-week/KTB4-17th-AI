# KTB-17DEV 6단계 기준선 측정 및 배포 게이트 실행 가이드

- **작성일:** 2026-10-28
- **기준 커밋:** `b5e158c` (Merge PR #235: 존댓말·반말 분류와 v2 말 놓기 판정 제거)
- **작성 워커:** Antigravity (`gemini-3.8-flash-high`, effort `high`)
- **실행 환경 원칙:** DB `ktb_eval`, Langfuse `eval-local` (운영 DB 사용 절대 금지)
- **관련 인덱스:** [Sonnet Historical 결과 인덱스](SONNET_RESULTS_2026-10-28.md)

---

## 1. 개요 및 배경

본 문서는 KTB-17DEV 소개팅 서비스의 LLM 기능들에 대한 **6단계 기준선(Baseline) 측정 및 배포 게이트(Gate 0 ~ Gate 3) 판정 도구**의 사용법, 무결성 검증 결과, 운영 실패 반영 절차를 정의합니다.

### 1.1 현재 버전과 과거 결과 분리
- **과거 결과:** 커밋 `cb53097-dirty` 시점에 Claude Sonnet(`claude-sonnet-5-5`)으로 실행된 5단계 calibration 결과(191행, 477판정, f12 sample.error 5건)는 **Historical Data**로 격리 보존됩니다. 현재 Claude 계정 주간 한도(weekly limit) 도달로 신규 Sonnet 재채점은 동결 상태입니다.
- **현재 HEAD (`b5e158c`):** PR #235 반영으로 존댓말·반말 분류 및 v2 말 놓기 판정(`f07`)이 코드 및 DB에서 완전히 삭제되었습니다. 따라서 과거의 `cb53097-dirty` 결과를 현재 코드베이스의 기준선으로 혼동해서는 안 됩니다.

### 1.2 DB 및 실행 환경 원칙
- 모든 평가는 오직 `ktb_eval` DB와 `eval-local` 환경만 사용합니다.
- 운영 DB(`ktb`) 및 호스트 환경 설정 변조, DB의 비가역적/파괴적 초기화(`DROP DATABASE`, 무차별 `TRUNCATE`)는 엄격히 금지됩니다.
- 전체 `DATABASE_URL`의 자격증명/호스트는 출력 및 로그에 노출하지 않으며 대상 DB명(`ktb_eval`)만 다룹니다.
- `urllib.parse` 경로 검사로 정확히 `/ktb_eval` 경로만 허용하며, `/ktb_eval_prod` 등은 즉시 차단합니다.
- URL 문자열 확인은 실제 DB 연결(connect)을 뜻하지 않으며, 현재 Postgres.app trust 인증 관련 이슈로 인해 사용자 질문이 대기 중입니다. 따라서 대량 앱/LLM 실행은 코디네이터가 최종 시작합니다.
- **실행 현황 주의:** 실제 DB 인증, 판정, LLM 기준선 성능 측정은 현재 시점에서 여전히 미실행(UNEXECUTED) 상태이며, 본 단계는 6단계 실행 인프라 안전 연결, 격리 환경 보장, 메타데이터 provenance 바인딩에 집중했습니다.

---

## 2. 16개 기능 Preflight (사전 비행 검사) 결과

`evals/_harness/stage6_preflight.py`를 통해 기능 및 골드셋 전수 점검을 수행했습니다.

> [!IMPORTANT]
> **골드셋 케이스 수량 및 대상 기능 수 정리:**
> - **전체 등록 기능 및 골드셋:** 총 18개 기능 x 40 = 720문제
> - **공식 제외 (v1 미사용):** `f10`, `f11` (2개 기능 x 40 = 80문제 제외)
> - **6단계 과거 평가 대상:** 16개 기능 x 40 = 640문제
> - **계약 제거 격리:** `f07` (PR #235 로 `speech.py` 및 테이블 제거 감지 -> `QUARANTINED_CONTRACT_DROPPED`, 자동 실행 차단 격리)
> - **현재 실행 가능 활성 기능:** 15개 기능 (600문제)

### 2.1 기능별 계약 및 분할 현황

| 기능 ID | 기능명 | 계약 상태 | Split 분할 (Cal/Reg/Hold) | 비고 |
|:---|:---|:---:|:---:|:---|
| **f01** | 온보딩 질문 생성 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f02** | 온보딩 답변 태깅 | `ACTIVE` | 12 / 20 / 8 (총 40) | 규칙 검사 전용 (Judge 없음) |
| **f03** | 온보딩 페르소나 추출 | `ACTIVE` | 12 / 20 / 8 (총 40) | **오라클 드리프트 주의 (하단 참조)** |
| **f04** | 카톡 말투 추출 v1 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f05** | 카톡 말투·점수 추출 v2 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f06** | 연습대화 답변 | `ACTIVE` | 12 / 20 / 8 (총 40) | 사용자 답변 대기 (임의 계약 해석 금지) |
| **f07** | 말 놓기 허락 판정 | `QUARANTINED_CONTRACT_DROPPED` | 12 / 20 / 8 (총 40) | **계약 불일치 격리 (자동 실행 차단)** |
| **f08** | 메모리 사실 추출 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f09** | 메모리 충돌 판정 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f10** | 시뮬레이션 v1 화자 생성 | `EXCLUDED_V1` | [SKIP] | **미사용 v1 골드셋 검사 전 공식 제외** |
| **f11** | 시뮬레이션 v1 리포트 | `EXCLUDED_V1` | [SKIP] | **미사용 v1 골드셋 검사 전 공식 제외** |
| **f12** | 시뮬레이션 v2 대본 생성 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f13** | 시뮬레이션 v2 대본 재생성 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f14** | 시뮬레이션 v2 대본 심사 | `ACTIVE` | 12 / 20 / 8 (총 40) | 규칙 검사 전용 (Judge 없음) |
| **f15** | 시뮬레이션 v2 결함 줄 수정 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f16** | 시뮬레이션 v2 리포트 서술 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |
| **f17** | 마무리 답변 초안 생성 | `ACTIVE` | **11 / 21 / 8** (총 40) | Cal 11, Reg 21 (규격 일치) |
| **f18** | 종료 메시지 생성 | `ACTIVE` | 12 / 20 / 8 (총 40) | 정상 |

### 2.2 핵심 발견 및 격리 사항
1. **`f07` 계약 제거 및 자동 실행 차단:**  
   PR #235(커밋 `b5e158c`)에서 `app/v2/practice/speech.py` 및 `v2_practice_speech_state` 테이블이 완전히 제거되었습니다. 기존 재현 도구 본문은 보존하되, 대상 선택 단계에서 `f07`을 제외하여 계약 불일치로 인한 자동 실행을 차단하고 격리하였습니다.
2. **`f10`, `f11` 미사용 v1 제외:**  
   시뮬레이션 v1 기능은 골드셋 검사 전에 공식 제외(`EXCLUDED_V1`)로 스킵됩니다.
3. **`splitGroup` 누수 검사 통과:**  
   동일한 `splitGroup`에 속한 케이스들이 서로 다른 split에 걸쳐 분할되지 않도록 전수 검사하여 누수 0건을 확인하였습니다.
4. **`f03` 골드셋 오라클 드리프트:**  
   `f03` 골드셋 40문제의 오라클 `expect`에 구버전 `default_50_allowed: True`가 남아 있습니다. 2026-10-28 최신 판정 기준은 "서비스가 제거한 차원에 모델이 점수를 냈는지 여부"로 변경되었으므로, 과거 오라클과 최신 기준 간의 모순을 감지하여 경고 및 격리 보고하도록 하였습니다.

---

## 3. 파일 내용 해시 기반 매니페스트 동결 (Manifest Freeze)

평가 재현에 필요한 모든 핵심 코드와 데이터를 비밀 없이 해시하여 동결했습니다.

- **동결 파일 경로:** [`evals/manifest_b5e158c.json`](manifest_b5e158c.json)
- **포함 범위:**
  1. `app/**/*.py` (앱 구현 코드)
  2. `docs/성능평가_v2/**/*.md` (평가 체계 및 기준서)
  3. `evals/f*/goldset.jsonl` (골드셋 18개)
  4. `evals/f*/labels/calibration_final.csv` (확정 라벨 18개)
  5. `evals/f*/judges/*.md` (Judge 프롬프트 41개)
  6. `evals/_harness/*.py` (실행기 및 검사기 코드)
  7. `evals/**/calibration_supplement.jsonl` (보충 데이터)
- **관리 대상 파일 수량:** 최종 동결(`freeze`) 시점에 `stage6_manifest.build_manifest()`가 동적으로 산출 (코디네이터의 최종 freeze 전까지 특정 숫자[과거 초안 254개 등]로 고정하지 않음. 코드, 보충 데이터 및 매니페스트 대상 변화에 따라 동적으로 검증)
- **결합 해시(Combined Hash):** 경로와 해시를 정렬 결합한 sha256 해시
- **검증(verify) 항목:**
  - 빈/위조 매니페스트 구조 차단
  - Git HEAD 변경 감지
  - 새 파일 추가(미등록 파일) 및 누락 감지
  - 파일 내용 변경(해시 불일치) 감지
> [!IMPORTANT]  
> 최종 공식 동결(freeze)은 워커가 아닌 코디네이터가 검토 후 최종 수행합니다 (워커 임의 freeze 금지).

---

## 4. 스코어카드 및 배포 게이트 (Gate 0 ~ 3) 사양

### 4.1 진실성 원칙 (No Guessing / Raw vs Product)
- **임의 추정 금지 및 관측/구현 한계:** 기존 historical 결과의 `verdict`는 모델 원문(raw)과 제품(product)이 혼합되어 있으며, `raw_verdict`와 `product_verdict` 같은 명시적 분리 필드가 결과 JSON에 저장되어 있지 않습니다.
  - 현재 시스템은 raw/product 분리 성능 측정을 위한 관측 및 수집 구현이 아직 미비한 상태입니다.
  - 따라서 증거가 부족한 통과율을 임의로 추정하거나 가공하지 않고 원칙대로 `UNKNOWN`으로 표기합니다.
  - 향후 실제 기준선 측정 파이프라인 가동 시 raw_verdict 와 product_verdict 분리 기록 구현이 필요합니다.
- **실제 기준선 성능 미실행 상태 명시:** 현재까지 실제 DB 연결/인증 및 LLM API를 통한 신규 6단계 기준선 판정은 미실행 상태(UNEXECUTED)이며, 게이트 판정 도구의 무결성 검증과 실행 안전성 환경 구축만 완료되었습니다.
- **관측 통계만 제시:** 전체 통과, 실패, NA, 불완전(incomplete) 케이스 수와 실패 유형별 판정 수만 관측치 그대로 보고합니다.
- **엄격한 비-통과(Non-Pass) 기준:**
  - 실행 오류(`sample.error`) 발생
  - 판정 불가(`NA`) 포함
  - `stage3_types.S.D`에 정의된 필수 실패 유형 중 채점 누락
  - 출력 잘림(truncated) 또는 미출력  
  위 항목은 **절대로 PASS로 카운트하지 않으며**, 불완전/오류/NA로 분류합니다.
- **S.D 원본 심각도(sev) 사용:** 가짜 매핑 대신 `S.D` 원본의 `sev`에 `CRIT`가 포함된 유형을 치명 결함으로 엄격 판정합니다.
- **임의 임계치 삭제:** 임의의 지연시간 상한이나 가짜 표본 부족(1~2건) 지표를 배포 기준으로 사용하지 않습니다.

### 4.2 Gate 0 ~ Gate 3 판정 기준

| 게이트 | 이름 | 판정 기준 |
|:---|:---|:---|
| **Gate 0** | **데이터 무결성** | • 실제 골드셋의 수량/split/ID 및 현재 동결 매니페스트 일치 확인 시에만 **PASS**<br>• 미확인 시 **UNKNOWN**, 불일치 발견 시 **FAIL** |
| **Gate 1** | **안전성 (Safety)** | • 관측 판정에서 S.D 원본 CRIT 심각도 결함 발견 시 즉시 **FAIL**<br>• product 확정 판정 증거가 없으면 **UNKNOWN** 보류 |
| **Gate 2** | **기술 안정성** | • 기술 오류 발생 시 즉시 **FAIL**<br>• 오류 0건이라도 상태·멱등성·동시성·지연예산 미관측 시 **UNKNOWN** 보류 (배포 통과 불가) |
| **Gate 3** | **생성 품질** | • 보정 완료 미확정(Claude 한도 등) 및 1/3/5 정성 루브릭 미관측으로 **UNKNOWN** 보류 (배포 통과 불가) |

> [!NOTE]  
> 관측치만 진실되게 보고하고, 증거가 부족한 모든 Gate를 UNKNOWN으로 보류하는 것이 배포 오판을 막는 안전한 원칙입니다.

---

## 5. CLI 사용법

`evals/_harness/stage6_cli.py`를 통해 모든 단계의 작업을 명령어로 제어할 수 있습니다.

### 5.1 사전 비행 검사 (Preflight)
```bash
python3 evals/_harness/stage6_cli.py preflight
```
- 자격증명 비노출, 정확한 `/ktb_eval` 경로 판정, splitGroup 누수 검사, `eval-local` 환경 검사 수행.

### 5.2 매니페스트 동결 및 무결성 대조
```bash
# 매니페스트 동결 (코디네이터가 최종 수행)
python3 evals/_harness/stage6_cli.py freeze

# 현재 코드·골드셋이 동결본과 일치하는지 대조
python3 evals/_harness/stage6_cli.py verify-manifest
```

### 5.3 정적 모의 검증 (Dry-run)
```bash
python3 evals/_harness/stage6_cli.py dry-run
```
- 로컬 골드셋 파일 구조 및 분할 스키마 무결성 정적 점검 (LLM API 호출, DB 인증 또는 실제 채점 준비 완료를 뜻하지 않음).

### 5.4 스코어카드 및 게이트 판정
```bash
python3 evals/_harness/stage6_cli.py scorecard <결과_JSON_경로>
# JSON 출력:
python3 evals/_harness/stage6_cli.py scorecard <결과_JSON_경로> --json
```

### 5.5 회귀 및 최종 시험 실행 (Preflight + Manifest 사전 검증 필수)
```bash
# 회귀 평가 (Preflight + Manifest 불일치 시 실행 차단, subprocess 호출 0회)
python3 evals/_harness/stage6_cli.py run-regression --only f01 --limit 2 --no-judge

# 최종 시험 (Preflight + Manifest 불일치 또는 --allow-holdout 누락 시 차단)
python3 evals/_harness/stage6_cli.py run-holdout --allow-holdout --only f01
```

---

## 6. 운영 실패 반영 절차 (골드셋 피드백 루프)

1. **운영 실패 감지:** Langfuse 모니터링 또는 CS 접수에서 LLM 이상 발화 수집.
2. **재현 케이스 작성:** PII 가명 처리 후 골드셋 스키마(`id`, `split`, `input`, `target_failures`, `oracle`)로 변환.
3. **독립 판정 및 라벨 확정:** Grok(`grok-4.7`) 및 도메인 전문가 교차 검토 후 최종 라벨 확정.
4. **회귀셋(`regression`) 배치:** 새 문제는 반드시 `regression` 분할에만 추가 (최종 검증용 `blind_holdout` 오염 금지).
5. **매니페스트 동결 및 회귀 실행:** 코디네이터가 `freeze`를 수행하여 무결성을 갱신하고 `run-regression`으로 회귀 검증.
