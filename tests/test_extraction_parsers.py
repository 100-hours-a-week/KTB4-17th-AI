"""카카오톡 내보내기 파싱 — PC · Android · iOS."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.features.persona_extraction.parsers import UnknownFormat, parse_kakao

KST = ZoneInfo("Asia/Seoul")

PC = """민수 님과 카카오톡 대화
저장한 날짜 : 2026-10-06 10:00:00

--------------- 2026년 10월 5일 일요일 ---------------
[민수] [오후 3:12] 오 대박 진짜?
[지은] [오후 3:13] ㅋㅋ 응 어제 갔다왔어
[민수] [오후 3:13] 어디어디
줄바꿈한 둘째 줄
[민수] [오전 12:05] 사진
"""

ANDROID = """2026년 10월 5일 오후 3:10
2026년 10월 5일 오후 3:12, 민수 : 오 대박 진짜?
2026년 10월 5일 오후 3:13, 지은 : ㅋㅋ 응
"""

IOS = """2026. 10. 5. 오후 3:12, 민수 : 오 대박 진짜?
2026. 10. 5. 오후 12:01, 지은 : 점심 뭐 먹어
"""


def test_pc_format_with_date_lines_multiline_and_media_placeholder():
    lines = parse_kakao(PC)

    assert [(m.speaker, m.text) for m in lines] == [
        ("민수", "오 대박 진짜?"),
        ("지은", "ㅋㅋ 응 어제 갔다왔어"),
        ("민수", "어디어디\n줄바꿈한 둘째 줄"),
    ]
    assert lines[0].sent_at == datetime(2026, 10, 5, 15, 12, tzinfo=KST)


def test_android_format_skips_date_only_lines():
    lines = parse_kakao(ANDROID)

    assert [(m.speaker, m.text) for m in lines] == [("민수", "오 대박 진짜?"), ("지은", "ㅋㅋ 응")]


def test_ios_format_noon_is_12():
    lines = parse_kakao(IOS)

    assert lines[1].sent_at == datetime(2026, 10, 5, 12, 1, tzinfo=KST)


def test_midnight_am_12_is_0():
    lines = parse_kakao("2026. 10. 5. 오전 12:30, 민수 : 안 자?\n")

    assert lines[0].sent_at.hour == 0


def test_unknown_format_raises():
    with pytest.raises(UnknownFormat):
        parse_kakao("민수: 안녕\n지은: 응 안녕\n")


def test_csv_format_parses_multiline_and_bom():
    csv_text = (
        '\ufeffDate,User,Message\n2026-08-18 13:48:31,"형륜","선재야\n도와줘!"\n2026-08-18 13:50:00,"선재","응 안녕"\n'
    )
    lines = parse_kakao(csv_text)

    assert len(lines) == 2
    assert lines[0].speaker == "형륜" and lines[0].text == "선재야\n도와줘!"
    assert lines[0].sent_at == datetime(2026, 8, 18, 13, 48, 31, tzinfo=KST)
    assert lines[1].speaker == "선재" and lines[1].text == "응 안녕"
