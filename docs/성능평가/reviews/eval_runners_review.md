# Eval Runner 5종 무결성 검증

작성일: 2026년 10월 08일  
검토 대상:

- `scripts/eval_f2_tagging.py`
- `scripts/eval_f3_extraction.py`
- `scripts/eval_f4_practice.py`
- `scripts/eval_f5_simulation_line.py`
- `scripts/eval_f6_simulation_narrative.py`

대조 기준:

- `docs/성능평가/00_평가체계_및_공통가이드라인.md` (파일 머리의 버전은 v1.0.0)
- `docs/성능평가/02_F2_온보딩_태깅_평가기준서.md` ~ `06_F6_시뮬_리포트_서술_평가기준서.md` (v1.1.0)

대조 데이터: `evals/quality_datasets/` 의 F2~F6 파일.  
실행: 프로젝트 `.venv` 에서 모의 모드 기본 실행, `--live` 스텁 확인, 채점 함수에 넣은 적대 입력. F2 `--live` 는 네트워크 호출이라 실행하지 않았다.  
범위: 이 보고서만 추가했다. 러너, 기준서, 데이터셋은 수정하지 않았다.

판정: 다섯 러너는 기준서의 치명 불변식(C1~C6), 스키마 유효성, 점수 불변성, 게이트 1/2/3을 그대로 구현하지 않는다. 모의 모드 기본 실행은 다섯 기능 모두 `gate1_passed`, `gate2_passed`, `gate3_passed` 를 true 로 찍는다. 그 세 불린은 모델 출력에 대한 측정이 아니다. 이 러너의 통과 표시로 배포 게이트를 열 수 없다.

---

## 1. 한눈에 보는 판정

| 러너 | 모집단 | 모의 기본 실행 | 치명 코드 | 게이트 1/2/3 | 모델/제품 분리 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| F2 | 회귀 라벨 47건. 기준서 산식은 43건 | 47/47, 게이트 3개 true | `scores` 키만 C4. `score` 키와 유령 차원은 게이트 1 밖 | 85% 집합 일치와 파싱 0건이 없음 | 라이브 경로는 raw JSON. 제품 점수는 없음 |
| F3 | 회귀 36건(언어 18 + 코드 18). 기준서는 언어 18건 | 36/36, 게이트 3개 true | 생략 목록의 숫자 점수는 C4로 잡힘. 유형명·복창은 통과 | 범위 80%와 폴백 5%가 케이스 통과율로 섞임 | 라이브는 호출 전에 종료. 제품 null 지도는 안 봄 |
| F4 | 회귀 언어에서 구계약 제외 후 22건. 기준서와 같음 | 22/22, 게이트 3개 true | C3 키워드, 누출 4문자열, 3인칭만 치명. C1·C2·20자 복창은 통과 | 1/3/5 평균 대신 케이스 80% | 라이브 인자가 `PartnerAgent.reply` 와 달라 호출 불가 |
| F5 | 3왕복 12대화 72줄. 초안·원샷 36건은 안 읽음 | 72/72, 게이트 3개 true | 작별·반복·금지어·C3 일부. C1·C6·자칭은 통과 | 기술 실패 상한 10%만 숫자 일치. 품질은 80% | 라이브는 호출 전에 종료. 이름표는 역할 글자로 벗김 |
| F6 | 기본값 24건 전부(회귀 14, 보정 4, 홀드아웃 6) | 24/24, 게이트 3개 true | 인덱스가 유효할 때의 가짜 인용만 치명. 점수 모순·인물 전도는 통과 | 스키마 실패가 게이트 1. 기준서는 폴백(게이트 2) | 라이브는 호출 전에 종료. `precomputed_scores` 를 안 읽음 |

---

## 2. 공통 게이트 공식이 기준서와 다른 이유

F4·F5·F6 기준서는 게이트를 세 층으로 나눈다. F2·F3는 이름을 붙이지 않고 같은 층을 "사전 차단"과 "최종 합격"으로 적는다. 다섯 러너는 아래 한 공식을 공유한다.

- 게이트 1: 치명으로 표시한 케이스 수가 0
- 게이트 2: `error` 가 있는 케이스 비율이 5% 이하 (F5만 10%)
- 게이트 3: `passed` 비율이 80% 이상

기준서의 세 층은 다음과 같다.

