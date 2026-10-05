# Profile Trust AI API 백엔드 연동 계약

이 문서는 메인 백엔드가 Profile Trust AI 서비스에 전달해야 하는 요청과 받아야 하는 응답을 정의한다. 기계 판독 가능한 전체 스키마는 [`docs/v2docs/openapi.json`](./openapi.json), 실행 중인 개발 서버에서는 `GET /openapi.json`으로 제공한다.

## 공통 규칙

| 항목 | 값 |
|---|---|
| Base path | `/ai/api` |
| 인증 헤더 | `X-Internal-Api-Key: <key>` |
| 이미지 형식 | JPEG, JPG, PNG |
| 이미지 최대 크기 | 기본 10 MiB |
| 영상 형식 | MP4, WebM, MOV |
| 영상 최대 크기 | 기본 25 MiB |
| 파일 요청 형식 | `multipart/form-data` |

사용자 로그인, 접근 권한, 현재 사용자의 대표사진 소유권, 인증 시도 횟수 제한은 모두 메인 백엔드 책임이다. AI 서비스는 사용자 액세스·리프레시 토큰을 받지 않으며 사용자 ID, 계정 상태 또는 역할을 판정하지 않는다.

`X-Internal-Api-Key`는 사용자 인증 헤더가 아니라 메인 백엔드에서 AI 서비스로 들어오는 호출을 보호하는 서비스 간 자격 증명이다. `INTERNAL_API_KEY`가 비어 있는 로컬 환경에서는 생략할 수 있지만 운영 환경에서는 반드시 설정하고 메인 백엔드만 이 API를 호출해야 한다.

공통 오류 본문은 일반적으로 다음 형태다.

```json
{
  "detail": "오류 설명"
}
```

| HTTP 상태 | 의미 |
|---|---|
| `400` | 만료되었거나 변조된 챌린지 토큰 |
| `401` | 내부 API 키 불일치 |
| `413` | 파일 크기 제한 초과 |
| `415` | 지원하지 않는 이미지·영상 MIME 타입 |
| `422` | 필수 필드 누락, 파일 디코딩 실패 또는 요청 검증 실패 |
| `503` | 필수 AI 모델 파일을 사용할 수 없음 |

## 1. 대표사진 정면·품질 검사

`POST /ai/api/v1/profile-trust/photos/primary/frontal-check`

메인 백엔드가 받는 대표사진을 저장 또는 확정하기 전에 호출한다.

### 요청 스키마

`Content-Type: multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `image` | binary file | 예 | 대표사진 JPEG, JPG 또는 PNG |

### 응답 스키마

```ts
type PrimaryPhotoResponse = {
  decision: "PASS" | "RETRY";
  reason_codes: PrimaryPhotoReasonCode[];
  quality: {
    width: number;
    height: number;
    face_count: number;
    detected_face_count: number;
    masked_face_count: number;
    ignored_background_face_count: number;
    detection_method: string;
    face_area_ratio: number | null;
    blur_variance: number;
    global_blur_variance: number;
    brightness: number;
    pose: { yaw: number; pitch: number; roll: number } | null;
  };
};

type PrimaryPhotoReasonCode =
  | "NO_FACE"
  | "MULTIPLE_FACES"
  | "NON_FRONTAL_FACE"
  | "IMAGE_TOO_SMALL"
  | "IMAGE_TOO_BLURRY"
  | "IMAGE_TOO_DARK"
  | "IMAGE_TOO_BRIGHT";
```

### 백엔드 처리

- `PASS`: 정면·품질 기준을 통과했다. AI 생성 검사까지 `CLEAR`이면 대표사진으로 확정할 수 있다.
- `RETRY`: `reason_codes`를 사용자 메시지로 매핑하고 다른 사진을 요청한다.
- 대표사진이 변경되면 기존 인증 마크를 해제한다.
- `quality`는 운영 분석용 진단값이며 이 API의 분기는 `decision`을 기준으로 한다.

## 2. 대표사진 AI 생성 위험 검사

`POST /ai/api/v1/profile-trust/photos/primary/synthetic-check`

정면 검사와 독립적으로 동일한 대표사진의 AI 생성 위험만 검사한다.

### 요청 스키마

`Content-Type: multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `image` | binary file | 예 | 대표사진 JPEG, JPG 또는 PNG |

