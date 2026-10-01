#!/usr/bin/env python3
"""생성기를 import하지 않고 저장된 JSONL의 분할·자극·도메인 계약을 검사한다."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPLITS = ("calibration", "regression", "blind_holdout")
EXPECTED = {
    "persona_onboarding_conversation": (12, 36, 12),
    "persona_onboarding_tagging": (16, 48, 16),
    "persona_build": (12, 36, 12),
    "practice_reply": (14, 42, 14),
    "simulation_run": (7, 22, 7),
    "simulation_report_preview": (5, 14, 5),
}
DIMENSIONS = {
    "avoidance",
    "anxiety",
    "disclosure",
    "openness",
    "positivity",
    "assurances",
    "contact_rhythm",
    "problem_solving",
    "withdrawal",
    "engagement",
    "compliance",
    "ideal_warmth",
    "ideal_vitality",
    "ideal_status",
    "seriousness",
}
TEXT_KEYS = {"content", "text", "quote", "question", "answer", "headline", "summary", "body", "message"}
FIXED_CONTRACT_TEXT = {"안녕하세요, 저는 하루예요"}
REWRITTEN_HOLDOUT = {
    "persona_onboarding_tagging": [74],
    "persona_build": [7, 8, 17, 18, 27, 28, 42, 44, 45],
    "practice_reply": [*range(20, 25), *range(56, 61)],
    "simulation_run": [32],
    "simulation_report_preview": [14, 15, 16, 21],
}


def normalize(text):
    return re.sub(r"[^\w가-힣]", "", unicodedata.normalize("NFKC", text).casefold())


def approximation(left, right):
    """길이 20자 이상, 최소 8종 문자, 공통 3-gram과 정렬 유사도를 모두 요구한다.

    짧은 관용 문장/예·아니요 및 반복 문자 길이 경계 fixture를 제외한다.
    주제/점수/이름 같은 구조 필드는 stimuli 단계에서 이미 제외한다.
    """
    if min(len(left), len(right)) < 20 or min(len(set(left)), len(set(right))) < 8:
        return None
    if min(len(left), len(right)) / max(len(left), len(right)) < 0.67:
        return None
    a = {left[i : i + 3] for i in range(len(left) - 2)}
    b = {right[i : i + 3] for i in range(len(right) - 2)}
    overlap = 2 * len(a & b) / (len(a) + len(b))
    if overlap < 0.45:
        return None
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    ratio = matcher.ratio()
    if ratio < 0.76 or sum(block.size for block in matcher.get_matching_blocks()) < 18:
        return None
    return {"sequenceRatio": round(ratio, 4), "characterTrigramDice": round(overlap, 4)}


def approximate_leaks(sentence_index):
    holdout, training = {}, {}
    for sentence, occurrences in sentence_index.items():
        if sentence in FIXED_CONTRACT_TEXT:
            continue
        normalized = normalize(sentence)
        if len(normalized) < 20:
            continue
        for split, target in [("blind_holdout", holdout), (None, training)]:
            selected = [o for o in occurrences if (o["split"] == split if split else o["split"] != "blind_holdout")]
            if selected:
                target.setdefault(normalized, {"text": sentence, "occurrences": []})["occurrences"].extend(selected)
    collisions = []
    for left, a in holdout.items():
        for right, b in training.items():
            score = approximation(left, right)
            if score:
                collisions.append(
                    {
                        "holdoutText": a["text"],
                        "otherText": b["text"],
                        **score,
                        "holdoutCases": sorted({x["caseId"] for x in a["occurrences"]}),
                        "otherCases": sorted({x["caseId"] for x in b["occurrences"]}),
                    }
                )
    return collisions


def stimuli(value, path=()):
    """기대값·코드 topic 정의를 제외하고 입력의 실제 발화와 후보 출력만 읽는다.

    한 글자 답도 포함하며 문장부호 차이와 공백 차이를 정규화한다.
    점수/이름/enum/프로필 단어 목록은 발화 자극이 아니므로 비교하지 않는다.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            if path == () and key == "topic":
                continue  # 코드의 주제/seed 계약은 합성 자극 원형이 아니다.
            yield from stimuli(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from stimuli(child, (*path, str(index)))
    elif isinstance(value, str) and path and path[-1] in TEXT_KEYS:
        for sentence in [value, *re.split(r"(?<=[.!?。！？])\s+|\n+", value)]:
            normalized = re.sub(r"\s+", " ", sentence.strip()).rstrip(".!?。！？")
            if normalized:
                yield normalized, ".".join(path)


def audit() -> dict:
    errors = []
    rows = {name: [json.loads(line) for line in (ROOT / f"{name}.jsonl").read_text().splitlines()] for name in EXPECTED}
    counts = {}
    sentence_index = defaultdict(list)
    families = defaultdict(set)
    mechanisms = defaultdict(set)
    for name, items in rows.items():
        counts[name] = {s: sum(r["metadata"]["split"] == s for r in items) for s in SPLITS}
        if tuple(counts[name].values()) != EXPECTED[name]:
            errors.append(f"{name}: split 불일치 {counts[name]}")
        for row in items:
            meta = row["metadata"]
            families[meta["familyId"]].add(meta["split"])
            for mechanism in row["input"].get("stimulusFamilies", []):
                mechanisms[mechanism].add(meta["split"])
            for sentence, path in set(stimuli(row["input"])):
                sentence_index[sentence].append({"caseId": meta["caseId"], "split": meta["split"], "path": path})
    leaked = {
        sentence: occurrences
        for sentence, occurrences in sentence_index.items()
        if sentence not in FIXED_CONTRACT_TEXT
        and any(x["split"] == "blind_holdout" for x in occurrences)
        and any(x["split"] != "blind_holdout" for x in occurrences)
    }
    if leaked:
        errors.append(f"holdout 자극 문장 타 split 재사용 {len(leaked)}개")
    near = approximate_leaks(sentence_index)
    if near:
        errors.append(f"holdout 근사 자극 교차 split 유사 문장 {len(near)}쌍")
    family_leaks = [family for family, splits in families.items() if len(splits) > 1]
    mechanism_leaks = [family for family, splits in mechanisms.items() if len(splits) > 1]
    if family_leaks or mechanism_leaks:
        errors.append(f"family 누수: {family_leaks}, 자극 원형 누수: {mechanism_leaks}")
    off_topic = Counter()
    secondary = Counter()
    short_answers = Counter()
    jokes = Counter()
    abusive = Counter()
    for row in rows["persona_onboarding_tagging"]:
        labels = row["expectedOutput"]["referenceLabels"]
        split = row["metadata"]["split"]
        if 1 <= len(row["input"]["answer"]) <= 3:
            short_answers[split] += 1
        if row["metadata"]["category"] == "joke_nonanswer":
            jokes[split] += 1
        if row["metadata"]["category"] == "abusive_off_topic":
            abusive[split] += 1
        if labels["off_topic"]:
            off_topic[split] += 1
            if labels["primary"] or labels["secondary"]:
                errors.append(f"off_topic 라벨 비어 있지 않음: {row['metadata']['caseId']}")
        if labels["secondary"]:
            secondary[split] += 1
        if set(labels["primary"]) & set(labels["secondary"]):
            errors.append(f"primary/secondary 중복: {row['metadata']['caseId']}")
    if sum(off_topic.values()) < 8 or any(off_topic[s] == 0 for s in SPLITS):
        errors.append("off_topic 최소 8건·모든 split 조건 위반")
    if any(not counts[split] for counts in (short_answers, jokes, abusive) for split in SPLITS):
        errors.append("태깅 split별 1~3자 답·농담·욕설 경계 누락")
    for row in rows["persona_onboarding_conversation"]:
        inp = row["input"]
        if len(inp["history"]) != 2 * inp["turnIndex"]:
            errors.append(f"history 길이 불일치: {row['metadata']['caseId']}")
        if [m["role"] for m in inp["history"]] != ["assistant", "user"] * inp["turnIndex"]:
            errors.append(f"history 역할 불일치: {row['metadata']['caseId']}")
        if len(set(inp["historyTopicIds"])) != inp["turnIndex"] or inp["topic"]["id"] in inp["historyTopicIds"]:
            errors.append(f"history 주제 반복: {row['metadata']['caseId']}")
        if inp["turnIndex"] == 0 and inp["topic"]["id"] != "weekend":
            errors.append(f"첫 주제 weekend 아님: {row['metadata']['caseId']}")
        if inp["history"] and (
            not inp["history"][0]["content"].startswith("안녕하세요, 저는 하루예요.")
            or inp["historyTopicIds"][0] != "weekend"
        ):
            errors.append(f"history 실제 intro/weekend 누락: {row['metadata']['caseId']}")
    persona_count = 0
    practice_count = 0
    for row in [*rows["simulation_report_preview"], *rows["practice_reply"]]:
        keys = ("partner",) if row["metadata"]["dataset"] == "practice_reply" else ("persona_a", "persona_b")
        for key in keys:
            p = row["input"][key]
            if set(p["scores"]) != DIMENSIONS or set(p["confidence"]) != DIMENSIONS:
                errors.append(f"15차원 누락: {row['metadata']['caseId']}/{key}")
            accuracy = round(
                100
                * sum({"LOW": 0.0, "MEDIUM": 0.6, "HIGH": 1.0}[p["confidence"].get(d, "LOW")] for d in DIMENSIONS)
                / 15
            )
            if p["accuracy"] != accuracy:
                errors.append(f"accuracy 불일치: {row['metadata']['caseId']}/{key}")
            if key == "partner":
                practice_count += 1
            else:
                persona_count += 1
    affected = {case for collision in near for case in collision["holdoutCases"]}
    affected.update(
        o["caseId"] for occurrences in leaked.values() for o in occurrences if o["split"] == "blind_holdout"
    )
    target_ids = [f"{name}-{n:03d}" for name, indices in REWRITTEN_HOLDOUT.items() for n in indices]
    available_holdout_ids = {
        r["metadata"]["caseId"] for items in rows.values() for r in items if r["metadata"]["split"] == "blind_holdout"
    }
    if missing := set(target_ids) - available_holdout_ids:
        errors.append(f"재작성 대상 holdout 누락 또는 분할 변경: {sorted(missing)}")
    return {
        "ok": not errors,
        "datasetSplits": counts,
        "total": sum(map(len, rows.values())),
        "holdoutStimulusCrossSplitExactSentenceCount": len(leaked),
        "sentenceCollisions": leaked,
        "holdoutStimulusCrossSplitApproximatePairCount": len(near),
        "approximateCollisions": near,
        "rewrittenHoldoutVerification": {
            "caseCount": len(target_ids),
            "caseIds": target_ids,
            "collidingCaseIds": sorted(affected & set(target_ids)),
            "collisionCount": len(affected & set(target_ids)),
        },
        "approximationPolicy": {
            "normalization": "NFKC/casefold/문장부호·공백 제거",
            "minimumCharacters": 20,
            "minimumDistinctCharacters": 8,
            "sequenceRatioAtLeast": 0.76,
            "characterTrigramDiceAtLeast": 0.45,
            "minimumMatchedCharacters": 18,
            "fixedContractExemptions": sorted(FIXED_CONTRACT_TEXT),
            "shortSentences": "근사 검사 제외, 정확 일치 검사는 유지",
            "semanticGuarantee": False,
        },
        "familyLeakCount": len(family_leaks),
        "declaredStimulusFamilyLeakCount": len(mechanism_leaks),
        "offTopicPositiveCounts": dict(off_topic),
        "secondaryNonemptyCounts": dict(secondary),
        "shortAnswerOneToThreeCharacterCounts": dict(short_answers),
        "jokeCounts": dict(jokes),
        "abusiveOffTopicCounts": dict(abusive),
        "previewPersonaConsistencyChecked": persona_count,
        "practicePersonaConsistencyChecked": practice_count,
        "uniqueRubrics": {
            name: len(
                {json.dumps(row["expectedOutput"]["rubric"], ensure_ascii=False, sort_keys=True) for row in items}
            )
            for name, items in rows.items()
        },
        "errors": errors,
        "scope": "전체 발화와 문장 단위의 정확 일치 및 정규화 후 SequenceMatcher/문자 3-gram 교차 검사를 수행한다. 고정 intro만 정확 검사에서 제외하며 짧은 관용문은 근사 검사에서 제외한다. 의미 동등성 전부의 자동 증명을 주장하지 않는다.",
    }


if __name__ == "__main__":
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
