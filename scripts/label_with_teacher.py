"""teacher 라벨링 — 합성 세션과 골드셋에 teacher 모델로 태깅(n회 샘플링)과 추출을 돌린다.

프롬프트는 운영 코드(app/features/persona/agents.py)의 TAG_PROMPT·RUBRIC·입력 형식을 그대로 쓴다.
student 가 배울 출력 형식이 운영과 같아야 하기 때문이다. teacher 는 docs/성능평가/10_증류_teacher_모델_선정기준.md
4.4절의 결정(qwen/qwen3.8-27b, 날짜 고정 버전)을 기본값으로 한다.

  - 태깅: 답변마다 n회(기본 5) 샘플링 → 차원별 득표율(소프트 라벨)과 다수결(3/5 이상) 라벨
          되묻기 전 무관 답변(reask.offTopicAnswer)도 태깅한다 — off_topic 양성 사례가 귀하다
  - 추출: 세션마다 1회(temperature 0). 허용 차원 목록(answered)은
          합성 세션 = 운영처럼 teacher 태깅 다수결 primary 의 합집합, 골드 = 데이터셋의 answeredDimensions
  - 원본 출력을 보존한다. 없는 차원·점수 키·스키마 오류는 걸러 버리지 않고 기록한다 (teacher 평가 지표)
  - blind_holdout 은 기본으로 라벨링하지 않는다 (최종 student 판정용으로 봉인)

    .venv/bin/python scripts/label_with_teacher.py --synthetic data/synthetic/onboarding_sessions.jsonl
    .venv/bin/python scripts/label_with_teacher.py --gold --limit 10
    .venv/bin/python scripts/label_with_teacher.py --synthetic ... --gold --resume   # 끊긴 곳부터 이어서

모델·엔드포인트는 TEACHER_LLM_BASE_URL / TEACHER_LLM_MODEL / TEACHER_LLM_API_KEY, 비어 있으면 LLM_* 를 쓴다.
OpenRouter 를 쓰면 입력을 학습에 쓰는 호스팅 업체는 제외(data_collection=deny)하고, 추론 모드는 기본으로 끈다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "qwen/qwen3.8-27b-20260814"
DEFAULT_OUT_DIR = ROOT / "data" / "teacher"
GOLD_DIR = ROOT / "evals" / "quality_datasets"
LABELER_VERSION = "teacher-label/1"
SCHEMA_VERSION = 1

# 운영 호출과 같은 출력 상한. 추론 모드를 켜면 추론 토큰이 따로 붙으므로 늘린다
TAG_MAX_TOKENS = 120
EXTRACT_MAX_TOKENS = 1500
REASONING_EXTRA_TOKENS = 4000

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


# ══ teacher 호출 ═══════════════════════════════════════════


@dataclass
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


# (system, user, temperature, max_tokens) → Completion. 테스트는 이 자리에 가짜를 넣는다
CompleteFn = Callable[[str, str, float, int], Awaitable[Completion]]


class TeacherClient:
    """OpenAI 호환 엔드포인트. 일시 오류는 지수 백오프로 몇 번 다시 보낸다."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        reasoning: bool,
        providers: list[str] | None,
        timeout: float = 60.0,
        retries: int = 3,
    ) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout)
        self.model = model
        self.reasoning = reasoning
        self.retries = retries
        self.extra_body: dict[str, Any] = {}
        if "openrouter.ai" in base_url:
            provider: dict[str, Any] = {"data_collection": "deny"}  # 입력을 학습에 쓰는 업체로 라우팅하지 않는다
            if providers:
                provider |= {"order": providers, "allow_fallbacks": False}
            self.extra_body = {"provider": provider, "reasoning": {"enabled": reasoning, "exclude": True}}

    async def complete(self, system: str, user: str, temperature: float, max_tokens: int) -> Completion:
        if self.reasoning:
            max_tokens += REASONING_EXTRA_TOKENS
        delay = 2.0
        for attempt in range(self.retries + 1):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=self.extra_body or None,
                )
            except Exception:
                if attempt == self.retries:
                    raise
                await asyncio.sleep(delay)
                delay *= 2
                continue
            usage = resp.usage
            return Completion(
                text=(resp.choices[0].message.content or "") if resp.choices else "",
                prompt_tokens=(usage.prompt_tokens or 0) if usage else 0,
                completion_tokens=(usage.completion_tokens or 0) if usage else 0,
            )
        raise AssertionError("unreachable")


