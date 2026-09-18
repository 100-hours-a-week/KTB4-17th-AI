# dev/ — 기능별 플레이그라운드

`app/` 을 **한 줄도 고치지 않고** 브라우저에서 기능을 돌려본다.
DB(SQLite)·LLM(Gemini/Anthropic, 요청별 키)을 바깥에서 갈아끼운다.
`app/` 은 `dev/` 를 모르고, Docker 이미지에도 들어가지 않는다.

```
dev/
  _shared/        기능 공통
    llm.py        Gemini/Anthropic 호출기 · X-Api-Key / X-Provider 헤더 · 모델 자동 전환 · /check
                  bind(): 기능의 _call 자리 · bind_stream(): 기능의 _stream 자리 (전문을 조각내서 냄)
    db.py         SQLite 로 get_db 대체 · SHARED_DB (세 플레이그라운드가 같은 파일)
    app.py        앱 뼈대 (/, /check, /meta/llm)
    personas.py   저장된 페르소나 목록 (/personas — 플레이그라운드 전용)
  persona/        온보딩 (8000)
  simulation/     시뮬레이션 (8001) — 페르소나 둘 골라 실행, 대본 + 리포트 렌더
  practice/       연습대화 (8002) — 상대 페르소나 골라 SSE 채팅
    playground.py 라우터 mount + 그 기능이 쓰는 것만 교체
    static/       테스트 프론트
```

저장소 루트에서:

```bash
uv sync
uv run uvicorn dev.persona.playground:app --reload --port 8000      # → http://localhost:8000
uv run uvicorn dev.simulation.playground:app --reload --port 8001   # → http://localhost:8001
uv run uvicorn dev.practice.playground:app --reload --port 8002     # → http://localhost:8002
```

순서: 8000 에서 페르소나를 둘 이상 만든다 → 8001/8002 에서 고른다. 셋 다 `dev/persona/playground.db` 를 본다.

## 새 기능 플레이그라운드 만들기

1. `dev/<feature>/playground.py` — `make_app()` 으로 뼈대 받고, 그 기능 라우터를 `include_router(..., dependencies=app.state.deps)`
2. 그 기능이 LLM 을 부르는 자리에 `llm.bind(<feature>.LLMError)` 를 끼움 (스트리밍이면 `llm.bind_stream`)
3. `get_db` 는 `SQLite(...).get_db` 로 override, lifespan 에서 `create_tables(Base)`
4. `static/index.html` — persona 것을 복사해서 엔드포인트만 바꿈
