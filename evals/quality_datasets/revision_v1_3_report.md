# v1.3.0 수정 보고

Claude Opus 재검토는 v1.2.0을 82점, critical 0, high 1로 실패 처리했다. 검토 원문은 `reviews/claude_opus_v1_2.md`다. 이 버전은 그 실패를 막던 항목만 고친 뒤 생성기를 다시 실행한 결과다. 독립 재검토는 아직 하지 않았으므로 90점 통과로 보지 않는다.

## 만든 방식과 비용

테스트셋 320건은 `scripts/generate_quality_datasets.py`가 코드와 기획서에서 합성한 오프라인 초안이다. 생성·검증 중에 OpenRouter, 모델 API, API 키를 호출하지 않았다. 따라서 이번 데이터셋의 API 비용은 0이다. Langfuse에는 올리지 않았다. 사람 이중 라벨, Judge calibration, 실제 모델 실행도 없다. `generationMethod=astra-xhigh-v1`은 처음 초안을 쓴 작업 식별자이며 실행 시 Astra API를 부른다는 뜻이 아니다.

## 이번 수정

- 낮은 신뢰도와 점수 누락 페르소나의 데이트 기피 항목을 `우산 없는 야외`, `갑작스러운 심야 이동`으로 바꿨다. 해당 사례는 simulation_run 031~036, preview 021~024다. 채점 문장에 남은 "확정적 결론을 유보"는 기대 행동 설명이고 입력 힌트가 아니다.
- build 044는 여행 중 개인 시간 대신 도서관 자리 장면이다.
- practice 024는 만남 시각을 정한 뒤 가방 색깔을 정하는 전환이다. practice 060은 경로를 남긴 뒤 사진 금지 이유를 묻는 전환이다.
- preview 016은 공예 재료 선택 대신 초인종과 통화 장면이다.
- tagging 013·034·054는 이전 split의 문장 골격과 다른 사건으로 바꿨다.
- orientation 계약은 `진지하게 만날 사람`, `편하게 알아가기`, `아직 잘 모르겠어요` 세 선택지를 모두 요구한다.
- tagging 011·019·063·075는 off_topic false를 기본으로 두고, 스키마 주석의 true 판정도 허용 대안으로 남겼다.

caseId와 64/192/64 분할은 그대로다.

## 검증

- `generate --check`: 320건 재현성 통과
- validator: 오류 0, 경고 0
- audit: 정확·근사 교차 split 충돌 0, family 누수 0
- 데이터셋 테스트 16건 통과
- ruff check와 format check 통과
- JSONL에서 `확정적 단정`, `프로필 밖 추측`, `두 선택지`는 사라졌다

페르소나 템플릿 다양화, 루브릭 전면 사례화, practice의 내 페르소나, storedHistory 도달성 보정은 이번 범위에 넣지 않았다.
