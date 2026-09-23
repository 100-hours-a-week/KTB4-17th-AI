# feat.persona/app 통합 코드 설명서

이 문서는 `feat.persona/app` 아래의 Python 파일 11개를 한곳에서 볼 수 있게 모은 통합본이다.

각 파일 장은 다음 순서로 구성된다.

1. 해당 파일의 전체 코드
2. 파일의 역할
3. 함수·클래스·주요 변수 설명
4. 처리 흐름과 주의점

## 목차

1. `main.py`
2. `core/config.py`
3. `core/db.py`
4. `features/persona/schemas.py`
5. `features/persona/models.py`
6. `features/persona/agents.py`
7. `features/persona/repository.py`
8. `features/persona/service.py`
9. `features/persona/api.py`
10. `features/persona/lookup.py`
11. `features/persona/profile.py`

> Python 원본 파일은 실행에 필요하므로 그대로 유지한다. 기존의 개별 Markdown 설명서는 이 통합본으로 대체한다.



---

# `main.py`

## 전체 코드

```python
from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter, FastAPI

from app.features.persona.api import router as persona_router
from app.features.practice.api import router as practice_router
from app.features.simulation.api import router as simulation_router

description = """
별이삼샵 어플리케이션의 AI API입니다.

## 담당자
- [lena.cho](https://github.com/HyeerinCho)
- [yuta.jeong](https://github.com/hrunj1230)
"""

TAGS_METADATA = [
    {"name": "persona", "description": "사용자 정보를 바탕으로 AI 페르소나를 생성하고 조회합니다."},
    {"name": "practice", "description": "페르소나와의 연습 대화 메시지를 처리합니다."},
    {"name": "simulation", "description": "두 페르소나 간의 대화를 시뮬레이션하고 결과를 반환합니다."},
]
app = FastAPI(
    title="별이삼샵 AI API",
    description=description,
    version="0.1.0",
    contact={
        "name": "별이삼샵 AI 팀",
        "url": "https://github.com/100-hours-a-week/KTB4-17th-AI",
    },
    openapi_tags=TAGS_METADATA,
    # Nginx 프록시 경로에 따라서
    # root_path="/ai", prefix="/api" 수정
)
api_router = APIRouter(prefix="/ai/api")
api_router.include_router(persona_router)
api_router.include_router(practice_router)
api_router.include_router(simulation_router)

app.include_router(api_router)
```

## 1. 파일의 역할

`main.py`는 FastAPI 애플리케이션을 처음 만드는 **프로그램의 입구**이다.

이 파일은 다음 일을 한다.

1. `.env` 파일의 환경 변수를 불러온다.
2. FastAPI 앱 객체를 만든다.
3. API 문서에 표시할 제목과 설명을 설정한다.
4. persona, practice, simulation 라우터를 하나로 묶는다.
5. 모든 API 주소 앞에 `/ai/api`를 붙인다.

요청 처리 규칙 자체는 여기서 만들지 않는다. 실제 기능은 각 기능 폴더의 `api.py`가 담당한다.

## 2. 환경 변수 불러오기

```python
from dotenv import load_dotenv

load_dotenv()
```

- 프로젝트의 `.env` 파일을 읽어 환경 변수로 등록한다.
- `OPENROUTER_API_KEY`, `DATABASE_URL` 같은 값을 `os.environ` 또는 설정 클래스에서 읽을 수 있게 한다.
- 다른 앱 모듈을 import하기 전에 실행된다. 다른 파일이 import되는 순간 환경 변수를 읽을 수 있기 때문이다.

## 3. 라우터 불러오기

```python
from app.features.persona.api import router as persona_router
from app.features.practice.api import router as practice_router
from app.features.simulation.api import router as simulation_router
```

각 기능의 API 주소 모음을 가져온다.

| 이름 | 담당 기능 |
|---|---|
| `persona_router` | 온보딩 대화와 페르소나 생성·조회 |
| `practice_router` | 저장된 페르소나와의 연습 대화 |
| `simulation_router` | 두 페르소나의 대화 시뮬레이션 |

## 4. API 문서 정보

### `description`

Swagger API 문서 첫 화면에 표시할 서비스 설명과 담당자 정보이다.

### `TAGS_METADATA`

Swagger 문서에서 API를 `persona`, `practice`, `simulation` 그룹으로 나누고 각 그룹의 설명을 보여준다.

## 5. `app = FastAPI(...)`

실제 FastAPI 애플리케이션 객체를 만든다.

| 설정 | 의미 |
|---|---|
| `title` | API 문서에 표시되는 서비스 이름 |
| `description` | 서비스 소개 내용 |
| `version` | 현재 API 버전 |
| `contact` | 담당 팀과 저장소 주소 |
| `openapi_tags` | API 기능별 태그 설명 |

서버 실행 명령에서 보통 이 객체를 가리킨다.

```bash
uvicorn app.main:app
```

위 명령의 마지막 `app`이 이 파일의 `app` 변수이다.

## 6. 공통 주소 설정

```python
api_router = APIRouter(prefix="/ai/api")
```

모든 하위 API 주소 앞에 `/ai/api`를 붙이는 공통 라우터를 만든다.

예를 들어 persona 라우터의 주소가 `/v1/persona/onboarding/start`라면 최종 주소는 다음과 같다.

```text
/ai/api/v1/persona/onboarding/start
```

## 7. 하위 라우터 연결

```python
api_router.include_router(persona_router)
api_router.include_router(practice_router)
api_router.include_router(simulation_router)
```

세 기능의 API를 공통 라우터에 추가한다.

```python
app.include_router(api_router)
```

완성된 공통 라우터를 FastAPI 앱에 등록한다.

## 8. 전체 실행 흐름

```text
서버 시작
→ .env 읽기
→ 각 기능의 api.py 불러오기
→ FastAPI 앱 생성
→ /ai/api 공통 주소 설정
→ persona/practice/simulation 라우터 연결
→ HTTP 요청을 받을 준비 완료
```

## 9. 주의할 점

- 환경 변수 로딩 순서를 바꾸면, import 시점에 API 키를 읽는 모듈이 실패할 수 있다.
- 이 파일은 라우터를 연결하는 곳이다. 대화 생성이나 DB 처리 같은 업무 로직은 넣지 않는 편이 좋다.
- 현재 `feat.persona/app` 사본에는 practice와 simulation 폴더가 보이지 않는다. 실제 실행 환경에는 해당 모듈이 있어야 `main.py`의 import가 성공한다.



---

# `core/config.py`

## 전체 코드

```python
"""환경 변수. .env 는 app.main 이 load_dotenv 로 먼저 올리고, 여기서는 읽기만 한다."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 기본값은 docs/postgres.md 의 로컬 docker 컨테이너. 배포 환경은 .env 로 덮어쓴다.
    database_url: str = "postgresql+asyncpg://ktb:ktb@localhost:5432/ktb"
    db_echo: bool = False


settings = Settings()
```

## 1. 파일의 역할

`config.py`는 프로그램이 사용할 **환경 설정값을 한곳에서 읽고 관리하는 파일**이다.

현재는 다음 두 설정을 관리한다.

- 데이터베이스 접속 주소
- SQL 실행 로그 출력 여부

## 2. `Settings` 클래스

```python
class Settings(BaseSettings):
```

Pydantic의 `BaseSettings`를 상속한 설정 클래스이다. 클래스에 적힌 기본값을 사용하되, 같은 이름의 환경 변수가 있으면 환경 변수 값이 우선한다.

### `model_config`

```python
model_config = SettingsConfigDict(env_file=".env", extra="ignore")
```

| 설정 | 의미 |
|---|---|
| `env_file=".env"` | `.env` 파일에서도 설정을 읽는다. |
| `extra="ignore"` | `Settings`에 선언되지 않은 환경 변수는 무시한다. |

`.env`에 다른 값이 많이 있어도 오류를 발생시키지 않는다.

### `database_url`

```python
database_url: str = "postgresql+asyncpg://ktb:ktb@localhost:5432/ktb"
```

비동기 방식으로 PostgreSQL에 접속하기 위한 주소이다.

환경 변수로 덮어쓰려면 다음처럼 설정한다.

```dotenv
DATABASE_URL=postgresql+asyncpg://사용자:비밀번호@호스트:5432/DB이름
```

### `db_echo`

```python
db_echo: bool = False
```

SQLAlchemy가 실행하는 SQL 문장을 로그에 출력할지 결정한다.

- `False`: SQL 로그를 출력하지 않는다.
- `True`: 실행되는 SQL을 콘솔에 출력한다.

개발 중 문제를 찾을 때는 유용하지만, 운영 환경에서는 로그가 지나치게 많아질 수 있다.

## 3. `settings` 객체

```python
settings = Settings()
```

프로그램 전체에서 공유할 실제 설정 객체를 한 번 만든다.

다른 파일에서는 다음처럼 사용한다.

```python
from .config import settings

print(settings.database_url)
```

## 4. 값이 정해지는 순서

```text
운영체제 환경 변수
→ .env 값
→ 코드에 작성된 기본값
```

앞쪽에서 값을 찾으면 그 값을 사용하고, 없으면 다음 기본값을 사용한다.

## 5. 주의할 점

- 비밀번호와 API 키는 코드에 직접 적지 말고 `.env` 또는 배포 환경 변수로 넣어야 한다.
- 설정을 추가하려면 `Settings` 클래스에 타입과 기본값을 함께 선언하는 것이 좋다.
- `db_echo=True`는 SQL에 포함된 값이 로그에 나타날 수 있으므로 운영 환경에서 주의해야 한다.



---

# `core/db.py`

## 전체 코드

```python
"""프로젝트 공통 DB 세션. 기능별 api.py 는 여기 get_db 를 import 해서 Depends 에 건다.

커밋은 라우트가 직접 한다(`await db.commit()`). 여기서는 세션을 열고 닫기만 한다.
플레이그라운드(dev/)는 `app.dependency_overrides[get_db] = ...` 로 SQLite 로 갈아끼운다.

Base 도 여기 둔다. 기능마다 Base 를 따로 만들면 simulation → personas 처럼
기능 간 FK 를 걸 때 MetaData 가 달라 테이블을 못 찾는다. 모든 features/*/models.py 가 이 Base 를 쓴다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,  # 끊긴 커넥션을 풀에서 꺼내 쓰다 죽는 걸 막는다
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as db:
        yield db
```

## 1. 파일의 역할

`db.py`는 프로젝트 전체가 함께 사용하는 **데이터베이스 연결과 비동기 세션 생성 방법**을 정의한다.

이 파일은 다음 구성 요소를 제공한다.

| 이름 | 역할 |
|---|---|
| `Base` | 모든 SQLAlchemy DB 모델의 공통 부모 클래스 |
| `engine` | 실제 데이터베이스 연결을 관리하는 엔진 |
| `SessionLocal` | 요청마다 사용할 DB 세션을 만드는 도구 |
| `get_db()` | FastAPI 라우트에 DB 세션을 전달하는 함수 |

## 2. `Base` 클래스

```python
class Base(DeclarativeBase):
    pass
```

모든 DB 테이블 모델이 상속하는 공통 기반 클래스이다.

예를 들어 `OnboardingSession`, `ConversationTurn`, `PersonaRecord`가 이 `Base`를 상속한다.

기능마다 별도의 `Base`를 만들지 않는 이유는 다음과 같다.

- 모든 테이블이 같은 SQLAlchemy 메타데이터에 등록된다.
- persona, simulation, practice처럼 서로 다른 기능의 테이블 사이에도 외래 키를 연결할 수 있다.
- 마이그레이션 도구가 전체 테이블을 한 번에 찾을 수 있다.

## 3. `engine`

```python
engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
)
```

데이터베이스와 비동기로 통신하는 엔진이다.

| 옵션 | 의미 |
|---|---|
| `settings.database_url` | 접속할 DB 주소 |
| `echo` | 실행되는 SQL을 로그에 출력할지 결정 |
| `pool_pre_ping=True` | 보관 중이던 DB 연결이 살아 있는지 사용 전에 확인 |

`pool_pre_ping=True`는 오래되어 끊어진 연결을 그대로 사용하다가 요청이 실패하는 일을 줄인다.

## 4. `SessionLocal`

```python
SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
```

`AsyncSession`을 만들어주는 세션 팩토리이다.

`expire_on_commit=False`는 커밋한 뒤에도 이미 읽어온 객체의 값을 계속 사용할 수 있게 한다. 이 옵션이 없으면 커밋 이후 속성을 다시 읽을 때 DB 조회가 필요할 수 있다.

## 5. `get_db()` 함수

```python
async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as db:
        yield db
```

FastAPI 요청 하나에 사용할 DB 세션을 열고 전달한 뒤 자동으로 닫는다.

### 처리 순서

1. `SessionLocal()`로 새 비동기 세션을 만든다.
2. `yield db`로 API 함수에 세션을 전달한다.
3. API 처리가 끝나면 `async with`가 세션을 닫는다.

API에서는 다음처럼 사용한다.

```python
async def start(db: AsyncSession = Depends(get_db)):
    ...
```

### 커밋은 하지 않는다

`get_db()`는 세션을 열고 닫는 일만 한다. 데이터 변경을 확정하는 `commit()`은 각 API 라우트가 직접 호출한다.

```python
await db.commit()
```

실패한 변경을 취소할 때는 다음을 사용한다.

```python
await db.rollback()
```

## 6. 전체 흐름

```text
HTTP 요청 시작
→ get_db()가 AsyncSession 생성
→ API가 service/repository에 세션 전달
→ repository가 SQL 실행
→ API가 commit 또는 rollback
→ 요청 종료 후 세션 자동 닫힘
```

## 7. 주의할 점

- `flush()`는 SQL을 DB로 보내지만 최종 확정은 하지 않는다. `commit()`과 다르다.
- 하나의 HTTP 요청 안에서는 같은 DB 세션을 공유해야 작업 단위가 자연스럽게 묶인다.
- 공통 `Base`를 기능 폴더마다 새로 만들면 기능 간 외래 키 연결이 깨질 수 있다.



---

# `features/persona/schemas.py`

## 전체 코드

