# 품질 데이터셋 v1.4.0 독립 재검토 (Claude Opus)

- 대상: `evals/quality_datasets/` 6개 JSONL(320건), `revision_v1_4_report.md`, README, 생성기 `scripts/generate_quality_datasets.py`, 드리프트 테스트 `tests/test_quality_datasets_drift.py`, 근거 코드 `git diff f7cc806 HEAD -- app/features app/core/config.py`(11개 파일, +296/−60)
- 방식: 읽기 전용. 네트워크·OpenRouter·모델 API·API 키를 쓰지 않았다. 규칙 오라클은 로컬 `.venv/bin/python`에서 앱 모듈의 순수 함수를 직접 호출해 다시 계산했다. 사용한 함수는 `score_layer`, `overall_score`, `grade_of`, `_date_suggestion`, `_confidence`, `build_report(agent=None)`, `known_scores`, `confidence_of`, `accuracy_of`, `narrative_contradiction`, `valid_summaries`, `RawExtraction`, `_validate_script`, `_self_addressed_lines`, `describe`, `trait_lines`, `_persona_section`, `PartnerAgent.system_prompt`다. 생성기와 드리프트 테스트는 재계산 기준으로 쓰지 않았다.
- revision 보고서의 주장은 따로 확인했다(§5). JSONL의 줄 번호는 caseId 번호와 같다(4개 파일에서 확인). 그래서 근거 위치는 `파일:줄`로 적는다.

## 1. 결론

| 항목 | 값 |
| --- | --- |
| 점수 | **78 / 100** |
| critical | **0** |
| high | **1** |
| medium | **7** (새 항목 2, v1.2에서 넘어온 항목 5) |
| low | **7** |
| 통과 여부 | **실패** (90점 미만, high 1) |

수치 오라클은 전부 현재 앱 코드와 맞았다. 시뮬레이션·preview 규칙 오라클 60/60, 이상형 재계산 24/24, template 리포트 경로 24/24, build 후처리 60/60, practice 프롬프트 60/60을 확인했다. revision 보고서의 수치 주장(총점 60건 변경, 등급 변경 24건, 최대 변화 30점, `max_tokens`, 재시도 조건)도 재계산과 같았다. v1.2 검토의 high(H1, holdout 입력에 루브릭 목표 행동이 섞인 문제)는 v1.3에서 해소된 상태로 유지된다.

그런데 v1.4는 v1.3 이후 바뀐 앱 동작 중 하나를 반영하지 않았다. 서비스는 이제 답변에서 확인된(answered) 차원 밖의 **텍스트 항목도 버린다**. 이 변경 때문에 persona_build 31건의 텍스트 기대값이 코드와 모순된다(H1). 이 31건에는 holdout 7건이 들어 있고, 그중 텍스트 근거만 검사하는 holdout 사례(build-050)는 검사 목적 자체를 잃었다. 드리프트 테스트는 점수만 검사하고 텍스트 항목과 answered 집합은 보지 않는다. 그래서 "전부 통과"가 이 문제를 가리지 못했다.

**이전 점수(82)와의 비교:** 서로 독립된 검토라 직접 비교할 수 없다. 이번 검토는 v1.3 이후 바뀐 앱 코드와 데이터셋의 정합성에 무게를 두었다. holdout 64건의 의미 누수는 v1.2에서 지적한 사례와 v1.3 재작성 사례만 다시 읽었고, 64건 전체를 다시 읽지는 않았다(§6). H1은 v1.3 이후의 앱 변경(`46c87cd`)으로 새로 생긴 모순이라 82점 검토 시점에는 없었다. 점수가 4점 낮아진 것을 "데이터 품질이 나빠졌다"로 읽으면 안 된다. 기준이 된 앱 코드가 움직였고, 그 변경을 반영하는 일이 일부 빠졌다는 뜻이다.

## 2. 점수 산정

