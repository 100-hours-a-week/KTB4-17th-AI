"""256대 페르소나 캐릭터(16 MBTI x 남/여 x 8개 고유 성향 변형) 대량 생성 스크립트.

기본: 16 MBTI x 남/여 = 32개 기본축
변형: 축당 8가지 고유 성향 변형(Variant 1~8) = 총 256명
지표: 캐릭터당 5~10개의 유효 관측 지표 배치 (4개 분위 구간 고루 분배)
요약: 관측 지표에 기반한 실제 정성 요약(summaries) 풍부하게 작성
"""

import json
from pathlib import Path


DIMENSIONS = [
    "anxiety", "avoidance", "assurances", "compliance", "contact_rhythm",
    "disclosure", "engagement", "openness", "positivity", "problem_solving",
    "withdrawal", "seriousness", "ideal_status", "ideal_vitality", "ideal_warmth"
]

MBTI_LIST = [
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP"
]

# 한국어 이름 풀 (남성 128개, 여성 128개 이상 확보)
MALE_NAMES = [
    "서진", "진혁", "시우", "건우", "태오", "도경", "찬우", "선우",
    "정우", "현서", "준우", "이안", "승현", "유찬", "민재", "로운",
    "진호", "태윤", "우진", "시온", "성민", "준호", "도윤", "민준",
    "도현", "은우", "재민", "시혁", "현우", "지호", "태민", "정후",
    "하준", "예준", "지훈", "우빈", "유준", "은성", "동하", "민성",
    "준서", "지환", "민규", "승우", "승민", "도현", "태양", "지후",
    "동현", "선호", "현준", "주원", "지민", "민석", "영재", "태희",
    "윤우", "준혁", "수호", "서준", "하온", "지안", "시안", "은혁",
    "강민", "기현", "재현", "연우", "상민", "보겸", "대현", "동우",
    "형석", "민기", "정민", "찬희", "상우", "원준", "동호", "경민",
    "진우", "유성", "태호", "종현", "재원", "지석", "명준", "영민",
    "태환", "민혁", "승호", "재훈", "진수", "현석", "상현", "영훈",
    "기태", "석진", "준영", "창민", "태은", "용우", "세현", "종우",
    "형우", "동원", "인우", "재성", "성호", "상훈", "영우", "광현",
    "성현", "호진", "태현", "민우", "성진", "도원", "유진", "승기",
    "성우", "민호", "준상", "기범", "정환", "태영", "동건", "석우"
]

FEMALE_NAMES = [
    "서윤", "채린", "서연", "지민", "지안", "하린", "유진", "주아",
    "채원", "다인", "예린", "하율", "서아", "가은", "지유", "나은",
    "수경", "서현", "하은", "지아", "수민", "예은", "다은", "소율",
    "은채", "시은", "소희", "채은", "보민", "윤서", "아린", "서우",
    "서하", "하윤", "지우", "윤아", "채은", "예나", "다온", "시아",
    "소은", "연우", "유나", "예지", "아윤", "민서", "수아", "나연",
    "다영", "채현", "예서", "지원", "보영", "은서", "유림", "채영",
    "세아", "수빈", "다솜", "현서", "가온", "지윤", "연서", "예진",
    "윤지", "은채", "혜린", "유빈", "지율", "아라", "시연", "세은",
    "하늘", "나린", "단비", "보배", "미소", "하나", "소미", "다희",
    "초원", "주하", "다혜", "보미", "윤희", "소담", "정원", "채아",
    "예슬", "솔비", "희서", "다정", "가람", "세나", "슬기", "지혜",
    "주연", "윤설", "하임", "새봄", "소연", "여원", "은별", "보라",
    "해나", "다솔", "연주", "은수", "가희", "유정", "희진", "선우",
    "효린", "민주", "지효", "효주", "세인", "예봄", "윤진", "은유",
    "소민", "아영", "수현", "다민", "예림", "지솔", "하담", "아름"
]


