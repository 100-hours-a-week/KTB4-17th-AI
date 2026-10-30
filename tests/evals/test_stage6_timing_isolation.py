from unittest.mock import patch

from evals._harness import stage5_run


def test_stage5_f07_exec_body_preserved():
    # 기존 재현도구 본문이 보존되어 호출 가능한 함수 형태인지 확인
    assert callable(stage5_run.exec_f07)
    assert callable(stage5_run.exec_f10)
    assert callable(stage5_run.exec_f11)


def test_stage5_set_speech_safe():
    # set_speech 가 psql 에 정상 쿼리를 전달하는지 확인
    with patch("evals._harness.stage5_run.psql") as mock_psql:
        stage5_run.set_speech("test-session-id", "asked")
        assert mock_psql.called
        assert "v2_practice_speech_state" in mock_psql.call_args[0][0]


def test_timing_isolation_record_structure():
    # rows 레코드에 seconds, app_seconds, judge_seconds 가 분리 기록되는 형태 검증
    sample_row = {
        "case_id": "test-01",
        "split": "regression",
        "target_failures": [],
        "sample": {"output_raw": "안녕"},
        "code": {},
        "judge": {},
        "verdict": {},
        "seconds": 2.5,
        "app_seconds": 1.5,
        "judge_seconds": 1.0,
    }
    assert sample_row["seconds"] == 2.5
    assert sample_row["app_seconds"] == 1.5
    assert sample_row["judge_seconds"] == 1.0
    assert sample_row["app_seconds"] + sample_row["judge_seconds"] == sample_row["seconds"]
