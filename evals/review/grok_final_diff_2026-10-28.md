# Grok 4.7 최종 독립 검증 (2026-10-28)

task `task_3c0c9180ce3b` / dispatch `ctx_4933104f606d` / run `run_76fb4de54e1c`.

이 문서는 수정 후 하네스를 읽기 전용으로 다시 본 결과다. 새 재판정과 6단계 기준선은 실행하지 않았다. Claude 주간 한도, Postgres.app 인증, f06 계약 선택은 그대로 막혀 있다. 제품 평가 통과나 배포 게이트 통과로 읽으면 안 된다.

코디네이터 마감 회신: 환경 작업은 13:52:17 KST에 완료·release 되었고, 아래 환경 파일 4개의 해시가 그 완료본이다. 이 워커는 그 네 파일을 수정하지 않았다. 공식 freeze는 동시 변경으로 `INVALIDATED_BY_CONCURRENT_CHANGE`다. 이 검토 완료는 코드 승인, 배포 승인, 보정 완료가 아니다.

## 스냅샷

시각: 2026-10-28 14:10:56 KST. HEAD `b5e158c`. sha256 앞 16자.

| 파일 | sha256-16 | mtime |
| --- | --- | --- |
| `evals/_harness/stage5_labels.py` | `25baed1e51de42fe` | 13:53:41 |
| `evals/_harness/stage5_rejudge.py` | `1317b8d698bfba54` | 13:35:51 |
| `evals/_harness/stage5_supplement.py` | `7b07279aa197da49` | 13:35:51 |
| `evals/_harness/stage5_judges.py` | `91a66b28af5663ea` | 13:57:52 |
| `evals/_harness/stage5_run.py` | `0d6f33aac3059983` | 13:48:15 (환경 완료본) |
| `evals/_harness/stage6_cli.py` | `b7573a5fd31297e2` | 13:48:19 (환경 완료본) |
| `evals/_harness/stage6_preflight.py` | `f575ce91eaadf99d` | 13:50:31 (환경 완료본) |
| `evals/_harness/stage6_scorecard.py` | `24b341f1abfb2f41` | 13:34:58 |
| `evals/_harness/stage6_manifest.py` | `6535db8b6d23a788` | 13:32:09 |
| `tests/evals/test_stage5_labels_integrity.py` | `f3b5e36a575c0218` | 13:55:36 |
| `tests/evals/test_stage6_environment.py` | `d09f67f9a2f0ecbd` | 13:50:31 (환경 완료본) |
| `evals/manifest_b5e158c.json` | `afebec049e73e253` | 13:57:57 (검증 시각의 파일. 14:11:34에 다른 주체가 `d9236809883a87b2`로 바꿈) |

매니페스트 `frozen_at` `2026-10-28T13:57:57`, `git_commit` `b5e158c`, `combined_hash` `427b53d9e57e…`, 파일 254, 골드셋 케이스 720. 구성은 app `*.py` 132, 하네스 `*.py` 38, goldset 18, `calibration_final.csv` 18, supplement 경로 7, `docs/성능평가_v2` 1, 나머지 Judge 템플릿.

이전 검토 `evals/review/grok_remaining_work_2026-10-28.md`와 원 요청 `evals/EVAL_PROCESS_2026-10-28.md`를 기준으로 봤다. Sonnet 결과 위치는 `evals/SONNET_RESULTS_2026-10-28.md`다. 그 문서는 저장된 calibration(191행, 477판정, 모델 `claude-sonnet-5-5`)을 완료로 적고, 새 판정은 추가하지 않았다고 적는다. `FEATURE_EVAL_STATUS.md`와 `BASELINE_EXECUTION_2026-10-28.md`도 새 기준선을 미실행으로 둔다.

## 이 턴에서 실제로 통과한 확인

재현은 `/tmp`와 몽키패치뿐이고, 저장소 결과·골드셋·라벨·EVAL.md 는 쓰지 않았다. `R.J.call`은 `RuntimeError("LLM_BLOCKED")`로 막거나 횟수만 세었다. `_psql`은 실패 튜플을 반환하게 바꿔 실제 `psql`을 열지 않았다.