### 응답 스키마

```ts
type SyntheticDecision =
  | "CLEAR"
  | "SYNTHETIC_RISK"
  | "CONFIRMED_SYNTHETIC";

type SyntheticResult = {
  decision: SyntheticDecision;
  probability: number | null; // 0.0 ~ 1.0
  provenance: string;
  model_version: string;
  signals: string[];
};
```

### 백엔드 처리

- `CLEAR`: AI 생성 위험 기준을 통과했다. 정면 검사까지 `PASS`이면 대표사진으로 확정할 수 있다.
- `SYNTHETIC_RISK` 또는 `CONFIRMED_SYNTHETIC`: 다른 대표사진을 요청한다.
- `SYNTHETIC_RISK`는 확정 판정이나 계정 제재 근거가 아니다.
- `CONFIRMED_SYNTHETIC`는 신뢰 가능한 콘텐츠 출처 정보에서 합성 이력이 확인된 경우다.

## 3. 라이브 촬영 챌린지 발급

`POST /ai/api/v1/profile-trust/verifications/challenges`

메인 백엔드가 로그인과 권한을 확인하고 자체 인증 시도를 생성한 뒤, 라이브 촬영을 시작하기 직전에 호출한다. 요청 본문은 없다.

### 응답 스키마

HTTP `201 Created`

```ts
type VerificationChallengeResponse = {
  challenge_token: string;
  challenges: ("LOOK_STRAIGHT")[];
  expires_at: string; // ISO 8601 UTC datetime
};
```

### 백엔드 처리

- `challenge_token`은 변경하지 않고 인증 완료 요청에 전달한다.
- 토큰은 기본 5분 후 만료된다.
- 현재 챌린지는 고정 포즈 `LOOK_STRAIGHT` 하나다.
- 이 토큰에는 사용자 ID, 계정 권한, 대표사진 ID가 없으며 로그인 세션이나 API 접근 토큰으로 사용할 수 없다.
- 메인 백엔드는 자체 인증 시도에 챌린지와 현재 대표사진 ID를 결합하고, 같은 사용자와 대표사진으로 완료 요청이 이어지는지 확인한다.

## 4. 라이브 얼굴 인증 완료

`POST /ai/api/v1/profile-trust/verifications/complete`

촬영 완료 직후 대표사진과 라이브 영상을 함께 전달한다.

### 요청 스키마

`Content-Type: multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `challenge_token` | string | 예 | 챌린지 발급 API가 반환한 원문 토큰 |
| `primary_photo` | binary file | 예 | 현재 계정에 확정된 대표사진 |
| `live_video` | binary file | 예 | 해당 인증 시도에서 방금 촬영한 4초 영상 |

### 응답 스키마

```ts
type VerificationResponse = {
  decision: "VERIFIED" | "RETRY" | "NOT_VERIFIED";
  reason_codes: VerificationReasonCode[];
  similarity: number | null;
  liveness: LivenessResult;
  model_version: string;
  match: MatchDiagnostics | null;
};

type VerificationReasonCode =
  | "NO_FACE"
  | "LIVENESS_FAILED"
  | "FACE_MISMATCH"
  | "FACE_COMPARISON_FAILED";

type LivenessResult = {
  passed: boolean;
  completed_challenges: ("LOOK_STRAIGHT")[];
  sampled_frames: number;
  valid_face_frames: number;
  frontal_face_frames: number;
  required_sampled_frames: number;
  required_valid_face_frames: number;
  required_frontal_face_frames: number;
  candidate_frame_count: number;
  low_light_enhanced_frames: number;
  source_frame_count: number;
  source_fps: number | null;
  sampling_strategy: "uniform_max_24";
  max_abs_yaw_deg: number;
  max_abs_pitch_deg: number;
  max_abs_roll_deg: number;
  frame_diagnostics: FrameDiagnostic[];
};

