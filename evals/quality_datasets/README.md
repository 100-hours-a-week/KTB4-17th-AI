# 오프라인 품질 데이터셋 v1.12.0

현재 persona 온보딩·태깅·build, practice, simulation 코드 및 품질 데이터셋 기획서를 근거로 작성한 **합성 입력 330건과 평가 계약 초안**이다. 운영 대화·개인정보·API 키를 사용하지 않았다. 파일을 만드는 동안 외부 네트워크, OpenRouter, 모델 API를 호출하지 않는다.

## 구성

| 파일명(JSONL) | 전체 | calibration | regression | blind_holdout |
| --- | ---: | ---: | ---: | ---: |
| `persona_onboarding_conversation` | 60 | 12 | 36 | 12 |
| `persona_onboarding_tagging` | 80 | 16 | 48 | 16 |
| `persona_build` | 60 | 12 | 36 | 12 |
| `practice_reply` | 70 | 14 | 42 | 14 |
| `simulation_run` | 36 | 7 | 22 | 7 |
| `simulation_report_preview` | 24 | 5 | 14 | 5 |
| 합계 | 330 | 66 | 198 | 66 |

각 줄은 `input`, `expectedOutput`, `metadata`를 갖는다. `schema.json`은 Draft 2020-12 공통 구조와 데이터셋별 필수 입력을 정의한다. `manifest.json`에는 파일·생성기·근거 소스의 SHA-256, 분포, 누수 검사 결과, 전역 family 배정이 있다.

## 재생성과 검증

저장소 루트에서 이미 설치된 Python으로 실행한다. 생성기는 표준 라이브러리만 쓴다.

```sh
python3 scripts/generate_quality_datasets.py
python3 scripts/generate_quality_datasets.py --check
python3 scripts/validate_quality_datasets.py --json
python3 evals/quality_datasets/audit_quality_datasets.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check scripts/generate_quality_datasets.py evals/quality_datasets
.venv/bin/ruff format --check scripts/generate_quality_datasets.py evals/quality_datasets
```

`--check`는 쓰지 않고 현재 소스에서 다시 조립한 바이트와 모든 산출물을 비교한다. 파일명, 행 순서, 문장, 분할, 해시가 결정적이며 시각·환경 변수·난수·앱 모듈 import에 의존하지 않는다. 소스가 바뀌면 provenance 해시도 바뀌므로 변경을 검토하고 재생성한다. `--output-dir`로 별도 디렉터리에 생성할 수도 있다.

## 입력과 정답의 해석

