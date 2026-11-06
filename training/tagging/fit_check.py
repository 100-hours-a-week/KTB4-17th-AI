"""학습 전 길이 검사. torch 없이 테스트할 수 있게 train_lora.py 와 분리했다."""

from __future__ import annotations

from collections.abc import Iterable


def assert_fits(rows: Iterable[dict], tokenizer, *, max_length: int) -> None:
    """TRL 은 max_length 를 넘는 예시의 뒤쪽을 경고 없이 자른다. 뒤쪽이 정답 JSON 이라 잘리면 학습이 망가진 채 끝까지 돈다."""
    longest = max(
        len(
            tokenizer(
                tokenizer.apply_chat_template(row["prompt"] + row["completion"], tokenize=False),
                add_special_tokens=False,
            )["input_ids"]
        )
        for row in rows
    )
    if longest > max_length:
        raise SystemExit(f"가장 긴 예시가 {longest} 토큰인데 --max-length 는 {max_length} 입니다. 값을 올리세요.")
