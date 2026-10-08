# F2 온보딩 태깅 평가기준서 검토

작성일: 2026년 10월 07일  
검토 대상: `docs/성능평가/02_F2_온보딩_태깅_평가기준서.md` (v1.0.0)  
대조 기준: `docs/성능평가/00_평가체계_및_공통가이드라인.md` (v1.0.0), `app/features/persona/agents.py`의 `TaggingAgent`, `evals/quality_datasets/persona_onboarding_tagging.jsonl` (80행)  
범위: 이 파일만 추가했다. 기준서 원본과 코드, 데이터셋은 수정하지 않았다.

판정 요약: 건수(80/16/48/16, label 79 + code 1), 타임아웃(4초/8초), 필터 위치(`agents.py` 583행)는 현재 코드와 맞다. 라벨 풀을 15개로 적은 점, `off_topic` 정의, C4 매핑, 회귀 48건을 그대로 자동 일치율 분모로 쓰는 게이트는 코드·데이터셋·00 가이드라인과 어긋난다. 골드셋 80건은 모두 `pending_dual_human_review`라 게이트를 확정 기준으로 쓸 수 없다.

---

## 1. F2 문서 요약

F2는 `POST /ai/api/v1/persona/onboarding/{session_id}/answer` 안에서 비동기로 도는 `TaggingAgent.tag`를 평가한다. 단독 외부 라우트는 없다. 온보딩 태깅 제한 시간은 `onboarding_tag_timeout_s` 4초, `/build` 실패 재태깅은 `persona_retag_timeout_s` 8초다.

문서가 적는 계약은 다음과 같다.

- 입력은 질문 문자열과 답변 문자열이다.
- 출력은 `{"primary": [], "secondary": [], "off_topic": false}` 형태의 JSON만 허용하고, 설명과 마크다운을 금지한다.
- 허용 라벨을 `ALL_DIMENSIONS` 15개로 적고, 점수 필드는 금지한다.
- `primary`는 직접 근거, `secondary`는 간접 추론, `off_topic`은 질문과 무관하거나 유의미한 정보가 없는 답이다.
- 데이터셋은 80건이다. calibration 16, regression 48, blind_holdout 16. 이 중 79건은 `label_accuracy`, 1건은 장애 주입 `code_behavior`다.
- 단답 4건(`011`, `019`, `063`, `075`)은 `off_topic` 해석이 갈려 게이트 분모에서 빼 둔다고 적는다.
- 런타임은 15개 밖 차원명을 조용히 버리므로, 채점기는 필터 전 raw JSON에서 유령 차원을 잡아야 한다고 제안한다.
- 배포 차단은 유령 차원 0건, 전도 오분류 0건, 회귀 집합의 `primary`·`off_topic` 자동 일치율 85% 이상이다. 사람 점수는 자동 검사가 충돌한 경계 사례에서 평균 3점 이상을 보조 조건으로 둔다.

오류 코드는 치명 3개(`E2-CRIT-01` 유령 차원, `E2-CRIT-02` 전도 오분류, `E2-CRIT-03` JSON 파싱 실패), 중대 3개(primary 누락, off_topic 오탐·미탐), 경미 2개(secondary 과다/부족, 점수 필드 추가)다. `E2-CRIT-01`과 `E2-CRIT-02`에 공통 코드 C4를 붙여 두었다.

---

## 2. 00 가이드라인 정합성

00의 5단계(프롬프트 분석, 샘플 검토, 오류 분류, 골드셋, 전문가 검토)에 대응하는 절은 F2 1~5장에 있다. 이원화 점수, C4의 정의, 치명 결함의 즉시 거절, 폴백의 집계 방식은 빠져 있거나 다르게 적혀 있다.

### 2.1 맞는 부분

- 평가 단위를 온보딩 답변 API 안의 태깅 호출로 분리한 점은 00의 1.1(한 트랜잭션 안의 LLM 호출을 기능별로 나눈다)과 00의 5장 맵(`F2 → TaggingAgent.tag()`)과 같다.
- 오류를 치명/중대/경미로 나누고 코드·정의·예시를 둔 점은 00의 3단계 형식과 같다.
- 사람 점수를 1/3/5로 두고, 경계 사례의 합격선을 3점(보통)에 둔 점은 00의 4.2와 같다.
- 데이터셋 `rubric.role=auxiliary`, `rubric.usage`도 자동 지표가 정답·허용 대안과 다를 때만 사람이 1/3/5를 보게 되어 있어, F2 5장의 보조 정성 평가와 방향이 같다.
- 태깅 경로에는 `apply_text` 가드레일이 없다. 대화 가드레일은 `ConversationAgent`와 추출 쪽에만 있다. 모델 원문과 제품 후처리를 나누려면 이 사실을 결과표의 `GUARDRAIL_MODE`에 적어야 한다. F2는 그 칸을 비워 두었다.

