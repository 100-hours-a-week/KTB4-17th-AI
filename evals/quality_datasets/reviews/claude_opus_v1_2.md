# 품질 데이터셋 v1.2.0 독립 재검토 (Claude Opus)

- 대상: `evals/quality_datasets/` 6개 JSONL(320건), schema/manifest/README/audit/revision 보고서, 생성기·검증기·테스트, 근거 코드 `app/features/{persona,practice,simulation}`, 기획서 `docs/v1docs/langfuse-quality-dataset-plan.md`
- 방식: 읽기 전용. 외부 네트워크·OpenRouter·모델 API·API 키 미사용. 규칙 오라클은 로컬 `.venv`에서 앱 모듈의 순수 함수(`score_layer`, `overall_score`, `accuracy_of`, `_history` 모사, `normalize_script`, `_normalize_speaker_labels`, `ScriptOutput`/`ReportNarrative` 검증, `next_topic`)를 호출해 재계산했다. LLM 호출은 없다.
- 자동 audit의 정확 일치 0·근사 충돌 0은 의미 동등성의 증거로 쓰지 않았다. holdout 64건을 다른 split의 대응 사례와 직접 읽어 비교했다.

## 1. 결론

| 항목 | 값 |
| --- | --- |
| 점수 | **82 / 100** |
| critical | **0** |
| high | **1** |
| medium | **8** |
| low | **7** |
| 통과 여부 | **실패** (90점 미만, high 1) |

재작성 25건 중 12건은 장면이 분리되었고 6건은 경미한 모티프만 공유한다. 5건은 부분적으로만 분리되었고, 2건(build-044, practice-060)은 다른 split과 명제·대화 행위가 같아 분리에 실패했다. 앱 코드로 계산되는 오라클(시뮬레이션 규칙 점수 60건, preview 이상형 재계산 24건, build·practice·simulation의 경계·장애 계약)은 모두 코드와 일치했다. 통과를 막는 high는 low_confidence holdout 페르소나 입력에 루브릭의 목표 행동("확정적 단정" 회피)이 데이트 기피 항목으로 들어간 문제다.

v1.1의 81점과 이 점수는 서로 독립된 검토라 직접 비교할 수 없다. 이번 검토에서 새로 찾은 항목(H1, M3, M5)은 v1.1에도 있었을 가능성이 높다.

## 2. 점수 산정

| 영역 | 배점 | 득점 | 근거 |
| --- | ---: | ---: | --- |
| 코드·규칙 오라클 정합성 | 30 | 28 | 수치 오라클 불일치 0. 감점: M4(선택지 수 계약), M8(도달 불가 상태 라벨) |
| holdout 독립성 | 30 | 21 | 감점: H1 −4, M1 −3, M2 −1, M3 −1 |
| 라벨·기대 답 타당성 | 15 | 13 | 감점: M5 −1.5, L1·L7 −0.5 |
| 루브릭의 사례별 판정 이유 | 10 | 8 | 감점: M6 |
| 범주 커버리지 | 10 | 8 | 감점: M7 −1.5, L2 −0.5 |
| 재현성·자동 검증·문서 | 5 | 4 | 재현성·검증 통과. 감점: audit가 페르소나 텍스트 필드를 검사하지 않음, README 오기 |
| **합계** | **100** | **82** | |

## 3. 발견 사항

### Critical — 0건

실행 코드와 모순되는 수치·등급·경계 오라클을 찾지 못했다(§5).

### High — 1건

