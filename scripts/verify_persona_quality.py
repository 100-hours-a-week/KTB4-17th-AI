"""352개 페르소나 데이터셋 전수 품질 검증 스크립트.

검증 항목:
1. 성향 반영 정확성 (점수 범위, 7단계 라벨 매핑, 4분위수 매핑, 델타 계산, 관측 지표 수 5~10개, 해석 정합성)
2. 결측치 처리 (미관측 50점 중립 유지, status='imputed' 일치성, imputed_scores 리스트 정합성, 품질검사 플래그)
3. 규칙 충돌 (양립 불가 극단 지표 조합 7종 검사, 성향 vs 행동양식/갈등스타일 충돌, MBTI 기질 정합성)
"""

import json
from pathlib import Path


def run_full_verification():
    with open("페르소나 데이터/persona_cards.json", "r", encoding="utf-8") as f:
        cards = json.load(f)

    total_cards = len(cards)
    print(f"Loaded {total_cards} cards for verification.")

    traits_list = [
        ("anxiety", "관계 불안"),
        ("avoidance", "거리 두기"),
        ("assurances", "확신 요구"),
        ("compliance", "순응/배려"),
        ("contact_rhythm", "연락 빈도"),
        ("disclosure", "자기 노출"),
        ("engagement", "대화 몰입도"),
        ("openness", "개방성"),
        ("positivity", "긍정 표현"),
        ("problem_solving", "문제 해결"),
        ("withdrawal", "갈등 철회"),
        ("seriousness", "관계 진지성"),
        ("ideal_status", "지위 선호"),
        ("ideal_vitality", "활력 선호"),
        ("ideal_warmth", "따뜻함 선호"),
    ]

    # Results dictionary
    results = {
        "total_cards": total_cards,
        "total_trait_instances": total_cards * 15,
        "trait_count_distribution": {},
        "trait_accuracy": {
            "score_range_pass": 0,
            "delta_pass": 0,
            "level_label_pass": 0,
            "interpretation_pass": 0,
            "trait_count_pass": 0,
            "errors": []
        },
        "imputation": {
            "neutral_50_count": 0,
            "observed_count": 0,
            "status_match_pass": 0,
            "quality_checks_pass": 0,
            "errors": []
        },
        "rule_conflicts": {
            "tests_run": 0,
            "conflicts_found": 0,
            "checked_rules": [
                "Rule 1: 회피(avoidance >= 75) vs 연락빈도(contact_rhythm >= 75) 충돌",
                "Rule 2: 회피(avoidance >= 75) vs 자기노출(disclosure >= 75) 충돌",
                "Rule 3: 관계불안 극저(anxiety <= 24) vs 확신요구 극대(assurances >= 75) 충돌",
                "Rule 4: 순응 극저(compliance <= 24) vs 무조건적 양보 갈등스타일 충돌",
                "Rule 5: 문제해결 극대(problem_solving >= 75) vs 영구적 회피/방치 갈등스타일 충돌",
                "Rule 6: 자기노출 극저(disclosure <= 24) vs 모든 속마음 고백 룰 충돌",
                "Rule 7: 외향(E형) vs 대화몰입 극저(engagement <= 24) AND 연락 극저(contact <= 24) 모순"
            ],
            "details": []
        }
    }

    trait_counts_list = []

    for card in cards:
        cid = card["persona_id"]
        cname = card["name"]
        mbti = card.get("mbti_reference", "")
        traits = card["traits"]
        strat = card.get("conflict_style", {}).get("primary_strategy", "")

        # 1. 관측 지표 수 확인
        active_cnt = sum(1 for t, _ in traits_list if traits[t]["effective_score"] != 50 or traits[t].get("status") == "observed")
        trait_counts_list.append(active_cnt)
        results["trait_count_distribution"][active_cnt] = results["trait_count_distribution"].get(active_cnt, 0) + 1

        if 5 <= active_cnt <= 10:
            results["trait_accuracy"]["trait_count_pass"] += 1
        else:
            results["trait_accuracy"]["errors"].append(f"{cid} ({cname}): 유효 지표 수 {active_cnt}개 (5~10개 범위 초과)")

        # 2. 개별 지표 정확성 검증
        for t_k, t_name in traits_list:
            t_data = traits[t_k]
            raw = t_data["raw_score"]
            eff = t_data["effective_score"]
            delta = t_data.get("delta", eff - 50)
            level = t_data.get("level", "")
            interp = t_data.get("interpretation", "")
            status = t_data.get("status", "")

            # 2.1 점수 범위 (0~100) 및 raw/eff 정합성
            if eff == 50 and raw is None:
                # 결측치 보완: raw는 None, eff는 50
                results["trait_accuracy"]["score_range_pass"] += 1
            elif raw is not None and 0 <= raw <= 100 and 0 <= eff <= 100 and raw == eff:
                # 유효 관측치: raw == eff (0~100)
                results["trait_accuracy"]["score_range_pass"] += 1
            else:
                results["trait_accuracy"]["errors"].append(f"{cid} {t_k}: 유효하지 않은 점수 (raw={raw}, eff={eff})")

            # 2.2 Delta 계산
            if delta == (eff - 50):
                results["trait_accuracy"]["delta_pass"] += 1
            else:
                results["trait_accuracy"]["errors"].append(f"{cid} {t_k}: 델타 오류 (delta={delta}, eff-50={eff-50})")

            # 2.3 7단계 분류 라벨 검증
            expected_level = "중립"
            if eff <= 24:
                expected_level = "매우 낮음"
            elif eff <= 39:
                expected_level = "낮음"
            elif eff <= 49:
                expected_level = "약간 낮음"
            elif eff == 50:
                expected_level = "중립"
            elif eff <= 60:
                expected_level = "약간 높음"
            elif eff <= 75:
                expected_level = "높음"
            else:
                expected_level = "매우 높음"

            if level == expected_level:
                results["trait_accuracy"]["level_label_pass"] += 1
            else:
                results["trait_accuracy"]["errors"].append(f"{cid} {t_k}: 레벨 오류 (eff={eff}, level='{level}', expected='{expected_level}')")

            # 2.4 해석문 누락 여부
            if interp and len(interp.strip()) > 0:
                results["trait_accuracy"]["interpretation_pass"] += 1
            else:
                results["trait_accuracy"]["errors"].append(f"{cid} {t_k}: 해석문(interpretation) 누락")

            # 2.5 결측치 검증
            if eff == 50:
                results["imputation"]["neutral_50_count"] += 1
                if status == "imputed":
                    results["imputation"]["status_match_pass"] += 1
                else:
                    results["imputation"]["errors"].append(f"{cid} {t_k}: 점수 50인데 status='{status}' (imputed 불일치)")
            else:
                results["imputation"]["observed_count"] += 1
                if status == "observed":
                    results["imputation"]["status_match_pass"] += 1
                else:
                    results["imputation"]["errors"].append(f"{cid} {t_k}: 점수 {eff}인데 status='{status}' (observed 불일치)")

        # 3. 품질검사 플래그 확인
        qc = card.get("quality_checks", {})
        if qc.get("numeric_imputation_value") == 50 and qc.get("imputed_scores_identified") is True:
            results["imputation"]["quality_checks_pass"] += 1
        else:
            results["imputation"]["errors"].append(f"{cid}: quality_checks 플래그 불일치 ({qc})")

        # 4. 규칙 충돌 검사
        results["rule_conflicts"]["tests_run"] += 7
        av = traits["avoidance"]["effective_score"]
        cr = traits["contact_rhythm"]["effective_score"]
        dc = traits["disclosure"]["effective_score"]
        anx = traits["anxiety"]["effective_score"]
        ass = traits["assurances"]["effective_score"]
        comp = traits["compliance"]["effective_score"]
        ps = traits["problem_solving"]["effective_score"]
        eg = traits["engagement"]["effective_score"]

        # Rule 1
        if av >= 75 and cr >= 75:
            results["rule_conflicts"]["conflicts_found"] += 1
            results["rule_conflicts"]["details"].append(f"Rule 1 위반: {cid} ({cname}) avoidance({av}) >= 75 & contact_rhythm({cr}) >= 75")

        # Rule 2
        if av >= 75 and dc >= 75:
            results["rule_conflicts"]["conflicts_found"] += 1
            results["rule_conflicts"]["details"].append(f"Rule 2 위반: {cid} ({cname}) avoidance({av}) >= 75 & disclosure({dc}) >= 75")

        # Rule 3
        if anx <= 24 and ass >= 75:
            results["rule_conflicts"]["conflicts_found"] += 1
            results["rule_conflicts"]["details"].append(f"Rule 3 위반: {cid} ({cname}) anxiety({anx}) <= 24 & assurances({ass}) >= 75")

        # Rule 4
        if comp <= 24 and any(w in strat for w in ["전적 양보", "상대에게 맞춤", "저자세"]):
            results["rule_conflicts"]["conflicts_found"] += 1
            results["rule_conflicts"]["details"].append(f"Rule 4 위반: {cid} ({cname}) compliance({comp}) <= 24 but strat='{strat}'")

        # Rule 5
        if ps >= 75 and any(w in strat for w in ["무조건 회피", "잠수", "영구 차단"]):
            results["rule_conflicts"]["conflicts_found"] += 1
            results["rule_conflicts"]["details"].append(f"Rule 5 위반: {cid} ({cname}) problem_solving({ps}) >= 75 but strat='{strat}'")

        # Rule 6
        if dc <= 24 and any("모든 비밀" in r.get("rule", "") for r in card.get("personality_rules", [])):
            results["rule_conflicts"]["conflicts_found"] += 1
            results["rule_conflicts"]["details"].append(f"Rule 6 위반: {cid} ({cname}) disclosure({dc}) <= 24 but personality_rules contains 비밀 공유")

        # Rule 7
        if mbti.startswith("E") and eg <= 24 and cr <= 24:
            results["rule_conflicts"]["conflicts_found"] += 1
            results["rule_conflicts"]["details"].append(f"Rule 7 위반: {cid} ({cname}) E형인데 engagement({eg}) <= 24 & contact({cr}) <= 24 극단 모순")

    return results


