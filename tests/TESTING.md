# 테스트 정리 — practice · simulation

> 2026-09-27 · `feat/lena-persona` · 기존 38개 → **127개** (신규 89개, parametrize 케이스 포함), 전부 통과

테스트가 하나도 없던 **연습대화(practice)** 와 **시뮬레이션(simulation)** 을 세 경계(seam)에서 채웠다.
작업 중 버그 2개를 찾아 red → green 으로 고쳤다.

## 실행

```bash
uv run pytest            # 전체
uv run pytest tests/test_practice_service.py -v   # 파일 하나
```

`pyproject.toml` 에 `[tool.pytest.ini_options] pythonpath = ["."]` 를 추가했다.
전에는 `PYTHONPATH=.` 없이 돌리면 `No module named 'app'` 으로 전부 수집 실패했다.

## 세 가지 seam

| seam | 무엇을 검증하나 | 무엇을 가짜로 바꾸나 | 파일 |
|---|---|---|---|
| ① HTTP API | 요청 → 상태코드·응답 본문·SSE 형식, 커밋/롤백 여부 | service, DB 세션 (`dependency_overrides`) | `test_practice_api.py`, `test_simulation_api.py` |
| ② LLM 파싱·검증 | LLM 원문 → JSON 추출 → 스키마 검증 → 도메인 예외 | LLM 호출 경계만 (`_call`, `_get_client`) | `test_practice_agents.py`, `test_simulation_agents.py` |
| ③ 비즈니스 규칙 | 저장·폴백·롤백·대본 정리·점수 규칙 | LLM 에이전트만. DB 는 in-memory SQLite + 진짜 repository | `test_practice_service.py`, `test_simulation_service.py` |

원칙: 가짜는 **시스템 경계(LLM, HTTP 의존성)** 에만 둔다. service 테스트는 DB 를 직접 조회하지 않고
`service.get()` / `list_for()` 같은 공개 인터페이스로 결과를 확인한다.

공용 헬퍼는 `tests/conftest.py`:
- `with_db(scenario)` — 빈 SQLite DB 를 만들고 세션 팩토리를 넘긴다. 요청 하나 = 세션 하나로 흉내낼 수 있다.
- `seed_persona(db, ...)` — 온보딩이 끝나 페르소나가 저장된 상태. `confirmed=False` 면 확정 전 초안.

## 테스트 목록

### ① HTTP API (19개)

**practice** (`test_practice_api.py`, 9)
- 공백만 보낸 메시지 → 422 🐞
- 메시지 앞뒤 공백은 잘라서 service 로 넘김
- SSE 본문이 `start → delta → done` 형식 그대로
- 없는 세션 404 / 종료된 세션 409
- 스트림 도중 세션 종료 → `event: error`
- `/start` 201 + 커밋, 상대 없음 404 (커밋 안 함), 상대 참조 두 개 422

**simulation** (`test_simulation_api.py`, 10)
- 실행 201 + 커밋 (기본 10턴)
- 상대 미지정·두 개 지정 422, `turns` 2·16 은 422 (허용 3~15)
- 페르소나 없음 404 (커밋 안 함), LLM 실패 503 + 롤백
- 없는 시뮬레이션 404, 저장된 리포트 조회
- 목록은 `user_id`/`persona_id` 중 정확히 하나
- `/report/preview?use_llm=false` → 템플릿 서술

### ② LLM 파싱·검증 (21개)

**simulation** (`test_simulation_agents.py`, 16)
- LLM 응답에서 JSON 추출: 맨 JSON, ` ```json `, ` ```JSON `, 앞말 + 펜스, 앞뒤 설명문 🐞
- JSON 아닌 응답 → `LLMError`
- 대본 + 리포트 파싱, 모르는 키는 무시
- 요청 턴 수·토큰 예산(`3000 + 300×turns`)이 프롬프트에 반영
- LLM 에러 / JSON 아님 / 한 줄짜리 대본 / 모르는 화자 / 리포트 없음 → 모두 `SimulationFailed`
- ReportAgent: 서술 파싱, 대화록 없을 때 안내 문구, 헤드라인 60자 초과 → `LLMError`

