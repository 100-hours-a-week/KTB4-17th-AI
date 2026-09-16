# KTB4-17th-AI

별이삼샵 어플리케이션의 AI API 서버입니다. FastAPI 기반으로 구축되었습니다.

## 기술 스택

- Python 3.12
- FastAPI, Uvicorn
- SQLAlchemy(asyncio)
- Anthropic SDK (페르소나 LLM)
- aiosqlite (로컬 플레이그라운드)
- 패키지 매니저: [uv](https://docs.astral.sh/uv/)

## 사전 준비물

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (없다면 아래 방법으로 설치)

  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # Homebrew
  brew install uv
  ```

## 시작하기

### 1. 저장소 clone

```bash
git clone https://github.com/100-hours-a-week/KTB4-17th-AI.git
cd KTB4-17th-AI
```

### 2. 가상환경 세팅 및 라이브러리 설치

`uv`는 `pyproject.toml`과 `uv.lock`을 기준으로 가상환경 생성과 의존성 설치를 한 번에 처리합니다.

```bash
uv sync
```

- 프로젝트 루트에 `.venv` 디렉터리가 생성되고, `uv.lock`에 고정된 버전으로 의존성이 설치됩니다.
- 이후 모든 명령은 `uv run <명령어>` 형태로 실행하면 가상환경을 직접 activate 하지 않아도 됩니다.
- 직접 activate 하려면 아래 명령을 사용하세요.

  ```bash
  source .venv/bin/activate   # macOS / Linux
  ```

### 3. 환경 변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 키를 채워주세요. 앱 시작 시 `.env`를 읽습니다.

페르소나 온보딩의 프로덕션 LLM은 Anthropic을 사용합니다. 플레이그라운드에서는 요청 헤더 또는 환경 변수로 키를 넘깁니다.

| 변수 | 용도 |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude 호출 (프로덕션 `agents.py`, 플레이그라운드 Anthropic 경로) |
| `GEMINI_API_KEY` | 플레이그라운드 Gemini 경로 |
| `LLM_PROVIDER` | 플레이그라운드 기본 프로바이더 (`gemini` 또는 `anthropic`) |

### 4. 서버 실행

엔트리포인트는 `app.main:app`입니다. (`모듈경로:FastAPI 인스턴스 이름`)
`uvicorn app:main`처럼 쓰면 `app` 패키지에서 `main` 속성을 찾아 실패합니다.

```bash
uv run uvicorn app.main:app --reload --port 8000
```

가상환경을 이미 activate 했다면 `uv run` 없이 실행해도 됩니다.

```bash
uvicorn app.main:app --reload --port 8000
```

서버가 실행되면 아래 주소에서 확인할 수 있습니다.

- API: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

프로덕션 엔트리포인트의 API prefix는 `/ai/api`입니다.

> `app.main`의 `get_db()`는 아직 프로젝트 공통 세션으로 교체되지 않았습니다. 페르소나 온보딩을 로컬에서 돌려보려면 아래 플레이그라운드를 사용하세요.

### 5. 로컬 플레이그라운드 (페르소나)

`dev/`는 `app/`을 import만 하고 한 줄도 고치지 않습니다. DB는 SQLite로, LLM은 요청별 키로 바깥에서 갈아끼웁니다. Docker 이미지에는 넣지 않는 전제입니다.

```bash
uv run uvicorn dev.persona.playground:app --reload --port 8000
```

- UI: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs

브라우저에서 API 키와 프로바이더를 넣거나, 헤더로 전달할 수 있습니다.

- `X-Api-Key`: Gemini 또는 Anthropic 키
- `X-Provider`: `gemini` 또는 `anthropic` (키가 `AIza`로 시작하면 Gemini로 추정)
- `X-Tagging: 0`: 턴별 태깅 LLM 호출을 건너뛰고 주제 기본 커버리지로 폴백

## 현재 구현 범위

| 기능 | 경로 | 상태 |
| --- | --- | --- |
| 페르소나 온보딩 | `app/features/persona/` | 구현됨. 소개팅 상대 "하루"와 대화한 뒤 연애 성향 점수를 뽑습니다. |
| 연습 대화 | `app/features/practice/` | 라우터만 있습니다. |
| 시뮬레이션 | `app/features/simulation/` | 라우터만 있습니다. |

페르소나 온보딩 API (`/ai/api` prefix 기준):

| 메서드 | 경로 | 역할 |
| --- | --- | --- |
| POST | `/v1/persona/onboarding/start` | 세션을 만들고 첫 질문을 반환합니다. |
| POST | `/v1/persona/onboarding/{session_id}/answer` | 답을 받고 다음 질문을 반환합니다. |
| POST | `/v1/persona/{session_id}/build` | 대화 전체에서 페르소나를 추출합니다. |

기본 턴 수는 10회(5~15)입니다. 플레이그라운드 UI는 10으로 고정합니다.

## 개발 도구

### Lint / Format (ruff)

```bash
uv run ruff format --check . && uv run ruff check .
```

자동 수정이 필요하면 다음을 사용하세요.

```bash
uv run ruff format .
uv run ruff check --fix .
```

### 의존성 추가

```bash
uv add <package>          # 실행 의존성 추가
uv add --dev <package>    # 개발 의존성 추가
```

## 프로젝트 구조

```
app/
├── main.py                 # FastAPI 앱 엔트리포인트 (`/ai/api`)
└── features/
    ├── persona/            # AI 페르소나 온보딩·생성
    │   ├── api.py          # 라우터. 검증·상태코드만
    │   ├── service.py      # 주제 선택·턴 진행·페르소나 조립
    │   ├── repository.py   # SQLAlchemy 접근
    │   ├── models.py       # 세션·턴·페르소나 테이블
    │   ├── schemas.py      # 차원·주제 카탈로그 + API 모델
    │   └── agents.py       # LLM 호출 (대화/태깅/추출)
    ├── practice/           # 페르소나와의 연습 대화
    └── simulation/         # 페르소나 간 대화 시뮬레이션
dev/                        # 로컬 플레이그라운드 (`app/`을 수정하지 않음)
├── _shared/                # SQLite·LLM 교체, 앱 뼈대
└── persona/
    ├── playground.py
    └── static/             # 브라우저 테스트 UI
```

레이어 규칙:

- `api.py` — 검증과 상태코드만. 로직은 service.
- `service.py` — 무슨 주제를 언제 다룰지는 코드가 정합니다.
- `agents.py` — 어떻게 말할지만 맡습니다. 프롬프트도 여기 있습니다.
- `repository.py` — service는 SQLAlchemy를 직접 만지지 않습니다.
- `dev/` — `app/`을 import만 합니다.

## 담당자

- [lena.cho](https://github.com/HyeerinCho)
- [yuta.jeong](https://github.com/hrunj1230)
