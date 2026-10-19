"""사용자 시뮬레이터를 API 대신 프론티어 모델 채팅(구독)으로 돌린다 — 붙여넣기용 프롬프트 내보내기 / 답 가져오기.

generate_synthetic_onboarding.py 와 같은 시드 프로필·주제 순서·답변 방식(정상·단답·농담·딴소리)을 쓴다. 이 셋은 답변 내용과
상관없이 --seed 로 정해지므로, 먼저 질문을 다 뽑아 채팅에 붙여 넣고 나중에 답을 받아 같은 세션 형식(JSONL)으로 만든다.
가져온 파일은 label_with_teacher.py --synthetic 에 그대로 넣는다.

  다른 점
    - 하루 질문은 LLM 이 아니라 주제의 시드 질문(운영에서 LLM 이 실패할 때 쓰는 질문)이다. API 비용 0.
      운영 질문은 앞 답에 공감하는 말이 붙어 더 길다 — 태깅 입력이 조금 다르다는 점을 학습 결과를 볼 때 감안한다
    - 시뮬레이터는 한 세션의 답을 한 번에 쓴다. 앞 턴에 자기가 쓴 답을 보며 이어 쓰므로 흐름은 유지된다

    # 1) 프롬프트 내보내기 — batch_000.md ... 를 채팅에 하나씩 붙여 넣는다
    uv run python scripts/manual_simulator.py export --n 100 --seed 300 --out-dir data/manual/run300
    # 2) 채팅 답(JSON)을 data/manual/run300/answers/batch_000.json ... 으로 저장. 구독 CLI 가 있으면 자동으로:
    uv run python scripts/manual_simulator.py run data/manual/run300 --cmd 'gemini -m gemini-3.5-flash-lite -p "{prompt}"'
    # 3) 가져오기 → 세션 JSONL
    uv run python scripts/manual_simulator.py import data/manual/run300 --model gpt-5 \\
        --out data/synthetic/manual-run300.jsonl
    # 4) 이후는 API 생성 때와 같다
    uv run python scripts/label_with_teacher.py --synthetic data/synthetic/manual-run300.jsonl --out-dir data/teacher/manual-run300
    uv run python scripts/check_synthetic_labels.py data/teacher/manual-run300 --sessions data/synthetic/manual-run300.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generate_synthetic_onboarding as gen  # noqa: E402

FORMAT_VERSION = "manual-simulator/1"

BATCH_HEADER = """\
아래 [세션]마다 소개팅 앱 온보딩에서 AI "하루"와 처음 대화하는 20~30대 사용자 한 명을 연기해, 하루의 질문에 답을 씁니다.
세션마다 다른 사람입니다. 앞 세션의 말투·표현·소재를 다음 세션에 섞지 마세요.

## 지켜야 할 것 (모든 답)
- 답변만 씁니다. 따옴표·설명·괄호 지문·이름표 금지. 줄바꿈 없이 메시지 하나
- 200자 이내. 실제 사람처럼 문장이 매끄럽지 않아도 됩니다. [말투]의 길이·말씨·웃음·맞춤법을 따릅니다
- 성향을 설명하듯 말하지 않습니다. 유형명·점수·성향 이름을 쓰지 않고 구체적인 습관·장면·최근 일로 드러냅니다
- 각 턴의 [이번 질문과 관련된 내 성향]과 어긋나는 말을 하지 않습니다. 적힌 수준(어느 쪽인지)이 드러나게 답합니다
- "잘 모름"으로 적힌 성향은 적힌 방식대로만 답하고, 그 성향을 짐작할 수 있는 행동·장면을 말하지 않습니다
- 그 턴과 관련 없는 다른 성향은 꺼내지 않습니다. 앞 턴에서 이미 한 말을 되풀이하지 않습니다
- 하루가 AI라는 걸 알고 있지만 굳이 언급하지 않습니다
- 관심사·데이트 목록을 나열하지 않습니다. 그 질문에 맞는 것 한두 개만 자연스럽게 꺼냅니다
- 예시 문장은 그대로 쓰지 말고 비슷한 나만의 경험·장면으로 바꿔 말합니다. 세션끼리 같은 문장이 나오지 않게 합니다
- [이번 답변 방식]을 따릅니다 (짧게·농담·딴소리 지시가 있으면 그대로)