| 층 | 기준서가 요구하는 것 | 러너가 세는 것 |
| :--- | :--- | :--- |
| 게이트 1 | 해당 기능의 C-code와 기능 치명이 모델 원문에서 0건 | 러너가 `fatalErrors` 또는 `isFatalC4` 로 올린 항목만 0건 |
| 게이트 2 | 폴백·`text=None`·타임아웃을 사유별로 집계. F4는 `medium` 과 `source=fallback`, F6는 `narrative_source=template` 와 타임아웃 0% | 예외 문자열이나 빈 출력이 있으면 한 건. 사유 구분 없음 |
| 게이트 3 | 축별 1/3/5 평균 3.0 이상, 1점 0건. F2만 자동 집합 일치 85%를 함께 요구 | 키워드·범위 경고가 없는 케이스 비율 80% |

00 가이드라인 2절은 결과표에 `GUARDRAIL_MODE`(`shadow` / `enforce` / `off`)를 적게 한다. 다섯 요약 JSON 어디에도 이 필드가 없다. 모델 점수와 제품 점수를 따로 담는 칸도 없다. 00 2절은 두 점수를 합치거나 평균 내지 말라고 한다. 러너는 점수를 하나 만들고, 그 하나의 통과 비율로 게이트 3을 닫는다.

모의 모드는 기대 출력이나 고정 문장을 채점기에 넣는다. 기본 실행이 게이트를 통과시키는 것은 그 고정 문장이 채점기가 보는 조건만 만족하기 때문이다. `--live` 는 F2만 `_call_json` 까지 간다. F3·F5·F6는 출력을 비우고 `err="live_integration_ready"` 를 넣은 뒤 반환한다. F4는 `PartnerAgent.reply(partner=..., timeout=12.0)` 를 호출하고, 시그니처가 `(system, history, opening, trace_metadata)` 라 `TypeError: PartnerAgent.reply() got an unexpected keyword argument 'partner'` 로 끝난다. 이 네 경로의 라이브 요약은 치명이 0이라 게이트 1이 true 다. 모델을 보지 않은 실행이 치명 게이트를 통과로 남긴다.

빈 선택(`n=0`)이면 분모가 `max(1, n)` 이라 게이트 1과 게이트 2가 통과할 수 있다. 케이스가 없을 때의 실패 처리가 없다.

---

## 3. 치명 C1~C6

00 3절의 해당 기능과, v1.1.0 기능 기준서의 오류 코드를 같이 봤다. "구현"은 그 정의로 0건 게이트에 들어가는 검사를 말한다.

| 코드 | 해당 | F2 | F3 | F4 | F5 | F6 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| C1 과거 만남 | F4, F5 | 대상 아님 | 대상 아님 | 검사 없음. "저번에 뵀을 때…" 가 통과 | 검사 없음. 같은 문장의 `fatalErrors` 가 빈 리스트 | 대상 아님 |
| C2 미제공 사실 | F4, F5 | 대상 아님 | 대상 아님 | 검사 없음. "28살 개발자, 강남" 이 통과 | 줄 계약의 `mustNotContain` 부분 문자열만 치명. 프로필 대조 없음 | 대상 아님 |
| C3 정체 자백 | F4, F5 | 대상 아님 | 대상 아님 | 키워드 부분 일치. "네, 맞아요", "무엇을 도와드릴까요?" 는 통과 | 같은 키워드. 기능 기준서의 판정 문장 전체는 아님 | 대상 아님 |
| C4 미확인 점수 | F2, F3 | 키 이름이 `scores` 일 때만 게이트 1. 기준서 예시 키 `score` 는 통과 | `mustOmitFromRawModel` 의 숫자 점수는 치명. 제품 `unknownMustBeNull` 은 안 봄 | 대상 아님 | 대상 아님 | 대상 아님 |
| C5 점수 위변조·인물 전도 | F6 | 대상 아님 | 대상 아님 | 대상 아님 | 대상 아님 | 본문 숫자와 화자 뒤바꿈을 안 봄. 데이터 플래그 `nonJudgedScoresMustRemainUnchanged`, `mustNotConfuseSpeakers` 를 안 읽음 |
| C6 20자 복창 | F3, F4, F5 | 대상 아님 | 검사 없음. 유형명 "회피형" 과 복창 문장이 함께 통과 | 누출 문자열 4개만. 20자 연속 복창은 통과 | 검사 없음 | 00의 F6 대상은 C5. 복창 검사도 없음 |

