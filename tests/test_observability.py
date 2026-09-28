from app.core.observability import build_langfuse_metadata


def test_build_langfuse_metadata_uses_standard_dimensions():
    metadata = build_langfuse_metadata(
        feature="practice",
        operation="reply",
        user_id="user-1",
        session_id="session-1",
        tags=("streaming", "practice"),
        messageIndex=3,
        opening=False,
        optional=None,
    )

    assert metadata == {
        "feature": "practice",
        "operation": "reply",
        "langfuse_user_id": "user-1",
        "langfuse_session_id": "session-1",
        "langfuse_tags": ["practice", "reply", "streaming"],
        "messageIndex": 3,
        "opening": False,
    }


def test_build_langfuse_metadata_omits_empty_identifiers_and_protects_reserved_fields():
    metadata = build_langfuse_metadata(
        feature="simulation",
        operation="run",
        user_id=None,
        session_id="",
        **{
            "langfuse_user_id": "must-not-win",
            "langfuse_session_id": "must-not-win",
            "langfuse_tags": "must-not-win",
            "requestedTurns": 5,
        },
    )

    assert "langfuse_user_id" not in metadata
    assert "langfuse_session_id" not in metadata
    assert metadata["langfuse_tags"] == ["simulation", "run"]
    assert metadata["requestedTurns"] == 5
