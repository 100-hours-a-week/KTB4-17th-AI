**Server Git & Development Convention**

**Branch Strategy**

**브랜치 종류**

우리는 **Git-Flow**를 간소화하여 사용합니다.

브랜치는 이슈 생성 → 브랜치 생성 순서대로 진행합니다.

!image.png

- **main** : 배포 가능한 최종 버전 브랜치 (직접 Push 금지)
    
    배포는 dev → main 머지로 진행 (release 브랜치 사용 안 함)
    
- **dev** : 개발 통합 브랜치
    
    dev는 항상 빌드/테스트 통과 상태를 유지, 부분 완료 기능은 feat
    
    - **feat/{기능명}** : 새로운 기능을 개발
    - **fix/{버그명}** : 버그를 수정
- **hotfix/{이슈-요약}** : main 배포 후 발생한 긴급 버그 수정

**브랜치 명명 예시**

feat/[닉네임]-[기능명]으로 기재합니다. 하나의 기능 개발이 완료되면 dev로 merge하는 PR을 올립니다.

- `feat/garnet-login` (로그인 기능)
- `fix/garnet-db-connection` (DB 연결 오류 수정)

---

**Commit**

**커밋 형식**

```
<Type> : <Subject>

<Body> (선택사항)

<Footer> (선택사항)

```

**Commit Type**

커밋 타입과 **`!`** 사용은 Conventional Commits v1.0.0을 따른다.

https://www.conventionalcommits.org/en/v1.0.0/

**공통**

- `feat`: 새로운 기능 추가 (Feature)
- `fix`: 버그 수정
- `docs`: 문서 수정
- `style`: 코드 포맷팅, 세미콜론 누락
- `refactor`: 코드 리팩터링
- `test`: 테스트 코드 추가/수정
- `chore`: 빌드 설정, 패키지 매니저 설정, 파일 이동 등

**Breaking Change (호환성 파괴)**

API 응답 변경, DB 스키마 변경 등 기존 코드와 호환되지 않는 경우 **Type 뒤에 `!`를 붙여** 명시합니다.

- **Not Break**: `feat: 로그인 기능` (안전)
- **Break**: `feat!: 로그인 테이블 스키마 변경` (주의 요망)

**커밋 작성 규칙**

1. **Subject (제목)**: 명령조, 현재 시제 사용. 끝에 마침표(`.`) 금지.
    - 50자 이내, 한글/영문 섞여도 OK.
2. **Body (본문)**: '무엇을', '왜' 변경했는지 설명. (Breaking Change 시 필수 작성)

**작성 예시**

**일반 기능**

```
feat: 교육과정 데이터 DB 적재 로직 구현

- JSON 파일 파싱하여 줄글(Content)로 변환하는 로직 추가
- MariaDB docs 테이블 Insert 쿼리 작성
- pymysql 라이브러리 의존성 추가

Resolves: #12
```

**Breaking Change**

```
feat!: 로그인 테이블 스키마 변경 (nickname -> name)

기존 name 스키마를 익명성을 위해 nickname 스키마로 변경

BREAKING CHANGE: 기존 nickname 필드는 제거되고 name 필드로 교체됨
Resolves: #45
```

---

**Issue & PR Convention**

**Issue Template**

이슈 생성 시 아래 형식을 따릅니다.

```
[분류][담당자] 작업 내용
```

- **`fix`**, **`chore`**, **`refactor`**  등 사용가능
- **예시**:
    - `[feat][가넷] 회원가입 API 유효성 검사 추가`
    - **`[fix][가넷] 로그인 API 500 에러 수정`**
    

**PR Title**

PR 제목은 해당 작업의 이슈 번호와 변경 사항을 명시합니다.

PR은 반드시 dev 또는 main(hotfix)로만 머지하고, feat↔feat 간 직접 머지는 금지합니다.

```
[#<이슈번호>] 변경 사항 요약
```

- **예시**: `[#3] 로그인 기능 구현`
- **예시**: `[#11] 회원 엔티티 스키마 변경`

**PR 템플릿**

```
## 관련 이슈
- closes #이슈번호

## 변경 내용
- 변경 사항을 간단히 bullet로 적어주세요.
- 예) 로그인 API 응답 포맷 변경
- 예) 회원가입 유효성 검사 추가

## 체크리스트
- [ ] 로컬에서 빌드/테스트 통과
- [ ] 관련 문서(README, API 문서 등) 업데이트
- [ ] Breaking Change 여부 확인 (있다면 아래에 명시)

## Breaking Change (선택)
- 예) 로그인 응답 스키마 변경으로 인해 기존 클라이언트 수정 필요

## 기타 참고 사항 (선택)
- 리뷰어가 알아두면 좋을 추가 내용, 스크린샷, 테스트 방법 등을 적어주세요.
```

---

**github rule 로 추가한 규칙들** 

- **적용 대상 브랜치: dev, main**
    
    
1. **병합 전 PR 필수**
    - dev, main에 직접 push로 merge안됨
    - 다른 브랜치에서 작업한 뒤 PR을 통해서만 반영해야함
2. **병합 전 승인 1명 필수**
    - 다른 팀원 한 명이 반드시 PR을 검토하고 승인해야함
3. **새 커밋이 올라오면 기존 승인 취소**
    - 승인 이후 코드가 변경되면 기존 승인이 무효화되고 변경된 최신 코드를 팀원이 다시 검토하고 승인해야 함.
4. **`마지막으로 코드를 push한 사람이 아닌 다른 사람의 승인 필요 (fullstack요청기능)`**
    - 자신이 올린 최신 변경 사항을 본인이 승인할 수 없음.
5. **브랜치 삭제 제한**
    - 브랜치를 일반 사용자가 실수로 삭제하지 못하고 admin권한이 있어야함
6. **강제 push 금지**
    - git push --force로 커밋 기록을 덮어쓸 수 없음.
7. **`CI 통과는 아직 필수가 아님(CI좀더 테스트 해보고 적용 예정)`**
    
    →  Require status checks to pass : off