**H1. holdout 페르소나 입력에 루브릭 목표 행동이 섞임**
- 해당 사례: `simulation_run-031`, `-032`, `-033`, `simulation_report_preview-021`, `-022`(모두 blind_holdout, family `simulation-pair-low_confidence`)
- 근거: 두 페르소나의 `date_avoid`가 `["확정적 단정"]`, `["확정적 단정", "밤샘"]`이다. 031·032·033의 rubric anchor 5는 "낮은 신뢰도에서는 관계에 대한 **확정적 결론을 유보**한다"이다. `persona/profile.py:describe`는 이 값을 "피하는 것: 확정적 단정"으로, `simulation/agents.py:_persona_section`은 "피함: 확정적 단정"으로 SUT 프롬프트에 그대로 넣는다. 채점 기준의 정답 행동이 입력 힌트로 주어지므로 holdout 점수가 부풀려진다.
- 부수 오류: 같은 값이 `ruleOracle.dateSuggestion.avoid`에 `["확정적 단정", "밤샘"]`으로 고정되어 있다. 사용자에게 데이트 기피 항목으로 표시될 문자열을 정답으로 인정하는 셈이다.
- 같은 패턴이 regression에도 있다: `simulation_run-034~036`, `simulation_report_preview-023·024`의 `date_avoid` "프로필 밖 추측"과 anchor 5 "정보가 풍부한 페르소나처럼 서술하지 않는다". forbidden의 "프로필 밖 직업·나이·거주지"와 같은 평가 용어다.
- 조치: `date_avoid`를 실제 데이트 항목으로 바꾸고, 신뢰도가 낮다는 사실은 `confidence`로만 전달한다.

### Medium — 8건

**M1. 재작성 25건 중 7건의 분리가 불완전함** (사례별 판정은 §4)
- `persona_build-044`(holdout) ↔ `persona_build-001`(calibration): "이번 여행에서는 하루를 따로 보내기로… 상대가 합류하자고 했지만 그날만큼은 제 계획을 지켰죠"와 "지도 보는 오전만큼은 혼자 있고 싶어요… 같이 여행해도 개인 시간은 지켜요"는 같은 명제다. 여행 중 상대 제안을 거절하고 개인 시간을 지킨다는 장면, 'X만큼은' 한정 구조가 같다. 분리 실패로 본다. 다만 이 사례의 점수 오라클은 후보 출력의 기계적 검사(65 경계)라서 평가 영향은 제한적이다.
- `practice_reply-060`(holdout) ↔ topic_switch 템플릿 `practice_reply-006·012·018·030·036·042·048·054`: "계획 얘기는 여기까지 하고 완성한 모형을 보관하는 방법을 들려주세요"와 "실 가게 일정은 다음에 정하고, 뜨개질을 처음 시작할 때 어려웠던 점을 이야기해 봐요"는 골격이 같다(데이트 일정 화제를 닫고 → 취미 질문). 사건 순서와 대화 행위도 같다. 분리 실패로 본다.
- `practice_reply-024`: "날짜보다 궁금한 게 생겼어요. 반죽이 부풀지 않을 때…"는 문장은 다르지만 행위와 순서가 위 템플릿과 같다(부분 분리).
- `simulation_report_preview-016`(holdout) ↔ `simulation_report_preview-018`(regression): 둘 다 공예 재료나 부스를 고르는 장면이다. 앞 세 박자(A의 선호·조건 → "저는 다른 재료/부스를 보고 있었는데/보고 싶어도" 말하지 못함 → A가 "금세 거칠어져요"/"날카롭게 말할 수도") 가 같다. 뒤쪽 전개(철수 대 순응)는 달라 부분 분리로 본다.
- `persona_build-027` ↔ `-023`: 기준 진술 → 기준을 어긴 과거 상대와의 만남 중단 → 가치 한 줄("성실함이 중요해요"/"경제적 전망을 빼놓을 수 없더라고요")로 골격이 같다.
- `persona_build-028` ↔ `-024`·`-026`: "상대가 X해도 그 조건은 따지지 않았어요 / 결정에 영향이 없었어요"를 두 번 반복하는 골격이 같다.
- `persona_build-042` ↔ `persona_onboarding_tagging-059`(regression): "만남을 시작한 뒤 제 마음을 살펴보고 싶어요"와 "만남을 시작하지 않아서 먼 계획까지는 아직 생각을 못 해 봤어요"는 관계 진지도 판단을 교제 시작 이후로 미루는 같은 대화 행위다.
- 참고: 위 쌍의 정규화 SequenceMatcher 비율은 0.07~0.39, 3-gram Dice는 0.00~0.12다. audit 임계(0.76/0.45)로는 원리상 잡히지 않는다.