### 2.2 C4를 다른 결함으로 바꿔 달았다

00의 C4는 "사용자가 답하지 않았거나 근거가 없는 성향 차원에 임의의 점수(50 포함)를 적는 것"이고, 대상 기능에 F2와 F3가 들어 있다. 해당 결함이 하나라도 있으면 평균과 상관없이 배포 게이트가 거절된다.

F2가 C4로 단 두 코드는 이 정의와 다르다.

| F2 코드 | F2가 적은 뜻 | 00 C4와의 관계 |
| :--- | :--- | :--- |
| `E2-CRIT-01` 유령 차원 | `ALL_DIMENSIONS`에 없는 이름 생성 | 점수를 적은 사건이 아니다. 허용 목록 밖 키를 만든 형식 위반이다. |
| `E2-CRIT-02` 전도 오분류 | 정반대 차원으로 분류. 예: 독립성 답변에 `engagement` | 태깅 출력에는 높낮이 점수가 없다. 같은 차원의 방향 전도는 이 출력으로 표현되지 않는다. `engagement`는 `avoidance`의 반대 축이 아니다. |

태깅 프롬프트는 "점수는 매기지 마세요"라고 되어 있고, `TaggingAgent.tag`는 `primary`·`secondary`·`off_topic`만 `Tags`에 넣는다. 점수 키가 나와도 제품 객체에는 남지 않는다. F2는 그 경우를 경미 `E2-MIN-02`로 내린다. 00에서 근거 없는 점수 기재는 즉시 거절이므로, raw JSON에 점수 필드가 있으면 모델 점수의 치명 결함으로 두고 제품 점수에서는 폐기된 필드로 따로 세는 편이 가이드라인과 맞다.

`E2-CRIT-02`의 예시는 코드의 차원 정의와도 어긋난다. `avoidance`의 높은 쪽은 "각자 생활을 중시", `engagement`의 높은 쪽은 "목소리가 커지거나 쏘아붙임"이다 (`schemas.py` `SCORED`). 독립적인 답에 `engagement`를 다는 것은 다른 축을 고른 오분류이지, 한 축의 전도가 아니다. 방향 전도는 점수를 내는 F3의 결함으로 남기고, F2에서는 "근거 없는 primary 포함"을 집합 불일치로 세는 쪽이 출력 형식과 맞다.

### 2.3 치명 코드와 게이트가 서로 다르다

00은 치명 결함 하나면 평균과 무관하게 거절한다. F2 3장은 `E2-CRIT-03`(JSON 파싱 실패)을 치명으로 올리면서, 5장 사전 차단에는 `E2-CRIT-01`과 `E2-CRIT-02`만 0건 조건으로 넣는다. 파싱 실패는 게이트 밖에 있다.

코드에서 JSON 파싱 실패는 사용자에게 깨진 JSON으로 전달되지 않는다. `_call_json`(`agents.py` 128–139행)이 `LLMError`를 올리고, `tag`는 `None`을 반환한다. `PersonaService.submit_answer`는 `None`이면 `topic.covers`와 `topic.also_touches`를 커버리지에 넣는다 (`service.py` 572–575행). 00의 2장 폴백 규칙에 따르면 이 건은 모델 점수 분모의 실패이고, 품질 평균 분자에는 넣지 않는다. 주제 기본 커버리지가 골드 라벨과 우연히 같아도 모델 성공으로 세면 안 된다.

데이터셋 `persona_onboarding_tagging-080`이 이 계약을 이미 적는다. `evaluationKind=code_behavior`, `autoMetrics.applies=false`, `faultContract.tagResult=null`, `faultContract.qualitySuccess=false`, 회복 커버리지는 `receiving_hurt` 주제의 `covers=("compliance",)`와 `also_touches=("problem_solving","withdrawal")`이다. F2 5장이 "회귀 48건" 전체의 자동 일치율을 보면, 이 1건이 라벨 일치 분모에 들어간다.

### 2.4 모델 점수와 제품 점수를 나누지 않았다

00의 2장은 같은 출력에 대해 모델 점수(후처리 전 원문)와 제품 점수(사용자에게 남거나 저장되는 결과)를 따로 집계하고, 둘을 평균하지 말라고 한다. 집계표에는 `GUARDRAIL_MODE`를 적는다.