| 영역 | 배점 | 득점 | 근거 |
| --- | ---: | ---: | --- |
| 코드·규칙 오라클 정합성 | 30 | 22 | 수치 불일치 0. 감점: H1 −4, M1 −1.5(위험 감점 소실을 표시 없이 정답으로 고정), M2 −1.5(앱이 만들 수 없는 페르소나 상태), M7 −1(storedHistory 도달성, 이월) |
| holdout 독립성 | 30 | 26 | v1.4가 텍스트를 바꾸지 않아 악화는 없다. 감점: M3 −1.5(부분 분리 3건 잔존), M4 −1.5(페르소나 틀 동형, 알려진 차원 1~4개로 더 빈약해짐), 전수 재열람 미실시 −1 |
| 라벨·기대 답 타당성 | 15 | 11 | 감점: H1 텍스트 라벨 −2, M1 all_risks의 OK 등급 −1, L3·L4 −1 |
| 루브릭의 사례별 판정 이유 | 10 | 8 | M5(v1.2 M6 이월). v1.4는 상수 forbidden만 추가했다 |
| 범주 커버리지 | 10 | 7 | M6(practice `me` 0건 등, 이월), orientation holdout 전용(L6), 재시도·MBTI·정확도 40 이상 사례 없음(L2·L3·L5) |
| 재현성·자동 검증·문서 | 5 | 4 | 재현성·검증·테스트 통과. 감점: 드리프트 테스트가 텍스트·answered를 검사하지 않음, README 수치가 낡음(L1) |
| **합계** | **100** | **78** | |

## 3. 발견 사항

### Critical — 0건

실행 코드와 모순되는 수치·등급·위험·경계 오라클은 찾지 못했다(§4).

### High — 1건

**H1. persona_build의 텍스트 기대값과 입력이 v1.4 서비스의 answered 필터와 모순됨**
- 해당 사례: `persona_build-001~030`(dimension_high/low 30건, 이 중 holdout 007·008·017·018·027·028)과 `persona_build-050`(holdout, textual_grounding). 모두 31건이고 holdout은 7건이다.
- 앱 코드(v1.3 이후 `46c87cd`):
  - `app/features/persona/service.py:586`에서 `answered = await self._dimensions_from_answers(session)`을 구한다. 턴별 태깅의 **primary**와 보강 주제만 인정한다.
  - `service.py:621-627`은 `TEXTUAL`(interests·routine·date_prefer·date_avoid) 값이 있어도 `key not in answered`이면 `[]`로 버린다.
  - 추출 프롬프트 `app/features/persona/agents.py:571-599`(`_answered_section`)는 answered 목록을 붙인다. 그리고 "위 목록에 없는 차원·항목은 키를 생략하고, narrative·traits·summaries 에도 쓰지 마세요"라고 지시한다.
- 데이터셋: 입력에는 턴별 태그가 없어서 answered를 재구성할 근거가 `input.coverage.primary`뿐이다. 이 31건의 coverage에는 텍스트 키가 하나도 없다.
  - 001: `{"avoidance": 2}`. 050: `{}`.
  - 그런데 `referenceLabels`는 텍스트 정답을 요구하고(`001`: interests "고지도"·routine "주말 아침 지도 보기", `050`: interests "목공"·routine "일요일 도구 정리"·date_prefer "숲길 걷기"·date_avoid "시끄러운 술집"), `rawModel.textualFields`도 네 항목을 나열한다.
  - 재계산(coverage를 answered로 본 경우): 31건 모두 해당 텍스트 항목이 서비스 후처리에서 버려진다. 운영 프롬프트도 모델에게 그 키를 생략하라고 지시한다.
- 영향:
  - `persona_build-050`은 텍스트 근거만 검사하는 holdout 사례인데 answered가 비어 있다. 프롬프트에는 "(없음)"이 들어가고, 서비스 결과의 텍스트 항목은 전부 비게 된다. 사례 목적이 운영 경로에서 성립하지 않는다.
  - 데이터셋의 태깅 라벨 자체는 텍스트 차원을 primary로 표시한다(`persona_onboarding_tagging` 정답에 interests·routine·date_prefer·date_avoid가 5건 있다). 실제 온보딩이었다면 coverage에 텍스트 키가 쌓였어야 하므로, build 입력의 coverage는 태깅 계약과도 맞지 않는다.
  - 평가 어댑터가 `extract(history)`를 `answered` 없이 부르면 운영과 다른(옛) 프롬프트를 평가한다. `answered`를 coverage로 넣으면 텍스트 라벨이 오답이 된다. 어느 쪽이든 데이터셋만으로는 SUT 입력이 정해지지 않는다.
