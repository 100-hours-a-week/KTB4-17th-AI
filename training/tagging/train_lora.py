"""오픈 웨이트 모델에 태깅용 LoRA 어댑터를 학습한다. GPU 서버(RunPod 등)에서 실행한다.

    pip install -r training/requirements.txt
    python training/tagging/train_lora.py --base-model kakaocorp/kanana-2-3b-instruct \\
        --data-dir training/tagging/out --output-dir training/tagging/adapters/v1

학습 의존성(torch·trl 등)은 서버 이미지에 넣지 않으려고 pyproject 가 아니라 training/requirements.txt 에 둔다.
prompt/completion 형식이라 손실은 completion(모델이 낼 JSON)에만 걸린다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# [산출물 보관 추가] 이 파일을 경로로 직접 실행해도 저장소 루트의 training.artifacts를
# import할 수 있게 한다. 업로드 모듈은 torch 학습이나 FastAPI 앱을 시작하지 않는다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from training.artifacts import manifest as artifacts  # noqa: E402
from training.artifacts.__main__ import upload_from_env  # noqa: E402
from training.artifacts.errors import error_code  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-model", required=True, help="Hugging Face 모델 id 또는 로컬 경로")
    # [산출물 보관 추가] 어댑터와 함께 원본 모델의 버전을 기록하기 위한 입력이다.
    # HF 모델은 아래에서 실제 로드된 commit을 확인하고, 로컬 모델은 출처의 hash를 지정한다.
    parser.add_argument("--revision", help="원본 모델 revision. HF 모델은 로드된 실제 commit hash를 기록한다")
    parser.add_argument("--data-dir", type=Path, required=True, help="build_sft.py 출력 폴더")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument(
        "--max-length",
        type=int,
        default=2048,
        help="TAG_PROMPT + 질문·답변 + JSON 이 들어갈 길이. 차원 정의가 붙은 TAG_PROMPT 만 약 1,000 토큰",
    )
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--load-in-4bit", action="store_true", help="QLoRA. GPU 메모리가 부족할 때")
    parser.add_argument("--max-steps", type=int, default=-1, help="파이프라인 점검용으로 몇 스텝만 돌릴 때")
    parser.add_argument("--seed", type=int, default=42)
    # [산출물 보관 추가] 기본값은 학습 결과 저장 후 RunPod S3 업로드다.
    # --no-upload-runpod로 원격 업로드만 끌 수 있으며, 로컬 adapter와 manifest는 그대로 저장한다.
    parser.add_argument(
        "--upload-runpod",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="저장 성공 후 RunPod S3 업로드 (기본 활성화). 오프라인은 --no-upload-runpod",
    )
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    # [산출물 보관 추가] 학습 전에 UTC 시각+UUID 실행 ID를 발급하고 artifact_run.json에
    # 원본 모델 ID·요청 revision·코드 commit/dirty 여부·시작 시각을 남긴다.
    # 기존 결과를 덮어쓰지 않도록 비어 있지 않은 output-dir은 거절한다.
    # 이 단계는 로컬 실행 정보만 예약하며, S3 업로드나 원격 완료 표시는 만들지 않는다.
    artifacts.start_run(args.output_dir, base_model=args.base_model, requested_revision=args.revision)

    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    manifest = json.loads((args.data_dir / "manifest.json").read_text())
    if manifest.get("unreviewed_allowed"):
        print("경고: 검수하지 않은 데이터로 만든 학습 파일입니다. 파이프라인 점검용으로만 쓰세요.")

    has_val = (args.data_dir / "val.jsonl").stat().st_size > 0
    files = {"train": str(args.data_dir / "train.jsonl")}
    if has_val:
        files["validation"] = str(args.data_dir / "val.jsonl")
    dataset = load_dataset("json", data_files=files)

    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    model_kwargs: dict = {"dtype": torch.bfloat16 if bf16 else torch.float32}
    if args.load_in_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16 if bf16 else torch.float16,
        )
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, revision=args.revision)
    model = AutoModelForCausalLM.from_pretrained(args.base_model, revision=args.revision, **model_kwargs)
    # [산출물 보관 추가] main 같은 변경 가능한 이름 대신 실제 로드한 원본 commit을 기록한다.
    # 원본 revision을 확인할 수 없으면 아래 manifest 확정 단계가 실패하여 게시를 막는다.
    base_revision = getattr(model.config, "_commit_hash", None) or args.revision

    config = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        max_length=args.max_length,
        lr_scheduler_type="cosine",
        warmup_steps=0.05,  # 0~1 실수는 전체 스텝 대비 비율
        logging_steps=10,
        eval_strategy="epoch" if has_val else "no",
        save_strategy="epoch",
        save_total_limit=1,
        bf16=bf16,
        seed=args.seed,
        completion_only_loss=True,  # 프롬프트(TAG_PROMPT)는 외우지 않고 JSON 출력만 배운다
        report_to="none",
    )
    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules="all-linear",
        task_type="CAUSAL_LM",
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=dataset["train"],
        eval_dataset=dataset.get("validation"),
        processing_class=tokenizer,
        peft_config=peft_config,
    )
    trainer.train()
    # [산출물 보관 추가] 학습이 정상 종료된 뒤에만 기존 저장 → manifest 확정 → 업로드로 진행한다.
    # 저장/전송을 별도 함수로 묶었으며, 학습 계산과 SFT/LoRA 설정 자체는 유지한다.
    save_artifacts(args, trainer, tokenizer, base_revision=base_revision)


def save_artifacts(args, trainer, tokenizer, *, base_revision):
    """Keep the existing payload, seal its provenance, then publish it."""
    adapter_dir = args.output_dir / "adapter"
    trainer.save_model(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    # 어떤 데이터·프롬프트·기본 모델로 만든 어댑터인지 함께 남긴다 (재현·롤백용)
    shutil.copy(args.data_dir / "manifest.json", adapter_dir / "data_manifest.json")
    (adapter_dir / "training_args.json").write_text(
        json.dumps({**vars(args), "data_dir": str(args.data_dir), "output_dir": str(args.output_dir)}, indent=2)
    )
    print(f"saved adapter: {adapter_dir}")
    # [산출물 보관 추가 1/2: 로컬 결과 확정]
    # 위의 기존 adapter·tokenizer·data_manifest.json·training_args.json 저장이 모두 끝난 뒤
    # artifact_manifest.json을 생성한다. 원본 모델 ID/revision, 코드 commit, 시간대가 있는
    # 학습 시작/종료 시각, 데이터 manifest 해시, 파일별 상대 경로·크기·SHA-256을 기록한다.
    # 평가 상태는 not_evaluated다. manifest 생성은 모델 품질 평가 통과를 의미하지 않는다.
    artifacts.seal_run(args.output_dir, base_revision=base_revision)
    if args.upload_runpod:
        try:
            # [산출물 보관 추가 2/2: RunPod 저장소 게시]
            # 전용 RUNPOD_S3_* 인증으로 RunPod S3 API에 명시적으로 업로드한다.
            # 학습 Pod의 /workspace에 저장됐다는 이유로 ktb-slm에도 있다고 가정하지 않는다.
            # 기본 key: training-artifacts/v1/persona-tagging/lora/<실행 ID>/
            # 기존 객체를 덮어쓰지 않고 manifest와 files/를 게시한 다음, 원격 파일의
            # 개수·크기·SHA-256을 검증하고 TRAINING_COMPLETE.json을 마지막에 생성한다.
            # 실제 완료 표시 생성 코드는 training/artifacts/transfer.py의 upload_run()에 있다.
            # AWS 복사는 CPU EC2에서 별도로 수동 실행하며, GPU 추론 모델은 교체하지 않는다.
            upload_from_env(args.output_dir)
        except Exception as error:
            # 전송·검증 도중 실패하면 완료 표시 작성 단계로 진행하지 않는다. 로컬 산출물과
            # 이미 전송한 파일은 삭제하지 않는다. 아래 upload 명령만 다시 실행하면 같은
            # 실행 ID로 검증·재시도하므로 학습을 다시 돌릴 필요가 없다.
            # SDK 오류 원문에 비밀키가 섞일 수 있어 로그에는 고정 오류 코드만 출력한다.
            print(f"artifact_upload_failed code={error_code(error)}; local artifacts preserved", file=sys.stderr)
            print("retry: python -m training.artifacts upload --output-dir <same-output-dir>", file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
