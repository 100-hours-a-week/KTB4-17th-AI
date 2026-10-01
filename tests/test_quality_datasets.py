import importlib.util
from pathlib import Path

import pytest


def load_validator():
    script_path = Path("scripts/validate_quality_datasets.py")
    spec = importlib.util.spec_from_file_location("validate_quality_datasets", str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator_module():
    return load_validator()


@pytest.fixture(scope="module")
def validation_result(validator_module):
    root = Path("evals/quality_datasets")
    return validator_module.validate(root), root, validator_module


def test_dataset_files_exist(validation_result):
    """파일이 없으면 skip하지 말고 실패해야 함"""
    _, root, validator_module = validation_result
    assert root.is_dir(), f"데이터셋 디렉터리가 존재하지 않습니다: {root}"
    for dataset in validator_module.DATASET_COUNTS.keys():
        dataset_path = root / f"{dataset}.jsonl"
        assert dataset_path.is_file(), f"필수 파일이 없습니다: {dataset_path}"


def test_dataset_total_count(validation_result):
    """정확한 6종 건수 및 전체 330건 검사"""
    result, root, _ = validation_result
    assert root.is_dir(), "데이터셋 디렉터리가 없습니다."
    assert len(result["counts"]) == 6, f"6종의 데이터셋이 아닙니다. 결과: {len(result['counts'])}"
    assert result["total"] == 330, f"전체 330건이 아닙니다. 결과: {result['total']}"


def test_dataset_splits(validation_result):
    """split 66/198/66 검사 (calibration 66, regression 198, blind_holdout 66)"""
    result, root, _ = validation_result
    assert root.is_dir(), "데이터셋 디렉터리가 없습니다."
    expected_splits = {"calibration": 66, "regression": 198, "blind_holdout": 66}
    assert result["splits"] == expected_splits, f"Split 비율이 일치하지 않습니다: {result['splits']}"


def test_dataset_split_group_leak(validation_result):
    """splitGroup 누수 없음 검사"""
    result, root, _ = validation_result
    assert root.is_dir(), "데이터셋 디렉터리가 없습니다."
    assert result["splitGroupLeakCount"] == 0, (
        f"splitGroup 누수가 발생했습니다. 누수 수: {result['splitGroupLeakCount']}"
    )


def test_dataset_validation_ok(validation_result):
    """caseId 유일성, 필수 메타데이터, Astra generationMethod, 비밀값 패턴 부재 검사 포함"""
    result, root, _ = validation_result
    assert root.is_dir(), "데이터셋 디렉터리가 없습니다."
    errors = result.get("errors", [])
    error_msg = "\n".join(errors)
    assert result["ok"] is True, f"데이터셋 검증 실패 내역이 존재합니다:\n{error_msg}"