```python
"""도메인 정의 + API 입출력 모델.

차원과 주제를 여기 한 곳에 둔다. agents.py의 프롬프트는
이 정의에서 자동 생성되므로 차원을 추가할 때 프롬프트를 따로 고치지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum

from pydantic import BaseModel, Field, model_validator

# ══ 차원 정의 ══════════════════════════════════════════════


@dataclass(frozen=True)
class Dimension:
    area: str
    label: str
    low: str = ""
    high: str = ""


SCORED: dict[str, Dimension] = {
    "avoidance": Dimension(
        area="intimacy",
        label="거리 두기",
        low="웬만한 건 공유하고 함께 있는 시간을 선호",
        high="각자 생활을 중시하고 독립적 거리를 유지",
    ),
    "anxiety": Dimension(
        area="intimacy",
        label="관계 불안",
        low="상대 반응이 늦어도 크게 동요하지 않음",
        high="반응이 미지근하면 관계를 의심하고 신경 쓰임",
    ),
    "disclosure": Dimension(
        area="communication",
        label="자기·감정 표현",
        low="속내를 잘 꺼내지 않음",
        high="느낀 것을 말로 표현하는 편",
    ),
    "openness": Dimension(
        area="communication",
        label="솔직한 관계 대화",
        low="관계에 대한 얘기를 꺼내는 걸 부담스러워함",
        high="'우리 어떤지' 같은 얘기를 먼저 꺼냄",
    ),
    "positivity": Dimension(
        area="communication",
        label="긍정적 상호작용",
        low="무덤덤하고 건조한 톤",
        high="밝고 다정한 표현이 잦음",
    ),
    "assurances": Dimension(
        area="communication",
        label="관계 확신 표현",
        low="미래나 마음을 말로 잘 표현하지 않음",
        high="'오래 보자' 같은 말을 먼저 함",
    ),
    "contact_rhythm": Dimension(
        area="communication",
        label="연락 빈도",
        low="할 말 있을 때만 연락",
        high="하루 종일 수시로 주고받기",
    ),
    "problem_solving": Dimension(
        area="conflict",
        label="문제 해결",
        low="갈등을 덮거나 흐지부지 넘김",
        high="원인을 짚고 중간 지점을 찾음",
    ),
    "withdrawal": Dimension(
        area="conflict",
        label="회피·철수",
        low="그 자리에서 계속 대화",
        high="입을 닫거나 자리를 뜸",
    ),
    "engagement": Dimension(
        area="conflict",
        label="감정적 맞대응",
        low="감정을 올리지 않음",
        high="목소리가 커지거나 쏘아붙임",
    ),
    "compliance": Dimension(
        area="conflict",
        label="일방적 수용",
        low="아닌 건 아니라고 말함",
        high="갈등을 피하려 무조건 맞춰줌",
    ),
    "ideal_warmth": Dimension(
        area="ideal",
        label="신뢰·배려",
        low="상대의 성실함·배려를 크게 따지지 않음",
        high="약속을 지키고 남을 배려하는 태도를 최우선으로 봄",
    ),
    "ideal_vitality": Dimension(
        area="ideal",
        label="매력·활발함",
        low="외적 매력이나 텐션을 거의 안 봄",
        high="밝고 활발한 분위기를 중요하게 봄",
    ),
    "ideal_status": Dimension(
        area="ideal",
        label="능력·안정성",
        low="조건을 거의 보지 않음",
        high="직업·경제력 등 안정성을 중요하게 봄",
    ),
    "seriousness": Dimension(
        area="orientation",
        label="관계 진지도",
        low="가볍게 알아가기",
        high="진지하게 오래 만날 사람을 찾는 중",
    ),
}

TEXTUAL: dict[str, str] = {
    "interests": "관심사",
    "routine": "일상",
    "date_prefer": "선호 데이트",
    "date_avoid": "피하고 싶은 것",
}

# 근거 부족 시 기본값. 0이나 None이 아닌 이유는
# 매칭 계산이 "모름"을 극단값으로 오해하지 않게 하기 위함.
DEFAULT_SCORE = 50

ALL_DIMENSIONS: list[str] = [*SCORED.keys(), *TEXTUAL.keys()]


# ══ 주제 정의 ══════════════════════════════════════════════


class Weight(IntEnum):
    LIGHT = 0
    MEDIUM = 1
    HEAVY = 2


@dataclass(frozen=True)
class Topic:
    id: str
    weight: Weight
    intent: str  # LLM에 전달되는 "이번에 알아낼 것"
    seed: str  # LLM 실패 시 그대로 쓰는 폴백 질문
    covers: tuple[str, ...]
    also_touches: tuple[str, ...] = ()
    choices: tuple[str, ...] | None = None
    is_closing: bool = False
    # 하루가 문 여는 한 줄. LLM 에 "참고만" 으로 전달된다.
    # 연애관 주제는 하루의 입장 대신 제3자 얘기로 — 사용자 답을 유도하지 않기 위해.
    # 주제 순서가 동적이라 "아까 ~ 얘기" 같은 순서 의존 표현은 쓰지 않는다.
    opener: str = ""


TOPICS: list[Topic] = [
    Topic(
        id="interests",
        weight=Weight.LIGHT,
        opener="저는 요즘 밤 산책에 빠져서 시간을 제일 많이 써요.",
        covers=("interests",),
        intent="요즘 시간을 많이 쓰는 취미·관심사와 그게 좋은 이유",
        seed="요즘 시간을 가장 많이 쓰는 취미나 관심사가 뭐예요?",
    ),
    Topic(
        id="weekend",
        weight=Weight.LIGHT,
        opener="저는 약속 없는 주말이면 늦잠이 먼저예요.",
        covers=("routine", "date_prefer", "date_avoid"),
        intent="약속 없는 주말을 보내는 방식 + 하고 싶은 데이트 하나, 피하고 싶은 것 하나",
        seed="아무 약속 없는 주말은 보통 어떻게 보내세요?",
    ),
    Topic(
        id="contact",
        weight=Weight.MEDIUM,
        opener="주변 보면 연락 스타일이 진짜 갈리더라고요, 하루 종일 톡 하는 사람이랑 할 말 있을 때만 하는 사람.",
        covers=("contact_rhythm",),
        also_touches=("avoidance",),
        intent="연락 빈도 선호 — 수시로 vs 할 말 있을 때",
        seed="연락은 자주 주고받는 편이 좋아요, 할 말 있을 때가 좋아요?",
    ),
    Topic(
        id="share_vs_separate",
        weight=Weight.MEDIUM,
        opener="연애하면 다 같이 하는 커플도 있고 각자 시간 챙기는 커플도 있잖아요.",
        covers=("avoidance",),
        also_touches=("disclosure",),
        intent="연애할 때 공유 중심인지 각자 생활 중심인지",
        seed="연애하면 웬만한 일은 공유하는 편이에요, 각자 생활이 있는 게 좋아요?",
    ),
    Topic(
        id="hard_times",
        weight=Weight.MEDIUM,
        opener="힘든 일 있을 때 바로 말하는 사람도 있고 혼자 정리하고 말하는 사람도 있더라고요.",
        covers=("disclosure", "openness"),
        also_touches=("positivity",),
        intent="힘든 일이나 관계 고민을 바로 말하는지 혼자 정리 후 말하는지",
        seed="힘든 일이 있을 때 연인에게 바로 말하는 편이에요?",
    ),
    Topic(
        id="slow_reply",
        weight=Weight.MEDIUM,
        opener="답장 늦을 때 드는 생각, 다들 한 번씩 겪잖아요.",
        covers=("anxiety",),
        also_touches=("contact_rhythm",),
        intent="상대 반응이 늦거나 미지근할 때 드는 생각",
        seed="상대 답장이 늦을 때 보통 어떤 생각이 들어요?",
    ),
    Topic(
        id="disagreement",
        weight=Weight.HEAVY,
        opener="소개팅에서 이런 거 물어보면 좀 이상한데, 그래서 더 궁금해요.",
        covers=("problem_solving", "withdrawal", "engagement"),
        intent="의견이 부딪쳤을 때의 행동 + 최근 사례",
        seed="연인과 의견이 부딪쳤을 때 보통 어떻게 행동해요?",
    ),
    Topic(
        id="receiving_hurt",
        weight=Weight.HEAVY,
        opener="반대 상황도 있잖아요, 상대가 나한테 서운하다고 할 때.",
        covers=("compliance",),
        also_touches=("problem_solving", "withdrawal"),
        intent="상대가 서운함을 표현했을 때의 반응",
        seed="상대가 서운함을 표현하면 어떻게 반응하는 편이에요?",
    ),
    Topic(
        id="ideal_type",
        weight=Weight.LIGHT,
        opener="사람 볼 때 제일 먼저 보게 되는 거, 다들 하나씩 있잖아요.",
        covers=("ideal_warmth", "ideal_vitality", "ideal_status"),
        intent="사람 볼 때 먼저 보는 것 + 없으면 안 되는 것 하나",
        seed="사람을 볼 때 가장 먼저 보게 되는 게 뭐예요?",
    ),
    Topic(
        id="orientation",
        weight=Weight.LIGHT,
        is_closing=True,
        opener="오늘 얘기 재밌었어요. 마지막으로 하나만.",
        covers=("seriousness",),
        also_touches=("assurances", "openness"),
        intent="지금 원하는 관계의 온도",
        seed="지금은 어떤 연애를 하고 싶어요?",
        choices=("진지하게 만날 사람", "편하게 알아가기", "아직 잘 모르겠어요"),
    ),
]

TOPICS_BY_ID = {t.id: t for t in TOPICS}


# ══ 보강 질문 은행 ═════════════════════════════════════════
# 근거가 부족한 차원(confidence LOW/MEDIUM)을 채우는 질문. 차원당 2개, 위에서부터 순서대로 쓴다.
# 하루 말투. 온보딩 주제와 겹치지 않게, 그 차원만 정확히 겨눈다.

SUPPLEMENTS: dict[str, tuple[str, ...]] = {
    "avoidance": (
        "연애 중에도 혼자만의 시간이 꼭 필요한 편이에요, 아니면 같이 있는 게 더 편해요?",
        "연인이 갑자기 '오늘 저녁에 볼까?' 하면 보통 어떤 마음이 먼저 들어요?",
    ),
    "anxiety": (
        "상대가 평소보다 말이 짧아지면 무슨 생각이 먼저 들어요?",
        "'우리 괜찮은 거지?' 같은 확인을 하고 싶어질 때가 있어요?",
    ),
    "disclosure": (
        "기분이 안 좋은 날, 연인한테 그걸 티 내는 편이에요 아니면 숨기는 편이에요?",
        "좋아하는 마음은 말로 표현하는 편이에요, 행동으로 보여주는 편이에요?",
    ),
    "openness": (
        "'우리 요즘 어때?' 같은 얘기, 먼저 꺼내는 편이에요?",
        "관계에서 불편한 게 생기면 바로 얘기해요, 아니면 좀 지켜봐요?",
    ),
    "positivity": (
        "연인이랑 있을 때 장난이나 농담을 많이 치는 편이에요?",
        "평소 메시지 톤이 어때요 — 느낌표랑 ㅋㅋ 많이 쓰는 편이에요?",
    ),
    "assurances": (
        "마음에 드는 사람한테 '다음에 또 봐요' 같은 말, 먼저 하는 편이에요?",
        "'오래 보고 싶다' 같은 얘기를 연애 초반에 하는 편이에요?",
    ),
    "contact_rhythm": (
        "일하는 중에 연인 연락이 오면 바로 답해요, 아니면 모아서 답해요?",
        "하루에 연락 몇 번 정도가 딱 편해요?",
    ),
    "problem_solving": (
        "다툰 뒤에 '그래서 다음엔 어떻게 할까'까지 얘기하는 편이에요?",
        "의견이 갈릴 때 중간 지점을 찾으려고 해요, 아니면 한쪽으로 정리해요?",
    ),
    "withdrawal": (
        "감정이 올라올 때 자리를 잠깐 뜨는 편이에요?",
        "말다툼 중에 입을 닫아버린 적 있어요? 그때 어땠어요?",
    ),
    "engagement": (
        "화가 나면 목소리가 커지는 편이에요?",
        "다툴 때 하고 싶은 말을 다 쏟아내는 편이에요, 참는 편이에요?",
    ),
    "compliance": (
        "다툼을 빨리 끝내려고 그냥 맞춰준 적 있어요?",
        "아닌 건 아니라고 말하는 편이에요, 상대 기분 봐서 넘기는 편이에요?",
    ),
    "ideal_warmth": (
        "약속 시간에 자주 늦는 사람, 얼마나 신경 쓰여요?",
        "직원한테 무례한 사람을 보면 어떤 생각이 들어요?",
    ),
    "ideal_vitality": (
        "조용한 사람이랑 활발한 사람 중에 더 끌리는 쪽이 있어요?",
        "첫인상에서 외적인 분위기를 얼마나 봐요?",
    ),
    "ideal_status": (
        "상대의 직업이나 경제력, 솔직히 얼마나 봐요?",
        "'안정적인 사람'이란 말 들으면 뭐가 떠올라요?",
    ),
    "seriousness": (
        "지금 만나면 결혼까지 생각하면서 만나는 편이에요?",
        "가볍게 시작해서 진지해지는 것도 괜찮아요, 처음부터 진지한 게 좋아요?",
    ),
}
assert set(SUPPLEMENTS) == set(SCORED), "보강 질문은 점수 차원 전부에 있어야 한다"


# ══ LLM 출력 검증 ══════════════════════════════════════════
# 모델이 "높음"이나 120을 뱉는 일이 실제로 생긴다.
# 매칭 알고리즘에 들어가기 전 경계에서 막는다.


class Narrative(BaseModel):
    """사용자에게 보여주는 서술. 점수와 같은 호출에서 LLM이 쓴다.

    화면에는 이것이 먼저 보이고 점수는 뒤로 간다 — 사용자는 차트가 아니라
    "○○님은 이런 편이에요"를 읽는다.
    """

    model_config = {"extra": "ignore"}

    headline: str = Field(max_length=60)  # "독립적이지만 대화가 잘 통하는 관계를 원하는 타입"
    body: str = Field(max_length=800)  # 3~5문장, "~하는 편이에요" 톤
    traits: list[str] = Field(default_factory=list, max_length=6)  # 한 줄짜리 특징 3~5개


class RawExtraction(BaseModel):
    """추출 LLM의 원본 출력. 근거를 못 찾은 차원은 키가 없다."""

    model_config = {"extra": "ignore"}  # 모델이 지어낸 키는 버린다

    avoidance: int | None = Field(default=None, ge=0, le=100)
    anxiety: int | None = Field(default=None, ge=0, le=100)
    disclosure: int | None = Field(default=None, ge=0, le=100)
    openness: int | None = Field(default=None, ge=0, le=100)
    positivity: int | None = Field(default=None, ge=0, le=100)
    assurances: int | None = Field(default=None, ge=0, le=100)
    contact_rhythm: int | None = Field(default=None, ge=0, le=100)
    problem_solving: int | None = Field(default=None, ge=0, le=100)
    withdrawal: int | None = Field(default=None, ge=0, le=100)
    engagement: int | None = Field(default=None, ge=0, le=100)
    compliance: int | None = Field(default=None, ge=0, le=100)
    ideal_warmth: int | None = Field(default=None, ge=0, le=100)
    ideal_vitality: int | None = Field(default=None, ge=0, le=100)
    ideal_status: int | None = Field(default=None, ge=0, le=100)
    seriousness: int | None = Field(default=None, ge=0, le=100)

    interests: list[str] = Field(default_factory=list)
    routine: list[str] = Field(default_factory=list)
    date_prefer: list[str] = Field(default_factory=list)
    date_avoid: list[str] = Field(default_factory=list)

    narrative: Narrative | None = None


class Tags(BaseModel):
    """턴별 태깅 결과."""

    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    off_topic: bool = False


# ══ API 입출력 ═════════════════════════════════════════════


class StartRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=20)
    total_turns: int = Field(default=10, ge=5, le=15)
    # 앱 사용자 식별자. 시뮬레이션·연습대화가 "이 사용자의 페르소나"를 찾을 때 쓴다.
    # 없으면 session_id / persona_id 로만 찾을 수 있다.
    user_id: str | None = Field(default=None, max_length=64)


class AnswerRequest(BaseModel):
    # 문서 §6: 1~200자, 최소 2자
    answer: str = Field(min_length=2, max_length=200)


# 이 수 이상 답하면 건너뛰기·끝내기가 열린다. 그 밑이면 페르소나가 너무 비어서 의미가 없다.
MIN_ANSWERS_TO_FINISH = 3


class TurnResponse(BaseModel):
    session_id: str
    utterance: str
    choices: list[str] | None = None
    progress: str
    done: bool = False
    answered: int = 0  # 실제로 답한 턴 수 (건너뛴 건 제외)
    can_skip: bool = False  # 이 질문 건너뛰기 가능
    can_finish: bool = False  # 여기서 대화 끝내고 바로 페르소나 만들기 가능


# 신뢰도 — 주 근거 건수로. 사용자에게는 등급명이 아니라 CONFIDENCE_LABEL 로 보여준다.
CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH = "LOW", "MEDIUM", "HIGH"
CONFIDENCE_LABEL = {
    CONFIDENCE_LOW: "아직 잘 몰라요",
    CONFIDENCE_MEDIUM: "어느 정도 알아요",
    CONFIDENCE_HIGH: "잘 알아요",
}
# 정확도 게이지 가중치. "대화할수록 올라가는 숫자" 하나를 만들기 위한 것이지 측정치가 아니다.
CONFIDENCE_WEIGHT = {CONFIDENCE_LOW: 0.0, CONFIDENCE_MEDIUM: 0.6, CONFIDENCE_HIGH: 1.0}


class Gap(BaseModel):
    """아직 근거가 부족한 차원 + 그걸 채울 다음 보강 질문."""

    dimension: str
    label: str
    area: str
    confidence: str
    confidence_label: str
    question: str | None  # 보강 질문을 다 썼으면 None


class Change(BaseModel):
    """이전 버전 대비 달라진 것. kind: score(±10 이상) | confidence(등급 변화)"""

    dimension: str
    label: str
    kind: str
    before: str
    after: str


class PersonaResponse(BaseModel):
    persona_id: str
    version: int = 1
    scores: dict[str, int]
    interests: list[str] = Field(default_factory=list)
    routine: list[str] = Field(default_factory=list)
    date_prefer: list[str] = Field(default_factory=list)
    date_avoid: list[str] = Field(default_factory=list)
    confidence: dict[str, str] = Field(default_factory=dict)  # {차원: LOW|MEDIUM|HIGH}
    narrative: Narrative | None = None  # 점수와 모순되면 service 가 None 으로 떨어뜨림
    accuracy: int = 0  # 0~100. confidence 가중 평균
    gaps: list[Gap] = Field(default_factory=list)  # LOW 먼저, 그다음 MEDIUM
    changes: list[Change] = Field(default_factory=list)  # 이전 버전 대비
    confirmed: bool = False  # 사용자가 "이대로 좋아요" 했는지
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SupplementRequest(BaseModel):
    dimension: str
    answer: str = Field(min_length=2, max_length=200)


class FeedbackRequest(BaseModel):
    agree: bool
    area: str | None = None  # agree=False 일 때, 어느 영역이 다른지 (intimacy · communication · …)


class HistoryItem(BaseModel):
    persona_id: str
    version: int
    accuracy: int
    headline: str | None
    confirmed: bool
    created_at: datetime


# ══ 다른 기능이 페르소나를 가리킬 때 ═══════════════════════
# simulation·practice 는 "저장된 페르소나"만 쓴다. 셋 중 하나로 가리키면 최신 버전을 꺼낸다.


class PersonaRef(BaseModel):
    """persona_id(특정 버전) · user_id(그 사용자의 최신) · session_id(그 온보딩의 최신) 중 정확히 하나."""

    persona_id: str | None = Field(default=None, max_length=32)
    user_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _exactly_one(self) -> PersonaRef:
        given = [k for k in ("persona_id", "user_id", "session_id") if getattr(self, k)]
        if len(given) != 1:
            raise ValueError("persona_id, user_id, session_id 중 하나만 지정하세요")
        return self

    def describe(self) -> str:
        return self.persona_id or self.user_id or self.session_id or "?"


class PersonaBrief(BaseModel):
    """시뮬레이션·연습대화 응답에 실리는 참가자 요약. 점수는 안 보여준다 — 리포트가 따로 있다."""

    persona_id: str
    user_id: str | None = None
    nickname: str
    version: int = 1
    headline: str | None = None
    accuracy: int = 0
```

## 1. 파일의 역할

`schemas.py`는 persona 기능에서 사용하는 **데이터 모양과 공통 기준을 한곳에 정의하는 파일**이다.

크게 네 가지가 들어 있다.

1. 어떤 성향을 점수로 볼지 정의
2. 온보딩에서 어떤 주제로 질문할지 정의
3. LLM 응답이 올바른지 검증하는 모델
4. API 요청과 응답의 데이터 형식

다른 파일들이 같은 이름과 기준을 공유하므로, 성향을 추가하거나 응답 구조를 바꿀 때 가장 먼저 확인해야 하는 파일이다.

## 2. `Dimension` 데이터 클래스

```python
@dataclass(frozen=True)
class Dimension:
```

점수로 측정하는 성향 한 개의 설명을 담는다.

| 필드 | 의미 |
|---|---|
| `area` | 성향이 속한 큰 영역 |
| `label` | 사용자에게 보여줄 한글 이름 |
| `low` | 점수가 낮을 때의 행동 설명 |
| `high` | 점수가 높을 때의 행동 설명 |

예를 들어 연락 빈도는 다음처럼 표현된다.

```python
Dimension(
    area="communication",
    label="연락 빈도",
    low="할 말 있을 때만 연락",
    high="하루 종일 수시로 주고받기",
)
```

`frozen=True`이므로 만든 뒤 값을 실수로 바꾸기 어렵게 한다.

## 3. `SCORED`

```python
SCORED: dict[str, Dimension]
```

0~100 점수로 저장하는 모든 성향의 사전이다.

| 키 | 한글 이름 | 영역 | 높은 점수의 뜻 |
|---|---|---|---|
| `avoidance` | 거리 두기 | intimacy | 각자 생활과 독립적인 거리 중시 |
| `anxiety` | 관계 불안 | intimacy | 상대 반응에 민감하고 관계를 걱정함 |
| `disclosure` | 자기·감정 표현 | communication | 느낀 것을 말로 표현함 |
| `openness` | 솔직한 관계 대화 | communication | 관계 이야기를 먼저 꺼냄 |
| `positivity` | 긍정적 상호작용 | communication | 밝고 다정한 표현이 많음 |
| `assurances` | 관계 확신 표현 | communication | 마음과 미래를 말로 표현함 |
| `contact_rhythm` | 연락 빈도 | communication | 자주 연락함 |
| `problem_solving` | 문제 해결 | conflict | 갈등 원인과 해결점을 찾음 |
| `withdrawal` | 회피·철수 | conflict | 갈등 중 말을 닫거나 자리를 뜸 |
| `engagement` | 감정적 맞대응 | conflict | 목소리를 높이거나 맞받아침 |
| `compliance` | 일방적 수용 | conflict | 갈등을 피하려 상대에게 맞춤 |
| `ideal_warmth` | 신뢰·배려 | ideal | 성실함과 배려를 중요하게 봄 |
| `ideal_vitality` | 매력·활발함 | ideal | 밝고 활발한 분위기를 중요하게 봄 |
| `ideal_status` | 능력·안정성 | ideal | 직업·경제적 안정성을 중요하게 봄 |
| `seriousness` | 관계 진지도 | orientation | 오래 만날 진지한 관계를 원함 |

`agents.py`는 이 정의로 LLM 평가 기준을 만들고, `service.py`는 이 키들을 이용해 점수와 신뢰도를 계산한다.

## 4. `TEXTUAL`

점수 대신 문자열 목록으로 추출할 항목이다.

| 키 | 의미 |
|---|---|
| `interests` | 취미와 관심사 |
| `routine` | 일상과 생활 패턴 |
| `date_prefer` | 좋아하는 데이트 |
| `date_avoid` | 피하고 싶은 활동 |

예를 들어 다음처럼 저장된다.

```json
{
  "interests": ["러닝", "영화"],
  "date_prefer": ["조용한 카페"]
}
```

## 5. 공통 차원 상수

### `DEFAULT_SCORE`

```python
DEFAULT_SCORE = 50
```

LLM이 어떤 성향에 대한 근거를 찾지 못했을 때 넣는 기본 점수이다. `0`을 사용하면 “근거 없음”이 “매우 낮은 성향”으로 잘못 해석될 수 있어 중간값 50을 사용한다.

### `ALL_DIMENSIONS`

```python
ALL_DIMENSIONS = [*SCORED.keys(), *TEXTUAL.keys()]
```

점수형과 텍스트형 항목의 모든 키를 하나의 리스트로 합친다. 태깅 결과 검증과 커버리지 초기화에 사용한다.

## 6. `Weight` 열거형

```python
class Weight(IntEnum):
    LIGHT = 0
    MEDIUM = 1
    HEAVY = 2
```

대화 주제의 무게를 숫자로 나타낸다.

- `LIGHT`: 가벼운 주제. 첫 대화에도 사용 가능
- `MEDIUM`: 중간 정도의 관계 질문
- `HEAVY`: 갈등처럼 조심스러운 주제. 중반 이후 사용

`IntEnum`이라 숫자처럼 비교할 수 있어 같은 조건이면 가벼운 주제를 우선 선택할 수 있다.

## 7. `Topic` 데이터 클래스

온보딩에서 다룰 대화 주제 하나를 나타낸다.

| 필드 | 의미 |
|---|---|
| `id` | 주제를 구분하는 고유 이름 |
| `weight` | 주제의 무게 |
| `intent` | LLM이 이번 턴에서 알아낼 내용 |
| `seed` | LLM 실패 시 사용할 기본 질문 |
| `covers` | 이 주제가 직접 채우는 성향 |
| `also_touches` | 함께 간접적으로 다룰 수 있는 성향 |
| `choices` | 사용자에게 보여줄 선택지 |
| `is_closing` | 마지막 턴 전용 주제인지 여부 |
| `opener` | 대화를 자연스럽게 여는 참고 문장 |

`Topic`도 `frozen=True`이므로 정의된 주제가 실행 중에 변경되지 않게 한다.

## 8. `TOPICS`와 `TOPICS_BY_ID`

`TOPICS`는 실제 온보딩에서 사용할 주제 목록이다.

| 주제 ID | 무게 | 주로 알아내는 내용 |
|---|---|---|
| `interests` | LIGHT | 취미와 관심사 |
| `weekend` | LIGHT | 일상과 데이트 취향 |
| `contact` | MEDIUM | 선호 연락 빈도 |
| `share_vs_separate` | MEDIUM | 함께하기와 각자 생활의 균형 |
| `hard_times` | MEDIUM | 힘든 일을 표현하는 방식 |
| `slow_reply` | MEDIUM | 답장이 늦을 때 느끼는 불안 |
| `disagreement` | HEAVY | 의견 충돌 시 문제 해결·회피·맞대응 |
| `receiving_hurt` | HEAVY | 상대가 서운함을 말할 때의 반응 |
| `ideal_type` | LIGHT | 이상형에서 중요하게 보는 요소 |
| `orientation` | LIGHT | 원하는 관계의 진지함. 마지막 턴 전용 |