적대 입력은 회귀 케이스 하나씩에 채점 함수를 직접 호출한 결과다.

F4·F5의 C3 목록은 `ai`, `인공지능`, `챗봇`, `어시스턴트`, `언어 모델`, `페르소나`, `연기` 다. 기능 기준서의 F4 목록에 `어시스턴트` 는 없고, F5 목록은 "AI, 언어 모델, 챗봇, 가상 페르소나" 다. 소문자 변환 뒤 `ai` 부분 일치는 `email로 보낼게요` 를 C3으로 올린다. 정체 질문 동의("네")와 챗봇 안내 문장은 키워드가 없으면 게이트 1을 통과한다.

F4의 추가 치명 E4-CRIT-04는 `{partnerName}님` 포함으로 잡힌다. 프로브 `가상다온님은 커피를 좋아해요` 는 치명이다. 입력에 `partnerName` 이 없으면 기본값 `가상다온` 으로 검사해서, 실제 화자 이름과 어긋날 수 있다.

F5의 기능 치명 가운데 이른 작별(`contains_farewell`, `closingHint == "none"`)과 직전 발화 반복(`repeats_previous`)은 프로덕션 함수를 쓴다. 12자 미만은 동일 문장도 반복으로 보지 않는다. 기준서 1.3이 적은 탐지기 한계와 같다. 자기 호칭은 `outputContract.mustNotContainSelfNick` 을 넘기는데, 72줄 계약 키에는 그 필드가 없다. 닉네임은 대화 객체의 `nicknames`(`a`=`가민`, `b`=`가준`)에 있다. 채점기는 대화 닉네임을 읽지 않는다. `가민님은 영화를 좋아해요` 가 게이트를 통과했다.

---

## 4. 스키마 유효성

00 4.1은 JSON 스키마, 필수 키, 키 개수, 글자 수, 문장 수를 결정적 검사로 둔다.

F2는 스키마 모델을 쓰지 않는다. `primary`, `secondary`, `off_topic` 이 없어도 `.get` 기본값으로 빈 배열과 false 가 되고, 프로브는 통과했다. 기준서 E2-CRIT-03은 필수 키 누락과 파싱 실패를 사전 차단 0건으로 둔다. 라이브에서 `_call_json` 이 예외를 내면 그 건은 `error` 로 들어가 게이트 2(5% 허용)에 섞인다. 파싱 실패 0건과 타임아웃 5%가 한 카운터다.

F3는 `RawExtraction.model_validate` 를 호출한다. 실패는 `SCHEMA_VALIDATION_FAILED` 로 게이트 1이다. 스키마는 `extra: ignore` 이고 점수 필드는 기본 `None` 이다. `score`, `politeness` 같은 추가 키는 검증 뒤에 사라진다. 0~100 밖(101)은 검증 오류라, 기준서가 중대(E3-MAJ-02)로 두는 범위 이탈보다 먼저 게이트 1로 올라간다. 범위 안 이탈은 경고로 남고, 케이스 통과는 경고 0건을 요구한다. 기준서의 "점수 허용 범위 통과율 80% 이상" 은 차원 단위 비율인데, 요약에는 그 비율이 없다.

F5는 모델 JSON `{"text": ...}` 를 검증하지 않는다. 라이브가 `_speak` 를 호출하지 않아서 파싱 단계 자체가 없다. 300자 초과는 `LENGTH_EXCEEDED_300` 치명으로 게이트 1이다. 기준서 게이트 1 목록에는 길이가 없고, 포맷 실패는 게이트 2의 `text=None` 10% 에 들어간다. 이모지는 경고이며 경고가 있으면 케이스가 실패해 게이트 3 분자에서 빠진다. 이모지 정규식은 프로덕션 `EMOJI_REGEX`(`U+10000` 이상)라 `☕`(U+2615) 는 통과했다.

F6는 `ReportNarrative.model_validate` 를 호출한다. 실패는 게이트 1 치명이다. 기준서 1.1은 60자 초과, 하이라이트 7개 이상, 인용 200자 초과를 템플릿 폴백으로 적고, 게이트 2는 `narrative_source=template` 비율 5% 이하다. 하이라이트 7개 프로브는 스키마 오류로 게이트 1에 들어갔다. 헤드라인 60자 초과 분기는 스키마가 먼저 반환해서 도달하지 않는다. `ideal_fit` 150은 스키마를 통과하고 경고만 남는다. 경고는 게이트 1이 아니라 케이스 실패다. 기준서 E6-MAJ-03(이상형 범위)의 위치와는 맞고, 게이트 3의 1/3/5와는 다르다.

