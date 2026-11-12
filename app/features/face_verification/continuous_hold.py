"""시간 기반 유지 판정. 짧은 미검출은 끊지 않고 유지 시간에 포함한다."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

HOLD_SECONDS = 3.0
# 브라우저는 3초를 채우면 촬영을 넘긴다. 서버는 영상을 약 0.38초 간격으로 샘플링하므로
# 같은 3초도 경계에 따라 한 칸 짧게 잴 수 있어 그만큼을 허용한다.
SAMPLE_TOLERANCE_SECONDS = 0.4
REQUIRED_HOLD_SECONDS = HOLD_SECONDS - SAMPLE_TOLERANCE_SECONDS
MAX_SAMPLE_GAP_SECONDS = 0.75
DETECTION_GRACE_SECONDS = 0.8
# 샘플 시각 합은 부동소수 오차로 기준을 아주 조금 밑돌 수 있다 (예: 2.5999999999999996).
HOLD_EPSILON_SECONDS = 1e-9


@dataclass(frozen=True)
class HoldWindow:
    indices: tuple[int, ...] = ()
    seconds: float = 0.0

    def reaches(self, seconds: float) -> bool:
        return len(self.indices) >= 2 and self.seconds + HOLD_EPSILON_SECONDS >= seconds


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
            and timestamps[index] - timestamps[last_match] > DETECTION_GRACE_SECONDS + HOLD_EPSILON_SECONDS
            and (not matches[index] or last_match != index - 1)
        ):
            run, elapsed, last_match = [], 0.0, None
        if not matches[index]:
            continue
        if last_match is None:
            elapsed = cell
        else:
            # 서버 모델이 한두 장을 브라우저와 다르게 읽어도, 유예 시간 안에 복구하면 그 사이도 유지 시간이다.
            # 미검출 한 장마다 샘플 두 칸(최대 약 0.75초)을 빼면 브라우저 3초 유지가 2.6초 기준을 못 넘는다.
            elapsed += timestamps[index] - timestamps[last_match]
        run.append(index)
        last_match = index
        if len(run) >= 2 and elapsed > longest.seconds:
            longest = HoldWindow(tuple(run), elapsed)
        if longest.reaches(seconds):
            return longest
    return longest