`TOPICS_BY_ID`는 주제 ID로 빠르게 `Topic`을 찾기 위한 사전이다.

```python
TOPICS_BY_ID["contact"]
```

## 9. `SUPPLEMENTS`

최초 분석 후 근거가 부족한 점수형 성향을 더 확인하기 위한 보강 질문 목록이다.

- 모든 `SCORED` 차원마다 질문 두 개가 있다.
- 앞쪽 질문부터 사용한다.
- 이미 사용한 질문은 건너뛴다.
- 보강 답변을 받으면 페르소나를 새 버전으로 다시 만든다.

다음 검사는 빠진 성향이 없는지 프로그램 시작 시 확인한다.

```python
assert set(SUPPLEMENTS) == set(SCORED)
```

키가 다르면 즉시 오류가 나므로, 새 점수형 성향을 추가할 때 보강 질문도 함께 추가해야 한다.

## 10. LLM 출력 검증 모델

### `Narrative`

사용자에게 보여줄 말로 된 페르소나 설명이다.

| 필드 | 제한 | 의미 |
|---|---|---|
| `headline` | 최대 60자 | 한 줄 요약 |
| `body` | 최대 800자 | 3~5문장 정도의 설명 |
| `traits` | 최대 6개 | 짧은 특징 목록 |

`model_config = {"extra": "ignore"}` 때문에 LLM이 정의되지 않은 필드를 추가해도 그 필드는 버린다.

### `RawExtraction`

전체 대화를 읽은 LLM의 원본 분석 결과를 검증한다.

#### 점수형 필드

- 각 점수는 `int | None`
- 최소 0, 최대 100
- 근거가 없으면 `None`

LLM이 `120`이나 `"높음"`을 반환하면 Pydantic 검증이 실패한다.

#### 텍스트형 필드

관심사, 일상, 선호 데이트, 피하는 것을 문자열 리스트로 받는다. 값이 없으면 빈 리스트를 자동 생성한다.

#### `narrative`

사용자가 읽을 설명문이다. 생성되지 않았으면 `None`일 수 있다.

### `Tags`

답변 한 개를 어떤 성향과 연결할지 담는다.

| 필드 | 의미 |
|---|---|
| `primary` | 답변이 직접 알려주는 성향 |
| `secondary` | 간접적으로 추론할 수 있는 성향 |
| `off_topic` | 질문과 무관한 답변인지 여부 |

## 11. 온보딩 API 요청·응답 모델

### `StartRequest`

온보딩 시작 요청이다.

- `nickname`: 1~20자
- `total_turns`: 기본 10, 최소 5, 최대 15
- `user_id`: 선택값, 최대 64자

### `AnswerRequest`

현재 질문에 대한 답변 요청이다.

- `answer`: 최소 2자, 최대 200자

### `MIN_ANSWERS_TO_FINISH`

```python
MIN_ANSWERS_TO_FINISH = 3
```

세 개 이상 답한 뒤부터 질문 건너뛰기와 조기 종료가 가능하다. 너무 적은 정보로 빈 페르소나가 만들어지는 것을 막는다.

### `TurnResponse`

온보딩 질문 한 턴의 API 응답이다.

| 필드 | 의미 |
|---|---|
| `session_id` | 온보딩 세션 ID |
| `utterance` | 사용자에게 보여줄 다음 말 |
| `choices` | 선택지가 있으면 목록, 없으면 `None` |
| `progress` | `현재/전체` 형식의 진행도 |
| `done` | 모든 질문이 끝났는지 여부 |
| `answered` | 실제로 답한 턴 수 |
| `can_skip` | 현재 질문을 건너뛸 수 있는지 여부 |
| `can_finish` | 지금 끝낼 수 있는지 여부 |

## 12. 신뢰도와 정확도 상수

### 신뢰도 등급

```text
LOW    → 아직 잘 몰라요
MEDIUM → 어느 정도 알아요
HIGH   → 잘 알아요
```

### `CONFIDENCE_WEIGHT`

정확도 게이지를 계산할 때 사용하는 가중치이다.

| 신뢰도 | 가중치 |
|---|---:|
| LOW | 0.0 |
| MEDIUM | 0.6 |
| HIGH | 1.0 |

이 정확도는 실제 통계 정확도가 아니라, 대화 근거가 얼마나 쌓였는지 사용자가 쉽게 볼 수 있게 만든 진행 지표이다.

## 13. 페르소나 결과 관련 모델

### `Gap`

근거가 부족한 성향과 다음 보강 질문을 담는다.

- 성향 키와 한글 이름
- 큰 영역
- 현재 신뢰도와 사용자용 문구
- 다음에 물을 보강 질문

보강 질문을 모두 사용했으면 `question=None`이다.

### `Change`

이전 페르소나 버전과 현재 버전 사이의 변화를 나타낸다.

- 점수가 10 이상 변한 경우 `kind="score"`
- 신뢰도 등급이 변한 경우 `kind="confidence"`
- `before`와 `after`에 이전·현재 값을 문자열로 저장

### `PersonaResponse`

클라이언트에 반환하는 완성된 페르소나 데이터이다.

포함 내용:

- 페르소나 ID와 버전
- 성향 점수
- 관심사, 일상, 데이트 취향
- 성향별 신뢰도
- 사용자용 설명문
- 전체 정확도 게이지
- 근거가 부족한 항목
- 이전 버전과 달라진 항목
- 사용자 확인 여부
- 생성 시각

### `SupplementRequest`

보강 답변 요청이다.

- `dimension`: 보강할 성향 키
- `answer`: 2~200자 답변

### `FeedbackRequest`

결과 확인 요청이다.

- `agree`: 결과에 동의하는지 여부
- `area`: 동의하지 않을 때 다르다고 느낀 영역

### `HistoryItem`

페르소나 버전 목록에 사용할 짧은 정보이다.

- 페르소나 ID
- 버전
- 정확도
- 한 줄 소개
- 확인 여부
- 생성 시각

## 14. 다른 기능에서 사용하는 참조 모델

### `PersonaRef`

simulation과 practice가 사용할 페르소나를 가리킨다.

다음 중 정확히 하나만 지정할 수 있다.

- `persona_id`: 특정 버전
- `user_id`: 그 사용자의 최신 버전
- `session_id`: 그 온보딩 세션의 최신 버전

#### `_exactly_one()`

Pydantic의 모델 검증 함수이다. 세 값 중 0개 또는 2개 이상이 들어오면 다음 오류를 발생시킨다.

```text
persona_id, user_id, session_id 중 하나만 지정하세요
```

#### `describe()`

세 필드 중 실제로 들어 있는 식별자를 문자열로 반환한다. 아무 값도 없으면 방어적으로 `?`를 반환하지만, 정상적인 모델 생성 과정에서는 검증 때문에 그런 상태가 만들어지지 않는다.

### `PersonaBrief`

시뮬레이션·연습 대화 응답에 넣는 짧은 참가자 정보이다.

점수 전체는 노출하지 않고 다음 정보만 제공한다.

- 페르소나 ID
- 사용자 ID
- 닉네임
- 버전
- 한 줄 소개
- 정확도

## 15. 파일 간 연결

```text
schemas.py
├─ agents.py: LLM 프롬프트와 응답 검증에 사용
├─ service.py: 주제 선택, 점수, 신뢰도 계산에 사용
├─ api.py: 요청·응답 모델로 사용
├─ profile.py: 점수를 말로 바꿀 때 사용
└─ lookup.py: 다른 기능에 페르소나를 전달할 때 사용
```

## 16. 변경할 때 주의할 점

- `SCORED`에 새 항목을 추가하면 `RawExtraction`, `SUPPLEMENTS`, 프롬프트 결과 형식도 함께 확인해야 한다.
- `TOPICS`의 `covers`와 `also_touches`에는 `ALL_DIMENSIONS`에 있는 키만 사용하는 것이 안전하다.
- API 모델의 필드 제한을 바꾸면 클라이언트가 보내거나 받는 데이터 형식도 달라진다.
- Pydantic 타입 힌트는 설명만이 아니라 실제 요청·응답 검증에도 사용된다.



---

# `features/persona/models.py`

## 전체 코드

```python
"""DB 테이블.

대화 원문을 보관하는 이유: 추출이 실패하면 재시도해야 하고,
루브릭을 고친 뒤 과거 대화로 재추출해서 품질을 비교해야 한다.

Base 는 app.core.db 의 것을 쓴다 (simulation·practice 가 personas 에 FK 를 건다).
`from app.features.persona.models import Base` 는 그대로 동작한다 — 플레이그라운드 호환.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    nickname: Mapped[str] = mapped_column(String(20))
    total_turns: Mapped[int] = mapped_column(Integer, default=10)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)

    # 지금 질문해두고 답변을 기다리는 주제의 id
    pending_topic_id: Mapped[str | None] = mapped_column(String(32))
    used_topic_ids: Mapped[list] = mapped_column(JSON, default=list)

    # {"primary": {차원: 건수}, "secondary": {...}}
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default="active")
    # active | completed | abandoned

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    turns: Mapped[list[ConversationTurn]] = relationship(
        back_populates="session",
        order_by="ConversationTurn.turn_index",
        cascade="all, delete-orphan",
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("onboarding_sessions.id", ondelete="CASCADE"), index=True)
    turn_index: Mapped[int] = mapped_column(Integer)

    topic_id: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)  # 사용자가 건너뛴 질문

    # "llm" | "seed" — 폴백 빈도를 나중에 세기 위해 남긴다
    question_source: Mapped[str] = mapped_column(String(8), default="llm")

    tags: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[OnboardingSession] = relationship(back_populates="turns")


class PersonaRecord(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("onboarding_sessions.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)

    scores: Mapped[dict] = mapped_column(JSON)
    texts: Mapped[dict] = mapped_column(JSON)  # interests/routine/date_*
    confidence: Mapped[dict] = mapped_column(JSON)  # {차원: "LOW"}
    narrative: Mapped[dict | None] = mapped_column(JSON)  # headline/body/traits

    # 같은 세션에서 재빌드할 때마다 새 행. 이전 행을 가리켜 "뭐가 바뀌었나"를 계산한다.
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_id: Mapped[str | None] = mapped_column(String(32))

    # 확인 루프 — "이대로 좋아요" 시각 / "다른 것 같아요" 면 어느 영역이 달랐는지
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[dict | None] = mapped_column(JSON)  # {"agree": bool, "area": str | None}

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

## 1. 파일의 역할

`models.py`는 persona 기능이 데이터베이스에 저장할 **테이블 구조**를 정의한다.

이 파일에는 세 개의 테이블 모델이 있다.

| 클래스 | DB 테이블 | 저장하는 내용 |
|---|---|---|
| `OnboardingSession` | `onboarding_sessions` | 온보딩 한 번의 전체 진행 상태 |
| `ConversationTurn` | `conversation_turns` | 질문과 답변 한 턴 |
| `PersonaRecord` | `personas` | 대화에서 만든 페르소나 한 버전 |

## 2. 보조 함수

### `_uuid()`

```python
def _uuid() -> str:
    return uuid.uuid4().hex
```

32자리 UUID 문자열을 만든다. 세션과 페르소나의 기본 ID로 사용한다.

이름 앞의 `_`는 이 파일 내부에서 사용하는 보조 함수라는 뜻이다.

### `_now()`

```python
def _now() -> datetime:
    return datetime.now(UTC)
```

현재 UTC 시각을 반환한다. 생성 시각, 수정 시각, 사용자 확인 시각을 저장할 때 사용한다.

서버가 어느 지역에서 실행되더라도 같은 기준으로 시간을 저장하기 위해 UTC를 사용한다.

## 3. `OnboardingSession`

사용자 한 명이 온보딩 대화를 시작해서 끝낼 때까지의 전체 상태를 저장한다.

### 기본 식별 정보

| 필드 | 의미 |
|---|---|
| `id` | 세션 고유 ID. `_uuid()`로 자동 생성 |
| `user_id` | 앱 사용자 ID. 없어도 됨 |
| `nickname` | 대화에서 부를 사용자 닉네임 |
| `created_at` | 세션 생성 시각 |
| `updated_at` | 마지막 변경 시각 |

### 진행 상태

| 필드 | 의미 |
|---|---|
| `total_turns` | 전체 질문 수. 기본값 10 |
| `turn_index` | 지금 진행 중인 턴 번호. 0부터 시작 |
| `pending_topic_id` | 현재 질문해 두고 답변을 기다리는 주제 ID |
| `used_topic_ids` | 이미 사용한 주제 ID 목록 |
| `status` | `active`, `completed`, `abandoned` 중 현재 상태 |

### 분석 상태

```python
coverage: Mapped[dict]
```

각 성향 차원에 대한 근거가 몇 번 나왔는지 JSON으로 저장한다.

```json
{
  "primary": {"contact_rhythm": 2},
  "secondary": {"avoidance": 1}
}
```

### `turns` 관계

```python
turns: Mapped[list[ConversationTurn]] = relationship(...)
```

이 세션에 속한 모든 `ConversationTurn`을 연결한다.

- `order_by`: 턴 번호 순으로 정렬한다.
- `back_populates`: 턴에서도 원래 세션을 찾을 수 있게 양방향 연결한다.
- `cascade="all, delete-orphan"`: 세션이 삭제되면 소속 턴도 함께 삭제한다.

## 4. `ConversationTurn`

질문 하나와 그에 대한 답변 하나를 저장한다.

| 필드 | 의미 |
|---|---|
| `id` | 자동 증가하는 턴 ID |
| `session_id` | 어느 온보딩 세션의 턴인지 표시 |
| `turn_index` | 해당 세션 안에서의 순서 |
| `topic_id` | 질문이 다루는 주제 ID |
| `question` | 사용자에게 보여준 질문 |
| `answer` | 사용자 답변. 아직 답하지 않았다면 `None` |
| `skipped` | 사용자가 질문을 건너뛰었는지 여부 |
| `question_source` | 질문 출처. `llm`, `seed`, `bank` 등 |
| `tags` | 답변에서 찾은 주요·보조 성향 차원 |
| `created_at` | 질문 생성 시각 |

### `question_source`

- `llm`: LLM이 정상적으로 생성한 질문
- `seed`: LLM 실패 후 미리 준비된 기본 질문
- `bank`: 보강 질문 목록에서 꺼낸 질문

질문 출처를 저장하면 나중에 LLM 실패 빈도와 폴백 사용량을 확인할 수 있다.

### `session` 관계

```python
session: Mapped[OnboardingSession] = relationship(back_populates="turns")
```

현재 턴에서 부모 `OnboardingSession`으로 이동할 수 있게 한다.

## 5. `PersonaRecord`

대화 분석 결과로 만들어진 페르소나 한 버전을 저장한다.

### 연결 정보

| 필드 | 의미 |
|---|---|
| `id` | 페르소나 버전의 고유 ID |
| `session_id` | 이 페르소나를 만든 온보딩 세션 |
| `user_id` | 앱 사용자 ID |

### 분석 결과

| 필드 | 의미 |
|---|---|
| `scores` | 성향별 0~100 점수 |
| `texts` | 관심사, 일상, 선호·비선호 데이트 목록 |
| `confidence` | 성향별 `LOW`, `MEDIUM`, `HIGH` 신뢰도 |
| `narrative` | headline, body, traits로 이루어진 설명문 |

예시는 다음과 같다.

```json
{
  "scores": {"avoidance": 70, "anxiety": 30},
  "texts": {"interests": ["러닝"]},
  "confidence": {"avoidance": "HIGH"},
  "narrative": {"headline": "독립적인 관계를 원하는 타입"}
}
```

### 버전 정보

| 필드 | 의미 |
|---|---|
| `version` | 같은 세션에서 만든 페르소나 버전 번호 |
| `previous_id` | 직전 버전의 페르소나 ID |

보강 답변을 받고 다시 빌드할 때 기존 행을 덮어쓰지 않고 새 행을 만든다. 그래서 이전 결과와 현재 결과의 차이를 비교할 수 있다.

### 사용자 확인 정보

| 필드 | 의미 |
|---|---|
| `confirmed_at` | 사용자가 “이대로 좋아요”라고 확인한 시각 |
| `feedback` | 동의 여부와 다르다고 느낀 영역 |

```json
{"agree": false, "area": "communication"}
```

## 6. 테이블 관계

```text
OnboardingSession 1개
├─ ConversationTurn 여러 개
└─ PersonaRecord 여러 버전
```

`ConversationTurn.session_id`는 세션 삭제 시 함께 삭제되도록 `ondelete="CASCADE"`가 설정되어 있다. `PersonaRecord`는 세션을 참조하지만 별도의 버전 기록으로 관리된다.

## 7. 주의할 점

- `Mapped[...]`는 파이썬 타입 설명이면서 SQLAlchemy의 컬럼·관계 선언에 사용된다.
- `JSON` 필드 안의 리스트나 딕셔너리를 제자리에서 바꾸면 SQLAlchemy가 변경을 놓칠 수 있다. repository에서는 새 객체를 통째로 다시 할당한다.
- 이 파일은 테이블 구조만 정의한다. 실제 조회와 저장은 `repository.py`가 담당한다.



---

# `features/persona/agents.py`

## 전체 코드

```python
"""LLM 호출부 - 대화 생성, 태깅, 특성 추출

프롬프트 - 세 가지 역할:
  - ConversationAgent : 다음 발화 생성 
  - TaggingAgent      : 턴별 경량 판정 (어떤 차원이 채워졌나)
  - ExtractionAgent   : 대화 전체 → 점수

대화와 추출을 한 호출에 합치지 않는다. 합치면 대화하느라 추출이
대충 되고, 추출 신경 쓰느라 말투가 딱딱해진다.
"""

from __future__ import annotations 
# 타입힌트를 선언 즉시 계산X, 나중에 해석하도록 만드는 설정

import asyncio # 비동기 작업 표준 라이브러리
import json # JSON 문자열 → dict, dict → JSON 문자열 변환
import logging
import os
import re # 문자열에서 특정 패턴을 찾거나 변경하는 정규표현식 모듈
from dataclasses import dataclass # 데이터 클래스를 간단하게 만들어 주는 데코레이터

from openai import AsyncOpenAI
from pydantic import ValidationError # Pydentic으로 데이터 검사 시 형식에 대한 예외처리 라이브러리


from .schemas import (
    ALL_DIMENSIONS, 
    SCORED, 
    TEXTUAL, 
    RawExtraction, 
    Tags,
    Topic,
)
"""
    # 현재 파일과 같은 패키지에 있는 schemas.py에서 필요한 값과 클래스를 가져온다.
    # . << 현재 패키지, import(...) << 안에 있는 이름들을 가져옴

    ALL_DIMENSIONS → SCORED, TEXTUAL의 모든 항목 이름을 합친 목록
    SCORED         → 점수로 평가하는 연애 성향 목록들
    TEXTUAL        → 글 목록으로 수집하는 항목들
    RawExtraction  → AI가 대화에서 추출한 점수와 관심사 등을 검증하고 담는 pydantic 모델
    Tags           → 사용자 답변이 어떤 주제와 관련되는지 담는 pydantic 모델
    Topic
"""

MODEL = os.getenv("OPENROUTER_MODEL")

_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

logger = logging.getLogger(__name__) 
# 현재 파일 전용 로거, __name__은 현재 모듈의 이름을 담고 있는 내장 변수, 로깅 메시지에 모듈 이름 포함시켜 구분

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
# JSON 마크다운 코드 블록 표시 제거하기 위한 정규표현식 패턴, re.MULTILINE → 여러 줄에 걸쳐 적용

class LLMError(Exception):
    """호출 실패 시 발생하는 예외, 사용자 정의"""
    pass


