"""시간 기반 유지 판정. 짧은 미검출은 시간을 더하지 않고 기다린다."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

HOLD_SECONDS = 3.0
MAX_SAMPLE_GAP_SECONDS = 0.75
DETECTION_GRACE_SECONDS = 0.8


@dataclass(frozen=True)
class HoldWindow:
    indices: tuple[int, ...] = ()
    seconds: float = 0.0


def find_hold(matches: Sequence[bool], timestamps: Sequence[float], seconds: float, *, start: int = 0) -> HoldWindow:
    if len(timestamps) < 2 or len(matches) != len(timestamps):
        return HoldWindow()
    intervals = np.diff(timestamps)
    if not np.isfinite(timestamps).all() or (intervals <= 0).any():
        return HoldWindow()
    # 샘플 한 장이 대표하는 시간 셀. 큰 간격은 셀 추정에서 제외한다.
    usable = intervals[intervals <= MAX_SAMPLE_GAP_SECONDS]
    if not len(usable):
        return HoldWindow()
    cell = float(np.median(usable))
    run: list[int] = []
    elapsed = 0.0
    last_match: int | None = None
    longest = HoldWindow()
    for index in range(start, len(matches)):
        if index > start and timestamps[index] - timestamps[index - 1] > MAX_SAMPLE_GAP_SECONDS:
            run, elapsed, last_match = [], 0.0, None
        if (
            last_match is not None
            and timestamps[index] - timestamps[last_match] > DETECTION_GRACE_SECONDS + 1e-9
            and (not matches[index] or last_match != index - 1)
        ):
            run, elapsed, last_match = [], 0.0, None
        if not matches[index]:
            continue
        if last_match is None:
            elapsed = cell
        elif last_match == index - 1:
            elapsed += timestamps[index] - timestamps[last_match]
        # 미검출 구간과 복구 프레임 사이의 시간은 유지 시간에 포함하지 않는다.
        run.append(index)
        last_match = index
        if len(run) >= 2 and elapsed > longest.seconds:
            longest = HoldWindow(tuple(run), elapsed)
        if len(run) >= 2 and elapsed + 1e-9 >= seconds:
            return longest
    return longest