type FrameDiagnostic = {
  sample_index: number;
  source_frame_index: number;
  timestamp_ms: number | null;
  status: "NO_FACE" | "MULTIPLE_FACES" | "NON_FRONTAL" | "FRONTAL" | "MATCH_CANDIDATE";
  face_count: number;
  detected_face_count: number;
  masked_face_count: number;
  ignored_background_face_count: number;
  yaw: number | null;
  pitch: number | null;
  roll: number | null;
  blur_variance: number | null;
  low_light_enhanced: boolean;
  candidate_rank: number | null;
};

type MatchDiagnostics = {
  threshold: number;
  compared_frame_count: number;
  frame_similarities: number[];
  aggregation: "median";
};
```

### 백엔드 처리

| `decision` | 의미 | 권장 처리 |
|---|---|---|
| `VERIFIED` | 라이브 촬영과 대표사진이 동일인 기준 통과 | 해당 대표사진 ID에 인증 마크 부여 |
| `RETRY` | 촬영 품질 또는 비교 특징 생성 실패 | 인증 마크를 부여하지 않고 재촬영 요청 |
| `NOT_VERIFIED` | 얼굴 비교는 완료됐으나 다른 사람으로 판정 | 인증 마크를 부여하지 않고 불일치 안내 |

`reason_codes` 상세 의미:

| 코드 | 의미 |
|---|---|
| `LIVENESS_FAILED` | 프레임 수, 얼굴 검출 또는 정면 유지 기준 미달 |
| `FACE_MISMATCH` | 유사도 점수가 임계값보다 낮아 서로 다른 사람으로 판정 |
| `FACE_COMPARISON_FAILED` | 얼굴은 검출했지만 비교 특징을 생성하지 못함 |
| `NO_FACE` | 비교 가능한 얼굴 자체가 없음. 현재 정상 흐름에서는 주로 하위 호환용 |

메인 백엔드는 요청을 보낸 사용자가 인증 시도와 대표사진의 소유자인지 다시 확인한 뒤에만 결과를 반영한다. 최종 인증 기록으로 `decision`, 인증 수행 시각, 현재 대표사진 ID, `model_version`만 보관하는 것을 권장한다. 라이브 영상, 후보 프레임 및 얼굴 임베딩은 보관하지 않는다.

## 책임 경계

| 책임 | 메인 백엔드 | AI 서비스 |
|---|---:|---:|
| 사용자 로그인·토큰 검증·권한 확인 | 예 | 아니요 |
| 인증 시도 생성·소유권·재시도 제한 | 예 | 아니요 |
| 인증 시도와 사용자·대표사진 결합 | 예 | 아니요 |
| 만료되는 촬영 동작 챌린지 서명·검증 | 아니요 | 예 |
| 대표사진·라이브 영상 분석 | 아니요 | 예 |
| 인증 기록 저장·마크 부여·취소 | 예 | 아니요 |

AI 서비스의 챌린지는 인증·인가 상태가 아니다. 메인 백엔드는 `VERIFIED` 응답만으로 임의 사용자에게 마크를 부여해서는 안 되며, 자신이 소유한 인증 시도와 현재 대표사진이 일치하는 경우에만 마크를 반영한다.

## 호출 순서

```text
대표사진 업로드
  → 대표사진 정면 검사
  → 대표사진 AI 생성 검사
  → 정면 PASS이고 AI 검사 CLEAR이면 메인 백엔드가 대표사진 확정

선택형 인증 시작
  → 메인 백엔드가 로그인·권한·대표사진 소유권 확인
  → 메인 백엔드가 인증 시도 생성
  → AI 서비스가 라이브 촬영 챌린지 발급
  → 프론트에서 4초 촬영
  → 인증 완료 API에 토큰 + 확정 대표사진 + 라이브 영상 전달
  → VERIFIED면 해당 대표사진에 인증 마크 결합
```

대표사진이 변경되면 메인 백엔드는 기존 인증 마크를 즉시 해제해야 한다.