def parse_json(text: str) -> dict:
    """운영 _call_json 과 같은 규칙(첫 { ~ 마지막 })에 추론 블록 제거만 더한다. 실패하면 ValueError."""
    text = _THINK.sub("", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    cleaned = text[start : end + 1] if start != -1 and end > start else text
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


# ══ 라벨링 대상 ════════════════════════════════════════════


@dataclass
class TagItem:
    id: str
    source: str  # synthetic | gold
    family_id: str
    question: str
    answer: str
    split: str | None = None
    context: dict = field(default_factory=dict)  # 합성: 답변 방식(mode) 등 — teacher 오류 분석용 참고값


@dataclass
class ExtractItem:
    id: str
    source: str
    family_id: str
    history: list[dict]
    answered: list[str] | None  # None 이면 같은 세션의 태깅 다수결로 채운다
    tag_ids: list[str] = field(default_factory=list)
    split: str | None = None
    context: dict = field(default_factory=dict)


def synthetic_items(path: Path) -> tuple[list[TagItem], list[ExtractItem]]:
    tags: list[TagItem] = []
    extracts: list[ExtractItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        sid, family = s["sessionId"], s["familyId"]
        ids: list[str] = []
        for t in s["turns"]:
            tid = f"synthetic:{sid}:t{t['turnIndex']}"
            ids.append(tid)
            tags.append(TagItem(tid, "synthetic", family, t["question"], t["answer"], context={"mode": t["mode"]}))
            if t.get("reask"):
                # 운영은 이 답을 저장하지 않지만 off_topic 학습에는 필요하다. answered 계산에는 넣지 않는다
                tags.append(
                    TagItem(
                        f"{tid}:offtopic",
                        "synthetic",
                        family,
                        t["question"],
                        t["reask"]["offTopicAnswer"],
                        context={"mode": "off_topic", "intendedOffTopic": True},
                    )
                )
        extracts.append(
            ExtractItem(
                f"synthetic:{sid}",
                "synthetic",
                family,
                s["history"],
                None,
                ids,
                context={"seedLevels": s["seedProfile"]["levels"]},
            )
        )
    return tags, extracts


def gold_items(gold_dir: Path, *, include_holdout: bool) -> tuple[list[TagItem], list[ExtractItem]]:
    """label_accuracy 사례만. code_behavior 는 모델이 아니라 서비스 코드 동작을 보는 fixture 다."""

    def rows(name: str) -> Iterable[dict]:
        path = gold_dir / f"{name}.jsonl"
        if not path.exists():  # 사람 골드셋(human/)은 한쪽만 있을 수 있다
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            meta = row["metadata"]
            if meta["evaluationKind"] != "label_accuracy":
                continue
            if meta["split"] == "blind_holdout" and not include_holdout:
                continue
            yield row

    tags = [
        TagItem(
            f"gold:{r['metadata']['caseId']}",
            "gold",
            r["metadata"]["familyId"],
            r["input"]["question"],
            r["input"]["answer"],
            split=r["metadata"]["split"],
        )
        for r in rows("persona_onboarding_tagging")
    ]
    extracts = [
        ExtractItem(
            f"gold:{r['metadata']['caseId']}",
            "gold",
            r["metadata"]["familyId"],
            r["input"]["history"],
            list(r["input"]["answeredDimensions"]),
            split=r["metadata"]["split"],
        )
        for r in rows("persona_build")
    ]
    return tags, extracts


# ══ 태깅 ══════════════════════════════════════════════════


def prompt_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def check_tag_sample(raw: dict) -> dict:
    """원본 판정 하나 → 정리된 판정 + 위반 기록. 운영 TaggingAgent 처럼 없는 차원은 빼되, 뺀 것을 남긴다."""
    from app.features.persona.schemas import ALL_DIMENSIONS

    valid = set(ALL_DIMENSIONS)
    primary = [d for d in raw.get("primary") or [] if isinstance(d, str)]
    secondary = [d for d in raw.get("secondary") or [] if isinstance(d, str)]
    return {
        "primary": sorted({d for d in primary if d in valid}),
        "secondary": sorted({d for d in secondary if d in valid}),
        "off_topic": bool(raw.get("off_topic", False)),
        "invalidDimensions": sorted({d for d in primary + secondary if d not in valid}),  # E2-CRIT-01
        "extraKeys": sorted(set(raw) - {"primary", "secondary", "off_topic"}),  # E2-CRIT-02 (점수 필드 등)
    }


def aggregate_tags(samples: list[dict]) -> dict:
    """성공한 샘플들 → 득표율(소프트 라벨)과 다수결. 다수결 문턱은 성공 샘플의 과반."""
    ok = [s for s in samples if "error" not in s]
    if not ok:
        return {"validSamples": 0}
    n = len(ok)
    need = n // 2 + 1
    primary = Counter(d for s in ok for d in s["primary"])
    secondary = Counter(d for s in ok for d in s["secondary"])
    off = sum(s["off_topic"] for s in ok)
    majority = {
        "primary": sorted(d for d, c in primary.items() if c >= need),
        "secondary": sorted(d for d, c in secondary.items() if c >= need),
        "off_topic": off >= need,
    }
    if majority["off_topic"]:
        majority["primary"], majority["secondary"] = [], []
    same = sum(
        s["primary"] == majority["primary"] and s["off_topic"] == majority["off_topic"] for s in ok
    )  # 다수결과 primary·off_topic 이 완전히 같은 샘플 비율 — 라벨 일관성
    return {
        "validSamples": n,
        "primaryVotes": {d: round(c / n, 3) for d, c in sorted(primary.items())},
        "secondaryVotes": {d: round(c / n, 3) for d, c in sorted(secondary.items())},
        "offTopicVote": round(off / n, 3),
        "majority": majority,
        "agreement": round(same / n, 3),
    }


async def label_tag(
    item: TagItem,
    complete: CompleteFn,
    *,
    samples: int,
    temperature: float,
    usage: Usage,
) -> dict:
    from app.features.persona.agents import TAG_PROMPT, tagging_messages

    # 운영 TaggingAgent·파인튜닝 학습 데이터(training/tagging)와 같은 입력
    user = tagging_messages(item.question, item.answer)[0]["content"]

    async def one() -> dict:
        try:
            c = await complete(TAG_PROMPT, user, temperature, TAG_MAX_TOKENS)
        except Exception as e:
            usage.failed_calls += 1
            return {"error": f"call: {type(e).__name__}: {e}"}
        usage.add(c)
        try:
            return check_tag_sample(parse_json(c.text))
        except (ValueError, json.JSONDecodeError) as e:
            return {"error": f"parse: {e}", "raw": c.text[:500]}  # E2-CRIT-03

    results = await asyncio.gather(*(one() for _ in range(samples)))
    return {
        "kind": "tagging",
        "id": item.id,
        "source": item.source,
        "familyId": item.family_id,
        "split": item.split,
        "input": {"question": item.question, "answer": item.answer},
        "context": item.context,
        "samples": list(results),
        "aggregate": aggregate_tags(list(results)),
    }


# ══ 추출 ══════════════════════════════════════════════════


async def label_extract(item: ExtractItem, answered: list[str], complete: CompleteFn, *, usage: Usage) -> dict:
    from pydantic import ValidationError

    from app.features.persona.agents import RUBRIC, ExtractionAgent
    from app.features.persona.schemas import RawExtraction

    content = ExtractionAgent._transcript(item.history) + ExtractionAgent._answered_section(set(answered))
    record = {
        "kind": "extraction",
        "id": item.id,
        "source": item.source,
        "familyId": item.family_id,
        "split": item.split,
        "input": {"history": item.history, "answered": sorted(answered)},
        "context": item.context,
    }
    try:
        c = await complete(RUBRIC, content, 0.0, EXTRACT_MAX_TOKENS)
    except Exception as e:
        usage.failed_calls += 1
        return record | {"error": f"call: {type(e).__name__}: {e}"}
    usage.add(c)
    try:
        raw = parse_json(c.text)
    except (ValueError, json.JSONDecodeError) as e:
        return record | {"error": f"parse: {e}", "raw": c.text[:2000]}
    try:
        RawExtraction.model_validate(raw)
        schema_error = None
    except ValidationError as e:
        schema_error = str(e)[:1000]
    # 허용 목록 밖에 점수를 낸 키 — 사후 필터가 지우지만 teacher 의 유보 습관 지표(환각률)다
    from app.features.persona.schemas import SCORED, TEXTUAL

    allowed = set(answered)
    unanswered_keys = sorted(
        k for k in raw if (k in SCORED and raw[k] is not None or k in TEXTUAL and raw[k]) and k not in allowed
    )
    return record | {"output": raw, "schemaError": schema_error, "unansweredKeys": unanswered_keys}


# ══ 실행 ══════════════════════════════════════════════════


@dataclass
class Usage:
    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, c: Completion) -> None:
        self.calls += 1
        self.prompt_tokens += c.prompt_tokens
        self.completion_tokens += c.completion_tokens


def read_done(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    done = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if "error" not in row:  # 실패한 건은 이어서 실행할 때 다시 시도한다
                done[row["id"]] = row
    return done


def append_line(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(
    tag_items: list[TagItem],
    extract_items: list[ExtractItem],
    complete: CompleteFn,
    *,
    out_dir: Path,
    samples: int,
    temperature: float,
    concurrency: int,
    resume: bool,
    teacher_meta: dict,
) -> dict:
    from app.features.persona.agents import RUBRIC, TAG_PROMPT

    tag_path, extract_path = out_dir / "tagging.jsonl", out_dir / "extraction.jsonl"
    if not resume:
        for p in (tag_path, extract_path):
            p.unlink(missing_ok=True)
    done_tags = read_done(tag_path) if resume else {}
    done_extracts = read_done(extract_path) if resume else {}

    meta = teacher_meta | {
        "labelerVersion": LABELER_VERSION,
        "tagPromptVersion": prompt_version(TAG_PROMPT),
        "extractPromptVersion": prompt_version(RUBRIC),
    }
    usage = Usage()
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    tag_results: dict[str, dict] = dict(done_tags)
    counts = Counter()

    async def write(path: Path, row: dict) -> None:
        async with lock:  # 한 줄씩 덧붙여 중간에 끊겨도 앞선 결과가 남는다
            await asyncio.to_thread(append_line, path, row)

    async def do_tag(item: TagItem) -> None:
        async with sem:
            row = await label_tag(item, complete, samples=samples, temperature=temperature, usage=usage)
        row = {"schemaVersion": SCHEMA_VERSION, **row, "teacher": meta | {"temperature": temperature}}
        tag_results[item.id] = row
        counts["tagging"] += 1
        await write(tag_path, row)

    await asyncio.gather(*(do_tag(i) for i in tag_items if i.id not in done_tags))

    async def do_extract(item: ExtractItem) -> None:
        answered = item.answered
        if answered is None:
            # 운영 _dimensions_from_answers 처럼 각 턴 primary(다수결)의 합집합. off_topic 턴은 빠진다
            answered = sorted(
                {
                    d
                    for tid in item.tag_ids
                    for d in tag_results.get(tid, {}).get("aggregate", {}).get("majority", {}).get("primary", [])
                }
            )
        async with sem:
            row = await label_extract(item, answered, complete, usage=usage)
        row = {"schemaVersion": SCHEMA_VERSION, **row, "teacher": meta | {"temperature": 0.0}}
        counts["extraction"] += 1
        await write(extract_path, row)

    await asyncio.gather(*(do_extract(i) for i in extract_items if i.id not in done_extracts))

    return {
        "labeledNow": dict(counts),
        "skippedResume": {"tagging": len(done_tags), "extraction": len(done_extracts)},
        "usage": asdict(usage),
        "teacher": meta,
    }


def summarize(out_dir: Path) -> dict:
    """출력 파일 전체(이어서 실행한 것 포함)로 teacher 품질 지표를 낸다. 선정 기준서 7절의 칸과 맞춘다."""
    tags = [json.loads(x) for x in (out_dir / "tagging.jsonl").read_text(encoding="utf-8").splitlines() if x]
    extracts = [json.loads(x) for x in (out_dir / "extraction.jsonl").read_text(encoding="utf-8").splitlines() if x]
    samples = [s for t in tags for s in t["samples"]]
    ok = [s for s in samples if "error" not in s]
    aggs = [t["aggregate"] for t in tags if t["aggregate"].get("validSamples")]
    intended = [t for t in tags if t["context"].get("intendedOffTopic")]
    valid_ex = [e for e in extracts if "output" in e]
    return {
        "tagging": {
            "items": len(tags),
            "samples": len(samples),
            "parseOrCallFailRate": round(1 - len(ok) / len(samples), 4) if samples else None,
            "invalidDimensionSamples": sum(bool(s["invalidDimensions"]) for s in ok),
            "extraKeySamples": sum(bool(s["extraKeys"]) for s in ok),
            "meanAgreement": round(sum(a["agreement"] for a in aggs) / len(aggs), 3) if aggs else None,
            "offTopicRecallOnIntended": (
                round(
                    sum(t["aggregate"].get("majority", {}).get("off_topic", False) for t in intended) / len(intended), 3
                )
                if intended
                else None
            ),
            "emptyPrimaryRate": (
                round(sum(not a["majority"]["primary"] and not a["majority"]["off_topic"] for a in aggs) / len(aggs), 3)
                if aggs
                else None
            ),
        },
        "extraction": {
            "items": len(extracts),
            "failRate": round(1 - len(valid_ex) / len(extracts), 4) if extracts else None,
            "schemaErrors": sum(bool(e.get("schemaError")) for e in valid_ex),
            "itemsWithUnansweredKeys": sum(bool(e["unansweredKeys"]) for e in valid_ex),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--synthetic", type=Path, help="generate_synthetic_onboarding.py 출력 JSONL")
    parser.add_argument("--gold", action="store_true", help="골드셋(label_accuracy)도 라벨링")
    parser.add_argument("--include-holdout", action="store_true", help="blind_holdout 까지 (최종 판정 때만)")
    parser.add_argument(
        "--gold-dir", type=Path, default=GOLD_DIR, help="골드셋 폴더. 사람 골드셋은 evals/quality_datasets/human"
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--samples", type=int, default=5, help="태깅 샘플링 횟수")
    parser.add_argument("--temperature", type=float, default=0.7, help="태깅 샘플링 온도 (추출은 0)")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="소스별 앞에서 N개 세션·사례만 (시험용)")
    parser.add_argument("--resume", action="store_true", help="출력에 이미 있는 성공 건은 건너뛴다")
    parser.add_argument("--reasoning", action="store_true", help="추론 모드 켜기 (기본 끔)")
    parser.add_argument("--providers", default="", help="OpenRouter 호스팅 업체 고정, 쉼표 구분")
    parser.add_argument("--price-in", type=float, default=None, help="입력 100만 토큰당 USD")
    parser.add_argument("--price-out", type=float, default=None, help="출력 100만 토큰당 USD")
    args = parser.parse_args(argv)
    if not args.synthetic and not args.gold:
        parser.error("--synthetic 또는 --gold 중 하나는 필요합니다")
    return args


def collect(args: argparse.Namespace) -> tuple[list[TagItem], list[ExtractItem]]:
    tags: list[TagItem] = []
    extracts: list[ExtractItem] = []
    if args.synthetic:
        t, e = synthetic_items(args.synthetic)
        if args.limit is not None:
            e = e[: args.limit]
            keep = {tid for x in e for tid in x.tag_ids}
            t = [x for x in t if x.id.split(":offtopic")[0] in keep]
        tags += t
        extracts += e
    if args.gold:
        t, e = gold_items(args.gold_dir, include_holdout=args.include_holdout)
        if args.limit is not None:
            t, e = t[: args.limit], e[: args.limit]
        tags += t
        extracts += e
    return tags, extracts


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from app.core.config import get_settings

    settings = get_settings()
    client = TeacherClient(
        base_url=os.getenv("TEACHER_LLM_BASE_URL") or settings.llm_base_url,
        api_key=os.getenv("TEACHER_LLM_API_KEY") or settings.llm_api_key,
        model=os.getenv("TEACHER_LLM_MODEL") or DEFAULT_MODEL,
        reasoning=args.reasoning,
        providers=[p.strip() for p in args.providers.split(",") if p.strip()] or None,
    )
    tags, extracts = collect(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"teacher={client.model} reasoning={args.reasoning} tagging={len(tags)} extraction={len(extracts)}",
        file=sys.stderr,
    )

    started = time.monotonic()
    result = asyncio.run(
        run(
            tags,
            extracts,
            client.complete,
            out_dir=args.out_dir,
            samples=args.samples,
            temperature=args.temperature,
            concurrency=args.concurrency,
            resume=args.resume,
            teacher_meta={
                "model": client.model,
                "reasoning": args.reasoning,
                "samples": args.samples,
            },
        )
    )
    u = result["usage"]
    if args.price_in is not None and args.price_out is not None:
        result["estimatedCostUsd"] = round(
            u["prompt_tokens"] / 1e6 * args.price_in + u["completion_tokens"] / 1e6 * args.price_out, 4
        )
    result |= {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "elapsedSeconds": round(time.monotonic() - started, 1),
        "quality": summarize(args.out_dir),
    }
    (args.out_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
