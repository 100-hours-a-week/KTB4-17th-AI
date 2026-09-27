from app.features.persona.models import OnboardingSession, OnboardingTurn


def test_onboarding_turn_uses_explicit_table_name():
    assert OnboardingTurn.__tablename__ == "onboarding_turns"
    assert "onboarding_turns" in OnboardingSession.metadata.tables
    assert "conversation_turns" not in OnboardingSession.metadata.tables