- revision 보고서 대조: "persona_build 60/60 변경(postService, forbidden, hardAssertions, raw_null)"이라고 적었지만 텍스트 필터와 `extract(answered=…)` 인자는 다루지 않았다. `tests/test_quality_datasets_drift.py:116`(`test_build_unanswered_dimensions_are_null`)은 점수 키만 검사한다.
- 조치:
  - build 입력에 `answeredDimensions`(또는 턴별 `tags`)를 명시해 SUT 프롬프트를 확정한다.
  - 텍스트 근거가 있는 사례는 coverage/answered에 해당 텍스트 키를 넣는다.
  - `postService`에 텍스트 항목의 기대값(유지/폐기)을 추가한다.
  - 050은 텍스트 키를 answered에 넣은 사례와 넣지 않은 대조 사례로 나눈다.
  - 드리프트 테스트에 텍스트 필터 재계산을 추가한다.

### Medium — 7건

**M1. 모든 영역이 null이면 위험 감점이 사라지는 동작을 표시 없이 정답으로 고정함, 위험 경계 사례의 총점 대비가 없어짐** (새 항목)
- 해당 사례: `simulation_run-013~030`(18건, holdout 020·021·022·023 포함), `simulation_report_preview-010~020`(11건, holdout 014·015·016 포함).
- 앱 코드: `app/features/simulation/report.py:118-123`의 `overall_score`는 가중 영역이 없으면 `return 50`을 한다. 위험 감점은 적용하지 않는다.
- 재계산: 이 사례들의 페르소나는 알려진 차원이 1~3개이고 양쪽에 공통으로 알려진 차원이 없다. 그래서 다섯 영역이 모두 null이고, 총점은 위험 수와 관계없이 50·OK다.
  - `pursue_64`(위험 없음)와 `pursue_65`(`pursue_withdraw`, penalty 8)가 모두 50·OK다.
  - `all_risks`(위험 3개, penalty 24)도 50·OK다. 50을 채워 계산한 v1.3 기준값은 44·CAUTION이었다(§5).
  - 오라클은 `riskPenalty: 24`와 `overallScore: 50`을 함께 적는데, 이 불일치를 설명하는 reviewFlag가 없다.
  - preview에서는 이상형 온정을 판정하면 감점이 다시 붙는다. 예를 들어 preview-019는 온정 60이면 36(CAUTION), null이면 50(OK)이다. 근거가 더 많을수록 총점이 낮아지는 비단조가 생긴다(재계산 확인).
- 경계 목적이 살아 있는지: 64/65 위험 경계는 `riskIds`에서는 살아 있다. 총점·등급으로는 더 이상 구분되지 않는다. README:41은 "모든 영역이 비어 총점 50"만 적고 감점 소실은 적지 않았다.
- 조치:
  - 이 동작을 앱 결함으로 볼지 정한다(앱 이슈 또는 `known_code_gap_risk_penalty_without_area` 플래그).
  - 위험 경계 family에는 양쪽에 공통으로 알려진 SIMILAR 차원을 하나 넣어 감점이 총점에 드러나게 한다.

**M2. 앱이 만들 수 없는 페르소나 상태가 입력에 있음, 한 건은 known_scores 규칙과 반대로 변환됨** (새 항목)
- `simulation_report_preview-020`(low_accuracy):
  - persona_a의 avoidance·disclosure·openness는 `null`인데 confidence가 HIGH이고, assurances·contact_rhythm·positivity는 null인데 MEDIUM이다. persona_b는 15개 차원이 모두 MEDIUM인데 12개가 null이다.
  - 앱에서 null 점수는 항상 LOW다. build 경로(`service.py:613-619`)에서 값이 없으면 LOW이고, `known_scores`(`service.py:306-311`)는 **LOW인 50만** null로 바꾸며 "근거 있는 50은 진짜 중간값"으로 둔다.
  - v1.3의 50(HIGH/MEDIUM)을 null로 바꾼 것은 이 규칙과 반대다.
  - known_scores 규칙을 적용해 다시 계산하면 영역 intimacy 45, communication 90, conflict 30이고 총점은 **27·CAUTION**이다. 데이터셋은 50·OK다.
  - `/report/preview`는 임의의 PersonaResponse를 받으므로 주어진 입력에 대한 오라클 자체는 맞다. 다만 사례가 표현하는 상태가 실재하지 않는다.
