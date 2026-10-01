"""Run the Langfuse quality-dataset calibration split against the current app.

The six hosted datasets contain two kinds of cases:

* generation cases call the production Agent classes and therefore call the
  model configured by ``LLM_MODEL``;
* deterministic fixture cases exercise parser, fallback, schema, and score
  boundaries locally without sending the fixture payload to the model.

The resulting outputs and deterministic scores are stored as Langfuse
Experiments. Semantic rubric judging is intentionally not included yet: the
calibration split exists so that human reviewers can calibrate that judge.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from langfuse import Evaluation, get_client  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.observability import build_langfuse_metadata  # noqa: E402
from app.features.persona.agents import (  # noqa: E402
    ConversationAgent,
    ExtractionAgent,
    TaggingAgent,
    _first_turn_fallback,
    _parse_first_turn,
)
from app.features.persona.schemas import (  # noqa: E402
    MAX_ANSWER_LEN,
    SCORED,
    TEXTUAL,
    AnswerRequest,
    PersonaResponse,
    Segment,
    Topic,
    Weight,
    answer_problem,
)
from app.features.persona.service import (  # noqa: E402
    MIN_ANSWERS_TO_FINISH,
    accuracy_of,
    confidence_of,
    fallback_extraction,
)
from app.features.practice.agents import FALLBACK_REPLY, PartnerAgent  # noqa: E402
from app.features.practice.schemas import PracticeMessageRequest  # noqa: E402
from app.features.simulation.agents import (  # noqa: E402
    ReportAgent,
    SimulationAgent,
    _self_addressed_lines,
    _validate_script,
)
from app.features.simulation.report import (  # noqa: E402
    assemble_report,
    build_report,
    score_layer,
    template_narrative,
)
from app.features.simulation.schemas import (  # noqa: E402
    ReportInput,
    ReportNarrative,
    Transcript,
    grade_of,
)

DATASETS = (
    "quality/persona/onboarding-conversation",
    "quality/persona/onboarding-tagging",
    "quality/persona/build",
    "quality/practice/reply",
    "quality/simulation/run",
    "quality/simulation/report-preview",
)

FEATURES = {
    "persona_conversation": ("persona", "conversation"),
    "persona_tagging": ("persona", "tagging"),
    "persona_build": ("persona", "extraction"),
    "practice_reply": ("practice", "reply"),
    "simulation_run": ("simulation", "run"),
    "simulation_report_preview": ("simulation", "report-preview"),
}

EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf]",
)
SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?", re.MULTILINE)
AI_WORDS = ("AI", "인공지능", "챗봇", "어시스턴트", "언어 모델")


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "unknown"


def _topic(raw: dict[str, Any]) -> Topic:
    choices = raw.get("choices")
    return Topic(
        id=raw["id"],
        weight=Weight[raw["weight"]],
        intent=raw["intent"],
        seed=raw["seed"],
        covers=tuple(raw.get("covers", [])),
        also_touches=tuple(raw.get("also_touches", [])),
        choices=tuple(choices) if choices else None,
        is_closing=bool(raw.get("is_closing", False)),
        opener=raw.get("opener", ""),
    )


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_RE.findall(text) if part.strip()]


def _mean_checks(checks: dict[str, bool]) -> float:
    if not checks:
        return 1.0
    return sum(bool(value) for value in checks.values()) / len(checks)


def _result(
    value: Any,
    *,
    checks: dict[str, bool],
    mode: str,
    status: str = "ok",
) -> dict[str, Any]:
    return {
        "status": status,
        "mode": mode,
        "result": value,
        "automaticChecks": checks,
        "automaticScore": _mean_checks(checks) if status == "ok" else 0.0,
    }


def _error(exc: Exception, *, mode: str) -> dict[str, Any]:
    return {
        "status": "error",
        "mode": mode,
        "errorType": type(exc).__name__,
        "error": str(exc),
        "automaticChecks": {"taskCompleted": False},
        "automaticScore": 0.0,
    }


def _trace_metadata(item: Any, task: str) -> dict[str, Any]:
    feature, operation = FEATURES[task]
    return build_langfuse_metadata(
        feature=feature,
        operation=operation,
        tags=("experiment", "calibration", "quality-dataset"),
        caseId=item.id,
        datasetName=item.dataset_name,
        datasetVersion=str(item.metadata.get("datasetVersion", "1.12.0")),
        split=str(item.metadata.get("split", "calibration")),
    )


def _text_checks(text: str, *, min_sentences: int = 1, max_sentences: int | None = None) -> dict[str, bool]:
    sentence_count = len(_sentences(text))
    checks = {
        "nonempty": bool(text.strip()),
        "noEmoji": EMOJI_RE.search(text) is None,
        "sentenceMinimum": sentence_count >= min_sentences,
    }
    if max_sentences is not None:
        checks["sentenceMaximum"] = sentence_count <= max_sentences
    return checks


def _fixture_match(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare only deterministic scalar/list fields exposed by the adapter."""

    for key, expected_value in expected.items():
        if key in {"note", "qualitySuccess"}:
            continue
        if key not in actual:
            return False
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict) or not _fixture_match(actual_value, expected_value):
                return False
        elif actual_value != expected_value:
            return False
    return True


