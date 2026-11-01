import json

from evals._harness import stage6_manifest as M


def test_manifest_sha256_computation(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world", encoding="utf-8")
    h = M.sha256_file(f)
    assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_manifest_freeze_and_verify(tmp_path):
    manifest_p = tmp_path / "test_manifest.json"
    M.freeze_manifest(manifest_p)
    assert manifest_p.exists()

    data = json.loads(manifest_p.read_text(encoding="utf-8"))
    assert "version" in data
    assert "git_commit_short" in data
    assert data["total_files"] > 100
    assert data["total_goldset_cases"] == 720

    ok, errors = M.verify_manifest(manifest_p)
    assert ok is True
    assert len(errors) == 0


def test_manifest_verify_detects_tampering(tmp_path):
    manifest_p = tmp_path / "test_manifest.json"
    M.freeze_manifest(manifest_p)

    data = json.loads(manifest_p.read_text(encoding="utf-8"))
    data["files"][0]["sha256"] = "0" * 64
    manifest_p.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = M.verify_manifest(manifest_p)
    assert ok is False
    assert any("해시 불일치" in e for e in errors)


def test_manifest_verify_detects_head_drift(tmp_path):
    manifest_p = tmp_path / "test_manifest.json"
    M.freeze_manifest(manifest_p)

    data = json.loads(manifest_p.read_text(encoding="utf-8"))
    # 기록된 commit 을 과거 커밋으로 조작
    data["git_commit"] = "cb53097000000000000000000000000000000000"
    manifest_p.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = M.verify_manifest(manifest_p)
    assert ok is False
    assert any("Git HEAD 불일치" in e for e in errors)


def test_manifest_verify_detects_new_untracked_file(tmp_path):
    manifest_p = tmp_path / "test_manifest.json"
    M.freeze_manifest(manifest_p)

    # 매니페스트에 없는 파일이 tracked 목록에 추가된 상황 시뮬레이션
    data = json.loads(manifest_p.read_text(encoding="utf-8"))
    data["files"] = data["files"][1:]  # 한 파일 누락시켜 등록
    manifest_p.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = M.verify_manifest(manifest_p)
    assert ok is False
    assert any("새 파일 추가 감지" in e or "결합 해시" in e for e in errors)


def test_manifest_blocks_empty_corrupted_structure(tmp_path):
    manifest_p = tmp_path / "empty_manifest.json"
    manifest_p.write_text("{}", encoding="utf-8")

    ok, errors = M.verify_manifest(manifest_p)
    assert ok is False
    assert any("위조" in e or "누락" in e for e in errors)