- 값이 있는데 LOW인 상태:
  - 해당 사례: `simulation_run-031~033`, `simulation_report_preview-021·022`(모두 holdout, low_confidence), `practice_reply-049~054`(`lowConfidenceWeaklyOnly`), preview-020의 engagement 90/LOW.
  - v1.4 서비스에서는 answered 차원의 증거를 최소 1건으로 친다(`service.py:618`). 그래서 새로 만든 페르소나에서는 값이 있으면 MEDIUM 이상이다.
  - 이 상태는 null 도입 전의 옛 행(50이 아닌 LOW 값)에서만 나온다. 이 사실이 플래그나 README에 없다.
- 조치: preview-020은 null 차원을 LOW로 두거나 50(HIGH/MEDIUM)으로 되돌린다. low_confidence 계열에는 `legacy_row_state` 같은 플래그를 붙이거나 도달 가능한 상태로 다시 설계한다.

**M3. v1.2 M1의 부분 분리 3건이 남음** (이월)
- 대상: `persona_build-027`↔`-023`, `persona_build-028`↔`-024·026`, `persona_build-042`↔`tagging-059`. v1.3 보고서의 수정 목록에 없고 본문도 v1.2 검토 때와 같다.
- 확인 결과: build-044, practice-024·060, preview-016, tagging-013·034·054는 v1.3에서 다른 장면으로 바뀌었다.

**M4. simulation·preview 페르소나 입력이 명사만 바꾼 틀임** (v1.2 M3 이월, 악화 요소 있음)
- 24개 페르소나 쌍의 틀은 그대로다(`narrative=null`, `routine=[]`, 같은 date 구조). v1.4에서는 빈 차원이 null이 되면서 대부분의 페르소나가 알려진 차원 1~4개만 남았다.
- 그 결과 holdout `simulation_run-020~023`과 regression `-013~019·024~027`의 SUT 프롬프트가 거의 같아졌다. 같은 점수 행에 `?`가 13~14개 있고 차원 이름 하나만 다르다.
- 긴 프로필 사례는 여전히 0건이다.

**M5. 루브릭 판정 이유가 템플릿 조립 수준임** (v1.2 M6 이월)
- v1.4는 build 60건 모두에 같은 forbidden "직접 답하지 않은 차원을 다른 답에서 미루어 채움"을 추가했다. 사례별 이유는 늘지 않았다.

**M6. practice 범주 공백** (v1.2 M7 이월)
- `input.me`는 여전히 60/60 null이다. 농담·욕설, 직업·나이·거주지를 직접 묻는 질문, 조언 유도도 없다.
- v1.4 앱은 "저번에/지난번에" 금지를 추가했지만(`practice/agents.py:113-114`, `persona/agents.py:60,353`) 이를 검사하는 사례도 없다. revision 보고서에 이 사실이 적혀 있다.

**M7. practice storedHistory가 서비스에서 도달할 수 없는 상태를 담음** (v1.2 M8 이월)
- `practice_reply-022`(39건, 마지막이 persona), `-034`(41건, 마지막이 persona), `-028`은 그대로다.

### Low — 7건

- **L1** README:68이 "010·011은 총점 69~76에서 OK 또는 GOOD, 019는 38~45"라고 적는다. v1.4 기대값은 010·011이 35~75(CAUTION/OK/GOOD), 019가 11~51(CAUTION/OK)이다. 문서가 낡았다.
- **L2** 재시도 계약의 기록이 일부 빠졌다.
  - `agents.py:343-356`은 `invalid_json`·`truncated`뿐 아니라 **speaker_mixup**(자기 닉네임+님)도 1회 재시도한다. 정상 사례의 `runtime.llmCalls: 1`은 명목값일 뿐이고, 이 점이 적혀 있지 않다.
  - `truncated` 재시도는 `max_tokens`를 1.5배로 늘리는데(`agents.py:346`) `runtime.maxTokens`는 첫 호출값만 적는다.
  - timeout faultContract에는 `httpStatus`가 없다(invalid_json에는 503이 있다).
  - `truncated`·`upstream_error`·speaker_mixup 사례와 `llm_json_mode` 기록이 없다. revision 보고서도 이를 인정한다.
