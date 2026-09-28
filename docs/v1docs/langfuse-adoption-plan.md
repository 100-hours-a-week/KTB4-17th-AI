# Langfuse 도입 기획서

## 1. 목적

별이삼샵 AI 서버의 LLM 호출을 Langfuse에서 한 흐름으로 관측한다. 장애가 발생했을 때 서버 로그만으로 추측하지 않고, 어떤 기능과 세션에서 어떤 모델 호출이 느렸거나 실패했는지 확인할 수 있게 하는 것이 목적이다.

이번 도입의 핵심 목표는 다음과 같다.

- 기능별 LLM 호출량과 응답 시간을 비교한다.
- 입력·출력·전체 토큰 사용량을 확인한다.
- 공급자 오류, 타임아웃, 출력 파싱·검증 오류를 추적한다.
- 내부 사용자 ID와 대화 세션 ID로 관련 호출을 묶는다.
- 관측 장애가 서비스 응답을 실패시키지 않게 한다.

프롬프트 관리, 데이터셋 평가, LLM-as-a-Judge, 비용 알림 자동화는 이번 범위에 포함하지 않는다.

## 2. 현재 구조와 도입 방식

서버는 LangChain 실행기를 사용하지 않고 `openai.AsyncOpenAI`로 OpenRouter 또는 OpenAI 호환 서버를 직접 호출한다. 따라서 Langfuse의 OpenAI 호환 래퍼를 사용한다.

구성은 다음과 같다.

1. `langfuse.openai.AsyncOpenAI`가 실제 LLM 요청과 응답을 감싼다.
2. 래퍼가 모델, 프롬프트, 응답, 지연시간, usage와 공급자 오류를 generation으로 기록한다.
3. `@observe`가 일반 coroutine의 기능 단위 span을 만들고 파싱·스키마 검증 오류를 기록한다. 연습 대화 async generator는 타임아웃 문맥 보존을 위해 명시적 observation context를 사용한다.
4. 공통 메타데이터 빌더가 기능, 작업, 사용자, 세션, 턴 정보를 일관된 형식으로 만든다.
5. FastAPI 종료 시 Langfuse 클라이언트를 종료하여 버퍼에 남은 이벤트를 전송한다.

Langfuse 전송 실패는 비즈니스 요청을 실패시키지 않는다. SDK의 비동기 배치 전송을 사용하며 애플리케이션의 기존 폴백 정책을 유지한다.