**practice** (`test_practice_agents.py`, 5)
- 빈 조각(`None`, `""`, choices 없음)은 건너뛰고 텍스트만 스트리밍
- system 프롬프트가 맨 앞, 그다음 대화 이력
- 첫 인사면 지시문을 user 메시지로 붙임
- 프로바이더 예외(401 등) → `LLMError`
- 내 페르소나가 있을 때만 "민수님에 대해 참고할 것" 섹션

### ③ 비즈니스 규칙 (28개)

**practice** (`test_practice_service.py`, 9)
- 내 페르소나 없으면 닉네임 "회원", 있으면 온보딩 닉네임
- 미확정 초안은 상대로 못 고름 → `PersonaNotFound`
- 답변 스트리밍 후 내 메시지 + 상대 답변 둘 다 저장
- 첫 조각 전 LLM 실패 → 폴백 문장으로 대화 계속 (`source="fallback"`)
- 답변 도중 LLM 끊김 → `error` 이벤트, 아무것도 저장 안 됨
- 종료된 세션 → `SessionEnded`
- 빈 세션에서 opening → 상대가 먼저 인사
- 상대가 먼저 인사한 세션의 이력은 인사 지시문(user)으로 시작

**simulation** (`test_simulation_service.py`, 19)
- 대본 정리: 같은 화자 연속 → 합침 / a 이전 b 줄·빈 줄 버림 / 요청 턴 수 초과분 자름
- 차원 점수 규칙 4종 (similar·both_high·both_low·judged), 등급 경계 (70/45)
- 총점: 갈등·관계방향 가중치 1.5, 위험 조합마다 −8, 점수 없으면 50
- 리포트 LLM 실패 → 템플릿 폴백
- LLM 이 빼먹어도 위험 조합 caution 은 반드시 포함
- 실행: 대본·리포트 저장 후 목록·조회로 다시 꺼내짐
- 대본 밖을 가리키는 하이라이트는 버림
- a 줄이 없는 대본 → `SimulationFailed`, 아무것도 저장 안 됨
- 없는 상대 → `PersonaNotFound` (LLM 호출 전), 없는 시뮬레이션 → `SimulationNotFound`

## 찾아서 고친 버그 (red → green)

### 1. 공백만 있는 연습대화 메시지가 통과함
- **증상**: `{"message": "   "}` 가 `min_length=1` 을 통과 → 라우트에서 `.strip()` 후 빈 문자열이 DB 와 LLM 으로 감
- **RED**: `test_message_of_only_whitespace_is_rejected` — 200 이 나옴
- **수정**: `PracticeMessageRequest` 에 `str_strip_whitespace` → 자른 뒤 길이 검사라 422
- 파일: `app/features/practice/schemas.py`

