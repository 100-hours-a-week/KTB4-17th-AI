# 사람 골드셋

상위 폴더의 306건은 모델이 쓴 합성 초안이고 생성기·manifest 해시로 고정돼 있다(`generate_quality_datasets.py --check`).
사람 두 명이 라벨을 확정한 골드셋은 이 폴더에 따로 쌓는다. 행 구조와 채점 계약(`autoMetrics`)은 같지만
`metadata.generationMethod` 는 `llm-draft:<작성 모델>`, `reviewFlags` 에 `dual_human_reviewed` 가 붙고,
상위 `schema.json`·`--check` 대상이 아니다.

```bash
# 1) 초안 검증 + 누수·중복 검사 + 라벨링 시트. 기존·사람 골드셋은 자동으로 비교하고, 학습 데이터는 --against 로 더한다
python3 scripts/gold_drafts.py prepare drafts/tagging_opus.jsonl drafts/tagging_codex.jsonl \
    --kind tagging --author opus-5.5,codex --against data/synthetic/pilot200.jsonl data/tagging.jsonl
#    holdout 묶음은 다른 세션에서 만든 초안으로:  ... --split blind_holdout
#    → data/gold_work/tagging/sheet.csv (라벨러용), items.jsonl (작성 의도 포함, 라벨러에게 주지 않음), prepare_report.json

# 2) sheet.csv 를 복사해 두 사람이 각자 채운다 (서로 보지 않기)
#    태깅: primary·secondary 는 차원 키를 쉼표로, off_topic 은 true/false 를 반드시 적는다
#    추출: 점수형 차원 칸에 low·mid·high(또는 낮음·중간·높음), 근거 없으면 빈 칸, 텍스트형은 항목을 쉼표로, 다 보면 done=y

# 3) 두 시트 비교 → 일치도(κ) → 갈린 건은 needs_adjudication.csv → 조정 시트로 다시 build
python3 scripts/gold_drafts.py build --kind tagging --labels A.csv B.csv --reviewers lena,yuta
python3 scripts/gold_drafts.py build --kind tagging --labels A.csv B.csv --reviewers lena,yuta --adjudicated adj.csv

# 채점에 쓰기
python3 scripts/score_teacher_gold.py data/teacher/run --gold-dir evals/quality_datasets/human
python3 scripts/label_with_teacher.py --gold --gold-dir evals/quality_datasets/human
```

- 누수 검사 기준은 상위 README "분할과 누수 방지"의 정규화·근사 규칙(SequenceMatcher 0.76, 3-gram Dice 0.45, 공통 18자)과 같다. 8자 미만 짧은 답은 겹쳐도 누수로 보지 않는다.
- 태깅은 primary·off_topic 이 같으면 확정하고, secondary 만 갈리면 두 답을 허용 대안으로 두고 `ambiguous_secondary` 를 붙인다.
- 추출은 차원별 수준(낮음 0~34·중간 35~64·높음 65~100)과 텍스트형 유무가 같으면 확정한다. 항목 문구는 합친다.
- 일치도가 0.6 미만이면 경고한다. 라벨링 가이드를 맞춘 뒤 다시 라벨링한다.
- 이 폴더의 답변은 합성 세션 생성기(`generate_synthetic_onboarding.py`)와 학습 데이터 검증(`training/tagging/data.py`, V2)의 누수 검사에도 들어간다.
