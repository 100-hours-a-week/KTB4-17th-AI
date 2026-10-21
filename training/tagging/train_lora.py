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
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-model", required=True, help="Hugging Face 모델 id 또는 로컬 경로")
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
    return parser.parse_args()


def main() -> None:
    args = _arguments()

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
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)

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

    adapter_dir = args.output_dir / "adapter"
    trainer.save_model(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    # 어떤 데이터·프롬프트·기본 모델로 만든 어댑터인지 함께 남긴다 (재현·롤백용)
    shutil.copy(args.data_dir / "manifest.json", adapter_dir / "data_manifest.json")
    (adapter_dir / "training_args.json").write_text(
        json.dumps({**vars(args), "data_dir": str(args.data_dir), "output_dir": str(args.output_dir)}, indent=2)
    )
    print(f"saved adapter: {adapter_dir}")


if __name__ == "__main__":
    main()