- dry-run `plan_rejudge`는 파일을 만들지 않았다. 계획의 `dry_run`는 true.
- 케이스 없음, 빈 템플릿, 실행 오류로 출력이 둘 다 없는 경우, 6000자 초과는 호출 0회로 `ERROR`였다. 잘림 스냅샷의 `trunc`는 true.
- f03 입력에 답이 없어 문맥이 `대화 내역 누락`을 포함하면 호출 0회, `REQUIRED_CONTEXT_MISSING`.
- 존재하지 않는 대상과 이미 있는 대상 경로는 `ValueError` / `FileExistsError`. 다만 아래 높음 항목처럼 판정 루프 뒤에서 거절한다.
- 할 일이 0건인 재판정은 호출 0회로 `/tmp`에 새 파일을 썼다. 기존 파일을 덮어쓰지는 않았다.
- `calib()`는 f10/f11 보고서를 만들지 않았다. 저장 결과의 Judge 행 통과(✅)는 0건. f09 F-01 행은 `QUARANTINE_LABEL_CONFLICT`. 기계 ERROR를 `unvalidated_count`로 넘기면 `UNKNOWN/NOT_VALIDATED`.
- 보충 fixture 36건, 유형 9개, 유형마다 FAIL 2 / PASS 2. 경로 `evals/f08-v2-memory-extract/calibration_supplement.jsonl`. 36개 프롬프트의 `oracle_full`은 `(공통 판정 기준만 적용)`. `description`과 `fail_if`는 프롬프트에 0건.
- 가짜 URL `…/ktb` 사전검사는 실패하고, 출력에 `user:pass`와 호스트가 없었다. `CONNECT_STATUS`는 `NOT_CONNECTED_PREFLIGHT_ONLY`.
- 빈 매니페스트 `{}`는 필수 필드 누락으로 거절. 현재 `verify_manifest`는 불일치 6건으로 실패한다.
- 실제 f01 최신 결과 `evals/f01-persona-conversation/results/20261026-2248_cb53097-dirty_calibration.json`의 게이트는 Gate0 UNKNOWN, Gate1 FAIL(CRIT 9), Gate2 UNKNOWN, Gate3 UNKNOWN. `passed`인 게이트는 없다. provenance와 `manifest_hash`는 없다.
- `tests/evals`: `.venv` pytest, `postgresql+asyncpg://invalid:invalid@127.0.0.1:1/ktb_eval`, Langfuse 호스트 `127.0.0.1:9`. `test_rejudge_creates_unique_timestamped_file` 제외. 61 passed, 1 deselected, 5.13초. 같은 파일 집합 ruff check 통과. 패킷 캡처는 하지 않았다.
- 골드셋, `calibration_final.csv`, `results/`는 `git diff --stat`이 비었다. 이 워커가 쓴 저장소 파일은 이 문서뿐이다.

## 남아 있는 문제

### 높음 — 필수 문맥이 없어도 Judge 호출에 도달

경로: `evals/_harness/stage5_judges.py` `context` (f03 답변이 있으면 `질문 본문 누락` fallback, f06/f12는 DB 실패 시 `DB 미연결` 문구). `evals/_harness/stage5_rejudge.py` `evaluate_single_judge` 179행은 f03이고 입력에 `대화 내역 누락`이 있을 때만 호출을 막는다. `find_lf_trace`는 플래그만 남긴다.

재현: `_psql`을 실패로 고정한 뒤 `evaluate_single_judge`.

- f03에 사용자 답 1개: 차단 문구 없음, `LLM_BLOCKED` 1회.
- f03에 답 없음: 호출 0, `REQUIRED_CONTEXT_MISSING`.
- f06 `F-01`, f12 `F-04`: 문맥은 미연결 문구, 호출 각 1회.

과거 캐시가 없다는 사실만으로 f06/f12를 멈추지는 않는다.

### 높음 — 호출이 없는 재판정이 전 유형을 active로 찍는다

경로: `stage5_rejudge.py` 331–339행. `rejudge_provenance`가 없으면 강제 유형이 아니어도 `status: active`와 현재 프롬프트 해시를 기록하고 새 파일을 쓴다. `stage5_labels.py` 342–350행은 `historical_unmodified`가 아니고 해시가 맞으면 provenance 일치로 본다. 그 상태에서 양쪽 표본이 맞고 결측이 없으면 `calib_metric_status`가 ✅를 반환할 수 있다.