**M2. 25건 목록 밖 holdout의 문장 골격 누수**
- `persona_onboarding_tagging-054`(holdout) "직업이나 경제 사정은 상대를 만날지 정할 때 거의 보지 않아요" ↔ `-046` "약속을 잘 지키거나 친절한지는 상대를 고를 때 큰 기준은 아니에요", `-050` "외모나 활발한 분위기는 제가 상대를 고르는 기준이 아니에요"(regression). 골격이 "[A]나 [B]는 상대를 고를/만날지 정할 때 기준이 아님"으로 같고 명사와 서술어만 바뀌었다. v1.1에서 지적한 명사 치환 유형에 가장 가깝다.
- `-034`(holdout) "다투는 중에도 자리를 지키고 대화를 끊지 않아요" ↔ `-038` "화가 나도 목소리를 낮게 유지하고 날카롭게 받아치지는 않아요"는 "[갈등 상황]에도 X를 유지하고 Y하지 않아요" 골격이 같다.
- `-013`(holdout) ↔ `-012`(regression)는 "[primary 행동]. [~하고 나면] [긍정적 후속]" 골격이 같고 secondary 정답도 둘 다 `positivity`다.

**M3. simulation·preview 페르소나 입력이 명사만 바꾼 템플릿임**
- 24개 페르소나 쌍이 모두 같은 틀을 쓴다: a는 `interests=[장소]`, `date_prefer=[장소, "산책"]`, `date_avoid=[X]`이고 b는 `date_prefer=[장소]`, `date_avoid=[X, "밤샘"]`이다. 양쪽 모두 `narrative=null`, `routine=[]`이다.
- holdout `simulation_run-020·021·023·031·033`은 입력 전체가 이 페르소나 쌍이다. 장소 명사와 64/65/80 차원만 regression(`-013·016·024` 등)과 다르다.
- 기획서 10.3의 "프로필 텍스트가 긴 경우"는 한 건도 없다. audit의 `TEXT_KEYS`는 페르소나 목록 필드를 검사하지 않아 이 패턴을 보지 못한다.

**M4. orientation 선택지 계약이 코드와 다름**
- 코드: `schemas.py` orientation `choices=("진지하게 만날 사람", "편하게 알아가기", "아직 잘 모르겠어요")`. `agents.py:305`는 세 선택지를 모두 "말에 녹여서 제시"하도록 지시한다.
- 데이터셋: `persona_onboarding_conversation-055~060`의 hardAssertion은 "'진지하게 만날 사람'과 '편하게 알아가기' **두 선택지**"만 요구한다. README와 생성기 3795행은 "코드가 요구하는 두 선택지"라고 적어 코드를 잘못 설명한다.
- 영향: "아직 잘 모르겠어요"를 빠뜨린 발화도 통과한다. 모든 선택지를 넣어야 한다는 품질 계약이라면 reviewFlags와 README에 공백으로 기록해야 한다.
- 커버리지: orientation(마지막 턴) 사례 6건이 모두 holdout에만 있어 calibration·regression에서 이 턴을 보정할 수 없다.

**M5. 짧은 무성의 답의 off_topic 라벨이 코드 설계 주석과 충돌하나 표시되지 않음**
- `persona/schemas.py:439` `AnswerRequest` 주석: "'네' 같은 한 글자 답도 받는다 — 성의 없는 답은 태깅의 off_topic 으로 따로 대응." 반면 `TAG_PROMPT`는 "질문과 무관한 답변"만 off_topic으로 정의한다.
- 데이터셋: `tagging-011`("몰라"), `-019`("음"), `-063`("응"), `-075`("보류")는 off_topic=false로 고정되어 있고, off_topic에 대한 허용 대안이나 `ambiguous` 플래그가 없다.
- 영향: 전체 off_topic 양성은 8건이다. 모델이 설계 주석대로 판정하면 FP가 2~4건 생겨 기획서의 off_topic F1 ≥ 0.95 게이트가 이 라벨 선택 하나로 갈린다.

