"""사용자별 말투 어댑터(LoRA) — persona-mlops 배치와 함께 쓰는 테이블, 학습 때와 같은 프롬프트, 서빙 호출.

배치(수집·정제·학습·채점·활성화)는 persona-mlops 저장소에서 서버 밖 ECS 작업으로 돈다. 두 저장소는 이 서비스의
PostgreSQL 을 같이 쓴다. 테이블은 이 저장소의 alembic 이 만들고, 배치가 읽고 쓴다. API 서버는 persona_adapter 만 읽는다.
"""