- **L3** 시뮬레이션·preview 60건 모두 리포트 정확도가 40 미만이다(최대 39). 그래서 `_confidence`(`report.py:174-183`)의 "N개 차원은 근거 부족" 분기를 검사하는 사례가 없다. low_accuracy(preview-020, 39)도 대비되는 반대편이 없다. v1.4 이전부터 있던 문제다.
- **L4** both_low_odd(10+11)와 both_high_odd(80+81)는 둘 다 .5 동점에 아래쪽이 짝수다. floor와 Python `round`(은행가 반올림)가 같은 값을 준다. 반올림 방식이 `round`로 바뀌어도 잡지 못한다. v1.4 이전부터 있던 문제다.
- **L5** v1.4 앱은 `describe`(`profile.py:50-54`)와 `_persona_section`(`simulation/agents.py:167`)에 MBTI 말투 힌트를 넣었다. 그런데 데이터셋 페르소나에 `mbti`가 있는 사례가 0건이라 이 분기를 검사하지 않는다.
- **L6** orientation 세 선택지 계약은 v1.3에서 고쳐졌다. 다만 해당 사례 6건(`persona_onboarding_conversation-055~060`)이 모두 holdout에만 있다(v1.2 M4의 커버리지 부분 이월).
- **L7** v1.2의 L1~L7은 대부분 다시 확인하지 않았다(미확인). 확인한 것은 하나다. `simulation_run-032`의 후보 대본은 8줄인데 `highlightContract.indexRange`는 여전히 [0, 19]다.

## 4. 재계산으로 확인한 항목

| 대상 | 방법 | 결과 |
| --- | --- | --- |
| (2) simulation_run 36 + preview 24 `ruleOracle` | `score_layer`·`overall_score`·`grade_of`·`_confidence`·`_date_suggestion`으로 차원 점수·표시값(a/b)·차원 신뢰도·영역·위험·감점·총점·등급·정확도·데이트 제안 재계산 | **60/60 일치**. 모름 차원의 규칙 점수 null, 표시값 50(`report.py:86-87`)이 코드와 같다 |
| preview `idealRecalculationOracle` | 온정 허용범위의 모든 정수와 null을 넣어 가능한 총점 집합·등급 구간·null 기준값 재계산 | **24/24 일치** |
| preview template 경로 | `build_report(inp, None)` 실행 | 24/24 예외 없음. 총점·위험·정확도가 오라클과 일치 |
| (1) persona_build `postService` | coverage.primary를 answered로 보고 `service.py:613-619` 로직과 `confidence_of`·`accuracy_of`로 재계산 | 60/60 confidence·accuracy·unknownScores 일치. **텍스트 항목은 H1** |
| build 후보 출력 14건 | `RawExtraction` 검증, answered 필터, `narrative_contradiction`, `valid_summaries`+grounded_areas | 0/100 허용, −1/101·headline 61 거부, 044 서술 폐기(65)·045 유지(64), 046 카드 1장, 047 "처음"만 유지, 059 null→LOW, 060 accuracy 100. 모두 일치 |
| build change_9/10 | `changes_between`의 null 가드 확인(`service.py:285-286`) | 50→59 미표시, 50→60 표시. 일치 |
| (4) practice 60건 | `PartnerAgent.system_prompt`, `describe`, `trait_lines` 실제 호출 | 예외 0, 프롬프트에 "None" 문자열 0. 35/36/64/65 표시 경계(037·043·049·055 계열) 유지 |
| (4) 시뮬레이션 페르소나 120개 | `_persona_section`, `describe` 호출 | 예외 0. null은 `?`로 표시되고 "None"은 나오지 않음 |
| (5) 재시도 계약 | `simulation/agents.py:325-356` 대조, 후보 11건에 `_validate_script`·`_self_addressed_lines` 실행 | `maxTokens = 3000+300×turns` 36/36, `maxLlmCalls 2`, invalid_json `llmCalls 2`, `retryCondition "remaining_time > timeout / 3"`(코드 `remaining > total / 3`)가 모두 코드와 같다. timeout은 재시도하지 않음. invalid_script(025·029)·script_too_short(012·027)는 재시도 없이 실패. 후보 대본 중 speaker_mixup 재시도를 유발하는 것 0건 |
| (3) 경계 목적 | 사례별 알려진 차원과 결과 확인 | 표 아래 참고 |
| 프롬프트 변경 충돌 | `_PAST_MEETING` 정규식을 conversation·practice의 모든 문자열에 적용, "저번/지난번/하셨잖" 검색 | 충돌 0 |