async def _conversation(item: Any) -> dict[str, Any]:
    inp = item.input
    expected = item.expected_output
    topic = _topic(inp["topic"])

    if fault := inp.get("faultInjection"):
        if fault.get("kind") != "timeout":
            return _error(ValueError(f"unsupported conversation fault: {fault}"), mode="fixture")
        if inp["turnIndex"] == 0:
            segments = _first_turn_fallback(topic, inp["nickname"])
        else:
            segments = [Segment(type="question", text=topic.seed)]
        actual = {
            "source": "seed",
            "question": topic.seed,
            "segmentTypes": [segment.type for segment in segments],
            "qualitySuccess": False,
        }
        contract = expected["faultContract"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    if candidate := inp.get("candidateOutput"):
        accepted = True
        try:
            segments = _parse_first_turn(candidate, inp["nickname"])
            source = "llm"
        except ValueError:
            accepted = False
            segments = _first_turn_fallback(topic, inp["nickname"])
            source = "seed"
        actual = {
            "parseAccepted": accepted,
            "source": source,
            "qualitySuccess": accepted,
            "segmentTypes": [segment.type for segment in segments],
        }
        contract = expected["codeBoundary"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    if probe := inp.get("serviceProbe"):
        kind = probe["kind"]
        if kind == "answer_200":
            request = AnswerRequest(answer=probe["answer"])
            actual = {
                "accepted": answer_problem(request.answer) is None,
                "rejectedBeforeTagging": answer_problem(request.answer) is not None,
                "trimmedLength": len(request.answer[:MAX_ANSWER_LEN]),
            }
        elif kind == "skip_3":
            allowed = probe["answered"] >= MIN_ANSWERS_TO_FINISH
            actual = {
                "canFinish": allowed,
                "canSkip": allowed,
                "minimumAnswered": MIN_ANSWERS_TO_FINISH,
            }
        elif kind == "off_topic":
            actual = {
                "answerStored": True,
                "maxReasks": 1,
                "retry": not bool(probe.get("alreadyReasked")),
                "turnConsumed": True,
            }
        else:
            return _error(ValueError(f"unsupported service probe: {probe}"), mode="fixture")
        contract = expected["codeBoundary"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    utterance = await ConversationAgent().generate(
        history=inp["history"],
        topic=topic,
        turn_index=inp["turnIndex"],
        total_turns=inp["totalTurns"],
        nickname=inp["nickname"],
        trace_metadata=_trace_metadata(item, "persona_conversation"),
    )
    text = utterance.text.strip()
    checks = _text_checks(text, max_sentences=None if inp["turnIndex"] == 0 else 3)
    checks["questionMaximum"] = text.count("?") + text.count("？") <= 1
    checks["modelOutput"] = utterance.source == "llm"
    return _result(
        {
            "text": text,
            "source": utterance.source,
            "segments": [segment.model_dump(mode="json") for segment in utterance.segments],
        },
        checks=checks,
        mode="model",
    )


async def _tagging(item: Any) -> dict[str, Any]:
    inp = item.input
    tags = await TaggingAgent().tag(
        inp["question"],
        inp["answer"],
        trace_metadata=_trace_metadata(item, "persona_tagging"),
    )
    output = tags.model_dump(mode="json") if tags is not None else None
    metrics = item.expected_output["autoMetrics"]
    checks = {"modelOutput": output is not None}
    if output is not None and metrics.get("applies"):
        checks.update(
            {
                "primary": output["primary"] in metrics["primary"]["accepted"],
                "secondary": output["secondary"] in metrics["secondary"]["accepted"],
                "offTopic": output["off_topic"] in metrics["off_topic"]["accepted"],
            }
        )
    return _result(output, checks=checks, mode="model")


def _post_process_build(raw: Any, inp: dict[str, Any]) -> dict[str, Any]:
    answered = set(inp["answeredDimensions"])
    coverage = inp["coverage"]["primary"]
    scores: dict[str, int | None] = {}
    confidence: dict[str, str] = {}
    for key in SCORED:
        value = getattr(raw, key, None)
        if value is not None and key not in answered:
            value = None
        scores[key] = value
        evidence = max(int(coverage.get(key, 0)), 1 if key in answered else 0)
        confidence[key] = confidence_of(value is not None, evidence)

    textual: dict[str, list[str]] = {}
    for key in TEXTUAL:
        value = list(getattr(raw, key, []))
        textual[key] = value if key in answered else []

    return {
        "scores": scores,
        "confidence": confidence,
        "accuracy": accuracy_of(confidence),
        "textual": textual,
    }


async def _build(item: Any) -> dict[str, Any]:
    inp = item.input
    expected = item.expected_output
    if inp.get("faultInjection", {}).get("kind") == "timeout":
        turn = SimpleNamespace(topic_id="orientation", answer=inp.get("orientationAnswer"))
        raw = fallback_extraction(SimpleNamespace(turns=[turn]))
        post = _post_process_build(raw, inp)
        actual = {
            "source": "fallback",
            "rawScores": {key: value for key, value in post["scores"].items() if value is not None},
            "narrative": None,
            "isConfirmed": False,
            "subsequentBuildRetriesLLM": True,
            "qualitySuccess": False,
        }
        contract = expected["faultContract"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    raw = await ExtractionAgent().extract(
        inp["history"],
        answered=set(inp["answeredDimensions"]),
        trace_metadata=_trace_metadata(item, "persona_build"),
    )
    post = _post_process_build(raw, inp)
    raw_dump = raw.model_dump(mode="json")
    raw_fields = set(raw.model_fields_set)
    metrics = expected["autoMetrics"]
    checks: dict[str, bool] = {}
    for key, bounds in metrics.get("scoreInRange", {}).items():
        value = raw_dump.get(key)
        checks[f"range:{key}"] = value is not None and bounds[0] <= value <= bounds[1]
    for key in metrics.get("mustOmitFromRawModel", []):
        checks[f"rawOmit:{key}"] = key not in raw_fields or raw_dump.get(key) is None
    for key in metrics.get("unknownMustBeNull", []):
        checks[f"unknownNull:{key}"] = post["scores"].get(key) is None
    for key in metrics.get("textualKept", []):
        checks[f"textKept:{key}"] = bool(post["textual"].get(key))
    for key in metrics.get("textualDropped", []):
        checks[f"textDropped:{key}"] = not post["textual"].get(key)
    if not checks:
        checks["modelOutput"] = True
    return _result(
        {
            "raw": raw_dump,
            "rawFields": sorted(raw_fields),
            "postService": post,
            "source": "llm",
        },
        checks=checks,
        mode="model",
    )


async def _practice(item: Any) -> dict[str, Any]:
    inp = item.input
    expected = item.expected_output
    partner = PersonaResponse.model_validate(inp["partner"])
    me = PersonaResponse.model_validate(inp["me"]) if inp.get("me") else None
    system = PartnerAgent.system_prompt(
        partner_name=inp["partnerName"],
        partner=partner,
        my_name=inp["myName"],
        me=me,
    )

    if probe := inp.get("requestProbe"):
        accepted = True
        try:
            request = PracticeMessageRequest(message=probe["message"])
            length = len(request.message)
        except ValidationError:
            accepted = False
            length = min(len(probe["message"].strip()), 500)
        actual = {"schemaAccepted": accepted, "trimmedLength": length, "httpStatusOnReject": 422}
        contract = expected["codeBoundary"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    if inp.get("faultInjection", {}).get("kind") == "before_first_chunk":
        actual = {
            "assistantSaved": True,
            "content": FALLBACK_REPLY,
            "events": ["start", "delta", "done"],
            "source": "fallback",
            "qualitySuccess": False,
        }
        contract = expected["faultContract"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    if item.metadata.get("evaluationKind") == "code_behavior":
        boundary = expected["codeBoundary"]
        visible = "연락 빈도" in system
        actual = {
            "dimension": boundary["dimension"],
            "profileHighFrom": 65,
            "profileLowTo": 35,
            "profileTraitVisible": visible,
            "score": partner.scores[boundary["dimension"]],
        }
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, boundary)}, mode="fixture")

    chunks = []
    async for chunk in PartnerAgent().reply(
        system=system,
        history=inp["history"],
        opening=bool(inp["opening"]),
        trace_metadata=_trace_metadata(item, "practice_reply"),
    ):
        chunks.append(chunk)
    text = "".join(chunks).strip()
    format_checks = expected["autoMetrics"]["formatChecks"]
    sentence_rule = format_checks["sentenceCount"]
    checks = _text_checks(
        text,
        min_sentences=sentence_rule.get("min", 1),
        max_sentences=sentence_rule.get("max"),
    )
    checks["questionMaximum"] = text.count("?") + text.count("？") <= format_checks["questionCount"]["max"]
    checks["noMarkdown"] = not any(marker in text for marker in ("```", "|---", "\n- ", "\n* "))
    identity_rule = format_checks.get("selfAIKeywords")
    if identity_rule:
        checks["noUnaskedAIIdentity"] = not any(word in text for word in identity_rule["keywords"])
    return _result({"text": text, "source": "llm"}, checks=checks, mode="model")


async def _simulation(item: Any) -> dict[str, Any]:
    inp = item.input
    expected = item.expected_output

    if probe := inp.get("ruleProbe"):
        if probe["function"] != "grade_of":
            return _error(ValueError(f"unsupported rule probe: {probe}"), mode="fixture")
        actual = {"grade": grade_of(probe["score"]).value}
        contract = expected["codeBoundary"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    if candidate := inp.get("candidateOutput"):
        script = _validate_script(candidate, inp["name_a"], inp["name_b"])
        speakers = [line.speaker for line in script.transcript]
        expected_count = 2 * inp["turns"]
        actual = {
            "normalizedSpeakers": speakers,
            "rawQualityPass": len(script.transcript) == expected_count,
            "shortScriptAcceptedByService": True,
            "serviceAccepted": True,
            "storedLineCount": len(script.transcript),
            "minimumPostNormalizedLines": 2,
        }
        contract = expected["codeBoundary"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    pa = PersonaResponse.model_validate(inp["persona_a"])
    pb = PersonaResponse.model_validate(inp["persona_b"])
    dims, areas, _ = score_layer(pa, pb)
    script = await SimulationAgent().run(
        persona_a=pa,
        persona_b=pb,
        name_a=inp["name_a"],
        name_b=inp["name_b"],
        turns=inp["turns"],
        area_scores=areas,
        dim_scores={dimension.dimension: dimension.score for dimension in dims},
        trace_metadata=_trace_metadata(item, "simulation_run"),
    )
    speakers = [line.speaker for line in script.transcript]
    expected_speakers = ["a" if index % 2 == 0 else "b" for index in range(2 * inp["turns"])]
    checks = {
        "lineCount": len(script.transcript) == 2 * inp["turns"],
        "speakerOrder": speakers == expected_speakers,
        "noSelfAddress": not _self_addressed_lines(script, inp["name_a"], inp["name_b"]),
        "reportPresent": bool(script.report.headline and script.report.summary),
    }
    return _result(script.model_dump(mode="json"), checks=checks, mode="model")


async def _preview(item: Any) -> dict[str, Any]:
    inp = item.input
    expected = item.expected_output
    pa = PersonaResponse.model_validate(inp["persona_a"])
    pb = PersonaResponse.model_validate(inp["persona_b"])
    transcript = Transcript.model_validate(inp["transcript"])
    report_input = ReportInput(
        persona_a=pa,
        persona_b=pb,
        transcript=transcript,
        nickname_a=inp["nickname_a"],
        nickname_b=inp["nickname_b"],
    )

    if not inp["useLlm"]:
        report = assemble_report(report_input, template_narrative(pa, pb), "template")
        oracle = expected["ruleOracle"]
        actual = {
            "narrativeSource": report.narrative_source,
            "qualitySuccess": False,
            "ruleScoresPreserved": (
                report.overall.score == oracle["overallScore"] and report.overall.grade.value == oracle["overallGrade"]
            ),
        }
        contract = expected["faultContract"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    if candidate := inp.get("candidateNarrative"):
        narrative = ReportNarrative.model_validate(candidate)
        report = assemble_report(report_input, narrative, "llm")
        actual = {
            "schemaAccepted": True,
            "assembleReportPreservesHighlight": bool(report.highlights)
            and report.highlights[0].quote == candidate["highlights"][0]["quote"],
            "qualitySuccess": False,
        }
        contract = expected["codeBoundary"]
        return _result(actual, checks={"fixtureContract": _fixture_match(actual, contract)}, mode="fixture")

    report = await build_report(
        report_input,
        ReportAgent(),
        trace_metadata=_trace_metadata(item, "simulation_report_preview"),
    )
    raw_turns = {turn.index: turn.text for turn in transcript.turns}
    quote_checks = [raw_turns.get(highlight.turn_index) == highlight.quote for highlight in report.highlights]
    checks = {
        "reportPresent": bool(report.overall.headline and report.overall.summary),
        "highlightCount": len(report.highlights) <= expected["highlightContract"]["maxItems"],
        "highlightQuotes": all(quote_checks),
    }
    return _result(report.model_dump(mode="json"), checks=checks, mode="model")


async def run_item(*, item: Any, **_: Any) -> dict[str, Any]:
    task = item.input.get("task")
    try:
        if task == "persona_conversation":
            return await _conversation(item)
        if task == "persona_tagging":
            return await _tagging(item)
        if task == "persona_build":
            return await _build(item)
        if task == "practice_reply":
            return await _practice(item)
        if task == "simulation_run":
            return await _simulation(item)
        if task == "simulation_report_preview":
            return await _preview(item)
        return _error(ValueError(f"unsupported task: {task}"), mode="adapter")
    except Exception as exc:  # Experiment must keep the remaining calibration cases running.
        return _error(exc, mode="model")


def automatic_contract(*, output: Any, **_: Any) -> Evaluation:
    value = float(output.get("automaticScore", 0.0)) if isinstance(output, dict) else 0.0
    checks = output.get("automaticChecks", {}) if isinstance(output, dict) else {}
    return Evaluation(
        name="automatic_contract",
        value=value,
        comment=json.dumps(checks, ensure_ascii=False, sort_keys=True),
    )


def task_success(*, output: Any, **_: Any) -> Evaluation:
    succeeded = isinstance(output, dict) and output.get("status") == "ok"
    comment = None if succeeded else str(output.get("error", "task returned no output"))
    return Evaluation(name="task_success", value=1.0 if succeeded else 0.0, comment=comment)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("all", *DATASETS), default="all")
    parser.add_argument("--split", default="calibration")
    parser.add_argument("--run-name")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-concurrency", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    langfuse = get_client()
    if not langfuse.auth_check():
        raise RuntimeError("Langfuse authentication failed")

    settings = get_settings()
    selected = DATASETS if args.dataset == "all" else (args.dataset,)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_name = args.run_name or f"baseline-{args.split}-{stamp}"
    git_sha = _git_sha()
    failures = 0

    try:
        for dataset_name in selected:
            dataset = langfuse.get_dataset(dataset_name)
            items = [item for item in dataset.items if item.metadata.get("split") == args.split]
            items.sort(key=lambda item: item.id)
            if args.limit is not None:
                items = items[: args.limit]
            print(f"{dataset_name}: {len(items)} items ({args.split})", flush=True)
            if args.dry_run:
                continue
            if not items:
                failures += 1
                continue

            result = langfuse.run_experiment(
                name=f"{dataset_name} quality evaluation",
                run_name=run_name,
                description=(
                    "KTB-17th quality dataset calibration baseline. "
                    "Deterministic evaluators only; semantic judge is not calibrated yet."
                ),
                data=items,
                task=run_item,
                evaluators=[task_success, automatic_contract],
                max_concurrency=args.max_concurrency,
                metadata={
                    "candidate": "baseline",
                    "datasetStatus": "draft-source-drift",
                    "datasetVersion": "1.12.0",
                    "evaluatorScope": "deterministic-only",
                    "gitSha": git_sha,
                    "model": settings.llm_model,
                    "split": args.split,
                },
            )
            print(result.format(), flush=True)
            print(f"URL: {result.dataset_run_url}", flush=True)
    finally:
        langfuse.flush()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