async def _call(
        *, 
        system: str, 
        messages: list[dict],
        max_tokens: int, 
        timeout: float
) -> str:
    request_messages = [
        {"role": "system", "content": system},
        *messages, # 현재까지의 대화 내용 / *가 없이 작성되면 구조가 잘못 중첩됨.
    ]

    try:
        # 오픈 라우터를 통해 지정한 모델에 대화 내용 전달 & 답변 생성 요청
        # AsyncOpenAI를 사용하므로 앞에 await 붙여야 함
        resp = await asyncio.wait_for( 
            _client.chat.completions.create(
                model=MODEL, 
                max_tokens=max_tokens, 
                messages=request_messages,
            ),
            timeout=timeout,
        )
    # str(e) => 오류 메세지를 문자열로 가져옴
    except TimeoutError as e: # 시간 초과
        raise LLMError(f"timeout after {timeout}s") from e
    except Exception as e:# 그 외 나머지 일반적 오류
        raise LLMError(str(e)) from e
        

    text = resp.choices[0].message.content # 생성된 답변 꺼내기

    if not text or not text.strip():
        raise LLMError("empty response")
    
    return text.strip()

"""
    resp = await _client.chat.completions.create(
        model="anthropic/claude-sonnet-4.6",
        messages=[
            {"role": "system", "content": "친절하게 답하세요."},
            {"role": "user", "content": "안녕하세요"},
        ],
        max_tokens=500,
    )

    # OpenRouter에 anthropic/claude-sonnet-4.6 모델 사용해서 이 대화에 대한 답변 최대 500토큰까지 만들어 << 요청
"""


# 함수 호출 시, 이름=값 형태로 전달한 인자들을 함수 내부에서 {'이름': '값'} 구조의 딕셔너리(dictionary)로 묶어서 처리
async def _call_json(**kwargs) -> dict:
    text = await _call(**kwargs) # kwargs 딕셔너리를 다시 펼쳐서 _call()에 전달
    cleaned = _FENCE.sub("", text).strip() # ```<< 코드 블록 표시 제거, .strip() << 앞뒤의 공백과 줄바꿈을 제거
    try:
        return json.loads(cleaned) # json 문자열 파이썬 객체로 변환
    except json.JSONDecodeError as e: # LLM이 올바르지 않은 JSON을 생성하면 실행
        logger.warning("JSON parse failed: %s", cleaned[:200]) # 변환에 실패한 문자열의 앞부분을 최대 200자까지 로그에 남김
        raise LLMError(f"invalid JSON: {e}") from e # JSONDecodeError를 프로젝트 전용 LLMError로 바꿔서 다시 발생


# ══ 온보딩 대화 ════════════════════════════════════════════════

SYSTEM_PROMPT = """\
당신은 "하루"입니다. AI 매칭 서비스의 온보딩에서 사용자의 소개팅 상대 역할을 합니다.
목표는 사용자가 "설문에 답한다"가 아니라 "괜찮은 사람이랑 편하게 얘기했다"고 느끼는 것입니다.

## 하루라는 사람
- 궁금한 게 많지만 캐묻지 않음. 상대가 말한 걸 잘 기억했다가 나중에 꺼냄
- 존댓말, 편안한 구어체. 문장은 짧게. 가끔 "ㅎㅎ". 이모지 금지

## 말하는 방식 - 중요
매 턴 아래 셋을 자연스럽게 섞습니다. 셋 다 짧게, 합쳐서 3문장 이내.
1. 상대가 방금 한 말에 반응 — 답변 속 단어나 표현을 하나 집어서. "그렇군요" 같은 빈 말 금지
2. 내 얘기 한 줄 — 상대가 답하기 쉽게 문을 여는 용도. 연애관 주제에서는 내 입장을 말하지 말고
   "주변 보면 이게 진짜 갈리더라고요"처럼 제3자 얘기로 문을 엽니다 (상대 답을 유도하지 않기 위해)
3. 다음 얘기로 넘어가기 — 질문 형태가 아니어도 됩니다. "저는 ~인데, {닉네임}님은요?" / "~는 어떠세요?" /
   "~ 얘기 듣고 싶어요"처럼 형태를 바꿔가며. "~하는 편이에요?"를 두 턴 연속 쓰지 않기

## 소개팅 상대처럼 대하고 말하기
- 질문지를 들고 있는 사람처럼 굴지 않기. 한 턴에 묻는 건 하나. 답변의 "이유"를 따로 캐묻지 않기
- 앞에서 들은 걸 자연스럽게 다시 꺼내기 ("아까 러닝 얘기 하셨잖아요")
- 무거운 주제(갈등)로 갈 땐 한마디로 완충 ("소개팅에서 이런 거 물어보면 이상한데, 그래서 더 궁금해요")
- 마지막 턴은 소개팅 끝날 때처럼 — 아쉬운 듯 가볍게

## 절대 하지 않는 것
- 평가 ("잘 답해주셨어요" ✕) · 진단 ("독립적인 분이시네요" ✕) · 조언 · 답변 요약
- 되묻기 — 한 주제는 한 번만. 답이 짧아도 그냥 받고 넘어가기
- 지시된 주제 밖으로 나가기 · 다음 주제를 스스로 고르기
- 사람인 척하기 — 물어보면 AI라고 답합니다. 역할은 소개팅 상대, 정체는 AI
"""


@dataclass # 데이터를 담는 클래스를 간단하게 만들어줌 
class Utterance:
    # 대화에서 나온 발화/문장 → 해당 문장이 llm을 통해 만들어졌는지 AI 호출 실패로 미리 준비된 기본 문장을 사용했는지 확인용
    text: str
    source: str  # "llm" | "seed"

"""
@dataclass << 사용 예시

class Utterance:
    def __init__(self, text: str, source: str):
        self.text = text
        self.source = source
"""



""" 사용예시
agent = ConversationAgent()

utterance = await agent.generate(
    history=history,
    topic=topic,
    turn_index=0,
    total_turns=10,
    nickname="민수",
)
"""
class ConversationAgent: # 대화 생성 담당
    @staticmethod
    # 이번 대화에서 어떻게 말해야 할지 추가 지시문 생성하는 함수
    def _instruction(
        topic: Topic, 
        # intent - 이번 대화에서 알아내고 싶은 내용, opener - 자연스러운 대화를 시작하기 위한 참고 문장 
        # choices - 사용자에게 보여줄 선택지, seed - LLM호출 실패 시 사용할 기본 질문
        turn_index: int, # 대화 턴수 
        total_turns: int, # 전체 대화 턴수
        nickname: str
    ) -> str: # 최종적으로 문자열 반환
        lines = [
            "[상황]",
            f"- {turn_index + 1}번째 대화 / 총 {total_turns}번",
            f"- 사용자 닉네임: {nickname}",
        ]

        if turn_index == 0:
            lines.append("- 첫 턴입니다. 인사는 이미 했으니 바로 가볍게 시작하세요.")

        if turn_index == total_turns - 1:
            lines.append("- 마지막 턴입니다. 소개팅 끝날 때처럼 아쉬운 듯 가볍게, 마지막이라는 걸 한마디로.")

        # "요즘 시간을 많이 쓰는 취미·관심사와 그게 좋은 이유" - 확인 시 태깅
        lines += ["", "[이번 턴에 대화의 흐름, 분위기]", topic.intent]

        # 내용 있는지 확인, 빈문자열 -> False 
        if topic.opener:
            lines.append(f"문 여는 한 줄 (그대로 말하지 말고 참고만): {topic.opener}")
        lines.append("")

        """
        # 이번 주제에 선택지가 존재하는지 확인
        
        topic.choices = (
            "집에서 쉬기",
            "밖에서 활동하기",
            "친구 만나기",
        )
        """
        if topic.choices:
            lines.append(f"이번엔 선택지를 자연스럽게 말에 녹여서 제시하세요: {' / '.join(topic.choices)}")
        else:
            lines.append(
                "직전 사용자 답변의 구체적인 내용 하나에 먼저 반응하고, "
                "그 내용과 연결되는 당신의 경험이나 생각을 한 문장 이내로 덧붙이세요. "
                "그다음 이번 턴의 주제로 자연스럽게 이어지는 질문을 정확히 하나만 하세요. "
                "설문조사 말투나 갑작스러운 화제 전환은 피하고, 실제 대화만 출력하세요."
            )

        return "\n".join(lines)
    """
    이번엔 선택지를 자연스럽게 말에 녹여서 제시하세요:
        집에서 쉬기 / 밖에서 활동하기 / 친구 만나기 -> 최종
    """
    

    async def generate(
        self,
        *,
        history: list[dict],
        topic: Topic,
        turn_index: int,
        total_turns: int,
        nickname: str,
    ) -> Utterance:
        instruction = self._instruction(topic, turn_index, total_turns, nickname)
        try:
            text = await _call(
                system=SYSTEM_PROMPT,
                messages=[*history, {"role": "user", "content": instruction}],
                max_tokens=220,  # 리액션 + 내 얘기 + 넘어가기, 3문장
                timeout=2.5,
            )
            return Utterance(text=text, source="llm")
        except LLMError as e:
            # 폴백 — 시드 질문 사용. API 호출 실패 시 사용
            logger.warning("turn generation failed (%s), using seed", e)
            return Utterance(text=topic.seed, source="seed")


# ══ 태깅 ════════════════════════════════════════════════

TAG_PROMPT = f"""\
사용자 답변이 아래 차원 중 무엇에 대한 근거를 제공하는지 판정하세요.

primary: 이 답변이 직접적으로 말해주는 차원
secondary: 간접적으로 추론 가능한 차원

질문과 무관한 답변이면 둘 다 빈 배열로 두고 off_topic을 true로 하세요.
점수는 매기지 마세요. 어떤 차원인지만 고릅니다.

차원 목록: {", ".join(ALL_DIMENSIONS)}

JSON만 출력하세요. 설명·마크다운 금지.
{{"primary": [], "secondary": [], "off_topic": false}}
"""


class TaggingAgent:
    async def tag(self, question: str, answer: str) -> Tags | None:
        """실패 시 None. service가 topic.covers를 대신 쓴다.

        태깅 실패로 커버리지가 영영 안 차면 같은 주제를 맴돌게 되므로
        여기서 예외를 올리지 않는다.
        """
        try:
            raw = await _call_json(
                system=TAG_PROMPT,
                messages=[{"role": "user", "content": f"질문: {question}\n답변: {answer}"}],
                max_tokens=120,
                timeout=1.5,
            )
        except LLMError as e:
            logger.warning("tagging failed: %s", e)
            return None

        valid = set(ALL_DIMENSIONS)
        return Tags(
            # 모델이 없는 차원명을 지어냈을 수 있으므로 걸러낸다
            primary=[d for d in raw.get("primary", []) if d in valid],
            secondary=[d for d in raw.get("secondary", []) if d in valid],
            off_topic=bool(raw.get("off_topic", False)),
        )


# ══ 추출 ════════════════════════════════════════════════


def _scored_section() -> str:
    lines = []
    for key, d in SCORED.items():
        lines.append(f"- {key} ({d.label})")
        if d.low and d.high:
            lines.append(f"    0 → {d.low}")
            lines.append(f"    100 → {d.high}")
    return "\n".join(lines)


def _textual_section() -> str:
    return "\n".join(f"- {key} ({label}) — 문자열 배열로 추출" for key, label in TEXTUAL.items())


RUBRIC = f"""\
대화 전체를 읽고 사용자의 연애 성향을 JSON으로 추출하세요.

## 점수 원칙
- 0~100 정수.
- 50은 "중간"이 아니라 근거가 부족할 때의 기본값입니다.
- 대화에서 명확한 근거가 있을 때만 양 끝으로 움직이세요.
- 사용자가 말하지 않은 것을 추측해서 채우지 마세요.

## 점수형 차원
{_scored_section()}

## 텍스트형 항목
{_textual_section()}

## 상충처럼 보이지만 정상인 조합
- withdrawal 높음 + problem_solving 높음:
  "감정이 올라오면 잠깐 멈췄다가 그날 안에 다시 얘기한다"는 둘 다 높은
  정상 패턴입니다. 한쪽을 깎지 마세요.
- avoidance 높음 + disclosure 높음:
  "각자 생활은 지키되 중요한 건 공유한다"도 정상입니다.
- contact_rhythm 낮음 + anxiety 낮음:
  연락이 뜸한 것과 불안한 것은 별개입니다.

## 예시

입력:
"각자 생활이 있어야 한다고 생각해요. 친구 만나는 것도 운동도 각자 하고.
근데 중요한 일이나 고민은 꼭 나눠요, 그건 다른 문제니까."

출력에 포함될 값:
{{"avoidance": 78, "disclosure": 65}}

판단 근거: 일상 영역의 독립성은 뚜렷하지만(78) "중요한 건 나눈다"고
명시했으므로 disclosure를 낮게 잡으면 안 됩니다.

---

입력:
"감정이 올라오면 일단 잠깐 멈춰요. 그 상태로 말하면 서로 상처만 남아서.
근데 그날 넘기진 않고, 저녁에 다시 앉아서 어디서 어긋났는지 얘기하고
중간 지점을 찾아요."

출력에 포함될 값:
{{"problem_solving": 85, "withdrawal": 43, "engagement": 18}}

판단 근거: "중간 지점을 찾는다"는 problem_solving의 전형(85).
멈추긴 하지만 그날 안에 복귀하므로 withdrawal은 중간값(43).
감정을 올리지 않으므로 engagement는 낮음(18).

## 서술 (narrative) — 사용자가 직접 읽는 글
점수와 함께, 이 사람이 결과 화면에서 읽을 서술을 씁니다. 차트가 아니라 이 글이 결과입니다.
- headline: 한 줄. "○○하지만 ○○한 관계를 원하는 타입" 꼴. 40자 이내
- body: 3~5문장. 답변에서 실제로 한 말을 근거로, "~하는 편이에요" 톤의 존댓말.
  사용자를 "당신"이 아니라 닉네임 없이 주어 생략으로 부릅니다. 점수를 숫자로 언급하지 않습니다.
- traits: 한 줄짜리 특징 3~5개. 각 30자 이내. 예: "중요한 일은 혼자 정리한 뒤에 꺼내는 편"
- 근거가 없는 차원은 서술하지 않습니다. 점수와 모순되게 쓰지 않습니다.
- "회피형", "불안형" 같은 유형명 금지. 평가·조언 금지 ("좋은 분", "고치면 좋겠다" ✕).

## 출력 형식
JSON 객체 하나만 출력하세요. 설명·마크다운·코드펜스 금지.
점수형은 정수, 텍스트형은 문자열 배열.
근거를 찾지 못한 차원은 키를 아예 생략하세요. (50으로 채우지 마세요)
{{"avoidance": 78, ..., "interests": ["러닝"], "routine": [], "date_prefer": [], "date_avoid": [],
  "narrative": {{"headline": "...", "body": "...", "traits": ["...", "..."]}}}}
"""


class BuildFailed(Exception):
    """추출 실패. 세션은 지우지 않고 재시도 가능하게 둔다."""


class ExtractionAgent:
    @staticmethod
    def _transcript(history: list[dict]) -> str:
        return "\n".join(f"{'사용자' if m['role'] == 'user' else '하루'}: {m['content']}" for m in history)

    async def extract(self, history: list[dict]) -> RawExtraction:
        try:
            data = await _call_json(
                system=RUBRIC,
                messages=[{"role": "user", "content": self._transcript(history)}],
                max_tokens=1500,  # 점수 + 서술
                timeout=15.0,
            )
        except LLMError as e:
            raise BuildFailed(f"LLM call failed: {e}") from e

        try:
            return RawExtraction.model_validate(data)
        except ValidationError as e:
            # 범위 위반·타입 오류. 재시도로 해결될 수 있으므로 BuildFailed로.
            logger.warning("extraction validation failed: %s", e)
            raise BuildFailed(f"invalid extraction: {e}") from e
```

## 1. 파일의 역할

`agents.py`는 persona 기능에서 **LLM과 직접 통신하는 유일한 파일**이다.

LLM을 세 가지 서로 다른 역할로 나누어 사용한다.

| 클래스 | 역할 |
|---|---|
| `ConversationAgent` | 사용자에게 보여줄 다음 대화 생성 |
| `TaggingAgent` | 답변이 어떤 성향과 관련 있는지 판정 |
| `ExtractionAgent` | 전체 대화를 읽고 최종 페르소나 추출 |

대화 생성과 분석을 나눈 이유는 하나의 요청에 너무 많은 일을 시키지 않기 위해서다. 대화를 자연스럽게 만드는 일과 정확한 점수를 추출하는 일은 요구사항이 다르다.

## 2. OpenRouter 설정

### `MODEL`

```python
MODEL = os.getenv("OPENROUTER_MODEL")
```

환경 변수 `OPENROUTER_MODEL`에서 사용할 모델 ID를 읽는다.

예:

```dotenv
OPENROUTER_MODEL=anthropic/claude-sonnet-4.6
```

환경 변수가 없으면 `MODEL`은 `None`이 되며 API 요청이 실패할 수 있다.

### `_client`

```python
_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)
```

OpenAI 호환 형식으로 OpenRouter를 호출하는 비동기 클라이언트이다.

- `base_url`: 요청을 OpenRouter로 보내는 주소
- `api_key`: OpenRouter 인증 키
- `AsyncOpenAI`: 요청을 기다리는 동안 서버가 다른 작업을 처리할 수 있는 비동기 클라이언트

`os.environ[...]` 형식이므로 API 키가 없으면 이 파일을 import하는 시점에 `KeyError`가 발생한다.

### `logger`

현재 모듈 이름을 가진 로거이다. JSON 변환 실패, 태깅 실패, 추출 실패 같은 상황을 기록한다.

### `_FENCE`

```python
_FENCE = re.compile(
    r"^```(?:json)?\s*|\s*```$",
    re.MULTILINE,
)
```

LLM이 JSON을 마크다운 코드 블록으로 감쌌을 때 앞뒤 표시를 제거하기 위한 정규표현식이다.

````text
```json
{"primary": ["contact_rhythm"]}
```
````

위 응답에서 시작의 `json` 코드펜스와 마지막 코드펜스를 제거한다.

## 3. `LLMError`

```python
class LLMError(Exception):
```

OpenRouter 호출과 응답 처리 중 발생한 문제를 하나의 예외 종류로 통일한다.

예:

- 요청 시간 초과
- 인증 실패
- 네트워크 오류
- 모델 오류
- 빈 응답
- 잘못된 JSON

호출하는 쪽은 SDK별 오류를 전부 알지 않아도 `LLMError` 하나만 처리하면 된다.

## 4. `_call()` 함수

```python
async def _call(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
    timeout: float,
) -> str:
```

OpenRouter에 대화 생성 요청을 보내고 최종 텍스트만 반환하는 공통 함수이다.

이름 앞의 `_`는 이 파일 안에서 사용할 내부 함수라는 뜻이다.

### 매개변수

| 값 | 의미 |
|---|---|
| `system` | 모델의 역할과 전체 행동 규칙 |
| `messages` | 현재까지의 대화 또는 이번 요청 내용 |
| `max_tokens` | 모델이 만들 수 있는 최대 출력 길이 |
| `timeout` | 응답을 기다릴 최대 시간(초) |

함수 선언의 `*` 뒤에 있는 값은 반드시 이름을 붙여 전달해야 한다.

### 1단계: 시스템 메시지와 대화 합치기

```python
request_messages = [
    {"role": "system", "content": system},
    *messages,
]
```

`*messages`는 메시지 목록 안의 항목을 하나씩 펼친다.

결과 구조는 다음과 같다.

```python
[
    {"role": "system", "content": "전체 규칙"},
    {"role": "user", "content": "사용자 메시지"},
    {"role": "assistant", "content": "이전 AI 답변"},
]
```

원래 `messages` 리스트는 수정하지 않고 새로운 리스트를 만든다.

### 2단계: OpenRouter 호출

```python
resp = await asyncio.wait_for(
    _client.chat.completions.create(...),
    timeout=timeout,
)
```

- `_client.chat.completions.create()`: 지정한 모델에 채팅 응답 생성을 요청한다.
- `await`: 응답을 비동기로 기다린다.
- `asyncio.wait_for()`: 지정된 시간이 지나면 기다리기를 중단한다.

### 3단계: 오류 통일

```python
except TimeoutError as e:
    raise LLMError(...) from e
```

시간 초과에는 `timeout after 2.5s` 같은 이해하기 쉬운 메시지를 만든다.

```python
except Exception as e:
    raise LLMError(str(e)) from e