재현: f01 Judge 6유형이 이미 PASS인 합성 결과를 `/tmp`에 두고 `rejudge_result_file`. 호출 0, todo 0, 새 파일, 6유형 모두 `active`.

저장 f01은 12행, Judge 집계 PASS 46 / FAIL 26 / ERROR 0, provenance 없음. 같은 조건으로 실제 결과 디렉터리에서 돌리면 모델 호출 없이 최신 파일이 바뀌고, 이어서 `calib()`가 옛 판정을 재검증된 것으로 볼 수 있다. 이 검증은 그 실행을 하지 않았다. 지금 저장본을 `calib(write_eval_md=False)`로 돌렸을 때 Judge ✅는 0건이다. 코드 채점 20행은 ✅다. 코드 유형은 351–353행에서 provenance를 참에 고정한다.

### 높음 — 덮어쓰기 거절이 판정 호출 뒤에 있다

경로: `stage5_rejudge.py`. 282–310행에서 `evaluate_single_judge`를 돌린 다음 318–324행에서 같은 경로·기존 파일을 거절한다.

재현: f01 한 행의 `judge`가 비어 할 일이 생기고, `out_path`가 이미 있는 `/tmp` 파일. 모킹된 `call` 6회 후 `FileExistsError`. `tests/evals/test_stage5_calibration.py`의 거절 테스트는 `rows: []`라 이 순서를 보지 않는다.

### 높음 — 기대값·힌트·예시가 Judge 입력에 남는다

경로: `stage5_judges.py` `oracle_text` 286–298행. `expect`가 있으면 `기대값:`으로 붙이고, `case_specific` 중 `실제로 있었다` 또는 `한 줄뿐` 문장을 남긴다. `render`는 FAIL/PASS 예시를 템플릿에 넣는다.

집계: f10/f11을 뺀 골드셋 640건 중 `expect`가 있어 `oracle_text`에 나가는 케이스 439건, 힌트 문장이 있는 케이스 6건. 합성 케이스에 `EXPECT_LEAK_MARKER`를 넣으면 반환 문자열에 그대로 있었다.

f09 `evals/f09-v2-memory-reconcile/judges/F-01.md`는 `DELETE하고 새 활동을 ADD하면` Fail이라는 보충 문장과 예시 `mem-08-b`, `mem-05-b`를 같이 가진다. 다섯 Judge 파일은 매니페스트 시각 13:57:57 이후인 13:58:07에 바뀌었고 재판정되지 않았다.

- `evals/f01-persona-conversation/judges/F-03.md`
- `evals/f03-persona-extraction/judges/F-03.md`
- `evals/f09-v2-memory-reconcile/judges/F-01.md`
- `evals/f12-simulation-v2-generate/judges/F-05.md`
- `evals/f13-simulation-v2-regenerate/judges/F-04.md`

조립된 최종 프롬프트 본문은 스냅샷에 없다. 잘림 경로 스냅샷 키는 `input_full`, `output_full`, `oracle_full`, `template_hash`, `final_prompt_hash`(16자)다. `final_prompt` 텍스트 키는 없다. 해시는 잘린 채움 기준이다.

### 높음 — Gate 0은 옛 버전으로도 통과할 수 있고, 제품 CRIT만으로는 Gate 1이 실패가 아니다

경로: `evals/_harness/stage6_scorecard.py`. CRIT 목록은 `S.D`의 sev에 `CRIT`이 들어 있는 유형이다. f01은 F-08, F-09. Gate 1–3의 `passed`는 코드에서 true로 바뀌지 않는다. Gate 0은 골드셋 ID·split이 맞고 결과 `manifest_hash`가 넘긴 매니페스트 `combined_hash`와 같으면 PASS다. 버전 문자열은 보지 않는다. CRIT 건수는 `verdict`의 FAIL만 센다. `product_verdict`는 세지 않는다. `raw_verdict`가 빈 객체면 `all(...)`이 참이라 원문 통과율이 100%가 된다.

재현: `/tmp`에 f01 정의로 케이스 1건, `version` `cb53097-dirty`, `manifest_hash` `abc123`, 혼합 `verdict`는 전부 PASS, `raw_verdict` `{}`, `product_verdict` F-08 FAIL.