F2 4.3의 "필터 전 raw JSON에서 미정의 차원을 검사"는 모델 점수 쪽과 같다. 그 다음이 없다.

- 제품 점수는 필터 뒤 `Tags`와, `None`일 때 서비스가 넣는 주제 커버리지다.
- 필터가 유령 차원을 지운 뒤 남은 `primary`가 골드와 같아도, raw에 유령 차원이 있으면 모델 게이트는 거절이다.
- 가드레일 모드는 태깅에 해당 없음이다. `shadow`/`enforce`/`off` 중 하나를 비워 두면 다른 기능 표와 비교할 때 누락으로 읽힌다.

00의 4.3(LLM Judge)도 F2에 없다. 태깅은 정답 라벨이 있어 Judge 없이 갈 수 있다. Judge를 쓰려면 calibration에서 평가자 2인과 일치율 또는 Kappa 85%가 나온 뒤 regression 보조 채점만 허용한다는 문장을 기준서에 두는 것이 00과 맞다. 이 85%는 F2 5장의 "회귀 자동 일치율 85%"와 다른 숫자다. 자동 일치율 85%는 00에 없는 기능별 임계값이다.

### 2.5 5단계 중 전문가 검토가 끝나지 않았다

00의 5단계는 도메인 전문가가 정답과 기준을 승인한 뒤 벤치마크를 고정하는 단계다. 데이터셋 80건의 `rubric.status`는 모두 `pending_dual_human_review`이고, `reviewFlags`의 `human_anchor_pending`도 80건이다. F2 5장은 이 상태를 적지 않고 게이트를 합격 기준으로 제시한다. 사람 앵커가 비어 있는 동안 85%와 3점은 초안이다.

---

## 3. 코드·데이터셋 대비 오류와 수정안

아래 수정은 `02_F2_온보딩_태깅_평가기준서.md`에 반영할 내용이다. 이번 검토에서는 원본을 고치지 않았다.

### 3.1 라벨 풀은 15개가 아니다

문서 1.1은 `ALL_DIMENSIONS`를 15개로 적고 `contact_rhythm`부터 `ideal_status`까지 점수형 키만 나열한다.

코드의 `ALL_DIMENSIONS`는 `SCORED` 15개 뒤에 `TEXTUAL` 4개를 붙인 19개다 (`schemas.py` 122–129행).

`interests`, `routine`, `date_prefer`, `date_avoid`.

`TAG_PROMPT`는 이 19개를 프롬프트에 넣는다 (`agents.py` 529–541행). 데이터셋 `labelUniverse`와 `hardAssertions`도 "등록된 19개 차원"이다. 골드 `primary`에는 텍스트형 4종이 모두 한 번 이상 있다 (`interests` 1, `routine` 1, `date_prefer` 1, `date_avoid` 2).

문서의 15개 목록을 허용 집합으로 쓰면, 맞는 텍스트형 라벨이 `E2-CRIT-01` 유령 차원이 된다.

수정안: 허용 집합을 코드와 같은 19개로 바꾸고, 나열 순서를 `SCORED` 정의 순서와 텍스트형 순서로 맞춘다. "15개 성향 차원"이라는 표현은 점수형만 가리킬 때 쓰고, 태깅 허용 집합과 구분한다.

### 3.2 `off_topic` 정의가 프롬프트보다 넓다

`TAG_PROMPT`의 조건은 "질문과 무관한 답변이면 둘 다 빈 배열, `off_topic` true"이다.

문서 1.2.3은 "완전히 무관하거나, 유의미한 정보를 담고 있지 않은 답"까지 포함한다. 두 번째 절은 `AnswerRequest` 주석("`schemas.py` 437행, 성의 없는 답은 태깅의 `off_topic`으로 대응")에 가깝고, 프롬프트와는 다르다.

데이터셋에는 `off_topic=false`이면서 `primary`·`secondary`가 둘 다 빈 행이 16건 있다. 범주는 `insufficient_evidence` 5, `joke_nonanswer` 3, `low_or_unknown` 3, 짧은 답 4, `preference_undecided` 1이다. 질문에는 답했지만 성향 근거가 없는 경우다. 문서 정의대로 "정보 없음 = off_topic"을 적용하면 이 16건의 골드가 오탐이 된다.

`off_topic=true` 골드는 8건이고, 그 행의 `primary`·`secondary`는 모두 비어 있다.

