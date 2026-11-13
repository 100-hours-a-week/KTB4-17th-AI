"""태깅 어댑터 자동 학습 — 학습 Pod 안에서 명령 하나로 데이터 받기 → 학습 → 평가 → 게이트 → 후보 업로드.

학습 후보 알림(Discord)을 받으면 사람이 학습용 RunPod Pod 를 켜고 이것을 실행한다. 운영 교체는 하지 않는다.
게이트를 통과한 어댑터만 HF 어댑터 리포에 candidate-* 태그로 올리고, 운영 반영(TAGGING_ADAPTER_REVISION 교체)은 사람이 승인한다.

  # Pod 안 (uv sync && pip install -r training/requirements.txt 후)
  export HF_TOKEN=... VLLM_API_KEY=... TRAINING_DISCORD_WEBHOOK_URL=...
  python training/pipeline/run_tagging.py \\
      --crowd-repo <org>/ktb-crowd-tagging --crowd-revision crowd-20261111-t1 \\
      --base-data /workspace/data/tagging.jsonl \\
      --champion-repo <org>/ktb-tagging-lora --champion-revision <운영 태그>
  # 단계만 확인:   ... --dry-run          GPU 없이 파이프라인 점검: ... --max-steps 4

단계
  1. 다수결 데이터 받기   HF private dataset 의 지정 리비전 (scripts/export_crowd_tagging.py 가 올린 것)
  2. 학습 데이터 합치기   --base-data(사람 확인된 TagExample) + 다수결 train.jsonl → build_sft.py (누수 · 검수 검사)
  3. LoRA 학습            train_lora.py. 기본 모델 · 리비전은 serving/vllm/config.yaml 과 같게
  4. 평가                 vLLM 에 champion · candidate 어댑터를 같이 올려 골드셋(eval_tagging) · 다수결(eval_crowd_tagging) 채점
  5. 게이트               gate.py — champion 보다 나빠진 지표가 하나라도 있으면 실패
  6. 업로드 · 알림         통과하면 어댑터 리포에 candidate-<다수결 리비전> 태그. 결과는 통과 · 실패 모두 Discord 로
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VLLM_CONFIG = ROOT / "serving/vllm/config.yaml"
HUMAN_GOLD = ROOT / "evals/quality_datasets/human/persona_onboarding_tagging.jsonl"
PY = sys.executable
HEALTH_TIMEOUT_S = 900  # 첫 기동은 모델 다운로드 · 컴파일로 오래 걸린다


def served_base_model(config: Path = VLLM_CONFIG) -> tuple[str, str]:
    """서빙 설정의 기본 모델과 리비전. 학습도 같은 가중치로 해야 어댑터가 서빙에서 맞는다."""
    text = config.read_text(encoding="utf-8")
    model = re.search(r"^model:\s*(\S+)", text, re.M)
    revision = re.search(r"^revision:\s*(\S+)", text, re.M)
    if not model or not revision:
        raise SystemExit(f"{config} 에서 model · revision 을 찾지 못했습니다")
    return model.group(1), revision.group(1)


class Paths:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.crowd = work / "crowd"
        self.data = work / "tagging.jsonl"
        self.sft = work / "sft"
        self.candidate_run = work / "candidate"
        self.candidate = work / "candidate/adapter"
        self.champion = work / "champion"
        self.eval = work / "eval"
        self.gate = work / "eval/gate.json"


def eval_commands(paths: Paths, base_url: str, gold: Path | None) -> list[list[str]]:
    commands = []
    for name in ("champion", "candidate"):
        gold_cmd = [PY, "scripts/eval_tagging.py", "--base-url", base_url, "--model", name]
        gold_cmd += ["--out", str(paths.eval / f"gold-{name}.json")]
        if gold is not None:
            gold_cmd += ["--dataset", str(gold), "--split", "calibration", "--split", "regression"]
        commands.append(gold_cmd)
        commands.append(
            [PY, "scripts/eval_crowd_tagging.py", str(paths.crowd / "eval.jsonl"), "--base-url", base_url]
            + ["--model", name, "--out", str(paths.eval / f"crowd-{name}.json")]
        )
    return commands


def gate_command(paths: Paths) -> list[str]:
    return [
        PY,
        "training/pipeline/gate.py",
        *("--gold-champion", str(paths.eval / "gold-champion.json")),
        *("--gold-candidate", str(paths.eval / "gold-candidate.json")),
        *("--crowd-champion", str(paths.eval / "crowd-champion.json")),
        *("--crowd-candidate", str(paths.eval / "crowd-candidate.json")),
        *("--out", str(paths.gate)),
    ]


def merge_training_data(base: list[Path], crowd_train: Path, out: Path) -> int:
    lines = []
    for path in [*base, crowd_train]:
        lines += [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)


def notify(content: str) -> None:
    url = os.environ.get("TRAINING_DISCORD_WEBHOOK_URL")
    if not url:
        print(f"[notify skipped: TRAINING_DISCORD_WEBHOOK_URL empty]\n{content}")
        return
    import httpx

    try:
        httpx.post(url, json={"content": content}, timeout=5).raise_for_status()
    except httpx.HTTPError as e:  # 알림 실패로 결과(파일 · 업로드)를 잃지 않는다
        print(f"[notify failed: {e}]\n{content}", file=sys.stderr)


def result_message(crowd_revision: str, gate: dict, tag: str | None) -> str:
    lines = [f"[태깅 자동 학습] {crowd_revision} → {'게이트 통과' if gate['passed'] else '게이트 실패'}"]
    lines += [
        f"{'✓' if c['passed'] else '✗'} {c['name']}: champion {c['champion']} → candidate {c['candidate']}"
        for c in gate["checks"]
    ]
    if tag:
        lines.append(f"후보 어댑터 태그: {tag}. 운영 반영은 승인 후 TAGGING_ADAPTER_REVISION 을 바꿔 주세요.")
    return "\n".join(lines)


def run(cmd: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, env=env, check=check).returncode


def wait_healthy(base_url: str, server: subprocess.Popen) -> None:
    import httpx

    health = base_url.removesuffix("/v1") + "/health"
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"vLLM 이 종료됐습니다 (exit {server.returncode})")
        try:
            if httpx.get(health, timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(5)
    raise SystemExit("vLLM 이 시간 안에 뜨지 않았습니다")


def pipeline(args: argparse.Namespace) -> int:
    from huggingface_hub import HfApi, snapshot_download

    paths = Paths(args.work_dir)
    base_model, base_revision = served_base_model()
    base_url = f"http://127.0.0.1:{args.port}/v1"
    gold = HUMAN_GOLD if HUMAN_GOLD.exists() else None
    plan = [
        f"download {args.crowd_repo}@{args.crowd_revision} → {paths.crowd}",
        f"merge {[str(p) for p in args.base_data]} + crowd/train.jsonl → {paths.data}",
        f"{PY} training/tagging/build_sft.py {paths.data} --out {paths.sft}",
        f"train {base_model}@{base_revision[:12]} → {paths.candidate}",
        f"download {args.champion_repo}@{args.champion_revision} → {paths.champion}",
        f"vllm serve :{args.port} champion + candidate",
        *[" ".join(c) for c in eval_commands(paths, base_url, gold)],
        " ".join(gate_command(paths)),
        f"if passed: upload {paths.candidate} → {args.adapter_repo}@candidate-{args.crowd_revision}; notify",
    ]
    if args.dry_run:
        print("\n".join(plan))
        return 0
    if not os.environ.get("VLLM_API_KEY"):
        raise SystemExit("VLLM_API_KEY 가 필요합니다 (평가용 vLLM)")
    paths.eval.mkdir(parents=True, exist_ok=True)

    snapshot_download(args.crowd_repo, repo_type="dataset", revision=args.crowd_revision, local_dir=paths.crowd)
    print(f"merged {merge_training_data(args.base_data, paths.crowd / 'train.jsonl', paths.data)} examples")
    run([PY, "training/tagging/build_sft.py", str(paths.data), "--out", str(paths.sft)])
    train = [PY, "training/tagging/train_lora.py", "--base-model", base_model, "--revision", base_revision]
    train += ["--data-dir", str(paths.sft), "--output-dir", str(paths.candidate_run)]
    if args.max_steps:
        train += ["--max-steps", str(args.max_steps)]
    run(train)
    snapshot_download(args.champion_repo, revision=args.champion_revision, local_dir=paths.champion)

    serve = ["vllm", "serve", "--config", str(VLLM_CONFIG), "--port", str(args.port), "--host", "127.0.0.1"]
    serve += ["--lora-modules", f"champion={paths.champion}", f"candidate={paths.candidate}"]
    print("$", " ".join(serve), flush=True)
    server = subprocess.Popen(serve, cwd=ROOT)
    try:
        wait_healthy(base_url, server)
        env = {**os.environ, "LLM_API_KEY": os.environ["VLLM_API_KEY"]}
        for cmd in eval_commands(paths, base_url, gold):
            run(cmd, env=env)
    finally:
        server.terminate()
        server.wait(timeout=60)

    code = run(gate_command(paths), check=False)
    if code not in (0, 3):
        raise SystemExit(f"gate.py 실패 (exit {code})")
    gate = json.loads(paths.gate.read_text(encoding="utf-8"))
    tag = None
    if gate["passed"]:
        tag = f"candidate-{args.crowd_revision}"
        for report in paths.eval.glob("*.json"):  # 평가 근거를 어댑터와 같이 남긴다
            (paths.candidate / "eval").mkdir(exist_ok=True)
            (paths.candidate / "eval" / report.name).write_bytes(report.read_bytes())
        api = HfApi()
        api.upload_folder(repo_id=args.adapter_repo, folder_path=str(paths.candidate), commit_message=tag)
        api.create_tag(args.adapter_repo, tag=tag)
    notify(result_message(args.crowd_revision, gate, tag))
    return 0 if gate["passed"] else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--crowd-repo", required=True, help="다수결 데이터 HF private dataset")
    parser.add_argument("--crowd-revision", required=True, help="export_crowd_tagging.py 가 붙인 태그")
    parser.add_argument("--base-data", type=Path, action="append", default=[], help="사람 확인된 TagExample JSONL")
    parser.add_argument("--champion-repo", default=os.environ.get("TAGGING_ADAPTER_REPO"))
    parser.add_argument("--champion-revision", default=os.environ.get("TAGGING_ADAPTER_REVISION"))
    parser.add_argument("--adapter-repo", help="후보를 올릴 어댑터 리포. 기본은 --champion-repo")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--max-steps", type=int, help="파이프라인 점검용으로 몇 스텝만 학습")
    parser.add_argument("--dry-run", action="store_true", help="단계만 출력")
    args = parser.parse_args(argv)
    if not args.champion_repo or not args.champion_revision:
        parser.error("--champion-repo · --champion-revision (또는 TAGGING_ADAPTER_REPO · _REVISION) 이 필요합니다")
    args.adapter_repo = args.adapter_repo or args.champion_repo
    args.work_dir = args.work_dir or Path("/workspace/pipeline") / args.crowd_revision
    if args.dry_run:
        return pipeline(args)
    try:
        return pipeline(args)
    except BaseException as e:  # 중간 단계가 죽어도 사람이 알 수 있게 알리고 그대로 올린다
        notify(f"[태깅 자동 학습] {args.crowd_revision} 중단: {type(e).__name__}: {e}"[:1900])
        raise


if __name__ == "__main__":
    raise SystemExit(main())