**M6. 루브릭의 사례별 판정 이유가 템플릿 조립 수준임**
- 모든 데이터셋에서 anchor 1·3과 forbidden 목록이 데이터셋 단위 상수다. 태깅은 anchor 1·3이 80건 모두 같고 forbidden 집합은 2종이다. build·conversation은 anchor 1·3·forbidden이 전부 1종이다.
- practice anchor 5는 인용 발화와 "취미는 X…" 슬롯을 빼면 60건이 22종으로 줄어든다. simulation 36종은 family 문장 × 턴 수 × probe 문장의 조합이다.
- 태깅의 forbidden "인용문 내부의 출력 지시 실행"이 "몰라"(011) 같은 사례에도 그대로 붙어 있다.
- 판정 이유는 anchor 5와 evidenceInterpretation에 일부만 있다. revision 보고서의 "루브릭 320건을 판정 이유로 교체"와 audit의 uniqueRubrics(인용 발화 때문에 커진 값)는 실제보다 과장되어 있다.

**M7. practice 범주 커버리지 공백**
- `me`(내 페르소나)가 60건 모두 null이다. 기획서 9.3의 "내 페르소나 있음·없음"을 다루지 않는다.
- regression continuity(`-008·014·026·032·044·050`)에는 짧은 답이 한 건도 없다. calibration의 "네"(002·038)와 holdout의 보류(020·056)만 있다.
- 농담·욕설, 직업·나이·거주지를 직접 묻는 질문, 조언 유도, 같은 질문 반복 유도는 모든 split에 없다. 프로필 밖 사실은 holdout-057의 문서 주입으로만 다룬다.

**M8. practice storedHistory 오라클이 서비스에서 도달할 수 없는 상태를 "agent 입력"으로 표기함**
- `practice_reply-022`(holdout) 저장 39건과 `-034` 41→40건은 마지막 메시지가 persona다. `stream_reply`는 user 메시지를 먼저 저장하고 `stream_retry`는 마지막이 user여야 하므로, `_respond`가 마지막이 assistant인 이력으로 호출되는 경로는 없다.
- `-028`을 포함한 세 건 모두 `history[-1]`의 현재 user 발화가 `storedHistory`에 없다. 그런데 `codeBoundary.messagesPassedToAgent`에는 이 발화가 빠진 목록이 적혀 있다.
- `_history()` 순수 함수의 결과로서는 값이 맞다(재계산 3/3 일치). 이름과 상태 설정이 실제 agent 입력과 달라 어댑터가 오용할 수 있다.

### Low — 7건

- **L1** 태깅 direct+indirect 8건(004·008·012·013·028·042·044·060)의 secondary 정답이 `Topic.also_touches`와 정확히 같다. 이 중 `-008`의 `contact_rhythm`은 본문의 둘째 문장("새 말풍선이 생기는지만 자꾸 보게")이 불안의 반복일 뿐이라 근거가 약하다. 빈 배열 대안을 허용하므로 low로 둔다.
- **L2** 순수 간접 근거(primary 빈 배열, secondary만 있음) 9건이 모두 calibration·regression에 있고 holdout에는 0건이다. 반대로 `quoted_boundary_injection` 4건(016·036·056·076)은 모두 holdout에만 있고 주입 문장도 4건이 같다. calibration에 해당 유형이 없다.
- **L3** 온보딩 `historyTopicIds` 순서(weekend → interests → ideal_type …)는 `next_topic`이 만들 수 없다. covers를 태그로 가정해 재현하면 코드 순서는 weekend → ideal_type → hard_times → interests …이다. 발화 생성 agent는 topic ID를 보지 않아 영향은 현실감 수준이다(`-002·003·005·008·009·011`, holdout `-037~042·055~060`의 이력).
- **L4** `persona_build-032`(calibration)와 `tagging-077`(regression)이 `agents.py` RUBRIC의 예시 문장을 바꿔 쓴 것이다. 기획서 14.2는 예시 문장 제공을 금지한다. 둘 다 holdout이 아니고 프롬프트가 명시한 정상 조합을 검사하는 용도라 low다.
- **L5** build `acceptableRanges`가 의미 범위(70~95 등)와 후보 출력의 기계적 단일값(044: 65–65, 045: 64–64, 057: 59–59, 058: 60–60)을 같은 필드에 섞는다. 플래그로 구분하지만, 기획서의 "허용 범위 적중률 ≥ 90%"를 일괄 계산하면 오집계된다.
- **L6** holdout 안의 약한 모티프 공유와 fixture 불일치:
  - build-008과 tagging-032는 "둘이 적은 종이·서랍" 모티프를 공유한다.
  - build-017 "여행 경로로 다툰 날엔"과 build-020 "여행지로 다툰 날도"의 표현 틀이 같다.
  - build-018 "지난 겨울 가족 행사 문제로 부딪혔을 때도"와 build-015 "지난달 비용 문제로 다퉜을 때"의 표현 틀이 같다.
  - build-045와 preview-014·015는 build-032·RUBRIC 예시와 같은 "잠시 떨어졌다 다시 만남" 순서를 쓴다.
  - `simulation_run-032` 후보 대본은 8줄인데 `highlightContract.indexRange`는 [0, 19]다.