수정안: 채점용 정의는 프롬프트를 따른다. 질문과 무관할 때만 `off_topic=true`이고 두 배열은 빈다. 질문에는 닿지만 근거가 없으면 `off_topic=false`와 빈 배열이다. 스키마 주석의 "짧은 무성의 답"은 3.3의 4건에만 허용 대안으로 남긴다.

### 3.3 단답 4건의 상태 기술이 데이터셋과 다르다

문서 2.2와 4.2는 사용자가 "네", "몰라요", "그냥요"라고 했고, 기대값이 갈려 게이트 분모에서 뺀다고 적는다.

실제 행은 다음과 같다. 기본 라벨은 모두 `off_topic=false`, `primary=[]`, `secondary=[]`이고, `autoMetrics.off_topic.accepted`는 `[false, true]`다. 플래그는 `schema_comment_off_topic_conflict`다.

| caseId | split | category | 답 |
| :--- | :--- | :--- | :--- |
| `persona_onboarding_tagging-011` | regression | `short_uncertain` | 몰라 |
| `persona_onboarding_tagging-019` | regression | `short_uncertain` | 음 |
| `persona_onboarding_tagging-063` | calibration | `short_affirmative` | 응 |
| `persona_onboarding_tagging-075` | blind_holdout | `short_withholding` | 보류 |

4건은 한 분할에 모여 있지 않다. 회귀 분모에서 빠지는 것은 011과 019뿐이다. 063은 calibration, 075는 holdout이다. 데이터셋은 이 4건을 제외하지 않고, 두 Boolean을 모두 정답으로 받으면서 라벨 배열은 빈 집합만 통과시킨다.

수정안: 답 원문을 위 표로 바꾼다. "분모에서 제외" 대신 "회귀 자동 일치율에는 포함하되 `off_topic`은 true/false 둘 다 통과, `primary`·`secondary`는 빈 집합만 통과"로 적는다. 사람 2인 합의로 하나를 고르기 전에는 플래그를 유지한다. 합의 뒤에 허용 배열을 한쪽으로 줄인다.

`ambiguous_secondary` 19건은 별도 쟁점이다. 이 행들은 `secondary`의 허용 대안에 빈 배열이 들어 있다. 문서 4.1은 `secondary` 지표 자체를 적지 않는다. 자동 지표에 `secondary` 집합 일치를 보고용으로 넣고, 차단 게이트에는 넣지 않는다고 적으면 `E2-MIN-01`과 데이터셋이 맞는다.

### 3.4 회귀 48건을 자동 일치율 분모로 쓰면 080이 섞인다

회귀 48건의 구성은 `label_accuracy` 47건과 `code_behavior` 1건(`080`, `faultInjection.kind=timeout`)이다. 080의 `autoMetrics.applies`는 false다. 참조 라벨(`primary: ["compliance"]`)은 의미 정답이고, 장애 실행의 합격 조건은 `tag`가 `None`을 반환하고 커버리지가 주제 기본값으로 회복되는 것이다.

수정안:

- 자동 일치율 분모는 회귀의 `label_accuracy` 47건이다. 011·019를 허용 대안으로 포함하면 47건을 유지한다.
- 080은 모델 품질 분자에 넣지 않는다. 통과 조건은 `tagResult is None`, `qualitySuccess is false`, 커버리지 primary `compliance`, secondary `problem_solving`과 `withdrawal`이다.
- 일치 판정은 데이터셋과 같이 순서 무관한 집합 일치(`set_match`)다. 리스트 완전 일치로 적으면 순서만 다른 정답이 실패한다.
- 85%는 00의 Judge 보정 기준과 다른, F2 초안 임계값이라고 적는다. `human_anchor_pending`이 해소되기 전에는 잠정 값이다.

### 3.5 유령 차원 검사 위치는 맞고, 예외 경로 기술이 빠졌다

문서가 가리키는 `agents.py` 583행은 현재 코드와 같다.

```python
primary=[d for d in raw.get("primary", []) if d in valid],
secondary=[d for d in raw.get("secondary", []) if d in valid],
off_topic=bool(raw.get("off_topic", False)),
```

필터 전 raw를 봐야 `E2-CRIT-01`을 셀 수 있다는 4.3의 제안은 유지할 만하다. 그 외에 채점기가 제품 `Tags`만 보면 놓치는 경로가 있다.