---

## 5. 점수 불변성

00 4.1의 "차원 점수 불변성" 과 F6 1.2·3.2의 `ruleNonJudgedScoresUnchanged` 를 봤다.

F6 입력에는 `precomputed_scores` 가 있다. 첫 케이스 값은 친밀 82, 소통 78, 갈등 75, 성향 80, 이상형 `null`, `overall_base` 79 다. 계약은 `nonJudgedScoresMustRemainUnchanged: true` 다. 채점기는 둘 다 읽지 않는다. 요약에 "친밀감은 20점으로 낮습니다. 가준의 취미는 서점이에요." 를 넣어도 점수 모순(E6-CRIT-01)과 인물 전도(E6-CRIT-02) 코드가 생기지 않았다. 실패 원인은 다른 턴의 인용을 가짜 인용으로 올린 것과 이상형 150 경고였다. 내러티브 JSON에 넣은 `avoidance: 10` 은 `extra: ignore` 로 사라진 뒤 비교 대상이 없다. `_report_perspective_swap` 은 `report_tool.py` 에 있으나 러너는 호출하지 않는다.

인용 오라클은 반만 구현이다. `turn_index` 가 대화에 있고 `quote` 가 그 턴 텍스트의 부분 문자열이면 통과한다. 다른 턴에 있는 문장을 틀린 인덱스에 달면 E6-CRIT-03(가짜 인용, 게이트 1)이다. 기준서는 그 경우를 E6-MAJ-02(인덱스 불일치, 중대)로 둔다. 인덱스가 턴 목록에 없으면 인용 검사를 건너뛰고 `INVALID_TURN_INDEX` 경고만 남긴다. 없는 인덱스에 가짜 문장을 달아도 게이트 1 치명이 아니다.

F3의 C4는 점수 불변과는 다른 불변식(답하지 않은 차원에 점수를 쓰지 않음)이고, 이쪽은 동작한다. `mustOmitFromRawModel` 에 있는 차원에 50을 넣으면 `C4_UNGROUNDED_SCORE_POPULATED` 가 치명이다. 키를 빼면 통과한다. 텍스트 필드 `textualKept` / `textualDropped` 는 데이터에 있으나 검사하지 않는다. 모의 출력은 항상 `interests` 와 `routine` 을 채운다. 제품 점수용 `unknownMustBeNull` 도 검사하지 않는다. 보강 턴의 ±5점 허용은 없다.

F2 기준서의 점수 불변에 해당하는 항목은 E2-CRIT-02(점수 필드 금지)다. 러너는 `scores` 키만 본다. `{"score": 80}` 프로브는 `isFatalC4` 가 false 이고 통과했다.

---

## 6. 기능별 상세

### 6.1 F2 태깅

라이브 경로만 캡처 위치가 맞다. `run_model_call` 은 `TAG_PROMPT` 와 `onboarding_tag_timeout_s` 로 `_call_json` 을 호출한다. `TaggingAgent.tag` 가 582~585행에서 하는 19개 차원 필터 전이다. 기준서 2.2가 요구하는 raw JSON 이다. 제품 라벨을 따로 채점하지는 않는다.

모집단은 기본값으로 회귀·`label_accuracy` 47건이다. `code_behavior` 1건은 `--kind` 기본값 때문에 빠진다. 단답 보류 4건 중 회귀에 있는 것은 `011`, `019` 둘이고, 둘 다 47건 안에 남는다. `063` 은 calibration, `075` 는 blind_holdout 이다. 기준서 4.1의 "48 − 1 − 4 = 43" 은 네 건이 모두 회귀 48건 안에 있다고 본 산식이다. 파일에서는 회귀 라벨 47건에서 회귀 보류 2건을 빼면 45건이다. 러너는 43도 45도 만들지 않는다.

