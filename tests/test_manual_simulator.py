"""채팅용 시뮬레이터 — 내보낸 질문과 가져온 답이 API 생성기와 같은 세션 흐름·형식이 되는지 고정한다."""

import json
import re

import pytest

from scripts import manual_simulator as ms


def _export(tmp_path, n=3, seed=11, batch_size=2):
    return ms.export(n, seed, batch_size, tmp_path, total_turns=10)


def _keys_by_session(tmp_path):
    keys: dict[str, list[str]] = {}
    current = None
    for md in sorted(tmp_path.glob("batch_*.md")):
        for line in md.read_text(encoding="utf-8").splitlines():
            if m := re.match(r"## \[세션\] (\S+)", line):
                current = m.group(1)
                keys[current] = []
            elif m := re.match(r"### 키: (\S+)", line):
                keys[current].append(m.group(1))
    return keys


def _write_answers(tmp_path, keys, *, fenced=False, drop=None):
    sessions = [
        {"session": sid, "answers": {k: f"{sid} {k} 답이에요" for k in ks if (sid, k) != drop}}
        for sid, ks in keys.items()
    ]
    body = json.dumps({"sessions": sessions}, ensure_ascii=False)
    (tmp_path / "answers" / "all.json").write_text(
        f"여기요\n```json\n{body}\n```" if fenced else body, encoding="utf-8"
    )


def test_export_writes_batches_with_every_turn_key(tmp_path):
    manifest = _export(tmp_path)

    keys = _keys_by_session(tmp_path)
    assert [b["sessions"] for b in manifest["batches"]] == [["seed-11-00000", "seed-11-00001"], ["seed-11-00002"]]
    assert all(sum(not k.endswith("_offtopic") for k in ks) == 10 for ks in keys.values())  # 정상 답 10턴


def test_round_trip_matches_generator_session_format(tmp_path):
    _export(tmp_path)
    keys = _keys_by_session(tmp_path)
    _write_answers(tmp_path, keys, fenced=True)
    out = tmp_path / "sessions.jsonl"

    summary = ms.import_answers(tmp_path, model="test-model", out=out)

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert summary["written"] == 3 and summary["failed"] == 0
    for row in rows:
        sid = row["familyId"]
        assert row["generator"]["simulatorModel"] == "manual:test-model"
        assert len(row["turns"]) == 10 and len(row["history"]) == 20
        for turn in row["turns"]:
            assert turn["answer"] == f"{sid} t{turn['turnIndex']} 답이에요"
            assert turn["questionSource"] == "seed"
            if turn["mode"] == "off_topic":
                assert turn["reask"]["offTopicAnswer"] == f"{sid} t{turn['turnIndex']}_offtopic 답이에요"


def test_missing_answer_fails_only_that_session(tmp_path):
    _export(tmp_path)
    keys = _keys_by_session(tmp_path)
    _write_answers(tmp_path, keys, drop=("seed-11-00001", "t4"))

    summary = ms.import_answers(tmp_path, model="m", out=tmp_path / "o.jsonl")

    assert summary["written"] == 2
    assert summary["failures"] == [{"session": "seed-11-00001", "error": "'답 없음: t4'"}]


def test_parse_answers_rejects_non_json():
    with pytest.raises(ValueError):
        ms.parse_answers("죄송하지만 답할 수 없어요")


def test_run_fills_answers_with_cli_and_skips_done_batches(tmp_path):
    """가짜 CLI: 표준 입력으로 받은 프롬프트의 키마다 답을 만들어 JSON 으로 낸다."""
    ms.export(3, 11, 2, tmp_path, total_turns=10)
    fake = tmp_path / "fake_cli.py"
    fake.write_text(
        "import json, re, sys\n"
        "text = sys.stdin.read(); sessions = []\n"
        "for block in text.split('## [세션] ')[1:]:\n"
        "    sid = block.split()[0]\n"
        "    keys = re.findall(r'### 키: (\\S+)', block)\n"
        "    sessions.append({'session': sid, 'answers': {k: f'{sid} {k} 답' for k in keys}})\n"
        "print('```json'); print(json.dumps({'sessions': sessions}, ensure_ascii=False)); print('```')\n",
        encoding="utf-8",
    )

    first = ms.run_batches(tmp_path, cmd=f"python {fake}", retries=0, timeout=30, pause=0)
    again = ms.run_batches(tmp_path, cmd=f"python {fake}", retries=0, timeout=30, pause=0)

    assert first == {"done": 0, "made": 2, "failed": []}
    assert again == {"done": 2, "made": 0, "failed": []}
    assert ms.import_answers(tmp_path, model="fake", out=tmp_path / "s.jsonl")["written"] == 3


def test_run_reports_batches_with_missing_answers(tmp_path):
    ms.export(2, 11, 2, tmp_path, total_turns=10)

    result = ms.run_batches(tmp_path, cmd="echo '{\"sessions\": []}'", retries=1, timeout=30, pause=0)

    assert result["made"] == 0 and result["failed"][0]["error"].startswith("답 빠짐")