연습 대화에는 `@observe`를 직접 붙이지 않는다. Langfuse의 async-generator 래퍼가 사용자 코드의 `asyncio.timeout` task 문맥을 바꿀 수 있다는 [공식 저장소 이슈](https://github.com/langfuse/langfuse/issues/13349)가 있어, generator 내부에서 `start_as_current_observation()` context를 직접 연다.

## 3. 관측 대상

| 기능 | generation 이름 | workflow span 이름 | 주요 메타데이터 | 실패 처리 |
|---|---|---|---|---|
| 온보딩 발화 | `persona-conversation` | `persona-conversation-workflow` | `turnIndex`, `topicId` | 시드/템플릿 폴백, span WARNING |
| 답변 태깅 | `persona-tagging` | `persona-tagging-workflow` | `turnIndex`, `topicId` | 주제 기본 커버리지 사용, span WARNING |
| 페르소나 추출 | `persona-extraction` | `persona-extraction-workflow` | `answeredTurns` | 기존 규칙 기반 폴백 또는 오류 반환 |
| 연습 대화 | `practice-reply` | `practice-reply-workflow` | `messageIndex`, `opening` | 첫 청크 전 실패는 문구 폴백, 중간 실패는 오류 이벤트 |
| 시뮬레이션 | `simulation-run` | `simulation-run-workflow` | `requestedTurns` | 오류 이유를 포함한 503 |
| 리포트 미리보기 | `simulation-report-preview` | `simulation-report-preview-workflow` | `useLlm`, `transcriptTurns` | 템플릿 서술 폴백 |

## 4. 메타데이터 정책

모든 LLM 호출은 아래 공통 필드를 사용한다.

| 필드 | 목적 |
|---|---|
| `feature` | `persona`, `practice`, `simulation` 기능 구분 |
| `operation` | 기능 내부의 세부 작업 구분 |
| `langfuse_user_id` | 동일 사용자의 호출을 묶는 내부 식별자 |
| `langfuse_session_id` | 동일 대화/작업 세션의 호출을 묶는 내부 식별자 |
| `langfuse_tags` | 기능 및 작업 기준의 빠른 필터 |

추가 필드는 낮은 카디널리티 또는 문제 재현에 필요한 내부 값만 사용한다. 닉네임, 대화 본문, 이메일, 전화번호, 인증 토큰과 API 키는 메타데이터에 넣지 않는다. 상세 규칙은 [Langfuse 메타데이터 가이드](./langfuse-metadata-guide.md)를 따른다.

## 5. 응답 시간과 토큰

- 비스트리밍 호출은 OpenAI 호환 응답의 `usage`를 Langfuse 래퍼가 수집한다.
- 스트리밍 호출은 `stream_options={"include_usage": true}`를 전송한다.
- 스트리밍 응답의 마지막 usage 전용 청크에는 choices가 없을 수 있으므로 기존 `if chunk.choices` 검사를 유지한다.
- Langfuse에서 generation latency와 스트리밍 time-to-first-token을 확인한다.
- OpenRouter나 로컬 모델이 usage를 반환하지 않으면 토큰 또는 비용이 비어 있을 수 있다. 이 경우 공급자 설정과 Langfuse 모델 정의를 별도로 점검한다.

## 6. 오류 관측

오류는 두 층으로 구분한다.

1. **generation 오류**: 인증 실패, 모델 없음, 연결 오류, 공급자 5xx, 스트림 중단처럼 LLM 호출 경계에서 발생한 오류다. OpenAI 래퍼가 ERROR와 status message로 기록한다.
2. **애플리케이션 오류**: JSON 파싱 실패, Pydantic 검증 실패, 의미 규칙 위반처럼 응답을 받은 뒤 발생한 오류다. 기능별 `@observe` span에서 기록한다.

서비스가 폴백으로 정상 응답을 반환하는 경우 전체 요청을 ERROR로 오해하지 않도록 기능 span을 WARNING으로 기록한다. 원래 공급자 오류 generation은 하위 observation에 그대로 남는다.

## 7. 환경변수와 배포

필수 환경변수:

```dotenv
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

권장 환경변수:

```dotenv
LANGFUSE_TRACING_ENVIRONMENT=production
LANGFUSE_RELEASE=<git-sha-or-release-name>
LANGFUSE_DEBUG=false
```

`LANGFUSE_BASE_URL`에는 Cloud 리전 주소 또는 self-hosted 루트 주소를 넣으며 `/api/public`을 붙이지 않는다. 키는 저장소에 커밋하지 않는다.

## 8. 개인정보와 접근 통제

OpenAI 래퍼는 기본적으로 LLM 프롬프트와 응답을 기록한다. 메타데이터에 개인정보를 넣지 않는 것만으로 본문 개인정보 문제가 해결되지는 않는다.

운영 배포 전 다음을 확인한다.

- Langfuse 프로젝트 접근 권한을 최소 인원으로 제한한다.
- 서비스 개인정보 처리방침과 보관 기간에 LLM 관측 데이터가 포함되는지 확인한다.
- 개발·스테이징·운영 환경을 `LANGFUSE_TRACING_ENVIRONMENT`로 분리한다.
- 실제 사용자 데이터로 연결 시험을 하지 않고 테스트 계정을 사용한다.
- 필요하면 샘플링 또는 입출력 마스킹 정책을 별도 단계로 적용한다.

## 9. 구현 범위

- `pyproject.toml`, `uv.lock`: Langfuse Python SDK v4 의존성
- `app/core/observability.py`: 메타데이터 생성과 속성 전파
- `app/features/*/agents.py`: Langfuse OpenAI 래퍼, 이름, 관측 span
- `app/features/*/service.py`: 사용자·세션·턴 메타데이터 주입
- `app/features/simulation/report.py`: 리포트 관측 컨텍스트 전달
- `app/main.py`: 프로세스 종료 시 SDK shutdown
- `.env.example`: 환경·릴리스·디버그 설정 예시
- `tests/`: 메타데이터 정책과 호출 옵션 회귀 테스트

## 10. 검증 및 완료 조건

### 자동 검증

- `ruff check .`
- `ruff format --check .`
- `pytest`

### 연결 검증

개발 환경에서만 다음 명령으로 인증을 확인한다.

```bash
uv run python -c 'from dotenv import load_dotenv; load_dotenv(); from langfuse import get_client; print(get_client().auth_check())'
```

`True`가 나오면 테스트 계정으로 각 기능을 한 번씩 호출한다. Langfuse 화면 반영은 비동기 수집 때문에 즉시 보이지 않을 수 있다.

### 대시보드 확인

- 여섯 generation과 대응하는 `-workflow` span이 기능별로 구분되는가
- `user_id`와 `session_id` 필터가 동작하는가
- 비스트리밍 호출의 input/output/total token이 보이는가
- `practice-reply`에 token과 TTFT가 보이는가
- 의도적으로 잘못된 모델을 사용했을 때 ERROR와 status message가 보이는가
- JSON 또는 스키마 검증 실패가 상위 span 오류로 보이는가
- 폴백 호출이 WARNING으로 구분되는가

## 11. 단계적 배포

1. 로컬에서 인증과 단일 호출을 확인한다.
2. 스테이징에서 전체 수집으로 각 기능의 필드와 개인정보 범위를 검토한다.
3. 운영에서 초기 24시간 호출량, 오류율, 지연시간과 Langfuse 수집 실패 로그를 관찰한다.
4. 데이터량이 예상보다 크면 `LANGFUSE_SAMPLE_RATE`를 적용한다.
5. 모델별 기준 latency와 오류율을 정한 뒤 대시보드·알림 자동화를 후속 작업으로 진행한다.
