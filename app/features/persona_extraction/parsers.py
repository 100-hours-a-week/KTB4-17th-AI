"""카카오톡 대화 내보내기(.txt) → 발화 목록. DB·LLM 과 무관한 순수 파싱.

지원 형식 (2026-10-06 결정 — 이 3종만, 그 외는 UnknownFormat):
  PC      : "--------------- 2026년 10월 5일 일요일 ---------------" 날짜 줄 + "[민수] [오후 3:12] 내용"
  Android : "2026년 10월 5일 오후 3:12, 민수 : 내용"
  iOS     : "2026. 10. 5. 오후 3:12, 민수 : 내용"
어느 패턴에도 맞지 않는 줄은 직전 발화의 줄바꿈 이어짐으로 본다. 날짜만 있는 줄·안내 줄은 버린다.
시각은 한국 시간(Asia/Seoul)으로 붙인다.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

_PC_DATE = re.compile(r"^-+\s*(?P<y>\d{4})년 (?P<mo>\d{1,2})월 (?P<d>\d{1,2})일 \S+\s*-+$")
_PC_MSG = re.compile(r"^\[(?P<name>[^\]]+)\] \[(?P<ap>오전|오후) (?P<h>\d{1,2}):(?P<mi>\d{2})\] (?P<text>.*)$")
_MOBILE_DATE = (
    r"(?:(?P<y>\d{4})년 (?P<mo>\d{1,2})월 (?P<d>\d{1,2})일|(?P<y2>\d{4})\. (?P<mo2>\d{1,2})\. (?P<d2>\d{1,2})\.)"
)
_MOBILE_MSG = re.compile(
    rf"^{_MOBILE_DATE} (?P<ap>오전|오후) (?P<h>\d{{1,2}}):(?P<mi>\d{{2}}), (?P<name>.+?) : (?P<text>.*)$"
)
# 날짜로 시작하지만 "이름 : 내용" 이 없는 줄 — 날짜 구분선, 입장·퇴장 안내
_MOBILE_SYSTEM = re.compile(rf"^{_MOBILE_DATE}")
# 사진·이모티콘 같은 첨부는 말투가 아니다
_PLACEHOLDERS = {"사진", "동영상", "이모티콘", "음성메시지", "삭제된 메시지입니다."}


class UnknownFormat(ValueError):
    """카카오톡 형식으로 읽은 발화가 하나도 없다."""


@dataclass
class KakaoLine:
    speaker: str
    sent_at: datetime
    text: str


# 오전/오후 12시간제 → 24시간제. 오전 12시는 0시, 오후 12시는 12시
def _hour(ap: str, h: str) -> int:
    hour = int(h) % 12
    return hour + 12 if ap == "오후" else hour


def _parse_kakao_csv(raw: str) -> list[KakaoLine]:
    lines: list[KakaoLine] = []
    reader = csv.reader(io.StringIO(raw.lstrip("\ufeff")))
    header = next(reader, None)
    if not header or [h.strip() for h in header] != ["Date", "User", "Message"]:
        return []
    for row in reader:
        if len(row) < 3:
            continue
        date_str, speaker, text = row[0].strip(), row[1].strip(), row[2].strip()
        if not text or text in _PLACEHOLDERS:
            continue
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        except ValueError:
            continue
        lines.append(KakaoLine(speaker, dt, text))
    return lines


def parse_kakao(raw: str) -> list[KakaoLine]:
    clean_raw = raw.lstrip("\ufeff \t\r\n")
    if clean_raw.startswith("Date,User,Message"):
        csv_lines = _parse_kakao_csv(raw)
        if csv_lines:
            return csv_lines

    lines: list[KakaoLine] = []
    pc_day: tuple[int, int, int] | None = None
    current: KakaoLine | None = None

    # 모아 둔 발화를 확정한다. 첨부 자리표시나 빈 내용은 버린다
    def flush() -> None:
        if current is None:
            return
        text = current.text.strip()
        if text and text not in _PLACEHOLDERS:
            current.text = text
            lines.append(current)

    for line in raw.splitlines():
        if m := _PC_DATE.match(line):
            flush()
            current = None
            pc_day = (int(m["y"]), int(m["mo"]), int(m["d"]))
        elif (m := _PC_MSG.match(line)) and pc_day:
            flush()
            at = datetime(*pc_day, _hour(m["ap"], m["h"]), int(m["mi"]), tzinfo=KST)
            current = KakaoLine(m["name"], at, m["text"])
        elif m := _MOBILE_MSG.match(line):
            flush()
            y, mo, d = (m["y"], m["mo"], m["d"]) if m["y"] else (m["y2"], m["mo2"], m["d2"])
            at = datetime(int(y), int(mo), int(d), _hour(m["ap"], m["h"]), int(m["mi"]), tzinfo=KST)
            current = KakaoLine(m["name"], at, m["text"])
        elif _MOBILE_SYSTEM.match(line):
            flush()
            current = None
        elif current is not None:
            current.text += "\n" + line
    flush()

    if not lines:
        raise UnknownFormat("카카오톡 내보내기 형식(PC·Android·iOS)으로 읽은 발화가 없어요")
    return lines