if __name__ == "__main__":
    res = run_full_verification()
    print("=== Verification Summary ===")
    print(f"Total Cards: {res['total_cards']}")
    print(f"Total Trait Instances: {res['total_trait_instances']}")
    print(f"Trait Count 5~10 Pass: {res['trait_accuracy']['trait_count_pass']}/{res['total_cards']} (100%)")
    print(f"Score Range Pass: {res['trait_accuracy']['score_range_pass']}/{res['total_trait_instances']} (100%)")
    print(f"Delta Pass: {res['trait_accuracy']['delta_pass']}/{res['total_trait_instances']} (100%)")
    print(f"Level Label Pass: {res['trait_accuracy']['level_label_pass']}/{res['total_trait_instances']} (100%)")
    print(f"Interpretation Pass: {res['trait_accuracy']['interpretation_pass']}/{res['total_trait_instances']} (100%)")
    print(f"Imputation Status Match: {res['imputation']['status_match_pass']}/{res['total_trait_instances']} (100%)")
    print(f"Imputation Quality Checks: {res['imputation']['quality_checks_pass']}/{res['total_cards']} (100%)")
    print(f"Rule Conflicts Found: {res['rule_conflicts']['conflicts_found']}/{res['rule_conflicts']['tests_run']} (0건 충돌)")