### 2. LLM 이 JSON 을 조금만 다르게 감싸도 시뮬레이션 전체가 503
- **증상**: ` ```JSON ` (대문자) 펜스나 "결과입니다:" 같은 앞말이 붙으면 `invalid JSON` → `SimulationFailed` → 503
- **RED**: `test_json_is_extracted_from_common_llm_wrappings` — 5개 중 3개 실패
- **수정**: `_call_json` 이 정규식으로 펜스를 지우는 대신 첫 `{` ~ 마지막 `}` 를 잘라 파싱
- 파일: `app/features/simulation/agents.py`

## 테스트가 진짜로 실패할 수 있는지 확인 (mutation 점검)

이미 있던 코드에 붙인 테스트는 처음부터 초록이라, 코드를 일부러 망가뜨려 빨간불이 켜지는지 봤다.

| 일부러 망가뜨린 것 | 결과 |
|---|---|
| 폴백 `source` 표시, 인사 지시문 삽입, 기본 닉네임 | 잡힘 |
| 하이라이트 필터, 같은 화자 병합, 짧은 대본 실패, 갈등 가중치 | 잡힘 |
| 위험 caution 강제 포함 | 처음엔 **안 잡힘** → 테스트가 LLM 경로를 안 타고 있었음. 고친 뒤 잡힘 |
| 리포트 폴백, 빈 조각 필터, 스키마 검증, 503·409 상태코드 | 잡힘 |
| 스트림 중단 시 `db.rollback()` 삭제 | 안 잡힘 — 커밋 안 한 세션은 닫힐 때 어차피 버려져서 동작("저장 안 됨")이 같다. 방어 코드라 정상 |

## 후속으로 볼 것

1. ~~**persona 에도 같은 JSON 파싱 버그**~~ → 고침 (아래 "추가 수정" 참고)
2. **CI 가 pytest 를 안 돌린다** — `.github/workflows/ai-ci.yml` 은 lint·format·audit·compile 만 한다.
   `uv run pytest` 단계를 추가하면 이 테스트들이 PR 마다 돈다.
3. ~~**답변 도중 끊기면 내 메시지도 사라짐**~~ → 고침 (아래 "연습대화 답변 다시 받기" 참고)
4. **LLM 서술 필드 하나가 길면 시뮬레이션 전체가 503** — `ReportNarrative` 는 headline 60자, highlights 6개,
   quote 200자 등을 넘으면 검증 실패 → 대본까지 버린다. 넘친 값은 잘라서 받는 편이 사용자에게 낫다.

## 추가 수정 — persona 도 같은 JSON 파싱 버그 (red → green)

- **증상**: `app/features/persona/agents.py` 의 `_call_json` 도 `_FENCE` 정규식으로 소문자 ` ```json ` 만 지웠다.
  대문자 펜스나 앞말이 붙으면 파싱 실패 → 영향받는 곳 3군데:
  - 첫 턴 인사 → 템플릿 문장으로 대체 (LLM 인사가 버려짐)
  - 답변 태깅 → `None` 으로 **조용히** 태그 누락 → 페르소나 정확도 하락
  - `/build` 페르소나 생성 → 503
- **RED**: `tests/test_persona_agents.py` 에 7개 추가
  - `test_json_is_extracted_from_common_llm_wrappings` (5 케이스 중 3개 실패)
  - `test_non_json_reply_is_llm_error`
  - `test_tagging_keeps_tags_when_llm_wraps_json_in_uppercase_fence` — 로그에 `tagging failed: invalid JSON` 이 찍히며 `None`
- **수정**: simulation 과 같은 방식 (첫 `{` ~ 마지막 `}`). 안 쓰게 된 `_FENCE`, `import re` 삭제
- `app/CODE_GUIDE.md` 의 `_FENCE` 절과 `_call_json` 처리 순서 설명도 새 코드에 맞춰 고침

## 연습대화 — 내 메시지 보존 + 답변 다시 받기 (red → green)

- **바뀐 동작**: `stream_reply` 가 LLM 을 부르기 **전에** 내 메시지를 커밋한다. 답변이 도중에 끊기면 반 토막 답변만 버린다.
  새로고침해도 내 메시지는 보이고, `POST /v1/practice/{id}/retry` 로 메시지를 다시 보내지 않고 답변만 받는다.
- **RED → GREEN 순서**
  1. `test_llm_failure_mid_reply_keeps_my_message_and_drops_half_reply` — 기존 "아무것도 저장 안 됨" 기대를 바꿈
  2. `test_retry_answers_my_unanswered_message_without_resending_it` — `stream_retry` 추가
  3. `test_retry_without_unanswered_message_is_refused` — 빈 대화·이미 답한 대화는 `NothingToRetry` (LLM 호출 안 함)
  4. API: `/retry` 스트리밍, 다시 받을 게 없으면 409, 없는 세션 404, 종료된 세션 409