- **L7** 표현 자연성과 라벨 근거:
  - build-007 `date_prefer: "함께 여행표 붙이기"`는 기록장 작업이지 데이트 선호로 보기 어렵다.
  - 태깅 062·066·070 "X에 대해서는 아직 제 선호를 정하지 않았어요"는 질문에 대한 답으로 부자연스럽다.

## 4. 재작성 25건 사례별 판정

| caseId | split 대응 비교 | 판정 |
| --- | --- | --- |
| tagging-074 | 062·066·070의 "…선호를 정하지 않았어요" 템플릿과 사건·문장 모두 다름(행사 표 → 가 본 뒤 판단) | 분리 |
| build-007 | openness 두 번 점검 → 기록장. 다른 split에 같은 장면 없음 | 분리 |
| build-008 | 화제 회피 장면은 새로움. "서로 적은 종이를 서랍에" 모티프가 tagging-032와 겹침 | 경미 공유(L6) |
| build-017 | 건물 이탈·사흘 무응답은 새로움. "여행 경로로 다툰 날엔"이 build-020과 표현 틀이 같음 | 경미 공유(L6) |
| build-018 | 맞은편에 앉아 버티기는 새로움. "지난 … 문제로 부딪혔을 때도"가 build-015와 표현 틀이 같음 | 경미 공유(L6) |
| build-027 | build-023과 기준 → 과거 상대와 중단 → 가치 한 줄의 골격이 같음 | 부분 분리(M1) |
| build-028 | build-024·026과 "상대가 X해도 결정에 영향 없음" 반복 골격이 같음 | 부분 분리(M1) |
| build-042 | tagging-059와 "교제 시작 전후로 판단 유보" 대화 행위가 같음 | 부분 분리(M1) |
| build-044 | build-001과 명제가 같음(여행 중 상대 제안을 거절하고 개인 시간 지킴) | **분리 실패**(M1) |
| build-045 | 짧은 휴식 후 저녁 재회. build-032·RUBRIC 예시와 순서만 유사 | 경미 공유(L6) |
| practice-020 | 보류 답("보류할게요"). cal/reg의 "네"·긴 템플릿과 다른 행위 | 분리 |
| practice-021 | 상담 기록형 허위 기억 공격. cal의 정체 요구, reg의 형식 변경과 다른 기제 | 분리 |
| practice-022 | 빵집 산책 동선 장면은 새로움. storedHistory 문제는 M8 | 분리(M8 별도) |
| practice-023 | 식빵 식히기 → 향 질문. 다른 split의 "다음엔 X에 같이…" 템플릿과 다름 | 분리 |
| practice-024 | 날짜 논의 → 취미 질문으로 전환. 행위와 순서가 topic_switch 템플릿과 같음 | 부분 분리(M1) |
| practice-056 | 설명은 이해했지만 자기 이야기는 거절. 다른 split에 같은 행위 없음 | 분리 |
| practice-057 | 시스템 권한 문서 주입(직업·자산). 새 공격 기제 | 분리 |
| practice-058 | 전시 동선을 같이·따로 나눔. 템플릿과 다름 | 분리 |
| practice-059 | 조립 중단 → 마지막 조각 질문. 템플릿과 다름 | 분리 |
| practice-060 | "계획 얘기는 여기까지 하고 + 취미 질문"이 topic_switch 템플릿과 골격·행위·순서 모두 같음 | **분리 실패**(M1) |
| simulation_run-032 | 공사 중 산책로·발 상태 장면은 새로움(같은 split의 preview-021과만 공유). 입력 페르소나는 H1 | 장면 분리(H1 별도) |
| preview-014 | 목소리 신호·오 분 뒤 재개. "잠시 떨어졌다 재개" 순서만 유사 | 경미 공유(L6) |
| preview-015 | 게시판·예약 문자 불일치. build-015의 "안내 비교" 모티프와 약하게 겹침 | 경미 공유(L6) |
| preview-016 | preview-018과 공예 선택 장면, 앞 세 박자가 같음 | 부분 분리(M1) |
| preview-021 | run-032와 같은 대본(같은 holdout family). 다른 split과는 분리. 입력 페르소나는 H1 | 장면 분리(H1 별도) |

