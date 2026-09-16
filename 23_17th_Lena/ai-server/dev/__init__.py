"""개발용 플레이그라운드. app/ 은 한 줄도 건드리지 않고, 밖에서 갈아끼운다.

    dev/_shared/   기능 공통 — LLM 호출기(Gemini/Anthropic · 요청별 키), SQLite, 앱 뼈대
    dev/<feature>/ 기능별 플레이그라운드 — 라우터 mount + 그 기능이 쓰는 것만 교체

app/ 에서는 이 패키지를 import 하지 않는다. Docker 이미지에도 들어가지 않는다.
"""