## 온보딩 — /build 추출 폴백 (red → green)

온보딩 답변은 지금처럼 계속 보관한다 (build·보강 재빌드의 입력이므로).

- **바뀐 동작**: `/build` 에서 추출 LLM 이 실패하면 503 대신 규칙 초안을 만든다 (`source: "fallback"`).
  - 자유 답변은 LLM 없이 못 읽으므로, 선택지로 답한 질문만 점수로 옮긴다: 관계 진지도 "진지하게 만날 사람" 80 · "편하게 알아가기" 25
    (`CHOICE_SCORES`, `app/features/persona/service.py`). 나머지 차원은 기본값 50 · 신뢰도 LOW, 서술·관심사는 비어 있다. (이후 `null` 로 바뀜 — 아래 "답변에 근거한 특성만" 절)
  - 다음 `/build` 호출은 LLM 추출을 다시 시도한다. 성공하면 새 버전(`source: "llm"`), 또 실패하면 같은 폴백 초안을 그대로 돌려준다(버전을 쌓지 않음).
  - 보강 문답 재빌드는 폴백하지 않는다 — LLM 초안을 기본값투성이로 덮지 않도록, 기존처럼 503 + 롤백.
- DB: `personas.source` 컬럼 추가 — 마이그레이션 `f6a7b8c9d0e1` (기존 행은 `llm`). upgrade/downgrade 로컬 확인함.
- **RED → GREEN 순서** (`tests/test_persona_build_fallback.py`)
  1. `test_build_finishes_with_rule_based_draft_when_llm_is_down` — `BuildFailed` 가 그대로 올라오던 것
  2. `test_next_build_retries_llm_and_replaces_fallback_draft` — 폴백 초안을 재사용만 하던 것
  3. `test_retry_while_llm_still_down_returns_same_fallback_draft` — 재시도마다 폴백 버전이 쌓이던 것
  4. 선택지 점수 규칙 3케이스, 보강 재빌드는 폴백 안 함, `/build` 응답에 `source` 포함
- 기존 테스트 2개의 가짜 레코드(`SimpleNamespace`)에 `source="llm"` 추가 — 실제 행에는 항상 있는 컬럼이라 테스트 데이터를 맞춤.

### mutation 점검 (이번 변경분 11개 모두 잡힘)
사용자 메시지 선커밋 제거 · retry 가드(service/api) 제거 · 선택지 점수/매칭 변경 · 첫 build 폴백 끔 · 폴백 초안 재시도 제거 ·
재시도 때 폴백 버전 쌓기 · 보강 재빌드도 폴백 · 폴백 표시 누락 · 응답에 source 누락

### 참고 — 이번 작업과 무관한 기존 불일치
`uv run alembic check` 가 모델과 DB 의 차이를 보고한다: `personas.feedback` 컬럼·`ix_personas_user_id_is_confirmed` 인덱스,
`practice_messages.user_id` 컬럼·인덱스가 DB 에는 있고 모델에는 없다. `source` 는 차이에 없다(모델·마이그레이션 일치). 손대지 않음.

## 온보딩 답변 — 짧은 답·빈 답·무관한 답 대응 (red → green)

`tests/test_persona_answer.py`

- **한 글자 답 허용**: 최소 2자 → 앞뒤 공백을 뺀 1자. `"네"` 는 받고, `" 네 "` 는 `"네"` 로 저장
- **422 + 안내 문구**: 빈 답·공백만·200자 초과는 422, `detail = {code, message}`. 질문은 소모하지 않아 바로 다시 보낼 수 있다
  - `answer_empty` — "조금 더 길게 입력해 주시면 페르소나를 더 정확하게 만들 수 있어요. 다시 답해 주세요."
  - `answer_too_long` — "답변은 200자 이내로 입력해 주세요. 다시 답해 주세요."
