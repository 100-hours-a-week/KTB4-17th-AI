# Langfuse 메타데이터 가이드

## 1. 메타데이터를 설정하는 이유

Langfuse는 메타데이터가 없어도 프롬프트, 응답, 지연시간과 토큰을 기록할 수 있다. 하지만 호출이 많아지면 기록만 보고 어느 기능과 사용자 흐름에서 발생했는지 알기 어렵다.

메타데이터는 다음 질문에 답하기 위한 검색·분류 정보다.

- 연습 대화만 느린가, 모든 기능이 느린가
- 특정 세션에서 오류가 반복됐는가
- 온보딩의 어느 단계에서 폴백이 자주 발생하는가
- 시뮬레이션 턴 수가 커질 때 지연시간과 토큰이 얼마나 증가하는가
- 같은 사용자가 경험한 연속 오류를 한 번에 볼 수 있는가

메타데이터는 프롬프트 본문을 복제하는 장소가 아니다. “호출을 찾기 위한 색인”으로 사용한다.

## 2. 필드별 역할

| 필드 | 역할 | 예시 |
|---|---|---|
| generation `name` | 실제 모델 호출 종류 | `practice-reply` |
| workflow span `name` | 파싱·검증·폴백까지 포함한 작업 | `practice-reply-workflow` |
| `feature` | 상위 기능 | `practice` |
| `operation` | 기능 내부 작업 | `reply` |
| `langfuse_user_id` | 동일 사용자 묶음 | 내부 사용자 ID |
| `langfuse_session_id` | 동일 대화/작업 흐름 묶음 | 연습 대화 세션 ID |
| `langfuse_tags` | 빠른 다중 필터 | `practice`, `reply`, `streaming` |
| 일반 metadata | 재현에 필요한 실행 조건 | `opening=false`, `messageIndex=4` |

`user_id`는 사람 단위, `session_id`는 한 번의 대화 흐름 단위다. 둘을 바꾸어 사용하면 사용자별·세션별 집계가 왜곡된다.

## 3. 프로젝트 표준 스키마

### 공통 필드

```python
{
    "feature": "practice",
    "operation": "reply",
    "langfuse_user_id": "internal-user-id",
    "langfuse_session_id": "practice-session-id",
    "langfuse_tags": ["practice", "reply", "streaming"],
}
```

### 기능별 추가 필드

| operation | 추가 필드 | 의미 |
|---|---|---|
| `conversation` | `turnIndex`, `topicId` | 온보딩 턴과 선택된 주제 |
| `tagging` | `turnIndex`, `topicId` | 태깅 대상 턴과 주제 |
| `extraction` | `answeredTurns` | 추출에 사용한 유효 답변 수 |
| `reply` | `messageIndex`, `opening` | 연습 대화 메시지 위치와 첫 인사 여부 |
| `run` | `requestedTurns` | 요청한 시뮬레이션 왕복 수 |
| `reportPreview` | `useLlm`, `transcriptTurns` | LLM 사용 여부와 대화록 길이 |

추가 필드 이름은 Langfuse 속성 전파 제한과 검색 일관성을 위해 영문 camelCase를 사용한다.

## 4. 코드 사용법

기능 코드에서 예약 키를 직접 조립하지 않고 공통 빌더를 사용한다.

```python
from app.core.observability import build_langfuse_metadata

metadata = build_langfuse_metadata(
    feature="practice",
    operation="reply",
    user_id=session.user_id,
    session_id=session.id,
    tags=("streaming",),
    messageIndex=session.message_count,
    opening=False,
)
```

생성된 값을 agent에 전달하고 Langfuse OpenAI 호출에도 그대로 전달한다.

```python
stream = await client.chat.completions.create(
    name="practice-reply",
    model=settings.llm_model,
    messages=messages,
    stream=True,
    stream_options={"include_usage": True},
    metadata=metadata,
)
```

