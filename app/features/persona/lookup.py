"""다른 기능(simulation·practice)이 저장된 페르소나를 꺼내 쓰는 입구.

PersonaRef(persona_id | user_id | session_id) → LoadedPersona(행 + 닉네임 + API 모델).
온보딩 서비스는 건드리지 않고, 읽기만 한다. 없으면 None — 404 로 바꾸는 건 호출부 api.py 의 몫.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .models import PersonaRecord
from .repository import PersonaRepository
from .schemas import PersonaBrief, PersonaRef, PersonaResponse
from .service import persona_response


@dataclass
class LoadedPersona:
    record: PersonaRecord
    nickname: str
    response: PersonaResponse

    @property
    def brief(self) -> PersonaBrief:
        return PersonaBrief(
            persona_id=self.record.id,
            user_id=self.record.user_id,
            nickname=self.nickname,
            version=self.record.version,
            headline=(self.record.narrative or {}).get("headline"),
            accuracy=self.response.accuracy,
        )


async def load_persona(db: AsyncSession, ref: PersonaRef) -> LoadedPersona | None:
    repo = PersonaRepository(db)
    record: PersonaRecord | None
    if ref.persona_id:
        record = await repo.get_persona(ref.persona_id)
    elif ref.user_id:
        record = await repo.latest_persona_for_user(ref.user_id)
    else:
        record = await repo.latest_persona(ref.session_id or "")
    if record is None:
        return None

    # 닉네임은 온보딩 세션에만 있다. 세션이 지워졌으면(없을 리 없지만) 페르소나 id 앞자리로.
    session = await repo.get_session_brief(record.session_id)
    nickname = session.nickname if session else record.id[:6]
    return LoadedPersona(record=record, nickname=nickname, response=persona_response(record))