결과: Gate0 `(true, PASS)`, Gate1 `(false, UNKNOWN)`, crit 0, raw `100.0%`, prod `0.0%`. 게이트 하나가 통과했다. 불완전·NA·ERROR만으로 Gate 1–3이 PASS가 되는 경로는 이 소스에 없다. 제품 CRIT은 FAIL로 내려가지 않고 UNKNOWN에 남는다.

실제 f01 저장 결과는 위 통과 확인대로 어떤 게이트도 PASS가 아니다.

### 중간 — 실행기와 CLI의 막음이 경로마다 다르다

`stage5_run.py` `judge_score` 1077–1083행은 `J.cut(..., 6000)` 뒤 `J.call`이다. `cut_with_flag`가 없다. 이 파일을 직접 실행하면 CLI 사전검사를 타지 않는다. 이번 검증은 `main`을 실행하지 않았다.

`stage6_cli.py` `cmd_run_regression` / `cmd_run_holdout`은 `_verify_preconditions_before_run`이 거짓이면 자식 프로세스를 띄우지 않는다. 홀드아웃은 `--allow-holdout`이 없으면 1로 끝난다. 현재 트리에서는 매니페스트 검증이 실패하므로 이 경로의 실제 실행은 막힌다.

`cmd_dry_run`은 매니페스트 불일치를 `[WARN]`으로 찍고 0을 반환할 수 있다. dry-run 성공은 매니페스트 일치를 뜻하지 않는다. `cmd_scorecard`는 게이트가 실패해도 0을 반환한다.

`stage6_preflight.py` `build_eval_env`: 프로세스에 `DATABASE_URL`이 이미 있으면 path를 바꾸지 않는다. 없는 경우에만 `.env` URL의 path를 `/ktb_eval`로 바꾼다. 이 셸에는 `DATABASE_URL`이 없었다. 그 경우 path는 `/ktb_eval`이 되고 userinfo가 있다. CLI 사전검사는 그 바뀐 환경을 보므로, 원본 DB 이름이 `ktb`라는 이유만으로 막히지는 않는다. 연결 소켓은 열리지 않는다. 매니페스트를 다시 얼리면 `git_commit`이 `b5e158c`인 채로 검증이 통과할 수 있고, 그때 CLI는 연결 확인 없이 `stage5_run.py`를 시작할 수 있다.

`J.call`의 예외 처리는 `TimeoutExpired`와 `JSONDecodeError`다. `FileNotFoundError` 문자열은 `folder()`에만 있다. `claude`가 없으면 ERROR 판정이 아니라 예외다. 이번 턴에는 그 바이너리를 부르지 않았다.

### 중간 — 보충 36건은 분리 표시가 약하고, dry-run이 아닌 실행은 모델을 부른다

경로: `evals/_harness/stage5_supplement.py`. `validate_case`는 `not_a_model_output`과 `split`을 요구하지 않는다. 36건 모두 두 필드가 없다. `is_synthetic`과 `fixture_category=judge_calibration_supplement`는 있다. 권고 144건은 없다.

`run_evaluation(dry_run=False)`는 `J.call`을 제출한다.  docstring과 달리 호출 하드 블록은 없다. 누출 경고는 기록만 한다. 이번 검증의 프롬프트 조립은 dry-run에 해당하는 `build_judge_prompt_for_case`만 호출했다.

대조 의도는 유형별 FAIL 2 / PASS 2와 `draft_expected`가 Judge 프롬프트에 직접 실리지 않는 데까지 확인했다. 기대 정답과 모델 출력을 맞춘 판정 실험은 모델을 부르므로 하지 않았다.

### 중간 — 매니페스트는 지금 실패하고, 실패 이유는 다섯 Judge 파일이다

`verify_manifest(evals/manifest_b5e158c.json)`: 실패, 6건. 해시 불일치 5(위 Judge md), `combined_hash` 1. HEAD 불일치와 신규 파일 항목은 이 시각에 없다. 13:57:57 동결이 하네스 변경을 이미 포함한 상태로 보인다. 빈 구조는 거절된다. `git rev-parse`가 빈 문자열이면 HEAD 비교 조건이 건너뛰어지는데, 지금은 HEAD가 있어 그 분기를 타지 않았다.

### 중간 — 재judge와 패키지 import는 서로 다른 모듈 객체다

