# dev/ — 기능별 플레이그라운드

`app/` 을 **한 줄도 고치지 않고** 브라우저에서 기능을 돌려본다.
DB(SQLite)·LLM(Gemini/Anthropic, 요청별 키)을 바깥에서 갈아끼운다.
`app/` 은 `dev/` 를 모르고, Docker 이미지에도 들어가지 않는다.

```
dev/
  _shared/        기능 공통
    llm.py        Gemini/Anthropic 호출기 · X-Api-Key / X-Provider 헤더 · 모델 자동 전환 · /check
    db.py         SQLite 로 get_db 대체
    app.py        앱 뼈대 (/, /check, /meta/llm)
  persona/        기능별 — 같은 틀로 practice/, simulation/ … 을 추가
    playground.py 라우터 mount + 그 기능이 쓰는 것만 교체
    static/       테스트 프론트
```

```bash
cd ai-server
python3 -m pip install -r dev/requirements.txt
python3 -m uvicorn dev.persona.playground:app --reload --port 8000
# → http://localhost:8000
```

## 새 기능 플레이그라운드 만들기

1. `dev/<feature>/playground.py` — `make_app()` 으로 뼈대 받고, 그 기능 라우터를 `include_router(..., dependencies=app.state.deps)`
2. 그 기능이 LLM 을 부르는 자리에 `llm.bind(<feature>.LLMError)` 를 끼움
3. `get_db` 는 `SQLite(...).get_db` 로 override, startup 에서 `create_tables(Base)`
4. `static/index.html` — persona 것을 복사해서 엔드포인트만 바꿈