- **무관한 답(태깅 `off_topic`)**
  - 처음이면 같은 주제의 기본 질문으로 한 번 되묻는다 (`retry: true`, 턴·LLM 소모 없음, 답 저장 안 함)
  - 되물어도 무관하면 다음 질문으로 넘어가되 `answered` 에서 뺀다 (건너뛰기·끝내기 조건 3개에 안 들어감)
  - 태깅 LLM 실패(`None`)는 무관한 답으로 보지 않는다
- 되물었다는 표시는 대기 중인 턴의 `tags` 칸(`{"reasked": true}`)에 둔다 — 답이 오면 `record_answer` 가 덮어쓴다. 새 컬럼 없음
- mutation 점검 9개 모두 잡힘 (공백 자르기·빈 답/길이 검사·422·되묻기·1회 제한·개수 집계·retry 표시·되묻기 문구)

## 온보딩 답변 — 중복 전송 방지 (red → green)

`tests/test_persona_duplicate_answer.py`

- **재전송 (`turn_index`)**: `TurnResponse.turn_index` 를 `/answer` 요청에 그대로 보내면
  - 이미 지난 턴 → 저장·LLM 호출 없이 지금 질문(끝났으면 마무리)을 다시 돌려줌. 끝난 세션이어도 409 아님
  - 아직 안 온 턴 → 409 `turn_mismatch`
  - 안 보내면 기존과 같음 (호환)
- **동시 요청 (세션 잠금)**: `/answer`·`/skip`·`/finish` 가 `SELECT … FOR UPDATE NOWAIT` 로 세션을 잠금. 처리 중이면 기다리지 않고 409 `request_in_progress`
  - SQLite 는 FOR UPDATE 를 무시하므로 잠금 자체는 로컬 Postgres 로 확인: 두 번째 요청이 18ms 만에 거절, 첫 요청 커밋 뒤엔 정상 획득
  - Postgres 오류 코드(55P03) → `SessionBusy` 변환은 가짜 DB 오류로 테스트
- **마지막 안전장치**: `onboarding_turns (session_id, turn_index)` 유니크 제약 (마이그레이션 `i9d0e1f2a3b4`). 걸리면 롤백 + 409 `request_in_progress`
- mutation 점검 11개 모두 잡힘

## 온보딩 — 답변에 근거한 특성만 · MBTI 말투 · 모름은 null (red → green)

합의한 seam 네 곳에서만 테스트한다: `/build` 결과(`build_draft`), 페르소나 불러오기(`load_persona`),
프로필 문장(`describe`), 궁합 계산(`build_report`). 가짜는 LLM 경계(`_call`, 추출·태깅 에이전트)에만 둔다.

- **답변에 근거한 차원만** (`tests/test_persona_build_fallback.py`)
  - 질문이 겨눈 차원(`topic.covers`)이 아니라 태깅이 **그 답변**에서 짚은 차원(`tags.primary`)만 인정
  - 온보딩 중 태깅이 실패한 답은 `/build` 때 다시 태깅, 또 실패하면 근거로 치지 않음
  - 목록 밖 점수는 `null`, 텍스트 항목은 빈 목록, 근거 없는 영역의 요약 카드는 버림. 추출 LLM 에도 "직접 답한 항목"을 알려줌
  - 다시 태깅해서 확인된 답은 신뢰도 계산에서 근거 1건으로 친다 (🐞 LOW 로 남던 것)
- **MBTI** — 온보딩 결과·궁합 점수엔 영향 없음, `describe` 에 말투 힌트(E/I·F/T·J/P)만. 초안·옛 확정 행은 세션 MBTI 로 채움
- **모름은 null** — `DEFAULT_SCORE` 제거. 옛 행의 "신뢰도 LOW 인 50"은 읽을 때 `null`.
  궁합에서 한쪽이라도 `null` 인 차원은 빠진다 (🐞 50 대 50 이 '완전 일치 100'으로 계산되던 것)