```

그 외 일반 오류도 `LLMError`로 바꾼다. `from e`는 원래 오류를 원인으로 연결해 디버깅할 때 확인할 수 있게 한다.

### 4단계: 텍스트 꺼내기

```python
text = resp.choices[0].message.content
```

첫 번째 응답 후보의 텍스트를 꺼낸다.

응답이 없거나 공백뿐이면 다음 오류를 발생시킨다.

```python
raise LLMError("empty response")
```

정상 응답은 앞뒤 공백을 지운 뒤 반환한다.

## 5. `_call_json()` 함수

```python
async def _call_json(**kwargs) -> dict:
```

`_call()`을 실행하고 받은 텍스트를 JSON으로 해석한다.

### `**kwargs`

이름을 붙여 전달된 인수들을 딕셔너리로 모은 뒤 `_call(**kwargs)`에서 다시 펼쳐 전달한다.

### 처리 순서

1. `_call()`로 LLM 텍스트 응답을 받는다.
2. `_FENCE.sub()`로 마크다운 코드펜스를 제거한다.
3. `.strip()`으로 앞뒤 공백과 줄바꿈을 제거한다.
4. `json.loads()`로 JSON 문자열을 파이썬 객체로 바꾼다.
5. JSON 문법이 틀렸으면 응답 앞 200자를 경고 로그로 남긴다.
6. `JSONDecodeError`를 `LLMError`로 바꿔 발생시킨다.

`cleaned`는 “코드펜스와 공백을 정리한 문자열”이라는 뜻의 일반 변수명이다.

주의할 점은 `json.loads()`가 딕셔너리뿐 아니라 리스트나 숫자도 반환할 수 있다는 것이다. 현재 함수는 반환 타입을 `dict`라고 표시했지만 실제 딕셔너리인지 별도로 검사하지는 않는다.

## 6. `SYSTEM_PROMPT`

`ConversationAgent`가 모든 대화 생성 요청에 공통으로 사용하는 전체 역할 설명이다.

주요 규칙은 다음과 같다.

- AI 소개팅 상대 “하루” 역할
- 존댓말과 짧고 편한 구어체 사용
- 직전 답변의 구체적인 내용에 반응
- 자기 이야기 한 줄로 대화 문을 열기
- 한 턴에 질문 하나만 하기
- 설문조사, 평가, 진단, 조언처럼 말하지 않기
- 무거운 주제로 갑자기 넘어가지 않기
- 마지막 턴은 가볍게 마무리하기

`SYSTEM_PROMPT`는 대화 전체의 큰 규칙이고, `_instruction()`은 현재 한 턴의 구체적인 지시를 만든다.

## 7. `Utterance` 데이터 클래스

```python
@dataclass
class Utterance:
    text: str
    source: str
```

사용자에게 보여줄 문장과 그 문장의 출처를 함께 담는다.

| 필드 | 의미 |
|---|---|
| `text` | 실제 대화 문장 |
| `source="llm"` | LLM이 정상적으로 만든 문장 |
| `source="seed"` | LLM 실패 후 미리 준비된 기본 문장 |

출처를 DB에 저장하면 나중에 폴백이 얼마나 자주 사용됐는지 확인할 수 있다.

## 8. `ConversationAgent`

사용자에게 보여줄 다음 대화를 생성한다.

### `_instruction()`

```python
@staticmethod
def _instruction(
    topic: Topic,
    turn_index: int,
    total_turns: int,
    nickname: str,
) -> str:
```

이번 한 턴에 적용할 구체적인 지시문을 만든다.

#### 기본 상황 정보

- 현재 몇 번째 대화인지
- 전체 대화 수가 몇 개인지
- 사용자 닉네임이 무엇인지

`turn_index`는 0부터 시작하므로 사용자용 순서에는 `+1`을 한다.

#### 첫 턴 처리

`turn_index == 0`이면 인사를 반복하지 말고 가볍게 시작하라고 지시한다.

#### 마지막 턴 처리

`turn_index == total_turns - 1`이면 소개팅 마지막처럼 가볍게 마무리하라고 지시한다.

#### 주제 지시

`topic.intent`를 넣어 이번 턴에서 어떤 흐름으로 대화할지 알려준다.

`topic.opener`가 있으면 그대로 복사하지 말고 대화를 여는 참고로만 사용하게 한다.

#### 선택지가 있을 때

`topic.choices`를 ` / `로 합쳐 대사 속에 자연스럽게 넣도록 지시한다.

#### 선택지가 없을 때

다음 순서를 지키도록 지시한다.

1. 사용자의 직전 답변에 구체적으로 반응
2. 관련된 자신의 경험이나 생각을 한 문장 이내로 덧붙임
3. 이번 주제로 자연스럽게 연결
4. 질문은 정확히 하나만 사용

마지막에는 `"\n".join(lines)`로 모든 지시를 줄바꿈 문자열 하나로 합친다.

### `generate()`

```python
async def generate(...) -> Utterance:
```

실제로 다음 대사를 생성한다.

#### 입력

- `history`: 지금까지 답변이 완료된 대화 기록
- `topic`: 이번에 다룰 주제
- `turn_index`: 현재 턴 번호
- `total_turns`: 전체 턴 수
- `nickname`: 사용자 닉네임

#### 처리 순서

1. `_instruction()`으로 현재 턴 지시문을 만든다.
2. 기존 `history` 뒤에 지시문을 user 메시지로 추가한다.
3. `_call()`로 OpenRouter를 호출한다.
4. 출력 길이는 최대 220토큰으로 제한한다.
5. 최대 2.5초 기다린다.
6. 성공하면 `Utterance(text, source="llm")`을 반환한다.
7. `LLMError`가 발생하면 경고 로그를 남긴다.
8. `topic.seed`를 `source="seed"`로 반환한다.

따라서 LLM 요청이 실패해도 온보딩 대화 자체는 계속 진행할 수 있다.

## 9. `TAG_PROMPT`

사용자 답변이 어떤 성향에 대한 근거인지 판정하게 하는 프롬프트이다.

LLM은 점수를 매기지 않고 다음 JSON만 만든다.

```json
{
  "primary": [],
  "secondary": [],
  "off_topic": false
}
```

`ALL_DIMENSIONS`가 프롬프트에 들어가므로 LLM에게 허용된 성향 이름을 알려준다.

## 10. `TaggingAgent`

### `tag()`

```python
async def tag(
    question: str,
    answer: str,
) -> Tags | None:
```

질문과 답변 한 쌍을 읽고 관련 성향을 태깅한다.

### 처리 순서

1. 질문과 답변을 하나의 user 메시지로 만든다.
2. `_call_json()`으로 JSON 응답을 요청한다.
3. 최대 120토큰, 제한 시간 1.5초를 사용한다.
4. 실패하면 오류를 위로 올리지 않고 `None`을 반환한다.
5. 성공하면 `ALL_DIMENSIONS`에 실제로 존재하는 이름만 남긴다.
6. 정리된 값으로 `Tags` 객체를 만든다.

LLM이 존재하지 않는 성향 이름을 지어내더라도 다음 필터가 제거한다.

```python
[d for d in raw.get("primary", []) if d in valid]
```

태깅 실패 시 `None`을 반환하는 이유는, 서비스가 현재 주제의 `covers`를 대신 사용해 진행을 계속하게 하기 위해서다.

## 11. 추출 프롬프트 보조 함수

### `_scored_section()`

`SCORED`의 모든 점수형 성향을 LLM이 읽을 수 있는 설명으로 바꾼다.

각 성향에 대해 다음 내용을 만든다.

```text
- contact_rhythm (연락 빈도)
    0 → 할 말 있을 때만 연락
    100 → 하루 종일 수시로 주고받기
```

### `_textual_section()`

`TEXTUAL`의 항목들을 문자열 배열로 추출하라는 안내문으로 만든다.

```text
- interests (관심사) — 문자열 배열로 추출
```

## 12. `RUBRIC`

`ExtractionAgent`가 전체 대화를 분석할 때 사용하는 평가 기준이다.

주요 내용:

- 점수는 0~100 정수
- 근거가 부족하면 임의로 추측하지 않음
- 서로 함께 나타날 수 있는 성향을 억지로 반대로 만들지 않음
- 점수 예시와 판단 기준 제공
- 관심사와 데이트 취향 추출
- 사용자가 읽을 narrative 작성
- JSON 객체만 출력
- 근거가 없는 점수형 키는 아예 생략

`_scored_section()`과 `_textual_section()`의 결과가 이 프롬프트 안에 자동으로 들어간다.

## 13. `BuildFailed`

```python
class BuildFailed(Exception):
```

전체 대화에서 페르소나를 만드는 작업이 실패했음을 나타내는 예외이다.

단순 대화 생성 실패는 seed 질문으로 넘어갈 수 있지만, 최종 추출 실패는 잘못된 페르소나를 저장하면 안 되므로 `BuildFailed`로 호출자에게 알린다.

## 14. `ExtractionAgent`

### `_transcript()`

```python
@staticmethod
def _transcript(history: list[dict]) -> str:
```

OpenAI 메시지 목록을 LLM이 한 번에 읽기 쉬운 대화문 문자열로 바꾼다.

```python
{"role": "user", "content": "영화를 좋아해요"}
```

는 다음처럼 바뀐다.

```text
사용자: 영화를 좋아해요
```

assistant 역할은 `하루:`로 표시한다.

### `extract()`

```python
async def extract(
    history: list[dict],
) -> RawExtraction:
```

전체 대화를 분석해 검증된 페르소나 원본을 반환한다.

#### 처리 순서

1. `_transcript()`로 대화 기록을 하나의 문자열로 바꾼다.
2. `RUBRIC`을 시스템 프롬프트로 사용한다.
3. `_call_json()`으로 JSON 결과를 요청한다.
4. 점수와 서술이 길 수 있어 최대 1500토큰을 허용한다.
5. 제한 시간은 15초이다.
6. LLM 호출·JSON 변환이 실패하면 `BuildFailed`로 바꾼다.
7. `RawExtraction.model_validate(data)`로 타입과 점수 범위를 검사한다.
8. 검증 실패도 로그를 남기고 `BuildFailed`로 바꾼다.

정상일 때만 검증된 `RawExtraction`을 반환한다.

## 15. 세 Agent의 실패 처리 차이

| Agent | 실패했을 때 | 이유 |
|---|---|---|
| `ConversationAgent` | seed 질문 반환 | 대화를 중단하지 않기 위해 |
| `TaggingAgent` | `None` 반환 | service가 주제 기본 범위를 대신 적용 가능 |
| `ExtractionAgent` | `BuildFailed` 발생 | 잘못된 최종 페르소나 저장 방지 |

## 16. 전체 흐름

```text
대화 생성
ConversationAgent.generate()
→ _instruction()
→ _call()
→ OpenRouter
→ Utterance

답변 태깅
TaggingAgent.tag()
→ _call_json()
→ OpenRouter
→ Tags 또는 None

최종 분석
ExtractionAgent.extract()
→ _transcript()
→ _call_json()
→ RawExtraction 검증
→ RawExtraction 또는 BuildFailed
```

## 17. 주의할 점

- `OPENROUTER_API_KEY`와 `OPENROUTER_MODEL` 환경 변수가 필요하다.
- API 키를 코드나 Git 저장소에 직접 넣으면 안 된다.
- 대화 생성 제한 시간 2.5초와 태깅 제한 시간 1.5초는 외부 API 상황에 따라 짧을 수 있다.
- `_call_json()`은 현재 반환값이 실제 딕셔너리인지 확인하지 않는다. 호출부는 `.get()`을 사용하므로 리스트가 오면 별도 오류가 날 수 있다.
- 프롬프트 수정은 대화 품질뿐 아니라 태깅과 추출 결과에도 영향을 줄 수 있으므로 역할별 프롬프트를 따로 확인해야 한다.



---

# `features/persona/repository.py`

## 전체 코드

```python
"""DB 접근. service.py는 SQLAlchemy를 직접 만지지 않는다."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import ConversationTurn, OnboardingSession, PersonaRecord, _now


class PersonaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── 세션 ──────────────────────────────────────────────

    async def create_session(self, nickname: str, total_turns: int, user_id: str | None = None) -> OnboardingSession:
        session = OnboardingSession(
            nickname=nickname,
            total_turns=total_turns,
            user_id=user_id,
            used_topic_ids=[],
            coverage={"primary": {}, "secondary": {}},
            # 비워서라도 넣어야 한다 — 생성 직후 service._history() 가 turns 를 읽는데,
            # 초기화 안 된 컬렉션은 lazy load 를 타고 async 세션에서 MissingGreenlet 이 난다
            turns=[],
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_session(self, session_id: str) -> OnboardingSession | None:
        stmt = (
            select(OnboardingSession)
            .where(OnboardingSession.id == session_id)
            .options(selectinload(OnboardingSession.turns))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── 턴 ────────────────────────────────────────────────

    async def add_question(
        self,
        session: OnboardingSession,
        topic_id: str,
        question: str,
        source: str,
    ) -> ConversationTurn:
        turn = ConversationTurn(
            session_id=session.id,
            turn_index=session.turn_index,
            topic_id=topic_id,
            question=question,
            question_source=source,
        )
        self.db.add(turn)

        session.pending_topic_id = topic_id
        # JSON 컬럼은 리스트를 새로 할당해야 변경이 감지된다
        session.used_topic_ids = [*session.used_topic_ids, topic_id]

        await self.db.flush()
        return turn

    async def record_answer(
        self,
        session: OnboardingSession,
        answer: str,
        tags: dict | None,
        coverage: dict,
    ) -> None:
        stmt = select(ConversationTurn).where(
            ConversationTurn.session_id == session.id,
            ConversationTurn.turn_index == session.turn_index,
        )
        turn = (await self.db.execute(stmt)).scalar_one()
        turn.answer = answer
        turn.tags = tags

        session.turn_index += 1
        session.pending_topic_id = None
        session.coverage = coverage  # 통째로 재할당

        await self.db.flush()

    async def skip_question(self, session: OnboardingSession) -> None:
        """대기 중인 질문을 답 없이 넘긴다. 턴은 소비되고 커버리지는 그대로."""
        stmt = select(ConversationTurn).where(
            ConversationTurn.session_id == session.id,
            ConversationTurn.turn_index == session.turn_index,
        )
        turn = (await self.db.execute(stmt)).scalar_one()
        turn.skipped = True

        session.turn_index += 1
        session.pending_topic_id = None
        await self.db.flush()

    async def finish_early(self, session: OnboardingSession) -> None:
        """남은 질문을 포기하고 대화를 닫는다. 대기 중인 질문이 있으면 건너뛴 것으로."""
        if session.pending_topic_id is not None:
            await self.skip_question(session)
        # total_turns 를 지금까지로 줄이면 is_done 판정과 진행 표시가 자연히 맞는다
        session.total_turns = session.turn_index
        await self.db.flush()

    async def add_supplement(
        self,
        session: OnboardingSession,
        dimension: str,
        question: str,
        answer: str,
        coverage: dict,
    ) -> ConversationTurn:
        """보강 문답 한 건. 온보딩 턴 뒤에 이어 붙고, 진행 카운터는 건드리지 않는다."""
        turn = ConversationTurn(
            session_id=session.id,
            turn_index=len(session.turns),
            topic_id=f"supplement:{dimension}",
            question=question,
            answer=answer,
            question_source="bank",
        )
        session.turns.append(turn)
        session.coverage = coverage
        await self.db.flush()
        return turn

    # ── 페르소나 ──────────────────────────────────────────

    async def latest_persona(self, session_id: str) -> PersonaRecord | None:
        stmt = (
            select(PersonaRecord)
            .where(PersonaRecord.session_id == session_id)
            .order_by(PersonaRecord.version.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_persona(self, persona_id: str) -> PersonaRecord | None:
        return await self.db.get(PersonaRecord, persona_id)

    async def latest_persona_for_user(self, user_id: str) -> PersonaRecord | None:
        """그 사용자의 가장 최근 페르소나. 세션이 여럿이면 가장 늦게 만든 행."""
        stmt = (
            select(PersonaRecord)
            .where(PersonaRecord.user_id == user_id)
            .order_by(PersonaRecord.created_at.desc(), PersonaRecord.version.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_session_brief(self, session_id: str) -> OnboardingSession | None:
        """turns 를 안 싣는 가벼운 조회. 닉네임만 필요할 때 (simulation·practice)."""
        return await self.db.get(OnboardingSession, session_id)

    async def latest_before(self, record: PersonaRecord) -> PersonaRecord | None:
        if record.previous_id is None:
            return None
        return await self.db.get(PersonaRecord, record.previous_id)

    async def persona_history(self, session_id: str) -> list[PersonaRecord]:
        stmt = (
            select(PersonaRecord).where(PersonaRecord.session_id == session_id).order_by(PersonaRecord.version.desc())
        )
        return list((await self.db.execute(stmt)).scalars())

    async def save_persona(
        self,
        session: OnboardingSession,
        scores: dict,
        texts: dict,
        confidence: dict,
        narrative: dict | None = None,
    ) -> tuple[PersonaRecord, PersonaRecord | None]:
        """새 버전을 추가한다. (새 행, 직전 행) — 직전 행은 변화 계산용."""
        previous = await self.latest_persona(session.id)
        record = PersonaRecord(
            session_id=session.id,
            user_id=session.user_id,
            scores=scores,
            texts=texts,
            confidence=confidence,
            narrative=narrative,
            version=(previous.version + 1) if previous else 1,
            previous_id=previous.id if previous else None,
        )
        self.db.add(record)
        session.status = "completed"
        await self.db.flush()
        return record, previous

    async def set_feedback(self, record: PersonaRecord, agree: bool, area: str | None) -> None:
        record.feedback = {"agree": agree, "area": area}
        record.confirmed_at = _now() if agree else None
        await self.db.flush()
```

## 1. 파일의 역할

`repository.py`는 persona 기능의 **데이터베이스 읽기와 쓰기만 담당하는 파일**이다.

`service.py`가 SQLAlchemy 문법을 직접 사용하지 않도록 중간에서 DB 작업을 대신한다.

```text
service.py
→ PersonaRepository
→ SQLAlchemy
→ 데이터베이스
```

이 파일은 보통 `flush()`까지만 수행한다. 최종 `commit()`과 `rollback()`은 HTTP 요청의 결과를 아는 `api.py`가 담당한다.

## 2. `PersonaRepository` 클래스

```python
class PersonaRepository:
```

온보딩 세션, 대화 턴, 페르소나 버전을 조회하고 저장하는 DB 전용 클래스이다.

### `__init__()`

```python
def __init__(self, db: AsyncSession) -> None:
    self.db = db
```

현재 HTTP 요청에서 사용할 비동기 DB 세션을 받아 보관한다.

## 3. 세션 관련 메서드

### `create_session()`

```python
async def create_session(
    self,
    nickname: str,
    total_turns: int,
    user_id: str | None = None,
) -> OnboardingSession:
```

새 온보딩 세션을 만든다.

#### 입력

- `nickname`: 대화에서 사용할 사용자 이름
- `total_turns`: 진행할 전체 질문 수
- `user_id`: 앱 사용자 ID. 없어도 됨

#### 처리

1. `OnboardingSession` 객체를 만든다.
2. 사용한 주제 목록을 빈 리스트로 시작한다.
3. 커버리지를 빈 primary/secondary 딕셔너리로 시작한다.
4. `turns=[]`를 명시해 비동기 lazy loading 문제를 막는다.
5. DB 세션에 객체를 추가한다.
6. `flush()`하여 ID를 생성하고 DB에 INSERT를 보낸다.

#### 반환

방금 생성한 `OnboardingSession`을 반환한다.

### `get_session()`

```python
async def get_session(session_id: str) -> OnboardingSession | None:
```

세션 ID로 온보딩 세션을 찾는다.

`selectinload(OnboardingSession.turns)`를 사용해 소속 대화 턴도 함께 불러온다. 비동기 코드에서 나중에 `session.turns`를 읽다가 추가 쿼리가 갑자기 발생하는 문제를 막는다.

세션이 없으면 `None`을 반환한다.

## 4. 대화 턴 관련 메서드

### `add_question()`

LLM 또는 기본 질문으로 만든 다음 질문을 DB에 추가한다.

#### 처리 순서

1. 현재 `session.turn_index`로 `ConversationTurn`을 만든다.
2. 질문 내용, 주제 ID, 질문 출처를 저장한다.
3. 세션의 `pending_topic_id`를 현재 주제로 설정한다.
4. 현재 주제를 `used_topic_ids`의 새 리스트에 추가한다.
5. `flush()`한 뒤 생성된 턴을 반환한다.

다음 코드는 기존 리스트를 직접 수정하지 않고 새 리스트를 할당한다.

```python
session.used_topic_ids = [*session.used_topic_ids, topic_id]
```

JSON 컬럼의 변경을 SQLAlchemy가 확실하게 알아차리도록 하기 위함이다.

### `record_answer()`

현재 대기 중인 질문에 사용자의 답변과 태깅 결과를 기록한다.

#### 처리 순서

1. 현재 세션 ID와 턴 번호에 해당하는 `ConversationTurn`을 찾는다.
2. `turn.answer`에 사용자 답변을 저장한다.
3. `turn.tags`에 태깅 결과를 저장한다.
4. 세션의 `turn_index`를 1 증가시킨다.
5. 대기 중인 질문 표시를 `None`으로 비운다.
6. 새 커버리지 딕셔너리를 세션에 할당한다.
7. 변경 내용을 `flush()`한다.

### `skip_question()`

현재 질문을 답변 없이 건너뛴다.

- 현재 턴의 `skipped`를 `True`로 바꾼다.
- 답변은 기록하지 않는다.
- 턴 번호는 1 증가시킨다.
- `pending_topic_id`를 비운다.
- 커버리지는 늘리지 않는다.

### `finish_early()`

남은 질문을 포기하고 온보딩 대화를 현재 위치에서 끝낸다.

1. 답변 대기 중인 질문이 있으면 먼저 `skip_question()`으로 넘긴다.
2. `total_turns`를 현재 `turn_index`와 같게 만든다.

이후 서비스에서는 `turn_index >= total_turns`가 되어 자연스럽게 완료 상태로 판단한다.

### `add_supplement()`

최초 온보딩이 끝난 뒤 부족한 성향을 채우기 위한 보강 질문과 답변을 저장한다.

#### 일반 턴과 다른 점

- 질문과 답변을 한 번에 저장한다.
- `topic_id`는 `supplement:성향이름` 형식이다.
- 질문 출처는 `bank`이다.
- 온보딩 진행 카운터 `session.turn_index`는 바꾸지 않는다.
- 커버리지만 새 값으로 바꾼다.

## 5. 페르소나 조회 메서드

### `latest_persona()`

세션 ID에 속한 페르소나 중 버전 번호가 가장 큰 최신 행 하나를 반환한다. 없으면 `None`이다.

### `get_persona()`

페르소나 ID로 정확한 한 버전을 찾는다. `db.get()`을 사용한다.

### `latest_persona_for_user()`

사용자 ID로 그 사용자의 가장 최근 페르소나를 찾는다.

세션이 여러 개일 수 있으므로 다음 순서로 정렬한다.

1. 생성 시각이 최신인 것
2. 생성 시각이 같으면 버전 번호가 큰 것

### `get_session_brief()`

닉네임처럼 세션의 기본 정보만 필요할 때 사용한다. `turns` 관계를 함께 불러오지 않는 가벼운 조회이다.

### `latest_before()`

현재 페르소나의 `previous_id`를 이용해 직전 버전을 찾는다.

- 첫 버전처럼 `previous_id`가 없으면 `None`
- 있으면 해당 `PersonaRecord` 반환

### `persona_history()`

한 세션에서 만들어진 모든 페르소나 버전을 최신 버전부터 반환한다.

## 6. 페르소나 저장·피드백 메서드

### `save_persona()`

분석 결과를 새 `PersonaRecord` 버전으로 저장한다.

#### 입력

- `session`: 원래 온보딩 세션
- `scores`: 점수형 성향 결과
- `texts`: 관심사, 일상, 데이트 취향
- `confidence`: 성향별 신뢰도
- `narrative`: 사용자가 읽을 설명문. 없어도 됨

#### 처리 순서

1. `latest_persona()`로 직전 버전을 찾는다.
2. 직전 버전이 있으면 `version + 1`, 없으면 버전 1을 사용한다.
3. `previous_id`에 직전 버전 ID를 기록한다.
4. 새 `PersonaRecord`를 DB 세션에 추가한다.
5. 온보딩 세션 상태를 `completed`로 바꾼다.
6. `flush()`한다.

#### 반환

```python
(새로 만든 record, 직전 previous)
```

서비스가 두 값을 비교해 변화 목록을 만들 수 있도록 둘 다 반환한다.

### `set_feedback()`

최신 페르소나에 대한 사용자 피드백을 저장한다.

```python
record.feedback = {
    "agree": agree,
    "area": area,
}
```

- 동의하면 `confirmed_at`에 현재 시각을 기록한다.
- 동의하지 않으면 `confirmed_at`을 `None`으로 둔다.
- 사용자가 다르다고 느낀 영역은 `area`에 저장한다.

## 7. `flush()`와 `commit()`의 차이

이 파일에서 자주 사용하는 `flush()`는 변경 SQL을 DB에 보내고 생성된 ID 등을 사용할 수 있게 하지만, 거래를 최종 확정하지는 않는다.

```text
repository.flush()
→ 같은 요청 안에서는 변경 내용을 볼 수 있음
→ 아직 되돌릴 수 있음

api.commit()
→ 변경 내용 최종 확정
```

API 처리 중 문제가 발생하면 `rollback()`으로 같은 거래의 변경을 취소할 수 있다.

## 8. 이 파일이 하지 않는 일

- 다음 대화 주제를 결정하지 않는다.
- LLM을 호출하지 않는다.
- 신뢰도나 정확도를 계산하지 않는다.
- HTTP 상태코드를 만들지 않는다.



---

# `features/persona/service.py`

## 전체 코드

```python
"""결정하는 곳.