- `input.task`에 따라 실제 agent 인자를 구성한다. `candidateOutput`, `candidateNarrative`, `faultInjection`, `serviceProbe`, `requestProbe`, `ruleProbe`, `storedHistory`는 **평가 어댑터용 fixture**다. 모델 프롬프트에 그대로 넣는 정답 힌트가 아니다. 이 저장소 변경은 모델 실행 어댑터를 구현하지 않는다.
- 일반 사례는 실제 합성 발화, 프로필, 이전 대화, 지정 주제를 담는다. 경계 사례는 200/201자 답변, 500/501자 메시지, 39/40/41개 이력, 35/36/64/65 프로필 경계, 0/100과 범위 밖 점수, 위험 64/65, 등급 44/45/69/70, 3/10/15왕복을 포함한다.
- `hardAssertions`는 원하는 품질 계약이다. `codeBoundary`는 주어진 후보 출력·상태에서 **현재 코드의 동작**이다. 둘이 다를 때 `reviewFlags`와 설명에 공백을 기록한다. 현재 코드가 허용한다고 품질 성공으로 처리하면 안 된다.
- build `rawModel`의 근거 없는 점수 키 생략과 `postService`의 미확인 차원 null(`unknownScores`)·confidence·accuracy를 분리한다. 서비스는 답변에서 근거가 확인된 차원만 값을 남긴다. 기계적 후보 출력 검사는 `mechanical_oracle_not_semantic_label`로 구별한다. 점수의 의미는 `acceptableRanges`라는 검토 대기 범위이며 정확한 단일 정답으로 강요하지 않는다.
- 시뮬레이션 `ruleOracle`은 `pre_llm_rules_ideal_unjudged` 단계다. 이상형 세 차원은 미판정 null이며 실제 생성 대화로 채우면 영역·총점을 다시 계산해야 한다. 입력 점수 누락은 50이 아니라 null(모름)이며 그 차원의 규칙 점수도 null이다. 백엔드 계약(#63) 때문에 화면 표시값 `dimensionDisplay`만 50이다. 모든 영역이 비어 총점 50이 되는 `overall_score` 직접 호출 검사와 구분한다.
- 고정 preview의 인용 후보는 원문을 그대로 담고 `ideal_*`의 판단 불확실성을 남겼다. LLM 실패/seed/fallback/template은 회복성 성공과 별개로 모델 품질 성공에 포함하지 않는다.

## 출처와 검토 상태

`generationMethod=astra-xhigh-v1`은 이 세션의 Astra xhigh가 코드 기반 합성 카드와 평가 초안을 작성한 방법 식별자다. 실행 시 Astra API를 호출한다는 의미가 아니다. `piiClass=synthetic`, `rubricVersion=1.0`이며 모든 항목은 `human_anchor_pending`이다. 사람 2인의 독립 라벨링·조정, Judge calibration, 실제 모델의 baseline/품질/지연/비용 평가는 **아직 수행하지 않았다**. 따라서 이 초안을 승인된 사람 정답이나 배포 차단 근거로 바로 사용하지 않는다.

`sourceRefs`는 저장소 상대 경로와 작성 시점의 심볼 시작 행이다. 실행 코드가 우선이고 프롬프트 계약과 Pydantic의 길이 상한 차이도 보존한다. 운영 모델명·endpoint·비밀값은 기록하지 않는다. source hash는 공개 코드 파일 바이트만 대상으로 한다.

## 분할과 누수 방지

같은 원형의 high/low 태깅과 build 파생은 `evidence-*` family에 묶었다. 동일 persona pair의 simulation과 report-preview는 같은 family에 묶되 family별 항목 수를 조절하여 각 Dataset도 20/60/20(36건은 7/22/7, 24건은 5/14/5)으로 맞췄다. 대화 주제 및 practice 프로필 변형은 각각 한 family다. `splitGroup=familyId`이며 전역 family와 prototype의 split 이동이 없다. `caseId`는 데이터셋과 안정적인 작성 순번이다. 중복을 피하려 입력에 무의미한 case ID를 덧붙이지 않는다.

`blind_holdout`은 데이터 분할 표식이다. 같은 저장소에 평문으로 제공되므로 접근 통제된 진짜 비공개 holdout을 보장하지 않는다. 프롬프트 작성자가 본 이후에는 새 원형을 독립 작성하여 봉인해야 한다. 자체 audit는 입력 발화·인용·후보 출력의 전체 문자열과 개별 문장을 검사한다. 정확 일치는 공백·종결 부호를 정규화하고 한 글자 답도 포함한다. 코드로 고정된 첫 인사 한 문장만 예외다. 근사 검사는 NFKC·문장부호·공백 정규화 뒤 SequenceMatcher 비율 0.76 이상, 문자 3-gram Dice 0.45 이상, 공통 문자 18자 이상을 동시에 요구한다. 20자 미만 관용문과 단일 문자 반복 길이 fixture는 근사 검사에서 제외한다. 실제 사례와 오탐 대조군을 단위 테스트로 확인한다. 이 검사는 문장 구조 유사를 찾는 휴리스틱이며 모든 의미 동등성을 증명하지는 않는다.

v1.2에서는 지적된 holdout 25건을 사건·문장 구조·대화 행위가 다른 장면으로 재작성했다. 태깅 074, build 007·008·017·018·027·028·042·044·045, practice 020~024·056~060, simulation_run 032, preview 014·015·016·021이 대상이다. `audit_report.json`은 이 25건의 검사 결과와 전체 교차 split 충돌을 별도로 보고한다. `audit_before_v1_2.json`은 수정 전 v1.1의 참고 기록이며 최종 검증 결과가 아니다.

태깅의 간접 근거 9건은 기록·관찰·선택의 정황을 각각 따로 작성하고 보수적인 빈 secondary도 허용한다. split마다 짧은 답·농담·욕설/불성실 답을 배치하여 주제 내 판단 유보와 무관 답변을 구분한다. holdout에는 관계 점검 뒤 따뜻한 분위기, 갈등 이탈 중 거절 불안, 경제 안정 선호와 가족 소개 생각이라는 서로 다른 간접 라벨을 둔다. 부족한 답변 안에 내부 차원 이름을 넣지 않는다.

온보딩의 turn0 9건은 모두 weekend이고, history가 있는 51건은 첫 assistant 메시지에 실제 고정 intro를 포함하며 weekend 문답으로 시작한다. 첫 인사 파서·실패 fixture는 서로 다른 조건을 검사한다. 마지막 관계 방향 질문에는 코드가 요구하는 세 선택지, 진지하게 만날 사람·편하게 알아가기·아직 잘 모르겠어요를 자연스럽게 제시한다.

v1.12에서는 리포트 preview의 채점 방식을 정했다. LLM 서술 케이스는 인용 원문 일치·하이라이트 번호·이상형 범위·규칙 점수 불변·총점 재계산을 자동 지표(`autoMetrics.formatChecks`)로 판정하고 설명 서술의 질만 1/3/5점으로 사람이 본다(`rubric.role=auxiliary`). 템플릿·후보 검사는 `code_behavior`다. 이 리포트는 분석 문서라 몰입(20~30대 말투) 기준은 적용하지 않는다. 짧은 대화록·카테고리 편중·빈약한 프로필은 한계로 명시했다.

v1.11에서는 시뮬레이션 대본의 채점 방식을 정했다. 언어 품질 18건에 대화 말투 20점을 추가하고(성향 충돌 25·대본 형식 25·규칙 준수 20·마무리 10·대화 말투 20), 앱이 추가한 화자 분리 규칙(자기 프로필만 말하기, 상대 닉네임으로 부르기, 성향 차이가 말투에 드러나기)을 hardAssertions·forbidden에 넣었으며, 줄 수·교대·문장 수·이모지·자기 닉네임+님 금지·하이라이트 범위 같은 자동 형식 검사(`autoMetrics.formatChecks`)를 적었다. 나머지 18건은 대본 정규화·장애 처리를 검사하는 `code_behavior`다. 카테고리 편중과 빈약한 프로필은 한계로 명시했다.

v1.10에서는 연습대화의 채점 방식을 정했다. 언어 품질 48건 중 범위 밖 요청을 뺀 38건은 프로필 사실 30·이어가기 25·문제 조건 20·말투 25로 말투 비중을 올렸고(`rubric.criteriaGuide`), 몰입 실패는 1점 문구에 그대로 둔다(온보딩 발화의 ‘말투 항목만 감점’과 다른 방식이다). 언어 품질 48건에는 문장 수·질문 수·이모지·코드블록/마크다운·AI 키워드 자동 형식 검사(`autoMetrics.formatChecks`)를 적었고, 코드 동작 22건은 `code_behavior`로 나눴다. 프로필 빈약·me 비어 있음·짧은 이력은 한계로 명시했다.

v1.9에서는 페르소나 build의 채점 방식을 정했다. 성향 30건은 `evaluationKind=label_accuracy`로 점수 허용 범위 통과·모름=null·텍스트 유지/폐기를 자동 지표(`autoMetrics`)로 판정하고, 서술의 질만 1/3/5점으로 사람이 본다. 후처리·경계 30건은 모델이 아니라 서비스 코드의 결정적 동작을 검사하는 `code_behavior`다. 점수 허용 범위와 분할은 바꾸지 않았고 한계로만 명시했다.

v1.8에서는 온보딩 태깅의 채점 방식을 정했다. 정답이 라벨이라 자동 지표(`expectedOutput.autoMetrics`: primary·secondary 집합 일치, off_topic 정확 일치, 허용 대안 포함)를 주로 쓰고 1/3/5점은 자동 지표가 애매하다고 본 답을 사람이 보는 보조로 둔다(`rubric.role=auxiliary`). 태깅 79건은 `evaluationKind=label_accuracy`, 장애 주입 1건은 `code_behavior`다. 다중 라벨 부족과 카테고리 편중은 한계로 manifest에 적었다.

v1.7에서는 온보딩 발화의 채점 기준을 정리했다. 언어 품질 28건은 주제 유지·특수 조건·직전 답 반응·대화하는 말투·자연스러운 질문을 20점씩 채점하고(`rubric.criteriaGuide`), 챗봇 말투나 스스로 AI 언급은 총점 상한 없이 말투 항목만 감점한다(`rubric.deductions`). 코드 동작 32건은 기존 배점을 유지한다. 모든 온보딩 발화 문제에 `metadata.evaluationKind`(language_quality / code_behavior)를 달았다. 주제가 분할 하나에만 있는 한계는 그대로 두고 manifest에 명시했다.

v1.6에서는 “사람과 대화하는 느낌” 몰입 기준을 추가했다. 모델이 자연어 발화를 만드는 94건(온보딩 발화 28, 연습대화 48, 시뮬레이션 대본 18)에 `immersionContract`를 두었다: 소개팅 중인 20~30대의 해요체 메신저 말투, 챗봇·설명서 말투 금지, 정체를 묻지 않았는데 스스로 AI라고 밝히지 않기. 정체를 진지하게 묻는 `identity_injection`·`adversarial` 20건은 기존 계약(페르소나를 연기하는 AI라고 답하고 역할 유지)을 그대로 두고 말투 기준만 적용했다. 이 기준은 코드가 직접 강제하지 않는 제품 요구다. 케이스 수·분할·caseId는 그대로다.

v1.5에서는 Opus 재검토(78점, high 1)의 지적을 고쳤다. (1) build는 서비스가 텍스트 항목(관심사·일과·데이트 선호·기피)도 답변에서 확인된(answered) 것만 남기므로 입력에 `answeredDimensions`를, 기대값에 `textualKept`/`textualDropped`를 두고 텍스트 라벨이 있는 사례는 해당 키를 answered에 넣었다. (2) 위험 64/65·all_risks family는 양쪽이 아는 openness 60을 더해 위험 감점이 총점에 드러난다. 그래도 모든 영역이 null이면 감점이 사라지는 앱 동작은 `known_code_gap_risk_penalty_dropped_when_all_areas_null` 플래그로 표시했다. (3) preview 020의 페르소나는 값이 없으면 LOW인 도달 가능한 상태로 고쳤고, 값이 있는데 LOW인 옛 행 상태는 `legacy_row_state_value_with_low_confidence`로 표시했다. (4) 재시도 사유(speaker_mixup 포함)와 truncated 재시도의 max_tokens 1.5배를 runtime에 기록했다.

v1.4에서는 v1.3 생성 이후 바뀐 앱 코드에 맞춰 기대값만 정정했다. 케이스 수·분할·caseId는 그대로다. (1) 근거 없는 점수는 50이 아니라 null이다: 페르소나 입력의 빈 차원, 규칙 점수·위험 오라클, build의 `unknownScores`. (2) 시뮬레이션은 `max_tokens = 3000 + 300×turns`이고 깨지거나 잘린 출력은 1회 재시도하므로 `maxLlmCalls`는 2다. 계속 깨지는 invalid_json은 두 번 호출한 뒤 실패한다. 새 케이스(“저번에” 금지, 화자 분리 규칙 등)는 이번에 추가하지 않았다.

v1.3에서는 검토에서 막힌 항목만 고쳤다. low_confidence와 missing_scores의 데이트 기피 항목은 실제 상황으로 바꿨고, 문장 골격이 같던 build 044, practice 024·060, preview 016, tagging 013·034·054를 다른 사건으로 다시 썼다. 짧은 무성 답 011·019·063·075는 질문 관련 답으로 두되, 스키마 주석의 무관 답변 해석도 허용 대안으로 남겼다.

build 일반 30건은 별도로 작성한 3~4턴 대화에서 두 사건과 취미·일과·데이트 근거를 분리한다. 질문에는 내부 차원 이름을 넣지 않는다. build 033의 아직 시도하지 않은 연락 계획은 필수 점수 근거로 강제하지 않는다. practice 60개 및 preview 48개 페르소나는 열다섯 score/confidence 키와 accuracy_of 공식에 맞는 정확도를 가진다.

preview는 이상형 외 차원과 위험만 고정한다. 온정의 허용범위 또는 null을 반영한 총점·등급의 가능한 값은 `idealRecalculationOracle`로 기록한다. 케이스별 가능한 총점 집합과 등급 구간은 각 항목의 `idealRecalculationOracle`에 있다. 주입 후보와 template 실행은 별도 코드 경계 계약을 따른다. 모든 데이터셋의 루브릭은 내부 slug나 객체 repr 대신 사례의 판정 이유·실제 발화·금지 조건을 명시한다.

## 발견한 현재 코드와 품질 계약의 차이

- next_topic은 정상 후보가 모두 소진되면 마지막 두 턴의 HEAVY 금지를 완화한다.
- build headline은 프롬프트 40자와 schema 60자, summary title/content는 20/40자와 40/200자로 다르다.
- practice는 오류 없이 빈 스트림이 끝나면 빈 답변을 저장할 수 있다.
- simulation은 요청보다 짧아도 정규화 후 두 줄 이상이면 서비스가 허용한다. 화자 병합과 자르기는 모델 원본 품질을 가릴 수 있다.
- run은 highlight 범위만 필터링하고 quote 일치는 검사하지 않는다. preview 조립은 index와 quote 모두 검사하지 않는다.
- ReportNarrative의 ideal_fit은 현재 값의 0~100 범위 제약이 없다.
- 위험 caution을 뒤에 붙인 후 `[:5]`로 자르므로 기존 caution 5개가 있으면 위험 문장이 유실될 수 있다.

이 공백은 평가용 반례로 기록했으며 앱 코드 수정은 범위 밖이다. Langfuse 업로드와 모델 실행도 수행하지 않았다.