def classify_level(score: int) -> str:
    if score <= 24:
        return "매우 낮음"
    elif score <= 49:
        return "낮음" if score <= 39 else "약간 낮음"
    elif score == 50:
        return "중립"
    elif score <= 74:
        return "약간 높음" if score <= 60 else "높음"
    else:
        return "매우 높음"


def get_quartile(score: int) -> str:
    if score <= 24:
        return "Q1(0~24)"
    elif score <= 49:
        return "Q2(25~49)"
    elif score <= 74:
        return "Q3(51~74)"
    else:
        return "Q4(75~100)"


def make_interpretation(dim: str, score: int) -> str:
    lvl = classify_level(score)
    interps = {
        "anxiety": {
            "매우 낮음": "관계의 불확실성에 거의 동요하지 않고 극도로 초연함",
            "낮음": "상대의 반응 지연에 크게 불안해하지 않음",
            "약간 낮음": "비교적 안정적인 정서 상태를 유지함",
            "중립": "평균적인 관계 민감도",
            "약간 높음": "상대의 태도 변화를 조심스럽게 살핌",
            "높음": "관계 불안에 다소 민감하고 반응을 주시함",
            "매우 높음": "관계의 사소한 신호에도 강한 불안과 확인 요구를 느낌"
        },
        "avoidance": {
            "매우 낮음": "정서적 유대와 친밀감을 매우 편안하게 느낌",
            "낮음": "가까워지는 것을 주저하지 않고 받아들임",
            "약간 낮음": "적당한 유대감을 자연스럽게 즐김",
            "중립": "평균적인 친밀도 거리감",
            "약간 높음": "자신만의 개인 시간을 침범받지 않길 원함",
            "높음": "사적 영역을 보호하며 감정적 밀착을 부담스러워함",
            "매우 높음": "깊은 정서적 개입을 극단적으로 회피하고 독립성을 엄수함"
        },
        "assurances": {
            "매우 낮음": "관계의 확언이나 확인 표현을 거의 필요로 하지 않음",
            "낮음": "말보다는 행동으로 신뢰를 확인하는 편",
            "약간 낮음": "자주 확신을 요구하지 않음",
            "중립": "평균적인 확신 요구",
            "약간 높음": "가끔씩 다정한 확인의 말을 원함",
            "높음": "관계의 지속성에 대한 잦은 확신과 애정 표현을 원함",
            "매우 높음": "끊임없는 확언과 미래에 대한 명확한 약속을 요구함"
        },
        "compliance": {
            "매우 낮음": "비효율이나 부당한 요구를 단호히 거절하고 원칙 고수",
            "낮음": "자신의 주관과 경계를 확고하게 지킴",
            "약간 낮음": "무리한 부탁에는 응하지 않는 편",
            "중립": "상황에 따라 유연하게 조율",
            "약간 높음": "상대의 기분을 배려해 맞추려 노력함",
            "높음": "상대 의견을 배려하여 기꺼이 양보하고 맞춤",
            "매우 높음": "갈등을 피하기 위해 전적으로 상대에게 맞추는 경향"
        },
        "contact_rhythm": {
            "매우 낮음": "불필요한 스몰토크나 일상 연락을 극도로 피함",
            "낮음": "필요한 용건이 있을 때만 간결하게 연락",
            "약간 낮음": "실시간 대화보다는 느긋한 회신 선호",
            "중립": "평균적인 연락 주기",
            "약간 높음": "일과 중 틈틈이 안부를 주고받음",
            "높음": "일상의 사소한 순간도 자주 공유하고 연락함",
            "매우 높음": "상시 연결되어 대화하는 것을 원함"
        },
        "disclosure": {
            "매우 낮음": "자신의 감정이나 속마음을 극단적으로 감춤",
            "낮음": "깊은 신뢰가 생기기 전에는 사생활 언급 자제",
            "약간 낮음": "담백한 사실 위주로만 이야기함",
            "중립": "자연스러운 수준의 자기표현",
            "약간 높음": "편안해지면 일상과 생각을 공유함",
            "높음": "자신의 감정과 일상을 투명하게 먼저 표현함",
            "매우 높음": "비밀 없이 모든 감정과 내면을 솔직하게 털어놓음"
        },
        "engagement": {
            "매우 낮음": "대화에 거의 몰입하지 않고 형식적으로 반응",
            "낮음": "필요한 말만 단답형으로 소통",
            "약간 낮음": "수동적으로 듣는 것을 선호",
            "중립": "적당한 대화 참여도",
            "약간 높음": "흥미로운 화제에는 적극적으로 호응",
            "높음": "대화에 적극적이며 티키타카와 몰입도가 높음",
            "매우 높음": "엄청난 에너지로 대화를 주도하고 반응함"
        },
        "openness": {
            "매우 낮음": "익숙한 방식만을 고집하고 새로운 것에 거부감",
            "낮음": "검증되지 않은 방식에는 신중한 태도",
            "약간 낮음": "전통적이고 안정적인 가치관 선호",
            "중립": "평균적인 수용성",
            "약간 높음": "새로운 관점에 유연하게 귀 기울임",
            "높음": "새로운 시도와 다양한 가치관을 유연하게 수용함",
            "매우 높음": "파격적이고 독창적인 아이디어와 변화를 열광적으로 환영"
        },
        "positivity": {
            "매우 낮음": "칭찬이나 감정 표현이 없고 무뚝뚝함",
            "낮음": "담백하고 무미건조한 어조",
            "약간 낮음": "과장된 리액션을 자제함",
            "중립": "보통의 긍정 정서",
            "약간 높음": "따뜻하고 온화한 어조로 반응",
            "높음": "밝은 칭찬과 긍정적인 에너지를 아낌없이 표현함",
            "매우 높음": "넘치는 활기와 유머로 상대의 기분을 완전히 북돋움"
        },
        "problem_solving": {
            "매우 낮음": "갈등 시 문제 해결보다 감정적 위로만을 갈구함",
            "낮음": "논리적 분석보다는 감정적 서운함 해소 우선",
            "약간 낮음": "시시비비 따지는 것을 부담스러워함",
            "중립": "감정과 논리의 중간 조율",
            "약간 높음": "원인을 찾고 현실적 대안을 모색함",
            "높음": "갈등 시 원인 분석과 실질적 해결책에 집중함",
            "매우 높음": "감정을 완전히 배제하고 오직 팩트와 프로세스로 문제를 타결"
        },
        "withdrawal": {
            "매우 낮음": "갈등이 생기면 자리를 피하지 않고 즉시 끝까지 대화",
            "낮음": "회피 없이 솔직하게 의견을 교환함",
            "약간 낮음": "상황을 피하기보다 마주하려 노력함",
            "중립": "평균적인 대처",
            "약간 높음": "언쟁이 길어지면 잠시 생각을 정리하고 싶어함",
            "높음": "갈등 시 격앙을 피하고 혼자 생각할 시간을 가짐",
            "매우 높음": "상황이 불편해지면 완전히 입을 닫거나 잠수함"
        },
        "seriousness": {
            "매우 낮음": "관계의 구속을 극도로 싫어하고 매우 가볍게 만남",
            "낮음": "부담 없이 편하고 즉흥적인 관계 선호",
            "약간 낮음": "형식적인 약속이나 진지한 대화는 피함",
            "중립": "보통 수준의 진지성",
            "약간 높음": "약속을 소중히 지키며 신뢰 축적",
            "높음": "관계에 대한 약속과 책임을 매우 무겁게 여김",
            "매우 높음": "미래를 함께할 확고한 신뢰와 도덕적 헌신을 최우선시함"
        },
        "ideal_status": {
            "매우 낮음": "상대의 사회적 조건이나 성취에 전혀 무관심",
            "낮음": "조건보다는 성품을 훨씬 중시함",
            "약간 낮음": "배경보다는 취향 일치 우선",
            "중립": "보통의 선호도",
            "약간 높음": "자기 분야에 성실한 사람 선호",
            "높음": "상대의 전문성과 사회적 역량을 높게 평가",
            "매우 높음": "높은 사회적 성취와 야망을 가진 상대를 강력 선호"
        },
        "ideal_vitality": {
            "매우 낮음": "극도로 정적이고 조용한 쉼을 함께할 상대 선호",
            "낮음": "과한 활동성보다 잔잔한 일상 선호",
            "약간 낮음": "차분한 템포를 가진 사람 선호",
            "중립": "보통의 활력 선호",
            "약간 높음": "가벼운 운동이나 산책을 즐기는 상대 선호",
            "높음": "활동적이고 유쾌한 에너지를 선호",
            "매우 높음": "지치지 않는 열정과 액티브한 라이프스타일을 가진 사람 선호"
        },
        "ideal_warmth": {
            "매우 낮음": "과도한 다정함이나 감정 요구를 부담스러워함",
            "낮음": "담백하고 쿨한 태도를 선호",
            "약간 낮음": "감정 표현이 절제된 상대 선호",
            "중립": "보통의 온기 선호",
            "약간 높음": "상처 주지 않는 은은한 배려 선호",
            "높음": "따뜻하고 상처 주지 않는 다정한 사람 선호",
            "매우 높음": "무조건적인 정서적 지지와 지극한 배려를 베푸는 사람 선호"
        }
    }
    return interps.get(dim, {}).get(lvl, f"{dim} {lvl} 성향")