`import evals._harness.stage5_rejudge`가 잡는 `stage5_judges`와 `import evals._harness.stage5_judges`는 같은 경로의 다른 모듈 객체다. 이번 측정에서 `R.J is package J`는 false. 패키지 객체만 패치하면 재judge는 실제 `call`로 간다.

이 워커의 pytest는 그 위험을 피하려고 `test_rejudge_creates_unique_timestamped_file`을 제외했다. 결과는 61 passed, 1 deselected, 5.13초다. 그 실행이 읽은 테스트는 14:00 해시 `1659f1b06236` 쪽이다.

14:07:49에 `tests/evals/test_stage5_calibration.py`가 `b5a10564d88d40bd`로 바뀌었다. 이 워커가 고친 파일이 아니다. 현재 154행과 202행은 `monkeypatch.setattr(R.J, "call", ...)`이다. 코디네이터는 root가 그 2줄을 고친 뒤 외부 `claude`/`grok`/`psql`/`codex`/`agy`를 막고 62 passed, 3.41초, 실제 CLI 시도 0, ruff 통과를 확인했다고 회신했다. 그 62회 실행은 이 워커의 측정이 아니다.

### 낮음 — 집계 도우미의 기본 인자와 저장된 결과 드리프트

`calib_metric_status(1, 0, 1, 0)`은 여전히 `✅ 통과`다. `tests/evals/test_stage5_labels_integrity.py`가 인자 4개 호출의 ✅를 기대값으로 고정한다. `calib()` 루프는 `unvalidated_count`를 넘기므로, 저장 결과의 재현 버그(ERROR를 분모에서 빼고 ✅)는 이 스냅샷의 `calib()`에서 재발하지 않았다.

`sheet()`는 `oracle_text`를 `calibration_template.csv`의 정답 기준 칸에 쓴다. 이번 턴에 실행하지 않았다.

`evals/_harness/runs/backup_pre_calib1`과 현재 `results/`는 같은 이름 18개 중 9개가 바이트가 같다. 다른 9개는 f01, f03, f06, f08, f09, f12, f13, f17, f18이다. f01은 양쪽 모두 `cb53097-dirty`, `run_at` `2026-10-26T22:54:43`, 12행이고 verdict가 다른 행이 9개다. f09는 4행, f12는 10행이 다르다. 이 세션은 그 파일을 쓰지 않았다. 백업이 이전 사본으로 남아 있다.

스코어카드의 잘림 표시는 `sample.truncated`, `finish_reason`, 출력 접미사를 본다. 행의 `truncation_warnings`만 있고 `verdict`가 PASS면 불완전으로 세지 않는다. 재judge의 잘림 경로는 verdict를 ERROR로 넣는다.

보정 ✅의 0.85는 `calib_metric_status` 안에 있는 기존 일치 기준이다. 스코어카드 게이트 숫자 임계값은 없다.

## 보존

사용자·다른 작업의 변경은 되돌리지 않았다. 보이는 변경에는 `.gitignore` 밖 평가 문서, 하네스, Judge md, EVAL.md, 보충 jsonl, 매니페스트, `tests/evals/`, 루트의 계획 파일이 포함된다. 골드셋과 `calibration_final.csv`와 결과 JSON은 diff가 없다. `calib`는 `write_eval_md=False`로만 계산했다.

## 테스트와 호출

- pytest: `PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/pyc-grok DATABASE_URL=postgresql+asyncpg://invalid:invalid@127.0.0.1:1/ktb_eval LANGFUSE_TRACING_ENVIRONMENT=eval-local LANGFUSE_HOST=http://127.0.0.1:9` 와 더미 Langfuse 키, 빈 `SENTRY_DSN`. 명령은 `.venv/bin/pytest tests/evals -q --tb=line -p no:cacheprovider -k 'not test_rejudge_creates_unique_timestamped_file'`. 결과 61 passed, 1 deselected, 5.13s.
- ruff: `.venv/bin/ruff check` 대상은 stage5 labels/rejudge/supplement/judges/run, stage6 cli/preflight/scorecard/manifest, `tests/evals`. `All checks passed!` `--fix`는 하지 않았다.
- 하지 않은 호출: 실제 `claude`, 보충 `run`, `stage5_run` 메인, 평가 DB 연결, 홀드아웃, 매니페스트 freeze.

