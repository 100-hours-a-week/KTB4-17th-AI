# 실행: uv run pytest tests/v2/test_v2_config.py -v
from app.core.config import Settings, get_settings


def test_v2_memory_rag_default_settings():
    settings = get_settings()
    assert settings.embedding_dim == 1024
    assert settings.embedding_pooling_mode == "cls"
    assert settings.memory_top_k == 5
    assert settings.memory_max_cosine_distance == 0.65
    assert settings.practice_v2_history_window == 20
    assert settings.memory_trigger_turn_count == 8
    assert settings.memory_trigger_idle_minutes == 20
    assert settings.memory_reconcile_noop_sim == 0.99
    assert settings.memory_reconcile_judge_sim == 0.70
    assert settings.memory_tentative_ttl_days == 30
    assert settings.memory_job_stale_minutes == 10


def test_v2_settings_env_override(monkeypatch):
    monkeypatch.setenv("MEMORY_TOP_K", "3")
    monkeypatch.setenv("EMBEDDING_POOLING_MODE", "mean")
    custom = Settings(_env_file=None)
    assert custom.memory_top_k == 3
    assert custom.embedding_pooling_mode == "mean"
