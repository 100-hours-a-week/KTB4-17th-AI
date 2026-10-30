"""새로운 온디바이스 모델 학습 프로젝트(/Users/hrun/persona_model)로 필수 자산을 복사하고 독립 구성을 완성하는 스크립트."""

import os
import shutil
from pathlib import Path

SRC_ROOT = Path("/Users/hrun/Documents/ktb4-17/KTB-17DEV")
DST_ROOT = Path("/Users/hrun/persona_model")

def copy_and_setup():
    print(f"Setting up persona_model project at {DST_ROOT}...")

    # 1. 디렉토리 구조 생성
    dirs = [
        DST_ROOT / "data" / "docs",
        DST_ROOT / "data" / "sft",
        DST_ROOT / "prompt_engine",
        DST_ROOT / "evals" / "f06-practice-reply",
        DST_ROOT / "evals" / "quality_datasets",
        DST_ROOT / "evals" / "harness",
        DST_ROOT / "tools",
        DST_ROOT / "training" / "configs",
        DST_ROOT / "export"
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # 2. 페르소나 데이터 및 문서 복사
    shutil.copy2(SRC_ROOT / "페르소나 데이터" / "persona_cards.json", DST_ROOT / "data" / "persona_cards.json")
    shutil.copy2(SRC_ROOT / "페르소나 데이터" / "AI_학습용_페르소나_시스템_기획서.md", DST_ROOT / "data" / "docs" / "AI_학습용_페르소나_시스템_기획서.md")
    shutil.copy2(SRC_ROOT / "페르소나 데이터" / "캐릭터_페르소나_통합_명세서.md", DST_ROOT / "data" / "docs" / "캐릭터_페르소나_통합_명세서.md")
    shutil.copy2(SRC_ROOT / "페르소나 데이터" / "페르소나_품질_검증_보고서.md", DST_ROOT / "data" / "docs" / "페르소나_품질_검증_보고서.md")
    print("Copied persona datasets and documents.")

    # 3. 평가 도구 (Evals) 복사
    # f06-practice-reply 폴더 복사
    f06_src = SRC_ROOT / "evals" / "f06-practice-reply"
    f06_dst = DST_ROOT / "evals" / "f06-practice-reply"
    for item in f06_src.iterdir():
        if item.is_file():
            shutil.copy2(item, f06_dst / item.name)
        elif item.is_dir() and item.name != ".DS_Store":
            shutil.copytree(item, f06_dst / item.name, dirs_exist_ok=True)

    # practice_reply.jsonl
    pr_src = SRC_ROOT / "evals" / "quality_datasets" / "practice_reply.jsonl"
    if pr_src.exists():
        shutil.copy2(pr_src, DST_ROOT / "evals" / "quality_datasets" / "practice_reply.jsonl")

    # auto_checks.py
    ac_src = SRC_ROOT / "evals" / "_harness" / "auto_checks.py"
    if ac_src.exists():
        shutil.copy2(ac_src, DST_ROOT / "evals" / "harness" / "auto_checks.py")
    print("Copied evals and benchmark datasets.")

    # 4. 검증 스크립트 복사
    shutil.copy2(SRC_ROOT / "scripts" / "verify_persona_quality.py", DST_ROOT / "tools" / "verify_persona_quality.py")
    print("Copied verification tools.")

copy_and_setup()