- **없는 과거 언급** (`tests/test_persona_agents.py`) — 대화 프롬프트의 "아까 러닝 얘기" 예시 제거.
  "저번에/지난번에 + 말씀·얘기·하셨잖·하신·뵀" 발화는 시드 질문으로 대체. "저번 주말엔 뭐 하셨어요?"·"예전에 봤던 영화" 는 통과
- 구현이 먼저 있던 조각은 코드를 잠깐 망가뜨려 테스트가 빨개지는지 확인하고 되돌렸다 (mutation 점검).
  구현 문구에 묶인 테스트(프롬프트에 "러닝"이 없는지)와, 되돌려도 실패하지 않은 테스트(모름으로 위험 조합이 안 걸리는지)는 지웠다

## 온보딩 — 페르소나 서술의 추측성 문장 (red → green)

`tests/test_persona_build_fallback.py` · seam: `/build` 결과 (`build_draft`, 추출 에이전트만 가짜)

- 서술 본문에서 짐작 어미(`겠네요`·`수 있겠`·`수도 있겠`·`수도 있어요`·`것 같아요`·`것 같네요`·`듯해요`·`듯합니다`) 문장만 뺀다
- 특징(traits)도 같은 기준으로 뺀다
- 한 줄 요약이 짐작이거나 본문이 전부 짐작이면 서술을 버린다 (점수와 모순될 때와 같은 처리)
- "천천히 알아가면 좋겠어요"처럼 사용자의 바람을 옮긴 문장은 남긴다 — mutation: 맨 `겠어요`를 넣으면 잡힘
- 추출 프롬프트: 문장마다 사용자가 한 말을 옮겨 쓰기, 짐작·대화체 반응 금지, 답이 적으면 문장 수를 줄이기

## 시뮬레이션 — 화자 검사 오탐 · 공급자 오류 503 (red → green)

`tests/test_simulation_agents.py` · seam: `SimulationAgent.run`, `_call` (LLM 호출·HTTP 클라이언트만 가짜)

운영 로그(2026-09-30 11:05~11:06) 재현 — 정상 대본을 오탐으로 버리고, 재생성이 공급자 오류로 끊겨 503.

- 닉네임이 서로를 포함할 때(a=셰일, b=내가진짜셰일) "내가진짜셰일님"을 자기 호칭으로 보지 않는다 — 이름 앞에 글자가 붙으면 제외
- `finish_reason=error`(HTTP 200 이지만 생성 도중 끊김)는 파싱하지 않고 `upstream_error`
- 화자 뒤바뀜으로 다시 받다가 실패(공급자 오류·깨진 JSON)하면 503 대신 1차 대본을 쓴다
- 세 조각 모두 테스트를 먼저 쓰고 실패를 확인한 뒤 구현

## 온보딩 — 첫 턴 타임아웃 · 폴백 인사 문구 (red → green)

`tests/test_persona_agents.py` · seam: 온보딩 발화 생성 (`ConversationAgent.generate`, LLM 호출만 가짜)

- 운영에서 `persona first turn fell back to template` 반복 (#82) — 첫 턴(5개 항목 JSON, 450토큰)이 일반 턴과 같은 2.5초 제한
- 첫 턴 전용 `onboarding_first_turn_timeout_s` 8.0 — 첫 턴 생성과 첫 턴 가드레일 재생성에 적용 (설정이 없어 먼저 실패 확인)
- 폴백 인사 문구: "알아가고 싶어서 가볍게 몇 가지 여쭤보려고요." / "○○님 얘기도 편하게 들려주세요." — segment 순서·타입은 유지 (기존 문구로 먼저 실패 확인)
- LLM 첫 턴 지시 5단계도 같은 방향으로 ("답해 주세요" 같은 설문 말투 금지)