`@observe`로 만든 상위 span에는 `propagate_langfuse_metadata()`를 사용한다. 이렇게 하면 상위 span과 하위 generation이 같은 사용자·세션·태그를 갖는다.

## 5. 허용하는 값

- 내부의 비식별 사용자 ID와 세션 ID
- 기능명과 작업명
- 턴 번호, 메시지 번호, 요청 개수
- 참/거짓 실행 옵션
- 코드에 정의된 주제 ID
- `local`, `staging`, `production` 같은 환경 구분
- Git SHA 또는 릴리스 이름

일반 메타데이터는 문자열, 정수, 실수, 불리언처럼 단순한 값으로 제한한다. 큰 JSON, 대화 배열, 전체 요청 객체를 넣지 않는다.

## 6. 금지하는 값

- API 키, 액세스 토큰, Authorization 헤더
- 이메일, 전화번호, 실명, 닉네임
- 사용자 발화나 모델 응답 전문
- 시스템 프롬프트 전문
- 페르소나 점수·관심사 전체 객체
- DB 접속 문자열과 내부 서버 주소
- stack trace 전체 또는 공급자 원문 응답 전체

프롬프트와 응답은 OpenAI 연동의 별도 input/output 필드에 기록된다. 메타데이터에 다시 복사하면 개인정보 노출 범위와 저장량만 늘어난다.

## 7. 카디널리티 원칙

`feature`, `operation`, tag는 가능한 값의 종류가 적어야 대시보드 집계가 유용하다. 호출마다 새로운 tag를 만들거나 `user-123` 같은 사용자 ID를 tag로 넣지 않는다.

- 좋은 tag: `practice`, `reply`, `streaming`
- 나쁜 tag: `user-123`, `session-8f21...`, 전체 오류 문장

사용자와 세션은 전용 필드를 사용한다. 세부 오류 내용은 status message와 애플리케이션 로그로 확인한다.

## 8. 오류와 폴백 해석

- 하위 generation이 `ERROR`이면 실제 LLM 호출 경계에서 실패한 것이다.
- 상위 기능 span이 `ERROR`이면 파싱·검증을 포함해 작업 자체가 실패한 것이다.
- 상위 기능 span이 `WARNING`이고 API 응답이 성공했다면 폴백으로 사용자 흐름을 유지한 것이다.
- 상위 span이 성공이고 하위 generation만 실패할 수도 있다. 이는 오류를 잡아 폴백을 반환한 정상적인 구조다.

오류율을 계산할 때 generation 오류율과 최종 기능 실패율을 구분한다.

## 9. 운영 조회 예시

### 연습 대화 지연 조사

1. observation name을 `practice-reply`로 필터한다.
2. environment를 운영으로 제한한다.
3. latency 또는 TTFT 내림차순으로 정렬한다.
4. `messageIndex`, `opening`, 모델과 token 사용량을 비교한다.

### 특정 세션 오류 조사

1. session ID로 필터한다.
2. 시간순으로 trace를 확인한다.
3. ERROR generation의 status message를 확인한다.
4. 상위 span이 WARNING인지 ERROR인지 확인해 폴백 성공 여부를 판단한다.

### 기능별 토큰 비교

1. `feature` 또는 observation name으로 그룹화한다.
2. input/output token 평균과 상위 백분위를 비교한다.
3. `requestedTurns`, `answeredTurns`, `transcriptTurns`와 증가 추세를 확인한다.

## 10. 새 LLM 호출 추가 체크리스트

- 기존 observation 이름 규칙을 따랐는가
- `feature`와 `operation`을 지정했는가
- 사용자·세션 ID를 알 수 있는 경우 전용 필드에 넣었는가
- 개인정보와 본문을 metadata에 넣지 않았는가
- 스트리밍이면 `include_usage`를 설정했는가
- 공급자 오류뿐 아니라 파싱·검증 오류도 상위 span에서 보이는가
- 폴백이면 WARNING으로 구분되는가
- metadata builder와 호출 옵션 테스트를 추가했는가
