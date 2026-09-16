# KTB4-17th-AI

별이삼샵 어플리케이션의 AI API 서버입니다. FastAPI 기반으로 구축되었습니다.

## 기술 스택

- Python 3.12
- FastAPI, Uvicorn
- SQLAlchemy(asyncio), Alembic, asyncpg
- LangChain(OpenAI), LangGraph
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

`.env` 파일을 열어 필요한 값을 채워주세요. (현재 `.env.example`에는 정의된 값이 없으며, 추가되는 대로 갱신 예정입니다.)

### 4. 서버 실행

```bash
uv run uvicorn app.main:app --reload
```

서버가 실행되면 아래 주소에서 확인할 수 있습니다.

- API: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

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

자세한 내용은 [`docs/ruff-guide.md`](docs/ruff-guide.md)를 참고하세요.

### 의존성 추가

```bash
uv add <package>          # 실행 의존성 추가
uv add --dev <package>    # 개발 의존성 추가
```

## 프로젝트 구조

```
app/
├── main.py            # FastAPI 앱 엔트리포인트
├── common/            # 공통 스키마 등
└── features/
    ├── persona/       # AI 페르소나 생성/조회
    ├── practice/      # 페르소나와의 연습 대화
    └── simulation/    # 페르소나 간 대화 시뮬레이션
```

## 담당자

- [lena.cho](https://github.com/HyeerinCho)
- [yuta.jeong](https://github.com/hrunj1230)