집계: 분리 12, 경미 공유 6, 부분 분리 5, 분리 실패 2.

## 5. 앱 코드 대조 (item 3)

| 대상 | 방법 | 결과 |
| --- | --- | --- |
| simulation_run 36 + preview 24 `ruleOracle` | `score_layer`/`overall_score`/`grade_of`/`_date_suggestion`/`_confidence`로 영역 점수·차원 점수·신뢰도·총점·등급·위험·정확도·데이트 제안 재계산 | 60/60 일치 |
| preview `idealRecalculationOracle` | 허용 온정 값(null 포함)을 모두 넣어 총점·등급 구간 재계산 | 24/24 일치(010·011: 69~76 OK/GOOD, 019·020: 38~45 CAUTION/OK) |
| simulation 후보 출력 11건 | `_normalize_speaker_labels`, `ScriptOutput`, `normalize_script`, highlight 필터 | 저장 줄 수·화자 정규화·잘림·invalid_script·script_too_short·범위 밖 highlight 제거·범위 안 오인용 잔존 모두 일치 |
| simulation 장애 계약 | agents `LLMError.reason` → `SimulationFailed` → API 503 | timeout·invalid_json·invalid_script 일치 |
| preview 후보 서술 6건 | `ReportNarrative` 검증과 `assemble_report` 동작 | ideal 101 허용(범위 제약 없음), null 허용, `cautions[:5]`의 위험 문장 유실 모두 일치 |
| 페르소나 180개(practice 60, sim 72, preview 48) | `accuracy_of`, 15개 score/confidence 키 | 180/180 일치 |
| build 경계·장애 20건 | `RawExtraction` 범위·headline 60자·요약 카드 폐기·중복 category·change ±10·null → 50/LOW·fallback 80/25/없음·재빌드 BuildFailed·HIGH 전원 정확도 100 | 모두 일치 |
| build postService | `confidence_of`/`accuracy_of` (예: 007 HIGH 1개 → 7, 044 MEDIUM 1개 → 4) | 표본 전부 일치 |
| practice 코드 경계 | 500/501자, `HISTORY_WINDOW=40`과 도입 지시 삽입(3건 재계산), `profile.py` 35/36/64/65 표시 경계, 스트림 장애 3종·SessionEnded·retry | 모두 일치(M8은 상태 도달성·표기 문제) |
| onboarding 서비스 경계 | heavy_5, closing(turn 9 → orientation), 재질문 1회(alreadyReasked 참/거짓), seed·template 폴백, 첫 턴 파서 실패 | 모두 일치(M4는 선택지 수 계약) |
| 태깅 라벨 | `TAG_PROMPT` 정의 대조 | 명백한 오답은 없음. M5(짧은 답 off_topic)와 L1(also_touches 일치)만 지적 |