`tests/evals` 일부는 `stage5_run`을 import한다. import 시 DB path를 `/ktb_eval`로 확인하고 `app.main`을 읽는다. 더미 URL의 path가 `/ktb_eval`이라 그 검사는 통과했다. `create_async_engine`은 체크아웃 전에 접속하지 않는 사용이다. 5.13초 안에 끝났고 연결 오류로 실패한 테스트는 없다. 외부로 패킷이 나가지 않았다는 증명은 없다.

## 본인 변경 여부

이 워커는 `evals/_harness/stage5_judges.py`를 변경하지 않았다. 관측 해시는 `91a66b28af5663ea`, mtime은 13:57:52다. 아래 Judge 프롬프트 5개도 변경하지 않았고 되돌리지도 않았다. mtime은 모두 13:58:07이다.

- `evals/f01-persona-conversation/judges/F-03.md`
- `evals/f03-persona-extraction/judges/F-03.md`
- `evals/f09-v2-memory-reconcile/judges/F-01.md`
- `evals/f12-simulation-v2-generate/judges/F-05.md`
- `evals/f13-simulation-v2-regenerate/judges/F-04.md`

저장소에서 이 워커가 쓴 파일은 이 검토 문서뿐이다.

## 이 시점에서 검증하지 않은 것

- 14:11:34에 바뀐 매니페스트 `d9236809883a87b2`는 다시 열지 않았다. 14:10에 본 `afebec049e73e253`만 검증했고 그때 실패했다. 공식 freeze 성공이 아니다.
- root가 알린 62 passed, 3.41초, 외부 CLI 0회는 이 워커가 다시 실행하지 않았다. 그 전의 테스트는 패키지 `J.call`을 패치했다. 재judge가 쓰는 객체는 그와 다르고, 이번 측정에서 `R.J is package J`는 false였다. 그래서 수정 전 62 통과는 재judge의 실제 호출 차단을 증명하지 못한다. 14:07:49 이후 파일은 `R.J.call`을 패치한다. 그 상태의 62 통과는 root의 확인이다.
- 실제 LLM 판정과 DB 연결은 하지 않았다. 패킷이 나가지 않았다는 증명은 없다.
- source seal 대기는 여기서 끝낸다. 환경 파일 4개의 해시는 13:48–13:50 관측값이고, 코디네이터는 그 값을 13:52:17 KST 완료본으로 지정했다.

## 이 워커가 수정한 것

수정한 저장소 파일은 이 문서 `evals/review/grok_final_diff_2026-10-28.md` 하나다. 소스, 테스트, 매니페스트, 골드셋, 라벨, 결과 JSON, Judge md, EVAL.md 는 쓰지 않았고 되돌리지도 않았다.

`stage5_judges.py` 13:57:52와 Judge 5개 13:58:07 변경, 그리고 14:11:34 매니페스트 교체(`d9236809883a87b2`)는 이 워커의 작업이 아니다. 14:10 검증은 그때의 매니페스트 `afebec049e73e253`에 대해 실패 6건(Judge md 5, combined hash 1)이었다. `stage5_judges.py`는 그 매니페스트와 해시가 맞았다. 다섯 템플릿이 동결 시각 13:57:57 이후라 공식 freeze는 코디네이터 표기 `INVALIDATED_BY_CONCURRENT_CHANGE`다. 바뀐 매니페스트는 다시 검증하지 않았다.

## 막힌 것과 이 문서가 아닌 것

새 Sonnet 재판정, 회귀·홀드아웃 기준선, 배포 게이트 통과는 미실행이다. 재개 시각으로 문서가 적은 값은 2026-10-30 19:00 Asia/Seoul이다. f06 허락 전 반말 기준은 `FEATURE_EVAL_STATUS.md`에 계약 선택 대기로 남아 있다. fixture 144건 권고는 하지 않았다.

환경 파일 4개는 코디네이터 회신 기준으로 13:52:17 KST 완료본이다. CLI·사전검사·`judge_score` 서술은 그 해시와 14:10:56 KST에 읽은 내용이다. 위에 적은 결함은 마감 후에도 남아 있다.

식별자: task `task_3c0c9180ce3b`, dispatch `ctx_4933104f606d`.