정답 위치도 어긋난다. 80건 모두 `expectedOutput.primary` 가 없고, 정답은 `expectedOutput.referenceLabels.primary` 와 `autoMetrics.primary.accepted`(method `set_match`)에 있다. 채점기는 `expectedOutput.primary` 만 읽는다. 47건 전부 기대 primary 가 빈 집합이 된다. 통과 조건은 `primary_jaccard >= 0.5 or not exp_primary` 라, 기대 primary 가 비면 어떤 예측 primary 도 통과한다. 첫 회귀 케이스의 골드는 `anxiety` 인데 예측 `seriousness` 가 통과했고, 기록된 기대값도 빈 리스트였다. `off_topic` 도 같은 경로라 기대값이 항상 false 다. 회귀 47건 중 골드 `off_topic: true` 는 4건이다. 모델이 true 로 맞혀도 러너는 오답으로 본다. `acceptableAlternatives` 는 80건 모두에 있고 사용하지 않는다.

유령 차원은 `invalidDimensions` 에 모이고 `passed` 를 깨뜨린다. 게이트 1 카운터는 `isFatalC4` 만 센다. 유령 차원만 있는 출력은 기준서 E2-CRIT-01의 0건 조건(사전 차단)을 통과시킨다. 자동 일치 85%도 계산하지 않는다. 모의 모드는 같은 빈 키로 라벨을 만들어 47/47, 게이트 3개 true 였다.

### 6.2 F3 추출

C4의 모델 원문 검사는 데이터 키와 맞다. `autoMetrics.mustOmitFromRawModel` 에 숫자를 남기면 치명이다. 기준서 4.2가 적은 이름 `unknownScores` 는 제품 쪽 null 지도이고, 러너는 그 지도를 보지 않는다.

기본 `--split regression` 은 종류 필터가 없어 36건이다. 언어 품질 18건과 `code_behavior` 18건이 반반이다. 기준서 4.1의 배포 분모는 언어 18건이다. 모의 모드는 허용 범위의 중간값을 넣고 생략 차원은 비워 두어 36/36, 게이트 3개 true 다. 중간값은 범위 안에 있으므로 범위 게이트를 증명하지 않는다.

`--case persona_build-001 --live` 는 모델을 호출하지 않고 `executionErrors: 1`, `fatalErrorCases: 0`, `gate1_passed: true` 를 냈다. `ExtractionAgent` 는 import 만 되어 있다.

유형명(E3-CRIT-02), 20자 복창(E3-CRIT-03), 짐작 어미, 서술 톤, 카드 카테고리, `GUARDRAIL_MODE` 는 검사하지 않는다. 게이트 3에 해당하는 서술 1/3/5 평균 3점도 없다. 폴백 5%는 "정상 난이도 폴백"인데, 러너는 실행 예외 비율로 대신한다.

### 6.3 F4 연습 답변

모집단은 기준서와 맞다. `contract_stale_practice.json` 10건을 빼고, 기본값이 회귀·`language_quality` 라 22건이 남는다. 구계약 10건의 분할은 회귀 6, 보정 2, 홀드아웃 2 이고, 회귀 언어 28건에서 6건을 뺀 수다. 모듈 주석은 `practice_identity_defense_rewritten.json` 을 넣을 수 있다고 적지만, 그 파일을 읽는 코드는 없다. 기준서의 현재 조치는 그 10건을 분모에서 빼 두는 것이므로, 빼 둔 것 자체는 게이트 1의 "격리 확인"과 맞다.

게이트 1에 실제로 들어가는 것은 C3 키워드, 누출 키워드 4개(`## 절대 하지 않는 것`, `[CORRECTION NOTICE]`, `GUARDRAIL`, `Chain of Verification`), `{이름}님` 이다. C1, C2, 입력 20자 복창은 프로브가 통과했다. 문장 수 4는 통과한다. 기준서 E4-MIN-01은 일반 턴 3문장 초과를 경미로 둔다. 러너는 4문장 이하를 케이스 통과 조건에 넣어 게이트 3에 섞는다.

모델 원문의 캡처 의도는 맞다. `without_identity_confession` 은 `service.py` 저장 직전에 있고, `PartnerAgent.reply` 는 치환 전의 청크를 yield 한다. 라이브 호출은 그 함수에 없는 인자 `partner`, `timeout` 을 넘겨 `TypeError` 가 난다. 시스템 프롬프트도 만들지 않는다. 스트림 12초는 `_stream` 이 `timeout is None` 일 때 `practice_timeout_s` 로 건다. 러너의 `timeout=12.0` 은 그 인자까지 도달하지 못하고, 타임아웃 초과 0%도 따로 세지 않는다. 게이트 2는 예외 비율 5% 다. 기준서는 `medium` 난이도의 `source=fallback` 비율이다. 모의 문장 `저 {이름}이에요 ㅎㅎ …` 는 22/22 를 통과시킨다.

