# Claude 라벨 골드셋 (기준 v1)

상위 폴더 골드셋 중 calibration+regression 의 label_accuracy 문항(태깅 63, 추출 24)에 Claude(claude-opus-5.5)가 정답을 단 것이다.
판단 기준은 사람 검수표의 기준선택 P1~P8(검수자 A·B 작성, 2026-10-27)이다. **사람 2인 독립 검수가 아니다.**

- `metadata.reviewFlags` 에 `claude_labeled_human_criteria_v1`, `metadata.labelProvenance.humanReviewed=false` 가 붙는다.
- 행 구조와 채점 계약(`autoMetrics`)은 `human/` 과 같아서 `--gold-dir evals/quality_datasets/claude_gold` 로 그대로 쓴다.
- P2(짧은 수긍)·P7(텍스트 표현)은 두 검수자의 선택이 갈렸다. 해당 문항은 어느 쪽 기준으로도 같은 답이 되도록 달았다
  (보류 3문항은 비움, 텍스트 항목은 원문에 가까운 표현).
- 태깅 63 에는 보류 3문항(011·019·063)이 들어 있다. 검수표 기준으로는 60문항 분모 밖이다.
- 사람이 확인해 `human/` 으로 옮기기 전까지 이 셋으로 낸 점수는 "Claude 라벨 기준"이라고 적는다.

만든 방법:

```bash
uv run python scripts/gold_drafts.py build --kind tagging --work-dir data/gold_work/existing \
    --labels data/gold_work/existing/labels/tagging_claude.csv data/gold_work/existing/labels/tagging_claude.csv \
    --reviewers claude-opus-5.5 --out-dir <임시 폴더>
# build 는 dual_human_reviewed 를 붙이므로, 출처 플래그를 위처럼 바꿔 이 폴더에 저장했다
```

라벨 시트(`data/gold_work/existing/labels/*_claude.csv`)는 원문이 들어 있어 Git 에서 제외된다.
