import pytest

from app.features.face_verification.continuous_hold import find_hold


def test_unrelated_gap_does_not_erase_completed_hold():
    result = find_hold([True] * 31 + [False] * 3, [i / 10 for i in range(31)] + [4.0, 4.1, 4.2], 3)
    assert result.seconds >= 3


def test_one_missed_sample_pauses_instead_of_erasing_progress():
    times = [i / 10 for i in range(36)]
    result = find_hold([i != 15 for i in range(36)], times, 3)
    assert result.seconds >= 3


def test_long_interruption_cannot_be_counted_as_hold():
    times = [i / 10 for i in range(40)]
    result = find_hold([i < 14 or i > 25 for i in range(40)], times, 3)
    assert result.seconds < 3


@pytest.mark.parametrize("times", [[0, 0, 1], [0, -1, 1], [0, float("nan"), 1]])
def test_invalid_timestamps_fail_closed(times):
    assert find_hold([True] * 3, times, 3).seconds == 0


def test_single_missed_uniform_sample_is_tolerated_at_mobile_sampling_rate():
    result = find_hold([index != 4 for index in range(10)], [index * 0.4 for index in range(10)], 3)
    assert result.seconds >= 3
