# v1.2.0 2차 개선 완료 보고

허용된 생성기와 evals/quality_datasets 안에서만 변경했다. 외부 네트워크, OpenRouter, 모델 API, API 키를 사용하지 않았다. 6개 데이터셋 320건과 기존 caseId·분할·스키마를 유지했다.

## 반영 내용

- H1: tagging 074, build 007·008·017·018·027·028·042·044·045, practice 020~024·056~060, simulation_run 032, preview 014·015·016·021의 25건을 장면과 대화 행위까지 다시 작성했다. 정규화 후 SequenceMatcher와 문자 3-gram을 결합한 근사 검사를 추가했다.
- M1: tagging 020·024·032·040·048·052·064·068·072를 서로 다른 관찰·기록·행동의 간접 정황으로 재작성했다. 직접 라벨을 비우고 간접 라벨 및 보수적 빈 배열을 허용했다.
- M2/L7: 각 split에 1~3자 답·농담·욕설 사례를 배치했다. holdout 간접 양성은 긍정적 어조·거절 불안·장기 관계 지향의 3종이다. off_topic 양성은 8건(2/4/2)이다.
- M3: build 001~030을 별도로 쓴 3~4턴 대화로 교체하고 점수 근거 두 사건과 취미·일과·데이트 근거를 나눴다. 일반/경계 질문에서 내부 차원 이름을 제거했다.
- M4/L4: turn0 9건을 모두 weekend로 맞췄고 history 51건은 실제 고정 첫 인사와 weekend 문답으로 시작한다. 마지막 관계 방향 질문에는 두 선택지를 자연스럽게 제시하도록 명시했다.
- M5: preview 010·011은 총점 69~76에서 OK/GOOD, 019는 38~45에서 CAUTION/OK가 가능하다. 온정의 허용값과 null을 열거해 영역·총점·등급 재계산 결과를 기록하고 사전 ruleOracle 등급을 최종값으로 고정하지 않았다.
- M6: 320건의 루브릭을 자연어 판정 이유·실제 근거·금지 조건으로 교체했다. 서로 다른 루브릭은 온보딩 58, 태깅 80, build 60, practice 60, simulation 36, preview 24개다.
- L1~L6: practice 60개와 preview 48개 페르소나의 열다섯 점수/신뢰도 및 정확도를 검증한다. build 033의 가정형 연락 발화에는 필수 점수를 강요하지 않는다. 불충분 태깅 답변의 내부 라벨 노출과 조사 오류를 수정하고 persona/api.py를 sourceSha256에 포함했다.

## 최종 검증

- generate 및 --check: 통과, 320건/64·192·64 및 재현성 유지.
- validator: 오류 0, 경고 0.
- audit: 정확 문장 교차 split 충돌 0, 근사 충돌 0, 지정 25건 충돌 0, family 누수 0.
- pytest 전체: **230 passed**, 기존 Starlette/AnyIO deprecation 경고 1건.
- ruff check .: 통과.
- 변경 범위 ruff format --check: 통과.
- manifest 버전: **1.2.0**.

근사 검사는 NFKC/문장부호/공백 정규화, 20자 이상, SequenceMatcher 0.76 이상, 문자 3-gram Dice 0.45 이상, 공통 문자 18자 이상을 요구한다. 짧은 관용 답변과 반복 문자 길이 경계의 오탐 대조군, 명사 치환·문장부호 변화의 양성 대조군을 추가 테스트로 확인했다. 정확 일치는 짧은 답도 검사하되 코드의 고정 첫 인사만 예외로 둔다.

## 제한

90점/critical0/high0은 독립 재검토의 목표이며 이번 로컬 검증만으로 점수를 확정하지 않았다. 사람 이중 라벨링, Judge calibration, 실제 모델 실행은 수행하지 않았고 모든 의미 동등성을 자동 증명하지 않는다. audit_before_v1_2.json은 수정 전의 더 보수적인 초기 검사 기록이며 최종 결과는 audit_report.json과 validation_report.json이다.