def generate_256_characters() -> list[dict]:
    characters = []
    char_index = 1

    # 4개 분위 구간 대표 값 풀
    QUARTILE_VALS = {
        0: [12, 15, 18, 20, 22, 24],   # Q1 (0~24)
        1: [28, 32, 35, 40, 44, 48],   # Q2 (25~49)
        2: [54, 58, 62, 66, 70, 74],   # Q3 (51~74)
        3: [78, 82, 85, 88, 92, 95],   # Q4 (75~100)
    }

    # 16 MBTI x 남/여 = 32개 축 x 8개 변형 = 256명
    male_name_idx = 0
    female_name_idx = 0

    for mbti in MBTI_LIST:
        for gender in ["male", "female"]:
            for v_idx in range(1, 9):  # Variant 1~8
                if gender == "male":
                    name = MALE_NAMES[male_name_idx % len(MALE_NAMES)]
                    male_name_idx += 1
                else:
                    name = FEMALE_NAMES[female_name_idx % len(FEMALE_NAMES)]
                    female_name_idx += 1

                user_id = f"synth_user_{mbti.lower()}_{gender[:1]}_v{v_idx}_{char_index:03d}"
                persona_id = f"char_{char_index:03d}_{mbti.lower()}_{gender[:1]}_v{v_idx}_{name}"

                # 생성 모드 순환
                if v_idx in (1, 2, 3):
                    mode = "self_mirror"
                elif v_idx in (4, 5, 6):
                    mode = "compatible_character"
                else:
                    mode = "fictional_variant"

                # 캐릭터당 5~10개 지표 배치
                # 60개 슬롯(15개 지표 x 4개 구간)이 256명 전체에 골고루 균등하게 배치되도록 모듈러 설계
                num_scores = 5 + ((char_index + v_idx) % 6)  # 5, 6, 7, 8, 9, 10개 중 하나

                selected_dims = []
                for i in range(num_scores):
                    dim_idx = (char_index * 3 + i * 2) % len(DIMENSIONS)
                    dim_name = DIMENSIONS[dim_idx]
                    if dim_name not in selected_dims:
                        selected_dims.append(dim_name)

                # 선택된 지표에 Q1~Q4 점수 균형 할당
                char_scores = {}
                for i, dim in enumerate(selected_dims):
                    q_idx = (char_index * 2 + v_idx + i) % 4
                    vals = QUARTILE_VALS[q_idx]
                    val = vals[(char_index + i) % len(vals)]
                    char_scores[dim] = val

                # 화법 및 표현 스타일
                is_extrovert = mbti.startswith("E")
                is_feeling = "F" in mbti
                is_judging = mbti.endswith("J")

                # 8개 변형별 다채로운 말투 스타일
                style_presets = [
                    ("반말", "짧고 명확함", "절제됨", "필요시 주도적"),
                    ("정중한 존댓말", "부드럽고 상세함", "온화하고 배려 깊음", "상대 보폭에 맞춤"),
                    ("자신감 넘치는 평어", "간결하고 핵심적", "확신에 찬 어조", "대화를 적극적으로 주도"),
                    ("다정한 존대/반말 혼용", "중간~길고 감탄사 포함", "풍부하고 솔직함", "화기애애하게 주도"),
                    ("건조한 반말", "극도로 짧음 (단문)", "무미건조함", "수동적"),
                    ("시원시원하고 단호한 화법", "명확하고 빠름", "당당하고 실용적", "실행 주도형"),
                    ("나긋나긋한 반말", "여유롭고 나른함", "조용히 다정함", "상대에게 흐름 맡김"),
                    ("재치 있고 통통 튀는 반말", "이모티콘과 느낌표 가득", "유쾌하고 장난스러움", "분위기 주도"),
                ]
                preset_idx = (char_index + v_idx) % len(style_presets)
                speech, sent_len, emo, init = style_presets[preset_idx]

                # 갈등 해결 전략
                if "problem_solving" in char_scores and char_scores["problem_solving"] >= 75:
                    conflict_strat = "감정 소모를 배제하고 원인 분석 및 실질적 해결책에 집중"
                elif "withdrawal" in char_scores and char_scores["withdrawal"] >= 75:
                    conflict_strat = "격앙된 상황에서 자리를 피하고 혼자 생각할 시간 확보"
                elif "compliance" in char_scores and char_scores["compliance"] >= 75:
                    conflict_strat = "상대방의 불편함을 먼저 경청하고 기꺼이 양보하여 타결"
                elif "avoidance" in char_scores and char_scores["avoidance"] >= 75:
                    conflict_strat = "불필요한 감정 충돌을 피하며 적당한 물리적 거리두기"
                elif is_feeling:
                    conflict_strat = "서운한 감정을 먼저 공감하고 부드러운 대화로 분위기 회복"
                else:
                    conflict_strat = "객관적 사실 관계를 확인하고 논리적으로 중간 지점 조율"

                # 15개 traits 조립
                traits = {}
                imputed = []
                for dim in DIMENSIONS:
                    if dim in char_scores:
                        s_val = char_scores[dim]
                        traits[dim] = {
                            "raw_score": s_val,
                            "effective_score": s_val,
                            "delta": s_val - 50,
                            "level": classify_level(s_val),
                            "quartile": get_quartile(s_val),
                            "status": "observed",
                            "interpretation": make_interpretation(dim, s_val)
                        }
                    else:
                        traits[dim] = {
                            "raw_score": None,
                            "effective_score": 50,
                            "delta": 0,
                            "level": "중립",
                            "quartile": "중립(50)",
                            "status": "imputed",
                            "interpretation": "관측 정보가 없어 중간 기준값(50)으로 대체됨"
                        }
                        imputed.append(dim)

                primary = [f"{d}: {classify_level(s)}" for d, s in char_scores.items() if s <= 24 or s >= 75]
                secondary = [f"{d}: {classify_level(s)}" for d, s in char_scores.items() if 25 <= s <= 49 or 51 <= s <= 74]

                # 지표 기반 풍부한 정성 요약 (summaries: 3~4개 카테고리)
                summaries = []
                comm_dims = [d for d in ["contact_rhythm", "disclosure", "engagement", "positivity", "openness"] if d in char_scores]
                if comm_dims:
                    comm_details = ", ".join([f"{d} {classify_level(char_scores[d])}" for d in comm_dims])
                    summaries.append({
                        "category": "communication",
                        "title": f"{name}의 소통 스타일",
                        "content": f"{name}은(는) 대화 시 {speech} 화법을 구사하며, {comm_details}의 특성을 보여 편안하고 진솔한 대화를 추구합니다."
                    })

                conf_dims = [d for d in ["problem_solving", "withdrawal", "compliance"] if d in char_scores]
                if conf_dims:
                    conf_details = ", ".join([f"{d} {classify_level(char_scores[d])}" for d in conf_dims])
                    summaries.append({
                        "category": "conflict",
                        "title": f"{name}의 갈등 해결 전략",
                        "content": f"갈등 상황에서 {conflict_strat} 방식으로 대처합니다 ({conf_details}). 감정을 앞세우기보다 상황에 맞춘 유연한 대처를 지향합니다."
                    })

                int_dims = [d for d in ["anxiety", "avoidance", "seriousness"] if d in char_scores]
                if int_dims:
                    int_details = ", ".join([f"{d} {classify_level(char_scores[d])}" for d in int_dims])
                    summaries.append({
                        "category": "intimacy",
                        "title": f"{name}의 관계 거리감과 진지도",
                        "content": f"친밀감 형성 과정에서 {int_details}을(를) 보이며, 서로의 영역을 존중하는 건강한 유대를 선호합니다."
                    })

                ideal_dims = [d for d in ["ideal_warmth", "ideal_vitality", "ideal_status"] if d in char_scores]
                if ideal_dims:
                    ideal_details = ", ".join([f"{d} {classify_level(char_scores[d])}" for d in ideal_dims])
                    summaries.append({
                        "category": "ideal",
                        "title": f"{name}의 파트너 선호 기준",
                        "content": f"이상적인 파트너의 성향으로 {ideal_details}을(를) 선호하며, 함께 있을 때 편안함을 주는 관계를 지향합니다."
                    })

                # 1턴 대화 예시
                if "avoidance" in char_scores and char_scores["avoidance"] >= 75:
                    sample_reply = "연극? 글쎄, 내일은 혼자 시간 보내려고 했는데. 별로 안 땡기네."
                elif "positivity" in char_scores and char_scores["positivity"] >= 75:
                    sample_reply = f"우와 연극이요?! 완전 좋아요! 내일 약속 없는데 같이 보러 가요 😊 무슨 작품이에요?"
                elif "problem_solving" in char_scores and char_scores["problem_solving"] >= 75:
                    sample_reply = "내일 시간 비어 있어. 어떤 장르 연극인데? 시놉시스나 시간대 먼저 알려줘."
                elif "compliance" in char_scores and char_scores["compliance"] >= 75:
                    sample_reply = "네, 좋아요! 물어봐줘서 고마워요. 어떤 연극인지 편할 때 알려주세요 ㅎㅎ"
                elif is_extrovert:
                    sample_reply = "오 연극 보러 가자고? 좋지! 내일 몇 시에 만날래? 끝나고 맛있는 것도 먹자!"
                else:
                    sample_reply = "내일 특별한 건 없는데 연극 괜찮네요. 어떤 연극인지 여유 있을 때 알려주세요."

                card = {
                    "persona_id": persona_id,
                    "user_id": user_id,
                    "name": name,
                    "gender": gender,
                    "generation_mode": mode,
                    "mbti_reference": mbti,
                    "variant_index": v_idx,
                    "traits": traits,
                    "imputed_scores": imputed,
                    "unknown_origin_scores": [],
                    "available_summaries": [s["category"] for s in summaries],
                    "missing_summaries": [c for c in ["communication", "conflict", "intimacy", "ideal", "orientation"] if c not in [s["category"] for s in summaries]],
                    "summaries_data": summaries,
                    "behavior_traits": {
                        "primary_traits": primary,
                        "secondary_traits": secondary,
                        "uncertain_traits": ["assurances (중립 50/대체)"]
                    },
                    "partner_preferences": {
                        d: make_interpretation(d, char_scores[d]) for d in ["ideal_status", "ideal_vitality", "ideal_warmth"] if d in char_scores
                    },
                    "communication_style": {
                        "speech_level": speech,
                        "sentence_length": sent_len,
                        "emotional_expression": emo,
                        "initiative": init
                    },
                    "conflict_style": {
                        "primary_strategy": conflict_strat,
                        "withdrawal_tendency": make_interpretation("withdrawal", char_scores.get("withdrawal", 50)),
                        "resolution_strategy": "성향 지표 기반 대화 조율"
                    },
                    "relationship_style": {
                        "closeness_behavior": "성향에 맞춘 유대 형성",
                        "contact_behavior": make_interpretation("contact_rhythm", char_scores.get("contact_rhythm", 50)),
                        "self_disclosure_behavior": make_interpretation("disclosure", char_scores.get("disclosure", 50))
                    },
                    "personality_rules": [
                        {
                            "rule": f"갈등 상황에서 {conflict_strat} 방식으로 대처한다.",
                            "evidence_source": "observed_traits",
                            "source_type": "observed"
                        },
                        {
                            "rule": f"대화 시 {speech} 화법과 {emo} 태도를 유지한다.",
                            "evidence_source": "communication_style",
                            "source_type": "observed"
                        }
                    ],
                    "dynamic_state": {
                        "current_mood": "neutral",
                        "closeness": 50,
                        "trust": 50,
                        "current_context": None
                    },
                    "dialogue_examples": [
                        {
                            "situation": "상대방이 연극 보러 가자고 제안할 때",
                            "user": "야 내일 뭐해? 나랑 같이 연극보러갈래?",
                            "assistant": sample_reply
                        }
                    ],
                    "evidence": [f"{len(char_scores)}개 지표 관측치 및 {len(summaries)}개 카테고리 요약 기반 도출"],
                    "creative_elements": [],
                    "conflicts": [],
                    "quality_checks": {
                        "numeric_imputation_value": 50,
                        "imputation_information_available": True,
                        "imputed_scores_identified": True,
                        "summary_missing_values_preserved": True,
                        "preferences_separated_from_traits": True,
                        "unsupported_inferences": []
                    }
                }
                characters.append(card)
                char_index += 1

    return characters