(3) 경계 사례의 목적이 살아 있는지:

| family | 목적 | v1.4 상태 |
| --- | --- | --- |
| pursue/attack/yield 64·65 | 위험 임계 64/65 | `riskIds`로는 살아 있음. 총점·등급은 64·65 모두 50·OK라 구분되지 않음(M1) |
| all_risks | 위험 3개 누적 감점 | `riskIds` 3개는 살아 있음. 감점 24가 총점에 반영되지 않아 50·OK(M1) |
| rule_grade_boundary(preview-018) | 44/45/69/70 등급 | `ruleProbe` 직접 호출로 살아 있음(CAUTION/OK/OK/GOOD). 사례 자체 총점은 50 |
| preview 이상형 재계산 | 등급을 가로지르는 구간 | 010·011·014·023·024가 CAUTION~GOOD, 019·020이 CAUTION~OK, 021·022가 OK~GOOD로 살아 있음 |
| both_low_odd / both_high_odd | 홀수 합 내림 | 90·80·90점으로 살아 있음. 단 L4 |
| opposite_rhythm | 연락·진지도 반대 | 15·30점, 24·CAUTION으로 오히려 선명해짐 |
| low_confidence | 근거 부족을 약하게 서술 | `trait_lines`의 "(근거 부족 — 약하게만)" 경로로 살아 있음. 단 M2(옛 행에서만 나오는 상태) |
| missing_scores / defaulted_persona_scores | 누락 null | 15개 모두 null이고 총점 50은 함수 기본값. 살아 있음 |
| build contradiction 64/65 | 서술 모순 임계 | 살아 있음 |
| build textual_grounding(050) | 텍스트 근거 | **목적 상실(H1)** |
| practice profile 35/36/64/65 | 표시 경계 | 살아 있음 |

## 5. revision_v1_4_report.md 주장 검증

| 주장 | 확인 | 결과 |
| --- | --- | --- |
| 근거 없는 점수 50→null, 규칙 점수·영역·위험·총점을 null 기준으로 재계산 | 앱 함수 재계산 | 맞음. 단 preview-020은 HIGH/MEDIUM인 50까지 null로 바꿔 `known_scores` 규칙과 어긋남(M2) |
| 표시값 `dimensionDisplay` 50 | `score_dimensions` 결과의 a/b | 60/60 맞음 |
| build `defaultScores`(50) → `unknownScores`(null), answered 차원만 값 유지 | 점수는 맞음 | 텍스트 항목의 answered 필터는 반영하지 않음(H1) |
| 시뮬레이션·preview 60건 총점 변경, 등급 변경 24건, 최대 변화 30점, 예시(run-001 81→97, preview-003 54→24) | null을 50으로 채워 v1.3 값을 재구성한 뒤 비교 | 60건 변경, 등급 변경 24건, 최대 30점(run-004 등 54→24), 예시 모두 맞음. 단 v1.3 입력이 "null 자리에 50"이었다고 가정한 재구성이다. v1.3 원본 JSONL은 git에 없어(evals/ 미추적) 직접 대조는 미확인 |
| `max_tokens = 3000 + 300×turns`, 1회 재시도, invalid_json `llmCalls 2`, timeout 미재시도 | `agents.py:325-356` | 맞음. 기록 누락은 L2 |
| 드리프트 테스트 전부 통과, 전체 528개 통과 | 데이터셋 관련 3개 파일만 실행 | 269 passed. 528개 전체 스위트는 실행하지 않아 **미확인** |
| generate `--check`·validator·audit 통과 | 실행 | 통과 |

## 6. holdout 독립성 (item 6)