## 6. 범주 전수 확인 (item 4)

| 데이터셋 | 짧은 답(1~3자 등) | 농담 | 욕설 | 간접 근거 |
| --- | --- | --- | --- | --- |
| tagging | cal: 043 꺼져, 063 응 / reg: 011 몰라, 019 음, 027 시발 / holdout: 035 닥쳐, 055 검색해, 075 보류 | cal 023 / reg 047 / holdout 015 | cal 043 / reg 027 / holdout 035 | direct+indirect: cal 004·044, reg 008·012·028·060, holdout 013·033·053 / 순수 간접: cal 024·064, reg 020·032·040·048·052·068·072, **holdout 0** |
| onboarding conversation | cal: 네·조금요 / reg: 아마요·모르겠어요·글쎄요·기억 안 나요·생각 중이에요·잘 모르겠네요 / holdout: 답은 보류할게요·침묵으로 남길게요 | 없음 | 없음 | 해당 없음 |
| practice | cal: 네(002·038) / **reg 0** / holdout: 보류할게요(020), 거절 문장(056) | 없음 | 없음 | 해당 없음 |

태깅은 세 범주가 모든 split에 실제로 있다. 대화·연습 데이터셋에는 농담·욕설이 없고 practice regression에는 짧은 답이 없다(M7). 욕설 3건은 모두 한 단어로 같은 대화 행위다. 범주 특성상 피하기 어려워 누수로 세지 않았다.

## 7. 실행한 검증 (쓰기 없이)

- `python3 -B scripts/generate_quality_datasets.py --check`: PASS(320건, schema/manifest/README 일치)
- `python3 -B scripts/validate_quality_datasets.py --json`: ok, errors 0, warnings 0
- `python3 -B evals/quality_datasets/audit_quality_datasets.py`: ok, 근사 충돌 0
- `.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_quality_datasets.py evals/quality_datasets/test_audit_quality_datasets.py`: 16 passed. 전체 스위트는 실행하지 않았다.
- 보조 근사 스캔(SequenceMatcher ≥ 0.55 또는 3-gram Dice ≥ 0.35, 10자 이상 문장): holdout ↔ 다른 split에서 걸린 것은 면담자 질문 수준의 쌍 10개뿐이다. §3의 잔여 누수는 모두 이 임계 아래에 있어 사람이 직접 읽어서 찾았다.
- `git status`는 검토 전후로 같다. 이 문서 외에는 쓰지 않았다.

## 8. 통과에 필요한 최소 조치

1. H1: low_confidence·missing_scores 페르소나의 `date_avoid`를 실제 데이트 항목으로 바꾸고 `ruleOracle.dateSuggestion`을 재생성한다.
2. M1·M2: build-044, practice-060·024, preview-016, tagging-054·034·013을 다른 대화 행위와 사건 순서로 다시 쓴다. topic_switch는 날짜 → 취미 전환이 아닌 유형으로 바꾼다.
3. M4·M5: orientation 세 선택지 계약과 짧은 답의 off_topic 방침을 코드 기준으로 확정한다. 모호하면 허용 대안이나 플래그를 둔다.
4. M3·M6·M7·M8: 페르소나 틀 다양화(서술·일과·긴 프로필), 루브릭 anchor 1·3의 사례화, practice `me`·짧은 답·직접 신상 질문 추가, storedHistory의 도달 가능 상태 보정.

## 9. 한계

- 사람 이중 라벨링과 Judge calibration은 수행되지 않았다. 이 검토의 라벨 판단도 단일 검토자 의견이다.
- 의미 누수 판정은 사람이 읽고 한 판단이다. 걸러 낸 쌍 외의 추가 유사성이 없다고 보장하지 않는다.
- 전체 pytest 스위트와 ruff는 실행하지 않았다(데이터셋 테스트 2개 파일만 실행).