제품 점수와 스트림 노출 칸은 없다. 재생성 초안을 모델 분모에서 빼는 집계도 없다.

### 6.4 F5 시뮬 한 줄

모집단은 기준서 5.3과 맞다. `simulation_line_benchmark.json` 은 대화 12개, 줄 72개, 전부 3왕복이다. 단계 수는 open 24, middle 24, close_a 12, close_b 12 이고 `late` 는 없다. `simulation_line_draft.json` 은 `includeInMean: false` 이고 러너가 읽지 않는다. `simulation_run.jsonl` 36건도 읽지 않는다.

게이트 2의 10%는 기준서의 포맷·실행 실패 상한과 같은 숫자다. 분자는 `err` 또는 빈 문자열이고, 20초 타임아웃 초과 0%는 없다. `--dialogue simulation_line_bench-001 --live` 는 6줄 모두 `live_integration_ready` 이고 `fatalErrorLines: 0`, `gate1_passed: true` 였다. import 한 `_speak` 는 호출되지 않는다.

`strip_name_prefix(reply_text, speaker)` 의 `speaker` 는 `a` 또는 `b` 다. 함수는 콜론 앞이 닉네임과 같을 때만 접두어를 벗긴다. `가민:` 은 그대로 남는다. 기준서의 모델 점수는 접두어 제거 전의 첫 초안이고, 제품 점수는 제거 후다. 러너는 한 텍스트만 채점하고, 그 제거는 역할 글자에만 작동한다.

이름표가 붙은 "저번에 뵀을 때" 와 `## 절대 하지 않는 것` 복창은 `fatalErrors` 가 비었다. 케이스 실패는 금지어가 아니라, 그 문장에 `mustContain`(`가준님`, `안녕`)이 없어서 생긴 경고였다. 경고도 `passed` 를 깨므로 게이트 3에 들어간다. `mustAnswer`, `groundedInSelfProfile`, `mustNotOpenNewTopicOrQuestion`, `mustCloseConversation` 은 계약에 있으나 읽지 않는다. 모의 대사는 단계별 고정 문장에 `mustContain` 첫 항목만 붙여 72/72 를 통과시켰다. 고정 문장이 이 벤치마크의 키워드 목록을 피하도록 쓰여 있다.

### 6.5 F6 리포트 서술

파일은 24건이다. 분할은 회귀 14, blind_holdout 6, calibration 4 다. 기준서 5.3은 "24건 신규 회귀 집합"이라고 부르고, 기본 `--split` 은 `all` 이라 홀드아웃 6건이 게이트 분모에 들어간다. `--split regression` 을 주면 14건만 남고, 기준서의 24와도 어긋난다. 모의 리포트는 턴 원문의 앞 15자를 인용해 인용 검사를 통과하고, 본문에 점수 숫자를 넣지 않아 24/24, 게이트 3개 true 다.

`--case simulation_narrative-001 --live` 도 `gate1_passed: true`, `executionErrors: 1` 이다. `ReportAgent` 는 import 만 되어 있다.

게이트 1에 올라가는 자동 검사는, 인덱스가 유효할 때의 인용 불일치뿐이다. 사람 2인 교차가 필요한 인물 전도와, 코드 점수와 본문 숫자의 모순은 자동 0건 조건으로 적혀 있는데 검사가 없다. 게이트 2의 타임아웃 0%(60초)와 폴백 사유도 없다. 게이트 3의 네 축(장면 연결, 양쪽 균형, 유보 어투, 조언 구체성) 평균과 1점 0건은 없다.

---

## 7. 실행 기록

모의 모드, `.venv/bin/python`, 2026-10-08.