- v1.4는 발화·대본·질문 텍스트를 바꾸지 않았다. 바뀐 것은 점수 null화, 오라클, runtime, 루브릭의 null 관련 문구다. 새 문구 "빠진 점수는 50으로 채우지 않고 모름(null)"은 regression(`simulation_run-034~036`, preview-023·024)에만 있다. build forbidden 추가는 모든 split에 같은 상수다. 따라서 텍스트 누수는 늘지 않았다.
- audit를 실행했다. 교차 split 정확·근사 충돌은 0이다.
- 악화 요소는 하나다. 페르소나의 알려진 차원이 줄면서 holdout과 regression의 시뮬레이션 입력이 더 비슷해졌다(M4). 이 차이는 차원 이름과 64/65 값뿐이다.
- v1.2 지적 사항은 다음과 같이 확인했다.
  - H1은 해소되었다. date_avoid가 "우산 없는 야외"·"갑작스러운 심야 이동"으로 바뀌었다.
  - M1 7건 중 4건(build-044, practice-024·060, preview-016)은 해소되었고 3건(build-027·028·042)이 남았다.
  - M2(tagging-013·034·054)는 해소되었다. tagging-054의 "명함을 서랍에" 모티프가 build-008·tagging-032의 "서랍" 모티프와 약하게 겹친다.
- holdout 64건 전체를 다시 읽는 의미 누수 검토는 하지 않았다(**미확인**).

## 7. v1.2 검토 항목의 현재 상태 (item 7)

| v1.2 항목 | 상태 |
| --- | --- |
| H1 holdout 입력에 목표 행동 | 해소(v1.3) |
| M1 재작성 분리 불완전 7건 | 4건 해소, 3건 잔존 → M3 |
| M2 tagging 골격 누수 | 해소(v1.3) |
| M3 페르소나 템플릿 | 잔존, 일부 악화 → M4 |
| M4 orientation 선택지 | 계약 해소(세 선택지), holdout 전용 커버리지 잔존 → L6 |
| M5 짧은 답 off_topic | 해소(허용 대안, `schema_comment_off_topic_conflict` 플래그) |
| M6 루브릭 템플릿 | 잔존 → M5 |
| M7 practice 커버리지 | 잔존 → M6 |
| M8 storedHistory 도달성 | 잔존 → M7 |
| L1~L7 | 대부분 미확인. L6의 run-032 indexRange만 잔존 확인 |

## 8. 실행한 검증 (쓰기 없이)

- `python3 -B scripts/generate_quality_datasets.py --check`: PASS(320건)
- `python3 -B scripts/validate_quality_datasets.py --json`: ok, errors 0
- `python3 -B evals/quality_datasets/audit_quality_datasets.py`: ok, errors 0
- `.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_quality_datasets.py tests/test_quality_datasets_drift.py evals/quality_datasets/test_audit_quality_datasets.py`: 269 passed. 전체 스위트와 ruff는 실행하지 않았다.
- 독립 재계산 스크립트는 세션 scratchpad에서만 실행했다. `git status`와 파일 변경 시각은 검토 전후로 같고, 이 문서 외에는 쓰지 않았다.

## 9. 통과에 필요한 최소 조치

1. H1: build 입력에 answered 집합(또는 턴별 태그)을 명시한다. 텍스트 근거가 있는 31건의 coverage/answered에 텍스트 키를 넣고, `postService`에 텍스트 항목 기대값을 추가한다. 050은 다시 설계한다. 드리프트 테스트에 텍스트 필터를 추가한다.
2. M1: 영역이 모두 null일 때 감점이 사라지는 동작을 앱 결함으로 처리할지 정한다. 위험 경계 family에는 공통 차원을 하나 넣어 총점으로 경계가 드러나게 한다.
3. M2: preview-020을 도달 가능한 상태로 고친다(null이면 LOW). low_confidence 계열에는 옛 행 상태 플래그를 붙인다.
4. 이월 항목(M3~M7)은 v1.2 §8의 조치를 따른다.

## 10. 한계

- 사람 이중 라벨링, Judge calibration, 실제 모델 실행은 없다. 라벨 판단은 단일 검토자 의견이다.
- v1.3 원본 JSONL이 저장소 이력에 없어(evals/ 미추적) v1.3 → v1.4 차이는 "null 자리에 50" 가정으로 재구성했다.
- H1의 answered를 coverage.primary로 재구성한 것은 서비스의 태깅 누적 방식에 근거한 추론이다. 평가 어댑터가 answered를 다른 방법으로 만들 계획이라면 그 방법은 데이터셋에 적혀 있지 않다. 이 점이 H1의 핵심이다.
- 전체 pytest 스위트(528개 주장), ruff, holdout 64건 전체 재열람은 수행하지 않았다(미확인).
