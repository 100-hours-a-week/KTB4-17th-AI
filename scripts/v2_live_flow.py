"""v2 전체 로컬 LLM 통합 테스트 — 실행 중인 서버(127.0.0.1:8765)에 v2 API 를 순서대로 호출하고 요청·응답을 JSON 으로 남긴다.

사용법: uv run python scripts/v2_live_flow.py <결과.json>
전제: 서버를 INTERNAL_API_KEY=local-test 로 띄우고, 민수(u-minsu)·지은(u-jieun) 확정 페르소나를 미리 시드한다.
결과 문서: docs/실험_및_분석_결과/v2_로컬_LLM_통합테스트_결과.md
"""

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8765/ai/api"
H = {"X-Internal-Api-Key": "local-test"}
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("v2_live_results.json")
steps: list[dict] = []
c: httpx.Client


def rec(name, method, path, *, json_body=None, data=None, params=None, stream=False):
    t = time.monotonic()
    if stream:
        events = []
        with c.stream(method, path, json=json_body) as r:
            status = r.status_code
            for line in r.iter_lines():
                if line.strip():
                    events.append(line)
        body = events
    else:
        r = c.request(method, path, json=json_body, data=data, params=params)
        status = r.status_code
        try:
            body = r.json()
        except Exception:
            body = r.text
        headers = {k: v for k, v in r.headers.items() if k.lower() in ("retry-after",)}
    ms = int((time.monotonic() - t) * 1000)
    step = {
        "name": name,
        "method": method,
        "path": path,
        "request": json_body or data or params,
        "status": status,
        "elapsed_ms": ms,
        "response": body,
    }
    if not stream and headers:
        step["headers"] = headers
    steps.append(step)
    print(f"[{status}] {ms:>6}ms {name}", flush=True)
    OUT.write_text(json.dumps(steps, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return body, status


def practice(me, partner, nickname, msgs, tag):
    s, _ = rec(
        f"{tag} 연습 시작",
        "POST",
        "/v2/practice/start",
        json_body={"me_user_id": me, "partner_user_id": partner, "nickname": nickname},
    )
    sid = s["session_id"]
    rec(f"{tag} 오프닝", "POST", f"/v2/practice/{sid}/opening")
    for k, m in enumerate(msgs):
        rec(f"{tag} 메시지 {k + 1}", "POST", f"/v2/practice/{sid}/messages", json_body={"message": m})
    return sid


MINSU = [
    "안녕하세요! 저는 민수라고 해요. 반가워요 ㅎㅎ",
    "저는 주말마다 근교 산으로 등산을 가요. 지난주엔 북한산 다녀왔어요.",
    "평일엔 퇴근하고 집에서 요리하는 걸 좋아해요. 요즘은 파스타에 빠졌어요.",
    "저는 약속 시간 지키는 걸 중요하게 생각하는 편이에요.",
    "연애할 때는 연락을 자주 주고받는 게 좋더라고요.",
    "가끔 주말에 캠핑도 가요. 불멍하면 마음이 편해져요.",
    "데이트는 같이 산책하거나 맛집 찾아다니는 게 좋아요.",
    "오늘 이야기 즐거웠어요!",
]
JIEUN = [
    "안녕하세요, 지은이에요! 반가워요~",
    "저는 트레킹 동호회에서 한 달에 두 번 정도 산에 가요.",
    "아침마다 요가를 해요. 하루를 차분하게 시작할 수 있어서 좋아요.",
    "주말엔 집에서 베이킹을 자주 해요. 지난주엔 스콘 구웠어요 ㅎㅎ",
    "작년에 회사를 옮겨서 요즘 적응 중이에요.",
    "사람을 대할 때 솔직한 게 제일 중요하다고 생각해요.",
    "데이트는 브런치 먹고 전시회 보는 거 좋아해요.",
    "오늘 재밌었어요, 고마워요!",
]


def main() -> None:
    global c
    c = httpx.Client(base_url=BASE, headers=H, timeout=180)
    # ── 1. 연습대화 ──
    sid_m = practice("u-minsu", "u-jieun", "민수", MINSU, "민수")
    rec("민수 재시도(마지막 AI 답 다시 받기)", "POST", f"/v2/practice/{sid_m}/retry")
    rec(
        "민수 스트리밍 메시지",
        "POST",
        f"/v2/practice/{sid_m}/messages/stream",
        json_body={"message": "혹시 등산 좋아하세요?"},
        stream=True,
    )
    rec("민수 세션 조회", "GET", f"/v2/practice/{sid_m}")
    rec("민수 세션 목록", "GET", "/v2/practice", params={"user_id": "u-minsu"})
    rec("민수 연습 종료", "POST", f"/v2/practice/{sid_m}/end")
    sid_j = practice("u-jieun", "u-minsu", "지은", JIEUN, "지은")
    rec("지은 연습 종료", "POST", f"/v2/practice/{sid_j}/end")

    # ── 2. 메모리 동기화 ──
    for uid in ("u-minsu", "u-jieun"):
        rec(f"{uid} 메모리 동기화 요청", "POST", f"/v2/persona/memory/{uid}/sync")
    for uid in ("u-minsu", "u-jieun"):
        for _ in range(45):
            body = c.get(f"/v2/persona/memory/{uid}").json()
            if body.get("facts"):
                break
            time.sleep(2)
        rec(f"{uid} 메모리 조회", "GET", f"/v2/persona/memory/{uid}")
    rec("tentative 정리", "POST", "/v2/persona/memory/cleanup-tentative")

    # ── 3. 카카오 추출 ──
    lines = ["--------------- 2026년 10월 12일 일요일 ---------------"]
    talk = [
        ("지은", "오늘 요가 끝나고 바로 베이킹 했어ㅋㅋ"),
        ("친구", "또? 부지런하다"),
        ("지은", "ㅇㅇ 스콘 구웠는데 대박 맛있어"),
        ("지은", "다음 주에 동호회 트레킹 가는데 날씨 좋겠지?"),
        ("친구", "아마도"),
        ("지은", "ㅋㅋㅋ 제발~"),
        ("지은", "아 근데 요즘 회사 적응하느라 좀 피곤해"),
        ("친구", "힘내"),
        ("지은", "웅웅 고마워ㅎㅎ"),
        ("지은", "주말에 전시회 갈래?"),
        ("친구", "좋아"),
        ("지은", "오 대박 그럼 브런치도 먹자ㅋㅋ"),
        ("지은", "나 요즘 책 모임도 다시 나가"),
        ("지은", "ㅋㅋㅋ 이번 책 진짜 좋아"),
        ("지은", "웅 담에 빌려줄게~"),
    ]
    for k, (who, msg) in enumerate(talk):
        lines.append(f"[{who}] [오후 {3 + k // 6}:{10 + k:02d}] {msg}")
    kakao = "\n".join(lines)
    job, _ = rec(
        "지은 카카오 대화 업로드",
        "POST",
        "/v2/persona-extraction/conversation",
        data={"user_id": "u-jieun", "speaker_name": "지은", "text": kakao},
    )
    if isinstance(job, dict) and job.get("job_id"):
        for _ in range(60):
            j = c.get(f"/v2/persona-extraction/jobs/{job['job_id']}").json()
            if j.get("status") in ("succeeded", "failed"):
                break
            time.sleep(2)
        rec("카카오 추출 작업 결과", "GET", f"/v2/persona-extraction/jobs/{job['job_id']}")
    steps[-2]["request"] = {"user_id": "u-jieun", "speaker_name": "지은", "text": kakao}

    # ── 4. 시뮬레이션 ──
    sims = []
    for attempt in range(3):
        j, st = rec(
            f"시뮬레이션 생성 #{attempt + 1}",
            "POST",
            "/v2/simulation",
            json_body={"me_user_id": "u-minsu", "partner_user_id": "u-jieun", "turns": 10},
        )
        if st != 202:
            continue
        if attempt == 0:
            rec(
                "같은 상대 중복 요청(409 기대)",
                "POST",
                "/v2/simulation",
                json_body={"me_user_id": "u-minsu", "partner_persona_id": "persona-jieun"},
            )
        seen = []
        while True:
            r = c.get(f"/v2/simulation/jobs/{j['job_id']}")
            b = r.json()
            if not seen or seen[-1] != (b["status"], b["stage"]):
                seen.append((b["status"], b["stage"]))
            if b["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
        st_body, _ = rec(f"시뮬레이션 작업 결과 #{attempt + 1}", "GET", f"/v2/simulation/jobs/{j['job_id']}")
        steps[-1]["polled_stages"] = seen
        if st_body.get("simulation_id"):
            rec(f"시뮬레이션 결과 조회 #{attempt + 1}", "GET", f"/v2/simulation/{st_body['simulation_id']}")
            sims.append(st_body["simulation_id"])
    rec("시뮬레이션 목록(지은 기준)", "GET", "/v2/simulation", params={"user_id": "u-jieun"})
    rec("인증 키 없이 요청(401 기대)", "GET", "/v2/simulation", params={"user_id": "u-jieun"})
    steps[-1]["status"] = httpx.get(f"{BASE}/v2/simulation", params={"user_id": "u-jieun"}).status_code
    steps[-1]["response"] = httpx.get(f"{BASE}/v2/simulation", params={"user_id": "u-jieun"}).json()
    OUT.write_text(json.dumps(steps, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("done", len(steps))


if __name__ == "__main__":
    main()