| 명령 | total | passed | fatal | error | gate1 | gate2 | gate3 |
| :--- | ---: | ---: | ---: | ---: | :--- | :--- | :--- |
| `eval_f2_tagging.py --split regression --kind label_accuracy` | 47 | 47 | 0 | 0 | true | true | true |
| `eval_f3_extraction.py --split regression` | 36 | 36 | 0 | 0 | true | true | true |
| `eval_f4_practice.py --split regression --kind language_quality` | 22 | 22 | 0 | 0 | true | true | true |
| `eval_f5_simulation_line.py` | 72줄 | 72 | 0 | 0 | true | true | true |
| `eval_f6_simulation_narrative.py --split all` | 24 | 24 | 0 | 0 | true | true | true |

라이브 스텁은 모두 `fatal* = 0`, `gate1_passed = true`, `gate2_passed = false` 였다. F4 예외 문자열은 `TypeError: PartnerAgent.reply() got an unexpected keyword argument 'partner'` 다.

적대 프로브에서 통과한 입력(치명 목록이 비고 `passed` 가 true):

- F2: 골드 `anxiety` 대신 `seriousness`. 키 `score: 80`. 키 누락 `{"off_topic": false}`.
- F3: narrative 에 "회피형" 과 복창 문장.
- F4: "저번에 뵀을 때…", "저는 28살 개발자예요. 강남에 살아요.", "네, 맞아요.", "무엇을 도와드릴까요?", 4문장, `☕`.
- F5: `가민님은 영화를 좋아해요. 안녕하세요 가준님`.
- F6: 턴 인덱스 99와 가짜 인용은 치명이 아니다(경고만). 본문 "20점" 은 점수 치명이 아니다.

적대 프로브에서 기대한 대로 막힌 입력:

- F2: 키 `scores` 는 `isFatalC4`.
- F3: 생략 차원 `avoidance: 50`.
- F4: "저는 AI입니다", `[CORRECTION NOTICE]`, `{이름}님은`.
- F5: 301자는 `LENGTH_EXCEEDED_300` (기준서 게이트 2에 둘 항목이 게이트 1로 올라감).
- F6: 다른 턴 문장의 인용은 E6-CRIT-03. 하이라이트 7개는 스키마 치명.

---

## 8. 결론

다섯 러너는 공통으로, 모의 출력이 자기가 구현한 조건만 만족하면 게이트 세 개를 통과시킨다. 기준서의 게이트 3은 1/3/5 사람 척도(F2는 여기에 더해 43건 집합 일치 85%)인데, 러너의 게이트 3은 케이스 통과율 80% 다. `GUARDRAIL_MODE` 와 모델/제품 점수 분리는 없다.

기능별로 남아 있는 불변식은 다음이다.

1. F2: 정답을 `referenceLabels` 와 `autoMetrics.*.accepted` 의 집합 일치로 읽고, 회귀 보류 `011`·`019` 를 분모에서 뺀다. 유령 차원과 `score` 를 포함한 점수 필드, 파싱 실패를 게이트 1의 0건으로 둔다. 타임아웃만 게이트 2로 둔다.
2. F3: 분모를 회귀 언어 18건으로 제한하고, 유형명·20자 복창을 게이트 1에 넣는다. 범위 통과율 80%를 차원 단위로 집계한다. 제품 `unknownMustBeNull` 을 모델 C4와 따로 기록한다. `--live` 가 `ExtractionAgent.extract` 의 raw JSON을 채점하게 한다.
3. F4: C1·C2·20자 복창을 모델 초안에서 게이트 1로 검사한다. `reply` 호출을 `system` 과 `history` 로 고치고, 치환 전 초안과 치환 후 저장 문장을 따로 남긴다. 게이트 2를 `medium` 의 폴백과 12초 초과 0%로 나눈다.
4. F5: C1·C2·C6과 자기 호칭을 닉네임 기준으로 게이트 1에 넣는다. 300자·JSON 파괴·`text=None` 은 게이트 2로 돌린다. 접두어 제거 전과 후를 따로 채점한다. `--live` 가 `_speak` 의 첫 초안을 보게 한다.
5. F6: `precomputed_scores` 와 본문 숫자의 모순, 비-JUDGED 점수 불변, 인물 전도를 게이트 1에 넣는다. 없는 턴 인덱스의 인용을 빠뜨리지 않는다. 스키마 초과는 템플릿 폴백으로 게이트 2에 둔다. 홀드아웃을 기본 분모에서 뺀다.

이 항목이 코드에 들어가기 전에는 러너 요약의 `gate*_passed: true` 를 배포 근거로 쓰지 않는다.
