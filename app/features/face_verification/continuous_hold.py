"""샘플의 시간 범위로 정면 확보 후 포즈 연속 유지를 검증한다."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class HoldWindow:
    indices: tuple[int, ...] = ()
    seconds: float = 0.0


def find_hold(matches: Sequence[bool], timestamps: Sequence[float], seconds: float, *, start: int = 0) -> HoldWindow:
    if len(timestamps) < 2 or len(matches) != len(timestamps):
        return HoldWindow()
    intervals = np.diff(timestamps)
    if not np.isfinite(intervals).all() or (intervals <= 0).any() or (intervals > 0.65).any():
        return HoldWindow()
    # 균등 샘플 한 장이 대표하는 시간 셀을 포함한다. 단일 프레임은 유지로 인정하지 않는다.
    cell = float(np.median(intervals))
    run: list[int] = []
    longest = HoldWindow()
    for index in range(start, len(matches)):
        if not matches[index]:
            run = []
            continue
        run.append(index)
        duration = timestamps[index] - timestamps[run[0]] + cell
        if len(run) >= 2 and duration > longest.seconds:
            longest = HoldWindow(tuple(run), duration)
        if len(run) >= 2 and duration >= seconds:
            return longest
    return longest
