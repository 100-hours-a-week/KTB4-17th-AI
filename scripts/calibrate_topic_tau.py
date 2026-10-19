"""공통 소재 임계값(simulation_v2_tau_shared) 보정 — 기획안 M0 ③.

라벨된 fact 쌍(같은 소재 / 다른 소재)을 실제 임베더(KURE-v1 ONNX)로 임베딩해 코사인 유사도 분포를 보고,
"공통 소재" 판정 정밀도가 0.9 이상인 가장 낮은 τ 와 F1 최대 τ 를 찾는다.

라벨셋은 v2 메모리 fact 문체("○○님은 …한다")로 만든 합성 데이터다 (운영 fact 표본이 생기면 교체할 것).
  - related   : 같은 소재를 다른 사람·다른 표현으로 말한 쌍 (예: 주말마다 산에 간다 ↔ 트레킹 동호회 활동)
  - unrelated : 서로 다른 소재 쌍. 같은 카테고리지만 다른 소재(요리 ↔ 독서)를 일부러 많이 넣어 어렵게 했다

사용법: uv run python scripts/calibrate_topic_tau.py [--out result.json]
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
from pathlib import Path

import numpy as np

# 소재별로 두 사람이 말할 법한 fact 문장. 같은 소재 안의 A×B 는 related, 다른 소재끼리는 unrelated
TOPICS: dict[str, tuple[list[str], list[str]]] = {
    "등산": (
        ["민수님은 주말마다 근교 산으로 등산을 간다.", "민수님은 지난주에 북한산을 다녀왔다."],
        ["지은님은 트레킹 동호회에서 한 달에 두 번 산에 간다.", "지은님은 가벼운 둘레길 걷기를 즐긴다."],
    ),
    "요리": (
        ["민수님은 퇴근 후 집에서 요리하는 것을 좋아한다.", "민수님은 요즘 파스타 만들기에 빠져 있다."],
        ["지은님은 주말에 집밥을 직접 해 먹는다.", "지은님은 새로운 레시피로 요리해 보는 걸 즐긴다."],
    ),
    "베이킹": (
        ["민수님은 가끔 쿠키를 굽는다.", "민수님은 빵 만드는 법을 배우고 있다."],
        ["지은님은 주말에 집에서 베이킹을 자주 한다.", "지은님은 지난주에 스콘을 구웠다."],
    ),
    "캠핑": (
        ["민수님은 주말에 가끔 캠핑을 간다.", "민수님은 캠핑 가서 불멍하는 걸 좋아한다."],
        ["지은님은 차박 캠핑을 즐긴다.", "지은님은 글램핑장에서 쉬는 걸 좋아한다."],
    ),
    "요가": (
        ["민수님은 아침마다 스트레칭을 한다.", "민수님은 필라테스를 배우고 있다."],
        ["지은님은 아침마다 요가를 한다.", "지은님은 요가로 하루를 차분하게 시작한다."],
    ),
    "독서": (
        ["민수님은 출퇴근길에 소설을 읽는다.", "민수님은 한 달에 책 두 권을 읽는다."],
        ["지은님은 독서 모임에 나간다.", "지은님은 에세이를 즐겨 읽는다."],
    ),
    "전시": (
        ["민수님은 미술관에 가는 걸 좋아한다.", "민수님은 사진전을 자주 본다."],
        ["지은님은 주말에 전시회를 보러 간다.", "지은님은 현대미술 전시를 좋아한다."],
    ),
    "운동": (
        ["민수님은 퇴근 후 헬스장에 간다.", "민수님은 일주일에 세 번 러닝을 한다."],
        ["지은님은 저녁마다 한강에서 달리기를 한다.", "지은님은 크로스핏을 꾸준히 한다."],
    ),
    "여행": (
        ["민수님은 휴가 때마다 해외여행을 간다.", "민수님은 일본 소도시 여행을 좋아한다."],
        ["지은님은 국내 여행지를 찾아다닌다.", "지은님은 혼자 떠나는 여행을 즐긴다."],
    ),
    "음악": (
        ["민수님은 인디 밴드 공연을 보러 다닌다.", "민수님은 기타를 친다."],
        ["지은님은 페스티벌에 자주 간다.", "지은님은 피아노를 배운다."],
    ),
    "반려동물": (
        ["민수님은 고양이를 키운다.", "민수님은 강아지 산책 봉사를 한다."],
        ["지은님은 강아지를 키운다.", "지은님은 동물 보호소에 기부한다."],
    ),
    "가치관_약속": (
        ["민수님은 약속 시간을 지키는 것을 중요하게 생각한다."],
        ["지은님은 시간 약속에 늦지 않으려고 한다."],
    ),
    "가치관_솔직": (
        ["민수님은 돌려 말하지 않는 대화를 좋아한다."],
        ["지은님은 사람을 대할 때 솔직한 것이 가장 중요하다고 생각한다."],
    ),
}


def build_pairs() -> list[tuple[str, str, int]]:
    pairs = []
    for _, (a_list, b_list) in TOPICS.items():
        for a, b in itertools.product(a_list, b_list):
            pairs.append((a, b, 1))
    names = list(TOPICS)
    for t1, t2 in itertools.permutations(names, 2):
        a = TOPICS[t1][0][0]
        b = TOPICS[t2][1][0]
        pairs.append((a, b, 0))
    return pairs


def metrics_at(sims: np.ndarray, labels: np.ndarray, tau: float) -> dict[str, float]:
    pred = sims >= tau
    tp = int(np.sum(pred & (labels == 1)))
    fp = int(np.sum(pred & (labels == 0)))
    fn = int(np.sum(~pred & (labels == 1)))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tau": round(tau, 3), "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


async def _embed(texts: list[str]) -> np.ndarray:
    from app.v2.persona.embedder import get_embedder

    vecs = await get_embedder().encode_batch(texts)
    m = np.asarray(vecs, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=None)
    args = p.parse_args()
    pairs = build_pairs()
    texts = sorted({t for a, b, _ in pairs for t in (a, b)})
    emb = asyncio.run(_embed(texts))
    idx = {t: i for i, t in enumerate(texts)}
    sims = np.asarray([float(emb[idx[a]] @ emb[idx[b]]) for a, b, _ in pairs])
    labels = np.asarray([lab for _, _, lab in pairs])

    def dist(x: np.ndarray) -> dict[str, float]:
        return {
            k: round(float(v), 3)
            for k, v in zip(("min", "p10", "p50", "p90", "max"), np.percentile(x, [0, 10, 50, 90, 100]), strict=True)
        }

    grid = [metrics_at(sims, labels, t) for t in np.arange(0.30, 0.91, 0.02)]
    best_f1 = max(grid, key=lambda m: (m["f1"], m["precision"]))
    precise = [m for m in grid if m["precision"] >= 0.9]
    lowest_precise = min(precise, key=lambda m: m["tau"]) if precise else None
    result = {
        "pairs": {"related": int(labels.sum()), "unrelated": int((labels == 0).sum())},
        "related_sim": dist(sims[labels == 1]),
        "unrelated_sim": dist(sims[labels == 0]),
        "current_tau_0.60": metrics_at(sims, labels, 0.60),
        "best_f1": best_f1,
        "lowest_tau_precision_ge_0.9": lowest_precise,
        "grid": grid,
        "hardest_unrelated": sorted(
            [(round(float(s), 3), a, b) for (a, b, lab), s in zip(pairs, sims, strict=True) if lab == 0], reverse=True
        )[:5],
        "hardest_related": sorted(
            [(round(float(s), 3), a, b) for (a, b, lab), s in zip(pairs, sims, strict=True) if lab == 1]
        )[:5],
    }
    print(json.dumps({k: v for k, v in result.items() if k != "grid"}, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