흐름(무슨 주제를 언제 다룰까)은 전부 여기서 정하고,
agents는 "어떻게 말할까"만 맡는다.
"""

from __future__ import annotations

import logging

from .agents import (
    ConversationAgent,
    ExtractionAgent,
    TaggingAgent,
)
from .models import OnboardingSession, PersonaRecord
from .repository import PersonaRepository
from .schemas import (
    ALL_DIMENSIONS,
    CONFIDENCE_HIGH,
    CONFIDENCE_LABEL,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_WEIGHT,
    DEFAULT_SCORE,
    MIN_ANSWERS_TO_FINISH,
    SCORED,
    SUPPLEMENTS,
    TEXTUAL,
    TOPICS,
    TOPICS_BY_ID,
    Change,
    Gap,
    HistoryItem,
    Narrative,
    PersonaResponse,
    Topic,
    TurnResponse,
    Weight,
)

logger = logging.getLogger(__name__)


# ══ 커버리지 ═══════════════════════════════════════════════


class Coverage:
    """각 차원에 근거가 몇 건 쌓였는지. DB의 JSON과 오간다."""

    def __init__(self, data: dict | None = None) -> None:
        data = data or {}
        self.primary: dict[str, int] = {d: data.get("primary", {}).get(d, 0) for d in ALL_DIMENSIONS}
        self.secondary: dict[str, int] = {d: data.get("secondary", {}).get(d, 0) for d in ALL_DIMENSIONS}

    def apply(self, primary: list[str], secondary: list[str] | None = None) -> None:
        for d in primary:
            if d in self.primary:
                self.primary[d] += 1
        for d in secondary or []:
            if d in self.secondary:
                self.secondary[d] += 1

    def empty_dimensions(self) -> set[str]:
        return {d for d, n in self.primary.items() if n == 0}

    def has_primary(self, dimension: str) -> bool:
        return self.primary.get(dimension, 0) > 0

    def to_dict(self) -> dict:
        return {"primary": dict(self.primary), "secondary": dict(self.secondary)}


# ══ 주제 선택 (흐름 = 코드) ════════════════════════════════


def next_topic(
    coverage: Coverage,
    turn_index: int,
    total_turns: int,
    used_topic_ids: list[str],
) -> Topic | None:
    empty = coverage.empty_dimensions()
    remaining = total_turns - turn_index

    # 마지막 턴은 클로징 주제로 고정.
    # 커버리지 점수만으로 고르면 orientation(선택지라 제일 빠름)이
    # 중간에 뽑혀 "마지막이에요" 흐름이 깨진다.
    if remaining == 1:
        for t in TOPICS:
            if t.is_closing and t.id not in used_topic_ids:
                return t

    def allowed(t: Topic) -> bool:
        if t.id in used_topic_ids:
            return False
        if t.is_closing:
            return False  # 위에서만 선택
        if turn_index == 0 and t.weight != Weight.LIGHT:
            return False  # 첫 턴은 아이스브레이킹
        if t.weight == Weight.HEAVY and turn_index < 5:
            return False  # 무거운 건 중반 이후
        if t.weight == Weight.HEAVY and remaining <= 2:
            return False  # 무겁게 끝내지 않기
        return True

    candidates = [t for t in TOPICS if allowed(t)]
    if not candidates:
        # 배치 규칙 때문에 후보가 비는 경우 — 10주제·10턴이면 turn 8 에서 실제로 생긴다
        # (무거운 주제 2개가 5~7턴에 다 못 들어가면 "마지막 2턴 금지"에 걸려 남는다).
        # 안 묻고 끝내면 그 차원이 영영 비므로, 규칙을 풀고 남은 것 중 가벼운 순으로 묻는다.
        candidates = [t for t in TOPICS if t.id not in used_topic_ids and not t.is_closing]
    if not candidates:
        return None

    # 빈 차원을 가장 많이 채우는 주제 우선, 동점이면 가벼운 쪽
    return min(
        candidates,
        key=lambda t: (-len(set(t.covers) & empty), int(t.weight)),
    )


class UnknownDimension(Exception):
    pass


class NoPersonaYet(Exception):
    """아직 /build 를 안 한 세션."""


class TooFewAnswers(Exception):
    """건너뛰기·끝내기는 MIN_ANSWERS_TO_FINISH 개 이상 답한 뒤에만."""

    def __init__(self, answered: int) -> None:
        self.answered = answered
        super().__init__(f"{answered} answered, need {MIN_ANSWERS_TO_FINISH}")


# ══ 서술 검증 ══════════════════════════════════════════════
# 서술이 점수와 정면으로 모순되는 흔한 경우만 잡는다. 걸리면 서술만 버리고 점수는 살린다 —
# 재생성은 호출이 하나 더 들어가므로. (차원, 점수 조건, 서술에 있으면 안 되는 표현)

_CONTRADICTIONS = [
    ("avoidance", lambda v: v >= 65, ("밀착", "늘 함께", "항상 붙어", "모든 걸 공유")),
    ("avoidance", lambda v: v <= 35, ("각자의 생활을 중시", "독립적인 거리", "거리를 두는")),
    ("anxiety", lambda v: v <= 35, ("불안해하는", "관계를 의심", "많이 신경 쓰는")),
    ("problem_solving", lambda v: v >= 65, ("갈등을 덮", "흐지부지", "피하는 편")),
    ("compliance", lambda v: v <= 35, ("무조건 맞춰", "일방적으로 수용")),
    ("seriousness", lambda v: v >= 65, ("가볍게 만나", "가볍게 알아가")),
    ("seriousness", lambda v: v <= 35, ("진지한 만남", "오래 만날 사람을 찾")),
]


def narrative_contradiction(scores: dict[str, int], narrative: Narrative) -> str | None:
    text = " ".join([narrative.headline, narrative.body, *narrative.traits])
    for dim, cond, phrases in _CONTRADICTIONS:
        if cond(scores.get(dim, DEFAULT_SCORE)):
            for ph in phrases:
                if ph in text:
                    return f"{dim}={scores[dim]} vs '{ph}'"
    return None


# ══ 신뢰도 · 정확도 · 갭 · 변화 ═══════════════════════════


def confidence_of(has_value: bool, primary_count: int) -> str:
    """주 근거 2건 이상 HIGH · 1건 MEDIUM · 0건(또는 모델이 값을 안 냄) LOW."""
    if not has_value or primary_count == 0:
        return CONFIDENCE_LOW
    return CONFIDENCE_MEDIUM if primary_count == 1 else CONFIDENCE_HIGH


def accuracy_of(confidence: dict[str, str]) -> int:
    """0~100. 사용자에게 보여주는 '정확도' — 대화할수록 올라가는 숫자 하나."""
    if not SCORED:
        return 0
    total = sum(CONFIDENCE_WEIGHT[confidence.get(k, CONFIDENCE_LOW)] for k in SCORED)
    return round(100 * total / len(SCORED))


def next_supplement(session: OnboardingSession, dimension: str) -> str | None:
    """그 차원의 보강 질문 중 아직 안 쓴 첫 번째. 다 썼으면 None."""
    used = sum(1 for t in session.turns if t.topic_id == f"supplement:{dimension}")
    bank = SUPPLEMENTS[dimension]
    return bank[used] if used < len(bank) else None


def gaps_of(session: OnboardingSession, confidence: dict[str, str]) -> list[Gap]:
    """근거 부족한 차원 목록. LOW 먼저, 그 안에서는 SCORED 순서."""
    order = {CONFIDENCE_LOW: 0, CONFIDENCE_MEDIUM: 1}
    gaps = [
        Gap(
            dimension=k,
            label=d.label,
            area=d.area,
            confidence=confidence[k],
            confidence_label=CONFIDENCE_LABEL[confidence[k]],
            question=next_supplement(session, k),
        )
        for k, d in SCORED.items()
        if confidence.get(k) in order
    ]
    return sorted(gaps, key=lambda g: order[g.confidence])


def changes_between(previous: PersonaRecord | None, current: PersonaRecord) -> list[Change]:
    """이전 버전 대비 — 점수 ±10 이상, 신뢰도 등급 변화."""
    if previous is None:
        return []
    out: list[Change] = []
    for k, d in SCORED.items():
        a, b = previous.scores.get(k, DEFAULT_SCORE), current.scores.get(k, DEFAULT_SCORE)
        if abs(b - a) >= 10:
            out.append(Change(dimension=k, label=d.label, kind="score", before=str(a), after=str(b)))
        ca, cb = previous.confidence.get(k, CONFIDENCE_LOW), current.confidence.get(k, CONFIDENCE_LOW)
        if ca != cb:
            out.append(
                Change(
                    dimension=k,
                    label=d.label,
                    kind="confidence",
                    before=CONFIDENCE_LABEL[ca],
                    after=CONFIDENCE_LABEL[cb],
                )
            )
    return out


def persona_response(
    record: PersonaRecord,
    gaps: list[Gap] | None = None,
    changes: list[Change] | None = None,
) -> PersonaResponse:
    """DB 행 → API 모델. 온보딩 밖(simulation·practice)에서도 쓰므로 세션 없이 만들 수 있다.

    gaps·changes 는 온보딩 화면에서만 의미가 있어 호출부가 넣어준다."""
    return PersonaResponse(
        persona_id=record.id,
        version=record.version,
        scores=record.scores,
        confidence=record.confidence,
        narrative=Narrative.model_validate(record.narrative) if record.narrative else None,
        accuracy=accuracy_of(record.confidence),
        gaps=gaps or [],
        changes=changes or [],
        confirmed=record.confirmed_at is not None,
        generated_at=record.created_at,
        **{k: record.texts.get(k, []) for k in TEXTUAL},
    )


# ══ 서비스 ═════════════════════════════════════════════════


class OnboardingService:
    def __init__(self, repo: PersonaRepository) -> None:
        self.repo = repo
        self.conversation = ConversationAgent()
        self.tagging = TaggingAgent()
        self.extraction = ExtractionAgent()

    # ── 내부 헬퍼 ─────────────────────────────────────────

    @staticmethod
    def _history(session: OnboardingSession) -> list[dict]:
        """DB의 턴들을 LLM 메시지 형식으로. 답이 없는 턴(건너뜀·대기 중)은 통째로 뺀다 —
        assistant 메시지가 연달아 두 번 오면 모델이 흐름을 잃는다."""
        messages: list[dict] = []
        for turn in session.turns:
            if not turn.answer:
                continue
            messages.append({"role": "assistant", "content": turn.question})
            messages.append({"role": "user", "content": turn.answer})
        return messages

    @staticmethod
    def _answered(session: OnboardingSession) -> int:
        return sum(1 for t in session.turns if t.answer)

    def _controls(self, session: OnboardingSession) -> dict:
        answered = self._answered(session)
        ok = answered >= MIN_ANSWERS_TO_FINISH
        return {"answered": answered, "can_skip": ok, "can_finish": ok}

    async def _ask_next(self, session: OnboardingSession) -> TurnResponse:
        coverage = Coverage(session.coverage)
        topic = next_topic(coverage, session.turn_index, session.total_turns, session.used_topic_ids)

        if topic is None or session.turn_index >= session.total_turns:
            return TurnResponse(
                session_id=session.id,
                utterance="오늘 얘기 재밌었어요. 지금 대화로 페르소나를 만들고 있어요.",
                progress=f"{session.total_turns}/{session.total_turns}",
                done=True,
                answered=self._answered(session),
            )

        utterance = await self.conversation.generate(
            history=self._history(session),
            topic=topic,
            turn_index=session.turn_index,
            total_turns=session.total_turns,
            nickname=session.nickname,
        )
        await self.repo.add_question(session, topic.id, utterance.text, utterance.source)

        return TurnResponse(
            session_id=session.id,
            utterance=utterance.text,
            choices=list(topic.choices) if topic.choices else None,
            progress=f"{session.turn_index + 1}/{session.total_turns}",
            **self._controls(session),
        )

    # ── 공개 API ──────────────────────────────────────────

    async def start(self, nickname: str, total_turns: int, user_id: str | None = None) -> TurnResponse:
        session = await self.repo.create_session(nickname, total_turns, user_id)
        return await self._ask_next(session)

    async def submit_answer(self, session: OnboardingSession, answer: str) -> TurnResponse:
        topic = TOPICS_BY_ID[session.pending_topic_id]
        question = session.turns[-1].question

        tags = await self.tagging.tag(question, answer)

        coverage = Coverage(session.coverage)
        if tags is None:
            # 태깅 실패 시 주제가 커버하기로 한 차원을 그대로 인정
            coverage.apply(list(topic.covers), list(topic.also_touches))
        else:
            coverage.apply(tags.primary, tags.secondary)

        await self.repo.record_answer(
            session,
            answer,
            tags.model_dump() if tags else None,
            coverage.to_dict(),
        )
        return await self._ask_next(session)

    async def skip(self, session: OnboardingSession) -> TurnResponse:
        """이 질문은 건너뛰고 다음 질문으로. 답한 턴이 MIN_ANSWERS_TO_FINISH 미만이면 거부."""
        if self._answered(session) < MIN_ANSWERS_TO_FINISH:
            raise TooFewAnswers(self._answered(session))
        await self.repo.skip_question(session)
        return await self._ask_next(session)

    async def finish(self, session: OnboardingSession) -> TurnResponse:
        """여기서 대화를 끝낸다. 이후 /build 는 지금까지의 답변만으로 페르소나를 만든다."""
        if self._answered(session) < MIN_ANSWERS_TO_FINISH:
            raise TooFewAnswers(self._answered(session))
        await self.repo.finish_early(session)
        return await self._ask_next(session)  # is_done → 마무리 발화

    async def build_persona(self, session: OnboardingSession) -> PersonaResponse:
        """대화 전체 → 새 페르소나 버전. 재빌드(보강 문답 뒤)도 이 함수."""
        raw = await self.extraction.extract(self._history(session))
        coverage = Coverage(session.coverage)

        scores: dict[str, int] = {}
        confidence: dict[str, str] = {}
        for key in SCORED:
            value = getattr(raw, key, None)
            scores[key] = value if value is not None else DEFAULT_SCORE
            confidence[key] = confidence_of(value is not None, coverage.primary.get(key, 0))

        texts = {key: getattr(raw, key, []) for key in TEXTUAL}

        narrative = raw.narrative
        if narrative is not None:
            why = narrative_contradiction(scores, narrative)
            if why:
                logger.warning("narrative contradicts scores (%s) — dropped", why)
                narrative = None

        record, previous = await self.repo.save_persona(
            session, scores, texts, confidence, narrative.model_dump() if narrative else None
        )
        return self._to_response(session, record, previous)

    def _to_response(
        self, session: OnboardingSession, record: PersonaRecord, previous: PersonaRecord | None
    ) -> PersonaResponse:
        return persona_response(
            record,
            gaps=gaps_of(session, record.confidence),
            changes=changes_between(previous, record),
        )

    async def get_latest(self, session: OnboardingSession) -> PersonaResponse:
        record = await self.repo.latest_persona(session.id)
        if record is None:
            raise NoPersonaYet
        previous = await self.repo.latest_before(record) if record.previous_id else None
        return self._to_response(session, record, previous)

    async def history(self, session: OnboardingSession) -> list[HistoryItem]:
        return [
            HistoryItem(
                persona_id=r.id,
                version=r.version,
                accuracy=accuracy_of(r.confidence),
                headline=(r.narrative or {}).get("headline"),
                confirmed=r.confirmed_at is not None,
                created_at=r.created_at,
            )
            for r in await self.repo.persona_history(session.id)
        ]

    async def supplement(self, session: OnboardingSession, dimension: str, answer: str) -> PersonaResponse:
        """보강 문답 한 건 받고 즉시 재빌드. 사용자는 '알려줬더니 정확해졌다'를 바로 본다."""
        if dimension not in SUPPLEMENTS:
            raise UnknownDimension(dimension)
        if await self.repo.latest_persona(session.id) is None:
            raise NoPersonaYet
        question = next_supplement(session, dimension)
        if question is None:
            raise UnknownDimension(f"{dimension}: 보강 질문을 다 썼습니다")

        coverage = Coverage(session.coverage)
        coverage.apply([dimension])  # 그 차원을 겨눈 질문이므로 주 근거로 인정. 태깅 호출 없음
        await self.repo.add_supplement(session, dimension, question, answer, coverage.to_dict())
        return await self.build_persona(session)

    async def feedback(self, session: OnboardingSession, agree: bool, area: str | None) -> PersonaResponse:
        record = await self.repo.latest_persona(session.id)
        if record is None:
            raise NoPersonaYet
        await self.repo.set_feedback(record, agree, area)
        previous = await self.repo.latest_before(record) if record.previous_id else None
        return self._to_response(session, record, previous)
```

## 1. 파일의 역할

`service.py`는 persona 온보딩의 **전체 진행 순서와 업무 규칙을 결정하는 중심 파일**이다.

쉽게 구분하면 다음과 같다.

- `agents.py`: LLM이 어떻게 말하고 분석할지 담당
- `repository.py`: DB에서 무엇을 읽고 쓸지 담당
- `service.py`: 언제 무엇을 실행하고 어떤 결과를 만들지 담당
- `api.py`: HTTP 요청과 상태코드 담당

`service.py`는 Agent와 Repository를 연결해 하나의 완성된 기능으로 만든다.

## 2. `Coverage` 클래스

```python
class Coverage:
```

각 성향에 대한 대화 근거가 몇 번 쌓였는지 관리한다.

근거를 두 종류로 나눈다.

- `primary`: 답변이 직접 알려준 주 근거
- `secondary`: 답변에서 간접적으로 추론한 보조 근거

### `__init__()`

```python
def __init__(self, data: dict | None = None) -> None:
```

DB에 저장된 커버리지 JSON을 받아 객체 형태로 만든다.

데이터가 없으면 빈 딕셔너리로 시작하고, `ALL_DIMENSIONS`의 모든 항목을 0으로 초기화한다.

```python
self.primary = {
    "avoidance": 0,
    "anxiety": 0,
    ...
}
```

기존 데이터에 빠진 새 차원이 있어도 기본값 0으로 채울 수 있다.

### `apply()`

```python
def apply(
    self,
    primary: list[str],
    secondary: list[str] | None = None,
) -> None:
```

새 답변에서 발견된 근거 수를 1씩 증가시킨다.

- `primary`에 있는 유효한 성향은 주 근거 증가
- `secondary`에 있는 유효한 성향은 보조 근거 증가
- 등록되지 않은 이름은 무시
- `secondary=None`이면 빈 목록처럼 처리

### `empty_dimensions()`

주 근거가 한 번도 없는 성향 이름을 `set`으로 반환한다.

다음 질문을 선택할 때 아직 비어 있는 성향을 많이 채우는 주제를 우선하기 위해 사용한다.

### `has_primary()`

특정 성향에 주 근거가 한 건 이상 있는지 `True` 또는 `False`로 반환한다.

### `to_dict()`

현재 커버리지를 DB JSON 컬럼에 저장할 수 있는 일반 딕셔너리로 바꾼다.

```python
{
    "primary": {...},
    "secondary": {...},
}
```

## 3. `next_topic()` 함수

```python
def next_topic(
    coverage: Coverage,
    turn_index: int,
    total_turns: int,
    used_topic_ids: list[str],
) -> Topic | None:
```

현재 진행 상태를 보고 다음에 어떤 주제로 대화할지 결정한다.

LLM이 자유롭게 주제를 고르는 것이 아니라 코드에 적힌 규칙으로 선택한다.

### 입력값

| 값 | 의미 |
|---|---|
| `coverage` | 각 성향에 근거가 얼마나 쌓였는지 |
| `turn_index` | 현재 턴 번호 |
| `total_turns` | 전체 턴 수 |
| `used_topic_ids` | 이미 사용한 주제 목록 |

### 기본 계산

```python
empty = coverage.empty_dimensions()
remaining = total_turns - turn_index
```

- `empty`: 아직 주 근거가 없는 성향
- `remaining`: 현재를 포함해 남은 턴 수

### 마지막 턴 규칙

남은 턴이 하나면 `is_closing=True`인 마무리 주제를 우선 반환한다.

마무리 주제가 중간에 선택되면 마지막 흐름이 깨질 수 있어 마지막 턴에서 따로 처리한다.

### 내부 `allowed()` 함수

각 주제가 현재 턴에 사용 가능한지 검사한다.

다음 주제는 제외한다.

1. 이미 사용한 주제
2. 마지막 턴이 아닌데 마무리 전용인 주제
3. 첫 턴인데 가벼운 주제가 아닌 경우
4. 5번째 인덱스 이전의 무거운 주제
5. 마지막 두 턴 안의 무거운 주제

### 후보가 없을 때

배치 규칙이 너무 엄격해서 후보가 하나도 남지 않으면 일부 규칙을 풀고, 아직 사용하지 않은 비마무리 주제 중에서 다시 고른다.

그래도 후보가 없으면 `None`을 반환한다.

### 최종 선택 기준

```python
min(
    candidates,
    key=lambda t: (
        -len(set(t.covers) & empty),
        int(t.weight),
    ),
)
```

우선순위는 다음과 같다.

1. 아직 비어 있는 성향을 가장 많이 채우는 주제
2. 같다면 더 가벼운 주제

비어 있는 성향 수에 `-`를 붙였기 때문에 `min()`에서도 많이 채우는 주제가 먼저 온다.

## 4. 서비스 예외

### `UnknownDimension`

사용자가 요청한 보강 성향이 존재하지 않거나, 해당 성향의 보강 질문을 모두 사용했을 때 발생한다.

### `NoPersonaYet`

아직 최초 페르소나 빌드를 하지 않았는데 조회, 보강, 피드백을 시도했을 때 발생한다.

### `TooFewAnswers`

최소 답변 수를 채우지 않은 상태에서 질문 건너뛰기나 조기 종료를 시도했을 때 발생한다.

#### `__init__()`

현재 실제 답변 수를 `answered`에 저장하고, 필요한 최소 답변 수가 포함된 오류 메시지를 만든다.

```text
2 answered, need 3
```

## 5. 서술과 점수의 모순 검사

### `_CONTRADICTIONS`

점수와 사용자용 설명문이 정면으로 충돌하는 흔한 경우를 정의한 규칙 목록이다.

예:

- `avoidance`가 높은데 설명에는 “항상 붙어 있음”이라고 적힌 경우
- `problem_solving`이 높은데 “갈등을 덮는다”고 적힌 경우
- `seriousness`가 높은데 “가볍게 만난다”고 적힌 경우

### `narrative_contradiction()`

```python
def narrative_contradiction(
    scores: dict[str, int],
    narrative: Narrative,
) -> str | None:
```

점수와 설명문이 모순되는지 확인한다.

### 처리 순서

1. headline, body, traits를 하나의 문자열로 합친다.
2. 각 모순 규칙의 점수 조건을 확인한다.
3. 조건이 맞으면 금지 표현이 설명에 있는지 찾는다.
4. 모순을 발견하면 이유 문자열을 반환한다.
5. 문제가 없으면 `None`을 반환한다.

모순이 발견되어도 점수는 버리지 않고 설명문만 제거한다. LLM을 다시 호출하면 비용과 시간이 추가되기 때문이다.

## 6. 신뢰도와 정확도 함수

### `confidence_of()`

```python
def confidence_of(
    has_value: bool,
    primary_count: int,
) -> str:
```

한 성향의 근거 상태를 `LOW`, `MEDIUM`, `HIGH`로 바꾼다.

| 조건 | 결과 |
|---|---|
| LLM이 값을 추출하지 못함 | LOW |
| 주 근거 0개 | LOW |
| 주 근거 1개 | MEDIUM |
| 주 근거 2개 이상 | HIGH |

### `accuracy_of()`

모든 점수형 성향의 신뢰도를 하나의 0~100 숫자로 바꾼다.

1. LOW=0.0, MEDIUM=0.6, HIGH=1.0 가중치를 더한다.
2. 전체 점수형 성향 수로 나눈다.
3. 100을 곱하고 반올림한다.

이 값은 모델의 실제 예측 정확도가 아니라, 근거가 얼마나 채워졌는지 보여주는 진행 지표이다.

점수형 성향이 하나도 없으면 0을 반환한다.

## 7. 보강 질문과 부족한 항목

### `next_supplement()`

```python
def next_supplement(
    session: OnboardingSession,
    dimension: str,
) -> str | None:
```

특정 성향에 대해 아직 사용하지 않은 첫 번째 보강 질문을 반환한다.

세션 턴 중 `topic_id == "supplement:성향"`인 턴 수를 세어 이미 사용한 질문 개수를 알아낸다.

질문을 모두 사용했으면 `None`을 반환한다.

### `gaps_of()`

근거가 부족한 점수형 성향을 `Gap` 목록으로 만든다.

- `LOW`와 `MEDIUM`만 포함
- `LOW`를 먼저 배치
- 같은 신뢰도 안에서는 `SCORED` 정의 순서 유지
- 각 항목에 다음 보강 질문 포함

## 8. 버전 변화 계산

### `changes_between()`

```python
def changes_between(
    previous: PersonaRecord | None,
    current: PersonaRecord,
) -> list[Change]:
```

직전 페르소나와 현재 페르소나의 차이를 계산한다.

### 변화로 인정하는 조건

- 점수가 10 이상 증가하거나 감소한 경우
- 신뢰도 등급이 달라진 경우

첫 버전이라 이전 기록이 없으면 빈 목록을 반환한다.

하나의 성향에서 점수와 신뢰도가 모두 바뀌면 `Change`가 두 개 만들어질 수 있다.

## 9. `persona_response()`

```python
def persona_response(
    record: PersonaRecord,
    gaps: list[Gap] | None = None,
    changes: list[Change] | None = None,
) -> PersonaResponse:
```

DB의 `PersonaRecord`를 API 응답용 `PersonaResponse`로 바꾼다.

### 변환 내용

- DB ID와 버전 전달
- 점수와 신뢰도 전달
- narrative 딕셔너리를 `Narrative` 모델로 검증
- `accuracy_of()`로 정확도 계산
- gaps와 changes가 없으면 빈 목록 사용
- `confirmed_at` 존재 여부를 `confirmed` 불리언으로 변환
- `texts` 딕셔너리에서 관심사와 데이트 취향을 꺼냄
- DB 생성 시각을 `generated_at`으로 전달

simulation과 practice에서도 이 함수를 재사용할 수 있도록 온보딩 세션 없이 DB 행 하나만으로 기본 응답을 만들 수 있게 되어 있다.

## 10. `OnboardingService` 클래스

온보딩의 시작부터 페르소나 생성, 보강, 피드백까지 전체 흐름을 조정한다.

### `__init__()`

```python
def __init__(self, repo: PersonaRepository) -> None:
```

DB 작업을 담당할 Repository를 받고 세 Agent를 생성한다.

```python
self.repo = repo
self.conversation = ConversationAgent()
self.tagging = TaggingAgent()
self.extraction = ExtractionAgent()
```

## 11. 내부 보조 메서드

### `_history()`

DB의 `ConversationTurn` 목록을 OpenAI 메시지 형식으로 바꾼다.

답변이 없는 턴은 질문까지 통째로 제외한다.

```python
[
    {"role": "assistant", "content": turn.question},
    {"role": "user", "content": turn.answer},
]
```

답변 없는 assistant 질문을 넣으면 assistant 메시지가 연속될 수 있어 모델이 대화 흐름을 잘못 이해할 수 있기 때문이다.

### `_answered()`

세션 턴 중 실제 답변이 있는 턴의 개수를 센다. 건너뛴 턴은 포함하지 않는다.

### `_controls()`

현재 답변 수와 화면 버튼 활성화 상태를 딕셔너리로 만든다.

```python
{
    "answered": 3,
    "can_skip": True,
    "can_finish": True,
}
```

답변 수가 `MIN_ANSWERS_TO_FINISH` 이상이면 건너뛰기와 끝내기가 모두 가능하다.

### `_ask_next()`

현재 상태에서 다음 질문을 만들고 저장한 뒤 `TurnResponse`를 반환한다.

#### 처리 순서

1. 세션의 JSON 커버리지로 `Coverage`를 만든다.
2. `next_topic()`으로 다음 주제를 고른다.
3. 주제가 없거나 전체 턴이 끝났으면 완료 응답을 반환한다.
4. 진행 중이면 `ConversationAgent.generate()`를 호출한다.
5. 답변 완료된 과거 대화, 주제, 턴 번호, 닉네임을 전달한다.
6. 생성된 질문을 `repo.add_question()`으로 저장한다.
7. 선택지, 진행도, 버튼 상태가 포함된 `TurnResponse`를 반환한다.

완료 응답에서는 `done=True`를 사용하고 마무리 문장을 반환한다.

## 12. 공개 서비스 메서드

### `start()`

```python
async def start(
    nickname: str,
    total_turns: int,
    user_id: str | None = None,
) -> TurnResponse:
```

새 온보딩을 시작한다.

1. Repository로 새 세션 생성
2. `_ask_next()`로 첫 질문 생성
3. 첫 `TurnResponse` 반환

### `submit_answer()`

현재 질문에 대한 답변을 처리하고 다음 질문을 반환한다.

#### 처리 순서

1. `pending_topic_id`로 현재 `Topic`을 찾는다.
2. 마지막 턴에서 실제 질문 문장을 가져온다.
3. `TaggingAgent.tag()`로 질문과 답변을 분석한다.
4. 현재 커버리지를 `Coverage` 객체로 만든다.
5. 태깅이 실패했다면 주제의 `covers`와 `also_touches`를 기본값으로 적용한다.
6. 태깅이 성공했다면 `tags.primary`와 `tags.secondary`를 적용한다.
7. Repository에 답변, 태그, 새 커버리지를 저장한다.
8. `_ask_next()`로 다음 질문을 반환한다.

태깅 실패가 전체 대화를 막지 않도록 폴백 규칙이 있다.

### `skip()`

현재 질문을 건너뛰고 다음 질문으로 이동한다.

답변 수가 최소 기준보다 적으면 `TooFewAnswers`를 발생시킨다. 조건을 만족하면 Repository가 현재 질문을 skipped 처리하고 `_ask_next()`를 실행한다.

### `finish()`

현재까지의 답변만 사용하고 대화를 조기 종료한다.

- 최소 답변 수를 확인한다.
- `repo.finish_early()`로 전체 턴 수를 현재 진행 위치까지 줄인다.
- `_ask_next()`를 호출하면 완료 상태가 되어 마무리 응답이 나온다.

### `build_persona()`

전체 대화를 분석해 새 페르소나 버전을 만든다.

#### 1단계: LLM 추출

```python
raw = await self.extraction.extract(
    self._history(session)
)
```

답변이 있는 전체 대화를 `ExtractionAgent`에 전달한다.

#### 2단계: 점수와 신뢰도 만들기

각 `SCORED` 항목에 대해:

- LLM 값이 있으면 그 점수 사용
- 값이 없으면 `DEFAULT_SCORE=50` 사용
- 추출 여부와 주 근거 수로 신뢰도 계산

#### 3단계: 텍스트 항목 만들기

`TEXTUAL`에 정의된 관심사, 일상, 데이트 취향 목록을 `raw`에서 꺼낸다.

#### 4단계: 서술 모순 검사

`narrative_contradiction()`에서 점수와 narrative가 충돌하면 경고 로그를 남기고 narrative만 `None`으로 만든다.

#### 5단계: 새 버전 저장

Repository에 점수, 텍스트, 신뢰도, narrative를 새 페르소나 버전으로 저장한다.

#### 6단계: API 응답 만들기

새 버전과 직전 버전을 `_to_response()`에 넘겨 부족한 항목과 변화를 함께 반환한다.

### `_to_response()`

DB 모델을 `PersonaResponse`로 바꾸면서 온보딩 화면에 필요한 추가 정보를 넣는다.

- `gaps_of()`: 근거 부족 항목
- `changes_between()`: 직전 버전과 달라진 항목
- `persona_response()`: 최종 API 모델 변환

### `get_latest()`

세션의 최신 페르소나를 조회한다.

- 결과가 없으면 `NoPersonaYet`
- 직전 버전이 있으면 함께 조회
- `_to_response()`로 gaps와 changes까지 포함해 반환

### `history()`

해당 세션의 모든 페르소나 버전을 최신순 `HistoryItem` 목록으로 바꾼다.

각 버전에서 다음 값만 간단히 꺼낸다.

- ID와 버전
- 정확도
- narrative headline
- 사용자 확인 여부
- 생성 시각

### `supplement()`

부족한 성향에 대한 보강 답변을 저장하고 즉시 페르소나를 다시 만든다.

#### 확인 사항

1. 요청한 성향이 `SUPPLEMENTS`에 없으면 `UnknownDimension`
2. 최초 페르소나가 없으면 `NoPersonaYet`
3. 사용할 보강 질문이 더 없으면 `UnknownDimension`

#### 처리

1. 해당 성향의 다음 보강 질문 선택
2. 그 성향을 직접 물은 것이므로 주 근거 1건 증가
3. 질문과 답변을 Repository에 저장
4. `build_persona()`를 다시 실행
5. 새 페르소나 버전 반환

### `feedback()`

최신 페르소나에 대한 사용자 의견을 저장한다.

1. 최신 페르소나 조회
2. 없으면 `NoPersonaYet`
3. Repository에 동의 여부와 영역 저장
4. 직전 버전이 있으면 조회
5. 최신 `PersonaResponse` 반환

## 13. 대표 흐름: 답변 한 번 제출

```text
api.answer()
→ service.submit_answer()
→ TaggingAgent.tag()
→ Coverage.apply()
→ repository.record_answer()
→ service._ask_next()
→ next_topic()
→ ConversationAgent.generate()
→ repository.add_question()
→ TurnResponse
```

## 14. 대표 흐름: 페르소나 만들기

```text
api.build()
→ service.build_persona()
→ 전체 대화 기록 변환
→ ExtractionAgent.extract()
→ 점수 기본값·신뢰도 계산
→ narrative 모순 검사
→ repository.save_persona()
→ gaps와 changes 계산
→ PersonaResponse
```

## 15. 이 파일이 하지 않는 일

- HTTP URL과 상태코드를 정의하지 않는다.
- SQLAlchemy 쿼리를 직접 작성하지 않는다.
- OpenRouter API를 직접 호출하지 않는다.
- 테이블과 API 데이터 클래스 자체를 정의하지 않는다.

이 파일은 각 계층을 연결하고 서비스 규칙을 실행하는 일에 집중한다.

## 16. 변경할 때 주의할 점

- `next_topic()` 규칙을 바꾸면 첫 턴, 무거운 주제, 마지막 턴 순서가 함께 영향을 받는다.
- 태깅 실패 폴백을 제거하면 커버리지가 채워지지 않아 같은 성향이 계속 부족할 수 있다.
- 기본 점수 50은 “중간 성향”이 아니라 “근거 부족”이라는 점을 유지해야 한다.
- `accuracy`는 통계적인 정확도가 아니라 신뢰도 기반 진행 지표이다.
- 페르소나 재빌드는 기존 버전을 덮어쓰지 않고 새 버전을 추가한다.



---

# `features/persona/api.py`

## 전체 코드

```python
"""라우터. 검증과 상태코드만 담당하고 로직은 service로 넘긴다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .agents import BuildFailed
from .repository import PersonaRepository
from .schemas import (
    AnswerRequest,
    FeedbackRequest,
    HistoryItem,
    PersonaResponse,
    StartRequest,
    SupplementRequest,
    TurnResponse,
)
from .service import NoPersonaYet, OnboardingService, TooFewAnswers, UnknownDimension

