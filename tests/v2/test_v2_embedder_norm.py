from app.v2.persona.embedder import (
    extract_numbers,
    is_batch_all_trivial,
    is_trivial_reaction,
    normalize_chat_text,
)


def test_normalize_chat_text_collapses_repeats_but_keeps_signal():
    assert normalize_chat_text("ㅋㅋㅋㅋㅋㅋㅋㅋ") == "ㅋㅋ"
    assert normalize_chat_text("로또 당첨되면 집 사줄게 ㅋㅋㅋㅋㅋ") == "로또 당첨되면 집 사줄게 ㅋㅋ"
    assert normalize_chat_text("진짜요?????") == "진짜요??"
    assert normalize_chat_text("주말에   등산   가요") == "주말에 등산 가요"


def test_extract_numbers_distinguishes_numeric_changes():
    assert extract_numbers("주 3회 헬스를 간다") != extract_numbers("주 5회 헬스를 간다")
    assert extract_numbers("주말마다 등산을 간다") == extract_numbers("주말마다 등산 다닌다")


def test_trivial_reaction_detection():
    assert is_trivial_reaction("ㅇㅇ") is True
    assert is_trivial_reaction("ㅋㅋㅋㅋㅋㅋ") is True
    assert is_trivial_reaction("맞아요") is True
    assert is_trivial_reaction("ㅎㅎ~!") is True
    assert is_trivial_reaction("저는 부산 해운대에 살아요") is False


def test_is_batch_all_trivial():
    assert is_batch_all_trivial(["ㅇㅇ", "ㅋㅋㅋㅋ", "맞아요"]) is True
    assert is_batch_all_trivial(["ㅇㅇ", "주말마다 등산 다녀요"]) is False
    assert is_batch_all_trivial([]) is False