1. 마크다운 코드펜스는 `_call_json`이 첫 `{`부터 마지막 `}`까지 잘라 파싱한다. 펜스 안에 JSON이 있으면 `E2-CRIT-03`이 아니다. 파싱에 실패할 때만 `None` 폴백이다.
2. `off_topic`에 문자열 `"false"`가 오면 `bool("false")`는 `True`다. JSON Boolean만 통과로 보고, 문자열은 모델 형식 오류로 센다.
3. `primary`가 문자열이면 문자 단위로 순회하다가 전부 걸러져 빈 배열이 된다. 누락처럼 보인다. raw 타입이 list가 아니면 집합 비교 전에 형식 오류로 분리한다.
4. 중복 라벨은 필터가 제거하지 않는다. `Coverage.apply`는 같은 키를 중복 횟수만큼 올린다. 집합 일치는 통과할 수 있다. 모델 점수에서는 중복을 경미 형식 오류로 보고, 제품 커버리지 가산과 구분한다.
5. `off_topic=true`여도 코드는 배열을 비우지 않는다. 첫 무관 답은 저장하지 않고 되묻는다 (`service.py` 567–570행). 이미 되물은 턴에서 다시 `off_topic=true`이면 비어 있지 않은 `primary`도 커버리지에 들어간다. 프롬프트 계약(무관하면 배열은 빈다)을 모델 점수의 중대 오류로 두고, 제품 경로의 2회차 동작을 `code_behavior`로 따로 둘지 기준서에 적는다. 현재 80건에는 이 2회차 케이스가 없다.
6. `/build` 재태깅이 또 실패하면 그 답은 근거로 치지 않는다 (`service.py` 422–437행). 온보딩 중 실패(주제 커버리지로 채움)와 재태깅 실패(근거에서 제외)는 다른 제품 동작이다. 080은 온보딩 타임아웃만 다룬다.

### 3.6 오류 코드 수정안

| 코드 | 수정 |
| :--- | :--- |
| `E2-CRIT-01` | C4 표기를 뺀다. 19개 `labelUniverse` 밖 문자열이 raw `primary` 또는 `secondary`에 있으면 모델 점수 치명, 게이트 0건. 필터 후 제품 라벨이 골드와 맞아도 취소하지 않는다. |
| `E2-CRIT-02` | 삭제하거나 F3로 보낸다. 태깅에서 "반대 차원"은 정의할 축 점수가 없다. 근거 없는 등록 차원은 `primary` 집합 불일치로 85% 분모에 남긴다. |
| 점수 필드 | `E2-MIN-02`를 모델 점수의 치명(00 C4에 해당하는 점수 기재)으로 올린다. 제품 `Tags`에 점수가 남지 않는 것은 제품 점수의 별도 칸으로 적는다. 두 점수를 평균하지 않는다. |
| `E2-CRIT-03` | "마크다운이면 치명"을 뺀다. `_call_json` 실패로 `None`이 된 경우만 해당하고, 00 폴백 규칙대로 품질 분자에서 제외한다. 게이트에는 "폴백을 라벨 성공으로 센 건수 0"을 넣는다. |
| `E2-MAJ-02` / `E2-MAJ-03` | 판정 문장을 3.2의 프롬프트 정의로 바꾼다. 짧은 답 4건은 허용 대안이라 오탐·미탐 건수에서 뺀다. |
| `E2-MIN-01` | `ambiguous_secondary` 19건은 빈 `secondary`를 허용 대안으로 채점한다. 차단 게이트에는 넣지 않고 보고만 한다. |

사람 앵커는 데이터셋에 이미 1/3/5 문장이 있다. 76건의 기준 키는 `labelSchema`, `primaryEvidence`, `secondaryEvidence`, `topicRelevance`이고, 4건은 `evidenceAttribution`, `multiLabelAccuracy`, `outputContract`이다. 기준서에 두 키 묶음이 있다고 적고, 사람 점수는 이 가중 합(합계 100)이 아니라 `rubric.usage`의 1/3/5만 사용한다고 고정한다.

### 3.7 기준서에 그대로 둘 내용

- 대상 호출이 답변 API 내부의 `TaggingAgent.tag`라는 점.
- 타임아웃 4초와 재태깅 8초 (`app/core/config.py` 89–91행).
- 80건, 분할 16/48/16, `label_accuracy` 79건과 `code_behavior` 1건.
- 점수 출력 금지, JSON만 출력.
- 필터가 미등록 차원명을 버린다는 운영상 은폐, 그리고 raw 검사 제안.
- 사람 검토는 자동 지표가 충돌한 사례의 보조이고, 합격 앵커는 3점.

---

## 4. 검토에 쓰지 않은 것

라이브 모델 호출, Langfuse 트레이스, 사람 채점은 하지 않았다. 85%가 이 데이터에서 달성되는지는 측정값이 없다. 라인 번호는 2026-10-07 작업 트리의 `agents.py` 기준이다.