## 출력 형식 — 이 JSON 하나만 출력합니다 (설명·마크다운 없이)
{"sessions": [{"session": "<세션 id>", "answers": {"<답 키>": "<답>", ...}}, ...]}
- 답 키는 각 턴 제목의 `키: ...` 를 그대로 씁니다. 모든 키에 답이 있어야 합니다
"""


# ══ 계획 (질문·답변 방식) ════════════════════════════════════


def answer_key(turn: int, mode: str) -> str:
    return f"t{turn}_offtopic" if mode == "off_topic" else f"t{turn}"


@dataclass
class _Calls:
    """run_session 이 시뮬레이터를 부르는 순서대로 (키, 질문, 답변 방식, 관련 차원)을 기록한다.
    딴소리 턴은 딴소리 답 → 되묻기 질문의 정상 답 순서로 두 번 불리고, 정상 답에서만 턴이 넘어간다."""

    turn: int = 0
    calls: list[dict] = field(default_factory=list)

    def next_key(self, mode: str) -> str:
        key = answer_key(self.turn, mode)
        if mode != "off_topic":
            self.turn += 1
        return key


class RecordingSimulator:
    def __init__(self) -> None:
        self.state = _Calls()

    async def answer(self, profile, history, question, mode, usage, focus=None) -> str:
        usage.simulator_calls += 1
        self.state.calls.append(
            {"key": self.state.next_key(mode), "question": question, "mode": mode, "focus": list(focus or [])}
        )
        return ""


class ManualSimulator:
    """채팅에서 받은 답을 run_session 이 부르는 순서대로 돌려준다."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.state = _Calls()

    async def answer(self, profile, history, question, mode, usage, focus=None) -> str:
        usage.simulator_calls += 1
        key = self.state.next_key(mode)
        if not (self.answers.get(key) or "").strip():
            raise KeyError(f"답 없음: {key}")
        return gen.clean_answer(self.answers[key])


def session_id(seed: int, index: int) -> str:
    return f"seed-{seed}-{index:05d}"


def _profile(seed: int, index: int) -> tuple[random.Random, gen.SeedProfile]:
    # generate_synthetic_onboarding.generate 와 같은 rng — 같은 --seed 면 같은 사람·같은 답변 방식
    rng = random.Random(f"{seed}:{index}")
    return rng, gen.sample_profile(rng, seed_id=session_id(seed, index))


async def plan_session(seed: int, index: int, total_turns: int) -> tuple[gen.SeedProfile, list[dict]]:
    rng, profile = _profile(seed, index)
    sim = RecordingSimulator()
    await gen.run_session(profile, rng=rng, ask=gen.seed_question, simulator=sim, total_turns=total_turns)
    return profile, sim.state.calls


def session_prompt(profile: gen.SeedProfile, calls: list[dict]) -> str:
    parts = [f"## [세션] {profile.seed_id}", "", gen.describe_profile_v2(profile), ""]
    for call in calls:
        parts += [
            f"### 키: {call['key']}",
            f"하루: {call['question']}",
            gen.focus_section(profile, call["focus"]),
            f"[이번 답변 방식] {gen.MODE_INSTRUCTION[call['mode']]}",
            "",
        ]
    return "\n".join(parts)