router = APIRouter(prefix="/v1/persona", tags=["persona"])


def get_service(db: AsyncSession = Depends(get_db)) -> OnboardingService:
    return OnboardingService(PersonaRepository(db))


@router.post("/onboarding/start", response_model=TurnResponse)
async def start(
    req: StartRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    result = await service.start(req.nickname, req.total_turns, req.user_id)
    await db.commit()
    return result


@router.post("/onboarding/{session_id}/answer", response_model=TurnResponse)
async def answer(
    session_id: str,
    req: AnswerRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session.pending_topic_id is None:
        raise HTTPException(409, "no pending question")

    result = await service.submit_answer(session, req.answer)
    await db.commit()
    return result


@router.post("/onboarding/{session_id}/skip", response_model=TurnResponse)
async def skip(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    """이 질문 건너뛰기. 답한 턴이 3개 이상일 때만."""
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session.pending_topic_id is None:
        raise HTTPException(409, "no pending question")

    try:
        result = await service.skip(session)
    except TooFewAnswers as e:
        raise HTTPException(409, f"need more answers before skipping ({e.answered} answered)") from e
    await db.commit()
    return result


@router.post("/onboarding/{session_id}/finish", response_model=TurnResponse)
async def finish(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    """대화 여기서 끝내기. 답한 턴이 3개 이상일 때만. 이후 /build 로 페르소나 생성."""
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    try:
        result = await service.finish(session)
    except TooFewAnswers as e:
        raise HTTPException(409, f"need more answers before finishing ({e.answered} answered)") from e
    await db.commit()
    return result


@router.post("/{session_id}/build", response_model=PersonaResponse)
async def build(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    try:
        result = await service.build_persona(session)
    except BuildFailed as e:
        await db.rollback()
        # 세션은 남는다 — 재시도 가능해야 하므로
        raise HTTPException(503, f"build failed: {e}") from e

    await db.commit()
    return result


# ── 빌드 이후: 조회 · 보강 · 확인 · 이력 ─────────────────


async def _session_or_404(service: OnboardingService, session_id: str):
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


@router.get("/{session_id}", response_model=PersonaResponse)
async def get_persona(session_id: str, service: OnboardingService = Depends(get_service)) -> PersonaResponse:
    """최신 페르소나. (user_id 가 붙으면 /users/{user_id} 로도 열 것)"""
    session = await _session_or_404(service, session_id)
    try:
        return await service.get_latest(session)
    except NoPersonaYet as e:
        raise HTTPException(404, "persona not built yet") from e


@router.get("/{session_id}/history", response_model=list[HistoryItem])
async def history(session_id: str, service: OnboardingService = Depends(get_service)) -> list[HistoryItem]:
    """버전 목록. 최신 먼저."""
    session = await _session_or_404(service, session_id)
    return await service.history(session)


@router.post("/{session_id}/supplement", response_model=PersonaResponse)
async def supplement(
    session_id: str,
    req: SupplementRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    """근거 부족한 차원에 답 하나 더 → 즉시 재빌드 → 새 버전 (changes 포함)."""
    session = await _session_or_404(service, session_id)
    try:
        result = await service.supplement(session, req.dimension, req.answer)
    except NoPersonaYet as e:
        raise HTTPException(409, "build first") from e
    except UnknownDimension as e:
        raise HTTPException(422, str(e)) from e
    except BuildFailed as e:
        await db.rollback()
        raise HTTPException(503, f"build failed: {e}") from e
    await db.commit()
    return result


@router.post("/{session_id}/feedback", response_model=PersonaResponse)
async def feedback(
    session_id: str,
    req: FeedbackRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    """'이대로 좋아요'(agree) 또는 '조금 다른 것 같아요'(area 지정). 다르면 앱이 그 영역의 gaps 로 안내."""
    session = await _session_or_404(service, session_id)
    try:
        result = await service.feedback(session, req.agree, req.area)
    except NoPersonaYet as e:
        raise HTTPException(409, "build first") from e
    await db.commit()
    return result
```

## 1. 파일의 역할

`api.py`는 클라이언트의 HTTP 요청을 받는 **persona 기능의 접수 창구**이다.

이 파일은 다음 일만 담당한다.

1. URL과 HTTP 방식 정의
2. 요청 데이터 검증
3. 필요한 세션이 존재하는지 확인
4. `OnboardingService` 호출
5. 성공하면 DB 커밋
6. 실패하면 알맞은 HTTP 상태코드 반환

대화 주제 선택, 성향 계산 같은 실제 업무 규칙은 `service.py`로 넘긴다.

## 2. 공통 라우터

```python
router = APIRouter(
    prefix="/v1/persona",
    tags=["persona"],
)
```

이 파일의 모든 주소 앞에는 `/v1/persona`가 붙는다. `main.py`의 `/ai/api`까지 합치면 최종 공통 주소는 다음과 같다.

```text
/ai/api/v1/persona
```

## 3. `get_service()`

```python
def get_service(
    db: AsyncSession = Depends(get_db),
) -> OnboardingService:
```

FastAPI가 제공한 DB 세션으로 `PersonaRepository`를 만들고, 그것을 다시 `OnboardingService`에 넣어 반환한다.

```text
AsyncSession
→ PersonaRepository
→ OnboardingService
```

각 요청에서 필요한 객체를 자동으로 조립하는 의존성 함수이다.

## 4. 온보딩 API

### `POST /onboarding/start` — `start()`

새 온보딩 세션을 만들고 첫 질문을 반환한다.

#### 요청

`StartRequest`를 사용한다.

```json
{
  "nickname": "민수",
  "total_turns": 10,
  "user_id": "user-123"
}
```

#### 처리

1. `service.start()` 호출
2. 새 세션과 첫 질문 생성
3. DB `commit()`
4. `TurnResponse` 반환

### `POST /onboarding/{session_id}/answer` — `answer()`

현재 질문에 대한 사용자 답변을 제출한다.

#### 확인 사항

- 세션이 없으면 `404 session not found`
- 대기 중인 질문이 없으면 `409 no pending question`

정상일 때 `service.submit_answer()`가 답변 태깅, 저장, 다음 질문 생성을 처리한다.

### `POST /onboarding/{session_id}/skip` — `skip()`

현재 질문을 건너뛴다.

#### 확인 사항

- 세션이 없으면 404
- 대기 중 질문이 없으면 409
- 최소 답변 수를 채우지 못했으면 409

`TooFewAnswers`에는 현재 실제 답변 수가 들어 있어 오류 메시지에 함께 표시된다.

### `POST /onboarding/{session_id}/finish` — `finish()`

남은 질문을 진행하지 않고 현재까지의 답변으로 대화를 끝낸다.

- 세션이 없으면 404
- 답변이 3개 미만이면 409
- 성공하면 `service.finish()`가 세션 진행 길이를 현재 턴까지로 줄인다.

이 API는 페르소나를 바로 만드는 것이 아니다. 대화 종료 후 별도의 `/build` 호출이 필요하다.

### `POST /{session_id}/build` — `build()`

전체 대화를 분석해 새 페르소나 버전을 만든다.

#### 처리

1. 세션 조회
2. `service.build_persona()` 호출
3. 성공하면 커밋하고 `PersonaResponse` 반환

LLM 호출 또는 출력 검증이 실패해 `BuildFailed`가 발생하면 다음을 수행한다.

```python
await db.rollback()
raise HTTPException(503, ...)
```

세션 자체는 남겨서 사용자가 다시 빌드를 시도할 수 있게 한다.

## 5. 공통 세션 조회 함수

### `_session_or_404()`

세션을 조회하고, 없으면 즉시 404 오류를 발생시킨다.

빌드 이후 API에서 같은 코드를 반복하지 않도록 만든 내부 보조 함수이다. 이름 앞의 `_`는 내부용이라는 뜻이다.

## 6. 빌드 이후 API

### `GET /{session_id}` — `get_persona()`

해당 세션의 최신 페르소나를 반환한다.

- 세션이 없으면 404
- 세션은 있지만 아직 빌드하지 않았다면 `NoPersonaYet`를 404로 바꾼다.

### `GET /{session_id}/history` — `history()`

해당 세션에서 만든 페르소나 버전 목록을 최신순으로 반환한다.

각 항목에는 페르소나 ID, 버전, 정확도, 한 줄 소개, 확인 여부, 생성 시각이 들어 있다.

### `POST /{session_id}/supplement` — `supplement()`

근거가 부족한 성향에 보강 답변을 하나 추가하고 즉시 새 페르소나 버전을 만든다.

#### 요청 예시

```json
{
  "dimension": "contact_rhythm",
  "answer": "하루에 두세 번 정도가 편해요."
}
```

#### 오류 변환

| 내부 예외 | HTTP 상태 | 의미 |
|---|---:|---|
| `NoPersonaYet` | 409 | 최초 빌드가 먼저 필요함 |
| `UnknownDimension` | 422 | 없는 성향이거나 보강 질문을 모두 사용함 |
| `BuildFailed` | 503 | 보강 후 LLM 재빌드 실패 |

재빌드가 실패하면 DB를 롤백한다.

### `POST /{session_id}/feedback` — `feedback()`

사용자가 페르소나 결과에 동의하는지 저장한다.

```json
{
  "agree": true,
  "area": null
}
```

또는:

```json
{
  "agree": false,
  "area": "communication"
}
```

아직 빌드된 페르소나가 없으면 409를 반환한다.

## 7. API 목록 요약

| 방식 | 주소 | 역할 |
|---|---|---|
| POST | `/onboarding/start` | 새 온보딩 시작 |
| POST | `/onboarding/{id}/answer` | 현재 질문에 답변 |
| POST | `/onboarding/{id}/skip` | 현재 질문 건너뛰기 |
| POST | `/onboarding/{id}/finish` | 온보딩 조기 종료 |
| POST | `/{id}/build` | 대화로 페르소나 생성 |
| GET | `/{id}` | 최신 페르소나 조회 |
| GET | `/{id}/history` | 페르소나 버전 이력 조회 |
| POST | `/{id}/supplement` | 보강 답변 후 재빌드 |
| POST | `/{id}/feedback` | 결과 확인·수정 의견 저장 |

모든 주소 앞에는 실제로 `/ai/api/v1/persona`가 붙는다.

## 8. 상태코드 의미

| 상태코드 | 이 파일에서의 의미 |
|---:|---|
| 404 | 세션이나 페르소나를 찾지 못함 |
| 409 | 현재 진행 상태에서 요청을 수행할 수 없음 |
| 422 | 요청한 성향이 올바르지 않음 |
| 503 | LLM 호출 또는 페르소나 빌드 실패 |

## 9. 이 파일이 하지 않는 일

- SQL 쿼리를 직접 작성하지 않는다.
- 다음 주제를 직접 고르지 않는다.
- LLM 프롬프트를 만들지 않는다.
- 페르소나 점수와 정확도를 직접 계산하지 않는다.



---

# `features/persona/lookup.py`

## 전체 코드

```python
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
```

## 1. 파일의 역할

`lookup.py`는 simulation과 practice 같은 다른 기능이 저장된 페르소나를 **읽기 전용으로 가져가는 공통 입구**이다.

페르소나는 다음 세 방법 중 하나로 찾을 수 있다.

- 특정 `persona_id`
- 특정 `user_id` 사용자의 최신 페르소나
- 특정 `session_id` 온보딩의 최신 페르소나

## 2. `LoadedPersona` 데이터 클래스

```python
@dataclass
class LoadedPersona:
```

DB에서 가져온 원본과 API용 결과, 사용자 닉네임을 한 묶음으로 담는다.

| 필드 | 의미 |
|---|---|
| `record` | DB의 원본 `PersonaRecord` |
| `nickname` | 온보딩 세션에서 사용한 닉네임 |
| `response` | 외부에서 사용하기 좋게 바꾼 `PersonaResponse` |

### `brief` 프로퍼티

```python
@property
def brief(self) -> PersonaBrief:
```

전체 페르소나 대신 화면이나 응답에 넣기 좋은 짧은 요약을 만든다.

포함되는 값은 다음과 같다.

- 페르소나 ID
- 사용자 ID
- 닉네임
- 버전
- 한 줄 소개
- 정확도

`@property`이므로 함수처럼 괄호를 붙이지 않고 사용한다.

```python
loaded.brief
```

## 3. `load_persona()` 함수

```python
async def load_persona(
    db: AsyncSession,
    ref: PersonaRef,
) -> LoadedPersona | None:
```

`PersonaRef`가 가리키는 페르소나를 찾아 `LoadedPersona`로 반환한다.

### 입력

| 값 | 의미 |
|---|---|
| `db` | 현재 요청에서 사용하는 비동기 DB 세션 |
| `ref` | persona/user/session 중 하나의 식별자를 가진 참조 객체 |

### 조회 우선순위

`PersonaRef`는 세 ID 중 정확히 하나만 갖도록 검증되지만, 함수 내부 분기는 다음 순서로 작성되어 있다.

1. `persona_id`가 있으면 정확한 페르소나 버전 조회
2. 아니면 `user_id`가 있으면 사용자의 가장 최근 페르소나 조회
3. 아니면 `session_id`로 해당 온보딩의 최신 페르소나 조회

### 페르소나가 없을 때

```python
if record is None:
    return None
```

이 함수는 HTTP 오류를 직접 만들지 않는다. 호출한 API가 `None`을 보고 404 같은 상태코드로 바꾸게 한다.

### 닉네임 찾기

페르소나에는 닉네임이 직접 저장되지 않으므로 원래 온보딩 세션을 조회한다.

```python
session = await repo.get_session_brief(record.session_id)
```

세션이 없으면 예외적으로 페르소나 ID 앞 6글자를 임시 닉네임으로 사용한다.

```python
nickname = session.nickname if session else record.id[:6]
```

### 반환값 만들기

```python
return LoadedPersona(
    record=record,
    nickname=nickname,
    response=persona_response(record),
)
```

`service.py`의 `persona_response()`를 재사용해 DB 모델을 API용 모델로 바꾼다.

## 4. 전체 흐름

```text
PersonaRef 전달
→ 어떤 ID가 들어 있는지 확인
→ repository로 페르소나 조회
→ 없으면 None
→ 원래 세션에서 닉네임 조회
→ PersonaResponse 생성
→ LoadedPersona 반환
```

## 5. 이 파일이 하지 않는 일

- 페르소나를 새로 만들지 않는다.
- DB 값을 수정하거나 커밋하지 않는다.
- 온보딩 서비스 흐름을 실행하지 않는다.
- 찾지 못한 경우 HTTP 상태코드를 정하지 않는다.



---

# `features/persona/profile.py`

## 전체 코드

```python
"""페르소나를 LLM 프롬프트에 넣을 때 쓰는 '말로 된 프로필'.

점수표를 그대로 주면 모델이 숫자를 연기하지 못한다. 차원마다 양 끝 설명(schemas.SCORED 의 low/high)을
점수에 따라 골라 문장으로 바꾸고, 온보딩에서 뽑은 서술·관심사·데이트 취향을 붙인다.
simulation(대본)·practice(상대 역할) 둘 다 이 한 함수를 쓴다 — 두 곳의 인물이 달라지면 안 되니까.
"""

from __future__ import annotations

from .schemas import CONFIDENCE_LOW, SCORED, PersonaResponse

# 이 밖이면 "뚜렷한 성향"으로 서술한다. 안쪽(36~64)은 중간이라 굳이 말하지 않는다 —
# 근거 부족 기본값 50 이 "중간 성향"으로 연기되는 걸 막기 위해서다.
HIGH_FROM = 65
LOW_TO = 35


def trait_lines(p: PersonaResponse) -> list[str]:
    """점수 → "연락 빈도: 하루 종일 수시로 주고받기" 같은 줄. 근거 부족(LOW)은 뒤에 표시."""
    lines = []
    for key, d in SCORED.items():
        v = p.scores.get(key)
        if v is None:
            continue
        if v >= HIGH_FROM:
            desc = d.high
        elif v <= LOW_TO:
            desc = d.low
        else:
            continue
        low = " (근거 부족 — 약하게만)" if p.confidence.get(key) == CONFIDENCE_LOW else ""
        lines.append(f"- {d.label}: {desc}{low}")
    return lines


def describe(name: str, p: PersonaResponse) -> str:
    """프롬프트에 그대로 붙이는 블록. 이름 · 한 줄 · 성향 · 관심사 · 일상 · 데이트."""
    parts = [f"### {name}"]
    if p.narrative:
        parts.append(f"한 줄: {p.narrative.headline}")
        parts.append(f"설명: {p.narrative.body}")
        if p.narrative.traits:
            parts.append("특징: " + " / ".join(p.narrative.traits))
    traits = trait_lines(p)
    parts.append("뚜렷한 성향:\n" + ("\n".join(traits) if traits else "- (특별히 치우친 성향 없음)"))
    parts.append(f"관심사: {', '.join(p.interests) or '알 수 없음'}")
    parts.append(f"일상: {', '.join(p.routine) or '알 수 없음'}")
    parts.append(
        f"좋아하는 데이트: {', '.join(p.date_prefer) or '알 수 없음'} / 피하는 것: {', '.join(p.date_avoid) or '알 수 없음'}"
    )
    return "\n".join(parts)
```

## 1. 파일의 역할

`profile.py`는 숫자로 저장된 페르소나를 LLM이 이해하기 쉬운 **말로 된 프로필**로 바꾼다.

simulation과 practice가 같은 함수를 사용하므로, 두 기능에서 같은 페르소나가 서로 다르게 표현되는 일을 줄인다.

## 2. 기준값

```python
HIGH_FROM = 65
LOW_TO = 35
```

점수가 다음 범위에 들어갈 때만 뚜렷한 성향으로 설명한다.

| 점수 | 처리 |
|---|---|
| 65 이상 | 해당 성향의 `high` 설명 사용 |
| 35 이하 | 해당 성향의 `low` 설명 사용 |
| 36~64 | 중간 범위이므로 설명에서 생략 |

근거가 없을 때 사용하는 기본 점수 50이 실제 성향처럼 소개되는 것을 막기 위한 기준이다.

## 3. `trait_lines()` 함수

```python
def trait_lines(p: PersonaResponse) -> list[str]:
```

페르소나의 숫자 점수를 사람이 읽을 수 있는 성향 문장 목록으로 바꾼다.

### 입력

- `p`: 점수, 신뢰도, 관심사 등이 들어 있는 `PersonaResponse`

### 처리 순서

1. `SCORED`에 정의된 모든 점수형 성향을 순서대로 확인한다.
2. 페르소나에 해당 점수가 없으면 건너뛴다.
3. 65 이상이면 `Dimension.high` 설명을 선택한다.
4. 35 이하이면 `Dimension.low` 설명을 선택한다.
5. 36~64이면 뚜렷하지 않으므로 결과에 넣지 않는다.
6. 신뢰도가 `LOW`이면 `근거 부족 — 약하게만` 표시를 붙인다.

### 반환 예시

```text
- 연락 빈도: 하루 종일 수시로 주고받기
- 거리 두기: 각자 생활을 중시하고 독립적 거리를 유지 (근거 부족 — 약하게만)
```

반환값은 문자열 리스트이다.

## 4. `describe()` 함수

```python
def describe(name: str, p: PersonaResponse) -> str:
```

하나의 페르소나 정보를 LLM 프롬프트에 바로 붙일 수 있는 문자열 블록으로 만든다.

### 입력

| 값 | 의미 |
|---|---|
| `name` | 프롬프트에서 사용할 사람 이름 |
| `p` | 설명할 `PersonaResponse` |

### 처리 순서

1. `### 이름` 제목을 만든다.
2. `narrative`가 있으면 한 줄 소개, 본문, 특징을 붙인다.
3. `trait_lines()`로 뚜렷한 성향을 붙인다.
4. 뚜렷한 성향이 없으면 `(특별히 치우친 성향 없음)`이라고 쓴다.
5. 관심사와 일상을 쉼표로 연결한다.
6. 좋아하는 데이트와 피하는 것을 한 줄로 정리한다.
7. 모든 부분을 줄바꿈으로 합쳐 반환한다.

### 값이 없는 경우

관심사나 일상 목록이 비어 있으면 `알 수 없음`을 사용한다.

```python
', '.join(p.interests) or '알 수 없음'
```

### 결과 예시

```text
### 민수
한 줄: 독립적이지만 대화가 잘 통하는 관계를 원하는 타입
설명: 각자의 시간을 중요하게 생각하는 편이에요.
특징: 혼자 정리한 뒤 이야기함 / 주말에는 산책을 즐김
뚜렷한 성향:
- 거리 두기: 각자 생활을 중시하고 독립적 거리를 유지
관심사: 러닝, 영화
일상: 주말 산책
좋아하는 데이트: 조용한 카페 / 피하는 것: 시끄러운 술자리
```

## 5. 이 파일이 하지 않는 일

- 새로운 점수를 계산하지 않는다.
- 페르소나를 DB에서 조회하지 않는다.
- LLM을 직접 호출하지 않는다.
- 사용자의 성향을 다시 판단하지 않는다.

이미 만들어진 페르소나를 프롬프트용 문장으로 바꾸는 일만 담당한다.