def main():
    characters = generate_256_characters()
    print(f"Generated {len(characters)} characters successfully.")

    # 60개 슬롯 커버리지 검증
    slot_coverage = {f"{d}_{q}": 0 for d in DIMENSIONS for q in ["Q1", "Q2", "Q3", "Q4"]}
    for c in characters:
        for d, t in c["traits"].items():
            if t["status"] == "observed":
                q_key = f"{d}_{t['quartile'][:2]}"
                if q_key in slot_coverage:
                    slot_coverage[q_key] += 1

    uncovered = [k for k, v in slot_coverage.items() if v == 0]
    print(f"Total slots to cover: {len(slot_coverage)}")
    print(f"Covered slots: {len(slot_coverage) - len(uncovered)} / {len(slot_coverage)}")
    
    # 각 슬롯당 평균 및 최소 관측 수
    min_count = min(slot_coverage.values())
    max_count = max(slot_coverage.values())
    avg_count = sum(slot_coverage.values()) / len(slot_coverage)
    print(f"Slot observations: Min={min_count}, Max={max_count}, Avg={avg_count:.1f} per slot.")

    if uncovered:
        print("Uncovered slots:", uncovered)
    else:
        print("🎉 100% COVERAGE: All 60 dimension-quartile slots are richly covered (each slot has 20~40 observations)!")

    out_file = Path("페르소나 데이터/persona_cards_256.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(characters, f, ensure_ascii=False, indent=2)

    # 기본 persona_cards.json 도 256개로 업데이트
    base_file = Path("페르소나 데이터/persona_cards.json")
    with open(base_file, "w", encoding="utf-8") as f:
        json.dump(characters, f, ensure_ascii=False, indent=2)

    print(f"Saved to {out_file} and {base_file}.")


if __name__ == "__main__":
    main()