def export(n: int, seed: int, batch_size: int, out_dir: Path, total_turns: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "answers").mkdir(exist_ok=True)
    batches = []
    for start in range(0, n, batch_size):
        sessions = [asyncio.run(plan_session(seed, i, total_turns)) for i in range(start, min(start + batch_size, n))]
        name = f"batch_{start // batch_size:03d}"
        body = "\n".join(session_prompt(p, calls) for p, calls in sessions)
        (out_dir / f"{name}.md").write_text(BATCH_HEADER + "\n" + body, encoding="utf-8")
        batches.append(
            {
                "name": name,
                "sessions": [p.seed_id for p, _ in sessions],
                "keys": {p.seed_id: [c["key"] for c in calls] for p, calls in sessions},
            }
        )
    manifest = {
        "formatVersion": FORMAT_VERSION,
        "generatorVersion": gen.GENERATOR_VERSION,
        "seed": seed,
        "n": n,
        "batchSize": batch_size,
        "totalTurns": total_turns,
        "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "batches": batches,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


# ══ 가져오기 ════════════════════════════════════════════════


def parse_answers(text: str) -> list[dict]:
    """채팅 답에서 JSON 을 꺼낸다. ```json 울타리·앞뒤 말이 붙어 있어도 첫 { ~ 마지막 } 을 읽는다."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("JSON 을 찾지 못했습니다")
    data = json.loads(text[start : end + 1])
    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(sessions, list):
        raise ValueError('"sessions" 목록이 없습니다')
    return sessions


def import_answers(run_dir: Path, *, model: str, out: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    seed, total_turns = manifest["seed"], manifest["totalTurns"]
    wanted = {sid for b in manifest["batches"] for sid in b["sessions"]}
    answers: dict[str, dict[str, str]] = {}
    bad_files: list[dict] = []
    for path in sorted((run_dir / "answers").glob("*.json")):
        try:
            for s in parse_answers(path.read_text(encoding="utf-8")):
                answers[str(s.get("session"))] = {str(k): str(v) for k, v in (s.get("answers") or {}).items()}
        except (ValueError, json.JSONDecodeError) as e:
            bad_files.append({"file": path.name, "error": str(e)})

    models = {"simulatorModel": f"manual:{model}", "simulatorPrompt": "v2-batch", "conversationModel": "seed"}
    checker = gen.LeakChecker()
    records, failed, dropped = [], [], []
    for sid in sorted(wanted):
        if sid not in answers:
            failed.append({"session": sid, "error": "답 파일에 없음"})
            continue
        index = int(sid.rsplit("-", 1)[1])
        rng, profile = _profile(seed, index)
        try:
            session = gen.run_session(
                profile,
                rng=rng,
                ask=gen.seed_question,
                simulator=ManualSimulator(answers[sid]),
                total_turns=total_turns,
            )
            result = asyncio.run(session)
        except KeyError as e:
            failed.append({"session": sid, "error": str(e)})
            continue
        leaks = checker.session_leaks(result)
        if leaks:
            dropped.append({"session": sid, "leaks": leaks})
            continue
        records.append(gen.to_record(result, index=index, run_seed=seed, models=models))

    summary = {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": str(run_dir),
        "out": str(out),
        "requested": len(wanted),
        "written": len(records),
        "droppedForGoldLeak": len(dropped),
        "failed": len(failed),
        "badFiles": bad_files,
        "models": models,
        "dropped": dropped,
        "failures": failed,
    }
    gen.write_outputs(out, records, summary)
    return summary


# ══ 구독 CLI 로 자동 실행 ═══════════════════════════════════


def missing_keys(sessions: list[dict], expected: dict[str, list[str]]) -> list[str]:
    """답에 빠진 세션·키. 비어 있으면 그 묶음은 완성이다."""
    got = {str(s.get("session")): s.get("answers") or {} for s in sessions}
    return [
        f"{sid}:{key}"
        for sid, keys in expected.items()
        for key in keys
        if not str(got.get(sid, {}).get(key, "")).strip()
    ]


def run_batches(run_dir: Path, *, cmd: str, retries: int, timeout: float, pause: float) -> dict:
    """batch_*.md 를 하나씩 CLI 에 넣고 답(JSON)을 answers/ 에 저장한다. 이미 완성된 답은 건너뛴다(이어서 실행).

    cmd 의 {prompt} 는 프롬프트 전문, {file} 은 프롬프트 파일 경로로 바뀐다. 둘 다 없으면 프롬프트를 표준 입력으로 넣는다."""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    answers_dir = run_dir / "answers"
    answers_dir.mkdir(exist_ok=True)
    done, made, failed = [], [], []
    for batch in manifest["batches"]:
        target = answers_dir / f"{batch['name']}.json"
        expected = batch.get("keys") or {}
        if target.exists():
            try:
                if not missing_keys(parse_answers(target.read_text(encoding="utf-8")), expected):
                    done.append(batch["name"])
                    continue
            except (ValueError, json.JSONDecodeError):
                pass
        prompt = (run_dir / f"{batch['name']}.md").read_text(encoding="utf-8")
        command = cmd.replace("{file}", shlex.quote(str(run_dir / f"{batch['name']}.md")))
        stdin = prompt
        if "{prompt}" in command:
            command, stdin = (
                command.replace('"{prompt}"', shlex.quote(prompt)).replace("{prompt}", shlex.quote(prompt)),
                None,
            )
        elif "{file}" in cmd:
            stdin = None
        error = ""
        for attempt in range(retries + 1):
            try:
                proc = subprocess.run(
                    command, shell=True, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
                )
                missing = missing_keys(parse_answers(proc.stdout), expected)
                if not missing:
                    target.write_text(proc.stdout, encoding="utf-8")
                    made.append(batch["name"])
                    print(f"  {batch['name']} ok", file=sys.stderr)
                    break
                error = f"답 빠짐 {len(missing)}개 (예: {missing[0]})"
            except (ValueError, json.JSONDecodeError) as e:
                error = f"JSON 아님: {e} · {(proc.stderr or proc.stdout)[-200:]}"
            except subprocess.TimeoutExpired:
                error = f"{timeout:.0f}초 넘김"
            print(f"  {batch['name']} 실패 ({attempt + 1}회): {error}", file=sys.stderr)
            time.sleep(pause)
        else:
            failed.append({"batch": batch["name"], "error": error})
        time.sleep(pause)
    return {"done": len(done), "made": len(made), "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export", help="붙여넣기용 프롬프트 파일 만들기")
    ex.add_argument("--n", type=int, required=True, help="세션 수")
    ex.add_argument("--seed", type=int, required=True, help="API 생성과 겹치지 않는 시드를 쓴다 (예: 300)")
    ex.add_argument("--batch-size", type=int, default=5, help="채팅 한 번에 붙여 넣을 세션 수")
    ex.add_argument("--out-dir", type=Path, required=True)
    ex.add_argument("--total-turns", type=int, default=None, help="기본은 운영과 같은 ONBOARDING_TOTAL_TURNS")
    rn = sub.add_parser("run", help="구독 CLI 로 batch_*.md 를 자동 실행해 answers/ 에 저장 (이어서 실행 가능)")
    rn.add_argument("run_dir", type=Path)
    rn.add_argument(
        "--cmd",
        required=True,
        help="CLI 명령. {prompt}=프롬프트 전문, {file}=프롬프트 파일. 둘 다 없으면 표준 입력으로 넣는다",
    )
    rn.add_argument("--retries", type=int, default=2, help="JSON 이 깨지거나 답이 빠졌을 때 다시 시도하는 횟수")
    rn.add_argument("--timeout", type=float, default=600, help="한 묶음 제한 시간(초)")
    rn.add_argument("--pause", type=float, default=5, help="호출 사이 쉬는 시간(초). 구독 사용량 한도 대비")
    im = sub.add_parser("import", help="채팅 답(JSON)을 세션 JSONL 로")
    im.add_argument("run_dir", type=Path, help="export 의 --out-dir (answers/*.json 을 읽는다)")
    im.add_argument("--model", required=True, help="답을 쓴 모델 이름 (기록용, 예: gpt-5, claude-opus)")
    im.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "export":
        from app.features.persona.service import ONBOARDING_TOTAL_TURNS

        manifest = export(args.n, args.seed, args.batch_size, args.out_dir, args.total_turns or ONBOARDING_TOTAL_TURNS)
        print(f"{len(manifest['batches'])}개 프롬프트 → {args.out_dir} (답은 {args.out_dir / 'answers'} 에 저장)")
        return 0
    if args.command == "run":
        result = run_batches(args.run_dir, cmd=args.cmd, retries=args.retries, timeout=args.timeout, pause=args.pause)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not result["failed"] else 1
    summary = import_answers(args.run_dir, model=args.model, out=args.out)
    print(
        json.dumps(
            {k: summary[k] for k in ("requested", "written", "droppedForGoldLeak", "failed", "badFiles")},
            ensure_ascii=False,
        )
    )
    return 0 if summary["written"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
