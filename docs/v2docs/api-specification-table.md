# Profile Trust AI API 명세표

> 현재 실행 코드 기준  
> Base path: `/ai/api/v1`  
> 파일 요청 형식: `multipart/form-data`  
> 응답 형식: `application/json`

## 공통 규칙

| 항목 | 값 | 설명 |
|---|---|---|
| 내부 호출 헤더 | `X-Internal-Api-Key: <key>` | 사용자 로그인 토큰이 아니라 메인 백엔드와 AI 서비스 사이의 서비스 키 |
| 이미지 파일 | JPEG, JPG, PNG | 기본 최대 10 MiB |
| 영상 파일 | MP4, WebM, MOV | 기본 최대 25 MiB |
| 성공 응답 | 기능별 원본 JSON | 현재 AI 서비스에는 `message`, `data` 공통 래퍼가 없음 |
| 일반 오류 응답 | `{"detail":"오류 설명"}` | FastAPI 입력 검증 오류는 `detail` 배열일 수 있음 |
| 사용자 인증·인가 | 메인 백엔드 담당 | AI 서비스는 사용자 ID와 액세스 토큰을 받지 않음 |

파일은 JSON의 Base64 문자열이 아니라 multipart binary part로 전송한다.

---

## 1. 대표사진 정면·품질 검사

| 요청 | Request method | URL | Body | 설명 | Response status code | Response body | 작성 이유 | 비고 |
|:---:|:---:|---|---|---|:---:|---|---|---|
| 대표사진 정면·품질 검사 | POST | `/ai/api/v1/profile-trust/photos/primary/frontal-check` | `multipart/form-data`<br>`image`: 대표사진 파일 | 대표사진에 유효한 얼굴이 한 명인지, 얼굴이 정면인지, 최소 해상도·선명도·밝기를 충족하는지 검사 | 200 | {<br>&nbsp;&nbsp;"decision": "PASS",<br>&nbsp;&nbsp;"reason_codes": [],<br>&nbsp;&nbsp;"quality": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"width": 3024,<br>&nbsp;&nbsp;&nbsp;&nbsp;"height": 4032,<br>&nbsp;&nbsp;&nbsp;&nbsp;"face_count": 1,<br>&nbsp;&nbsp;&nbsp;&nbsp;"detected_face_count": 2,<br>&nbsp;&nbsp;&nbsp;&nbsp;"masked_face_count": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"ignored_background_face_count": 1,<br>&nbsp;&nbsp;&nbsp;&nbsp;"detection_method": "yunet",<br>&nbsp;&nbsp;&nbsp;&nbsp;"face_area_ratio": 0.018,<br>&nbsp;&nbsp;&nbsp;&nbsp;"blur_variance": 17.545,<br>&nbsp;&nbsp;&nbsp;&nbsp;"global_blur_variance": 13.333,<br>&nbsp;&nbsp;&nbsp;&nbsp;"brightness": 119.566,<br>&nbsp;&nbsp;&nbsp;&nbsp;"pose": {<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"yaw": -1.91,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"pitch": -12.83,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"roll": -0.7<br>&nbsp;&nbsp;&nbsp;&nbsp;}<br>&nbsp;&nbsp;}<br>} | 대표사진 등록 전에 얼굴 수와 정면 여부를 확인하기 위해 별도 API로 분리<br>`reason_codes`는 사용자 안내 문구 매핑에 사용<br>`quality`는 판정 근거와 운영 튜닝에 사용<br>`masked_face_count`는 가려진 보조 얼굴 허용 여부 확인<br>`ignored_background_face_count`는 작은 배경 얼굴 제외 여부 확인 | AI 생성 여부는 검사하지 않음<br>대표사진 등록은 이 API가 `PASS`이고 AI 생성 검사가 `CLEAR`일 때만 가능 |
|  |  |  |  | 정면·품질 기준 미달 | 200 | {<br>&nbsp;&nbsp;"decision": "RETRY",<br>&nbsp;&nbsp;"reason_codes": [<br>&nbsp;&nbsp;&nbsp;&nbsp;"NON_FRONTAL_FACE",<br>&nbsp;&nbsp;&nbsp;&nbsp;"IMAGE_TOO_BLURRY"<br>&nbsp;&nbsp;],<br>&nbsp;&nbsp;"quality": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"width": 1080,<br>&nbsp;&nbsp;&nbsp;&nbsp;"height": 1440,<br>&nbsp;&nbsp;&nbsp;&nbsp;"face_count": 1,<br>&nbsp;&nbsp;&nbsp;&nbsp;"detected_face_count": 1,<br>&nbsp;&nbsp;&nbsp;&nbsp;"masked_face_count": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"ignored_background_face_count": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"detection_method": "yunet",<br>&nbsp;&nbsp;&nbsp;&nbsp;"face_area_ratio": 0.12,<br>&nbsp;&nbsp;&nbsp;&nbsp;"blur_variance": 5.71,<br>&nbsp;&nbsp;&nbsp;&nbsp;"global_blur_variance": 14.32,<br>&nbsp;&nbsp;&nbsp;&nbsp;"brightness": 91.4,<br>&nbsp;&nbsp;&nbsp;&nbsp;"pose": {<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"yaw": 27.3,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"pitch": 8.1,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"roll": 19.2<br>&nbsp;&nbsp;&nbsp;&nbsp;}<br>&nbsp;&nbsp;}<br>} | 요청 자체는 정상 처리됐으므로 HTTP 200 사용<br>업무 판정 실패는 `decision=RETRY`로 표현 | 여러 기준이 실패하면 `reason_codes`에 동시에 반환 |
|  |  |  |  | 내부 API 키 불일치 | 401 | {<br>&nbsp;&nbsp;"detail": "invalid internal api key"<br>} | 외부 클라이언트가 AI 서비스를 직접 호출하지 못하도록 서비스 간 요청 보호 | 로컬에서 `INTERNAL_API_KEY`가 비어 있으면 검사 생략 |
|  |  |  |  | 이미지 크기 제한 초과 | 413 | {<br>&nbsp;&nbsp;"detail": "업로드 파일이 허용 크기를 초과했습니다."<br>} | 과도한 메모리·CPU 사용 방지 | 기본 10 MiB |
|  |  |  |  | 지원하지 않는 이미지 MIME 타입 | 415 | {<br>&nbsp;&nbsp;"detail": "JPEG/JPG, PNG 이미지만 지원합니다."<br>} | 모델이 처리할 수 있는 이미지 형식만 허용 | JPG와 JPEG의 MIME 타입은 모두 `image/jpeg`이며 확장자가 아니라 multipart Content-Type 기준 |
|  |  |  |  | 빈 파일, 필드 누락, 이미지 디코딩 실패 | 422 | {<br>&nbsp;&nbsp;"detail": "지원되는 JPEG/JPG 또는 PNG 이미지가 아닙니다."<br>} | 요청 구조 또는 이미지 데이터 자체가 잘못된 경우 | 필드 누락은 FastAPI `detail` 배열로 반환될 수 있음 |
|  |  |  |  | YuNet 또는 LBF 모델 사용 불가 | 503 | {<br>&nbsp;&nbsp;"detail": "얼굴 랜드마크 모델이 없습니다: ..."<br>} | 입력 문제가 아닌 AI 서비스 준비 상태 문제를 구분 | 모델 경로와 배포 이미지 확인 필요 |
|  |  |  |  | 처리되지 않은 서버 오류 | 500 | `Internal Server Error` | 예상하지 못한 내부 예외 | 기본 FastAPI 동작에서는 `text/plain`일 수 있음. 서버 로그와 요청 추적 ID 확인 필요 |

### 대표사진 정면 검사 필드 형식

| 경로 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `decision` | enum | 예 | `PASS`, `RETRY` |
| `reason_codes` | string[] | 예 | 실패 사유 목록. 통과하면 빈 배열 |
| `quality.width` | integer | 예 | 원본 가로 픽셀 |
| `quality.height` | integer | 예 | 원본 세로 픽셀 |
| `quality.face_count` | integer | 예 | 마스킹·배경 얼굴을 제외한 유효 얼굴 수 |
| `quality.detected_face_count` | integer | 예 | 검출기가 처음 찾은 전체 얼굴 수 |
| `quality.masked_face_count` | integer | 예 | 가려져 있어 허용한 보조 얼굴 수 |
| `quality.ignored_background_face_count` | integer | 예 | 매우 작아 제외한 배경 얼굴 수 |
| `quality.detection_method` | string | 예 | `yunet`, `haar-closeup-fallback` 등 |
| `quality.face_area_ratio` | number 또는 null | 예 | 대표 얼굴 면적 비율. 실패 기준에는 사용하지 않음 |
| `quality.blur_variance` | number | 예 | 얼굴 주변 선명도 |
| `quality.global_blur_variance` | number | 예 | 이미지 전체 선명도 |
| `quality.brightness` | number | 예 | 회색조 평균 밝기 0~255 |
| `quality.pose` | object 또는 null | 예 | 얼굴 한 명일 때 자세 측정값 |
| `quality.pose.yaw` | number | 조건부 | 좌우 회전 각도 |
| `quality.pose.pitch` | number | 조건부 | 상하 회전 각도 |
| `quality.pose.roll` | number | 조건부 | 얼굴 기울기 각도 |

### 대표사진 정면 검사 사유 코드

| 코드 | 의미 |
|---|---|
| `NO_FACE` | 유효한 대표 얼굴을 찾지 못함 |
| `MULTIPLE_FACES` | 가리지 않은 유효 얼굴이 여러 명 |
| `NON_FRONTAL_FACE` | 정면 각도 범위 초과 |
| `IMAGE_TOO_SMALL` | 짧은 변이 최소 해상도 미만 |
| `IMAGE_TOO_BLURRY` | 얼굴 주변 선명도가 기준 미만 |
| `IMAGE_TOO_DARK` | 평균 밝기가 기준 미만 |
| `IMAGE_TOO_BRIGHT` | 평균 밝기가 기준 초과 |

---

## 2. 대표사진 AI 생성 위험 검사

| 요청 | Request method | URL | Body | 설명 | Response status code | Response body | 작성 이유 | 비고 |
|:---:|:---:|---|---|---|:---:|---|---|---|
| 대표사진 AI 생성 위험 검사 | POST | `/ai/api/v1/profile-trust/photos/primary/synthetic-check` | `multipart/form-data`<br>`image`: 대표사진 파일 | C2PA 출처 정보, Community Forensics ViT 모델, 픽셀아트 보조 신호를 이용해 대표사진의 AI 생성 위험을 검사 | 200 | {<br>&nbsp;&nbsp;"decision": "CLEAR",<br>&nbsp;&nbsp;"probability": 0.000037,<br>&nbsp;&nbsp;"provenance": "none",<br>&nbsp;&nbsp;"model_version": "community-forensics-vit-v1.1-int8",<br>&nbsp;&nbsp;"signals": []<br>} | 정면 여부와 AI 생성 여부는 서로 다른 판단이므로 별도 API로 분리<br>`probability`는 운영 임계값 튜닝에 사용<br>`provenance`는 C2PA 기반 강한 증거와 픽셀 모델 결과를 구분<br>`model_version`은 결과 재현성과 운영 기록에 사용 | 정면 검사에 전달한 것과 동일한 대표사진이어야 함<br>`CLEAR`는 비AI 원본임을 보증하는 결과가 아니라 현재 위험 신호가 낮다는 뜻 |
|  |  |  |  | 모델 확률 또는 보조 신호 기준 초과 | 200 | {<br>&nbsp;&nbsp;"decision": "SYNTHETIC_RISK",<br>&nbsp;&nbsp;"probability": 0.913284,<br>&nbsp;&nbsp;"provenance": "none",<br>&nbsp;&nbsp;"model_version": "community-forensics-vit-v1.1-int8",<br>&nbsp;&nbsp;"signals": []<br>} | 요청은 정상 처리됐으므로 HTTP 200 사용<br>위험 판정은 `decision`으로 표현 | AI 생성 확정이나 계정 제재 근거로 사용하지 않음 |
|  |  |  |  | 픽셀아트·일러스트형 보조 신호 감지 | 200 | {<br>&nbsp;&nbsp;"decision": "SYNTHETIC_RISK",<br>&nbsp;&nbsp;"probability": 0.041237,<br>&nbsp;&nbsp;"provenance": "none",<br>&nbsp;&nbsp;"model_version": "community-forensics-vit-v1.1-int8",<br>&nbsp;&nbsp;"signals": [<br>&nbsp;&nbsp;&nbsp;&nbsp;"PIXEL_ART_OR_ILLUSTRATION"<br>&nbsp;&nbsp;]<br>} | 모델 확률이 낮아도 명백한 비사진형 이미지를 위험으로 표시 | “AI 생성”과 “비사진형 이미지”를 완전히 분리하려면 향후 별도 코드 필요 |
|  |  |  |  | 신뢰된 C2PA에서 AI 생성 이력 확인 | 200 | {<br>&nbsp;&nbsp;"decision": "CONFIRMED_SYNTHETIC",<br>&nbsp;&nbsp;"probability": null,<br>&nbsp;&nbsp;"provenance": "confirmed-ai",<br>&nbsp;&nbsp;"model_version": "community-forensics-vit-v1.1-int8",<br>&nbsp;&nbsp;"signals": [<br>&nbsp;&nbsp;&nbsp;&nbsp;"TRUSTED_AI_PROVENANCE"<br>&nbsp;&nbsp;]<br>} | 신뢰된 출처 증거가 있으면 픽셀 추론 없이 확정 가능 | C2PA trust anchor 설정 필요 |
|  |  |  |  | 내부 API 키 불일치 | 401 | {<br>&nbsp;&nbsp;"detail": "invalid internal api key"<br>} | 서비스 간 요청 보호 |  |
|  |  |  |  | 이미지 크기 제한 초과 | 413 | {<br>&nbsp;&nbsp;"detail": "업로드 파일이 허용 크기를 초과했습니다."<br>} | 리소스 사용 제한 | 기본 10 MiB |
|  |  |  |  | 지원하지 않는 이미지 MIME 타입 | 415 | {<br>&nbsp;&nbsp;"detail": "JPEG/JPG, PNG 이미지만 지원합니다."<br>} | 허용 형식만 모델로 전달 | JPG와 JPEG는 모두 `image/jpeg` |
|  |  |  |  | 빈 파일, 필드 누락, 이미지 디코딩 실패 | 422 | {<br>&nbsp;&nbsp;"detail": "지원되는 JPEG/JPG 또는 PNG 이미지가 아닙니다."<br>} | 잘못된 입력 구분 |  |
|  |  |  |  | ONNX 모델 또는 런타임 사용 불가 | 503 | {<br>&nbsp;&nbsp;"detail": "합성 이미지 모델이 없습니다: ..."<br>} | 입력 오류와 서버 준비 문제 구분 | 배포 모델 파일 확인 필요 |
|  |  |  |  | 처리되지 않은 서버 오류 | 500 | `Internal Server Error` | 예상하지 못한 내부 예외 | 기본 FastAPI 동작에서는 `text/plain`일 수 있음 |

### AI 생성 검사 필드 형식

| 경로 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `decision` | enum | 예 | `CLEAR`, `SYNTHETIC_RISK`, `CONFIRMED_SYNTHETIC` |
| `probability` | number 또는 null | 예 | ONNX 모델 위험 확률 0~1. C2PA 즉시 확정 시 null |
| `provenance` | string | 예 | `confirmed-ai`, `untrusted-ai`, `verified-non-ai`, `none`, `unavailable` |
| `model_version` | string | 예 | 합성 위험 판별 모델 버전 |
| `signals` | string[] | 예 | 보조 판정 신호. 없으면 빈 배열 |

---

## 3. 라이브 촬영 챌린지 발급

| 요청 | Request method | URL | Body | 설명 | Response status code | Response body | 작성 이유 | 비고 |
|:---:|:---:|---|---|---|:---:|---|---|---|
| 라이브 촬영 챌린지 발급 | POST | `/ai/api/v1/profile-trust/verifications/challenges` | 없음 | 수행할 촬영 동작과 만료 시각을 HMAC 서명한 단기 토큰 발급 | 201 | {<br>&nbsp;&nbsp;"challenge_token": "eyJjaGFsbGVuZ2VzIjpbIkxPT0tfU1RSQUlHSFQiXSwiZXhwIjoxNzkxMjAwMDAwfQ.example_signature",<br>&nbsp;&nbsp;"challenges": [<br>&nbsp;&nbsp;&nbsp;&nbsp;"LOOK_STRAIGHT"<br>&nbsp;&nbsp;],<br>&nbsp;&nbsp;"expires_at": "2026-10-05T12:05:00Z"<br>} | 촬영 완료 요청이 서버가 발급한 동작과 유효시간에 연결되도록 함<br>변조된 촬영 지시 사용 방지 | 사용자 인증 세션이 아님<br>사용자 ID, 권한, 대표사진 ID가 없음<br>백엔드가 자체 인증 시도와 연결해야 함 |
|  |  |  |  | 내부 API 키 불일치 | 401 | {<br>&nbsp;&nbsp;"detail": "invalid internal api key"<br>} | 서비스 간 요청 보호 |  |
|  |  |  |  | 처리되지 않은 서버 오류 | 500 | `Internal Server Error` | 예상하지 못한 내부 예외 | 기본 FastAPI 동작에서는 `text/plain`일 수 있음. 운영에서는 `LIVENESS_TOKEN_SECRET`을 반드시 변경 |

### 챌린지 응답 필드 형식

| 경로 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `challenge_token` | string | 예 | Base64 payload와 HMAC-SHA256 서명으로 구성된 단기 토큰 |
| `challenges` | string[] | 예 | 현재 `LOOK_STRAIGHT` 한 개 |
| `expires_at` | ISO 8601 datetime | 예 | UTC 만료 시각. 기본 발급 후 5분 |

---

## 4. 라이브 얼굴 인증 완료

| 요청 | Request method | URL | Body | 설명 | Response status code | Response body | 작성 이유 | 비고 |
|:---:|:---:|---|---|---|:---:|---|---|---|
| 라이브 얼굴 인증 완료 | POST | `/ai/api/v1/profile-trust/verifications/complete` | `multipart/form-data`<br>`challenge_token`: string<br>`primary_photo`: 대표사진 파일<br>`live_video`: 촬영 영상 파일 | 챌린지 토큰 검증 후 영상 프레임을 분석하고 대표사진과 동일인인지 비교 | 200 | {<br>&nbsp;&nbsp;"decision": "VERIFIED",<br>&nbsp;&nbsp;"reason_codes": [],<br>&nbsp;&nbsp;"similarity": 0.684,<br>&nbsp;&nbsp;"liveness": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"passed": true,<br>&nbsp;&nbsp;&nbsp;&nbsp;"completed_challenges": ["LOOK_STRAIGHT"],<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampled_frames": 24,<br>&nbsp;&nbsp;&nbsp;&nbsp;"valid_face_frames": 22,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frontal_face_frames": 19,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_sampled_frames": 8,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_valid_face_frames": 6,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_frontal_face_frames": 11,<br>&nbsp;&nbsp;&nbsp;&nbsp;"candidate_frame_count": 3,<br>&nbsp;&nbsp;&nbsp;&nbsp;"low_light_enhanced_frames": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_frame_count": 120,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_fps": 30.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampling_strategy": "uniform_max_24",<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_yaw_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_pitch_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_roll_deg": 40.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frame_diagnostics": [<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"sample_index": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"source_frame_index": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"timestamp_ms": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"status": "MATCH_CANDIDATE",<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"face_count": 1,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"detected_face_count": 1,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"masked_face_count": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"ignored_background_face_count": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"yaw": 2.13,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"pitch": -0.91,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"roll": 9.84,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"blur_variance": 91.52,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"low_light_enhanced": false,<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"candidate_rank": 1<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;}<br>&nbsp;&nbsp;&nbsp;&nbsp;]<br>&nbsp;&nbsp;},<br>&nbsp;&nbsp;"model_version": "opencv-sface-2021dec",<br>&nbsp;&nbsp;"match": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"threshold": 0.42,<br>&nbsp;&nbsp;&nbsp;&nbsp;"compared_frame_count": 3,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frame_similarities": [0.671, 0.684, 0.702],<br>&nbsp;&nbsp;&nbsp;&nbsp;"aggregation": "median"<br>&nbsp;&nbsp;}<br>} | `decision`은 백엔드의 인증마크 처리 기준<br>`reason_codes`는 불일치와 촬영 실패를 구분<br>`frame_diagnostics`는 어떤 프레임이 탈락·선택됐는지 확인<br>`match`는 후보별 비교 점수와 임계값 확인<br>`model_version`은 인증 기록 재현성에 사용 | 실제 `frame_diagnostics`에는 분석한 모든 샘플 프레임이 포함됨<br>원본 영상·프레임·임베딩은 저장하지 않음 |
|  |  |  |  | 얼굴 비교 결과 다른 사람 | 200 | {<br>&nbsp;&nbsp;"decision": "NOT_VERIFIED",<br>&nbsp;&nbsp;"reason_codes": ["FACE_MISMATCH"],<br>&nbsp;&nbsp;"similarity": 0.217,<br>&nbsp;&nbsp;"liveness": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"passed": true,<br>&nbsp;&nbsp;&nbsp;&nbsp;"completed_challenges": ["LOOK_STRAIGHT"],<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampled_frames": 24,<br>&nbsp;&nbsp;&nbsp;&nbsp;"valid_face_frames": 21,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frontal_face_frames": 18,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_sampled_frames": 8,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_valid_face_frames": 6,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_frontal_face_frames": 11,<br>&nbsp;&nbsp;&nbsp;&nbsp;"candidate_frame_count": 3,<br>&nbsp;&nbsp;&nbsp;&nbsp;"low_light_enhanced_frames": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_frame_count": 120,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_fps": 30.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampling_strategy": "uniform_max_24",<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_yaw_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_pitch_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_roll_deg": 40.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frame_diagnostics": []<br>&nbsp;&nbsp;},<br>&nbsp;&nbsp;"model_version": "opencv-sface-2021dec",<br>&nbsp;&nbsp;"match": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"threshold": 0.42,<br>&nbsp;&nbsp;&nbsp;&nbsp;"compared_frame_count": 3,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frame_similarities": [0.194, 0.217, 0.231],<br>&nbsp;&nbsp;&nbsp;&nbsp;"aggregation": "median"<br>&nbsp;&nbsp;}<br>} | 얼굴 미검출과 동일인 불일치를 구분하기 위해 `FACE_MISMATCH` 사용 | 사용자 문구는 “얼굴을 찾을 수 없음”이 아니라 “대표사진과 일치하지 않음” |
|  |  |  |  | 프레임·얼굴·정면 유지 기준 미달 | 200 | {<br>&nbsp;&nbsp;"decision": "RETRY",<br>&nbsp;&nbsp;"reason_codes": ["LIVENESS_FAILED"],<br>&nbsp;&nbsp;"similarity": null,<br>&nbsp;&nbsp;"liveness": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"passed": false,<br>&nbsp;&nbsp;&nbsp;&nbsp;"completed_challenges": [],<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampled_frames": 8,<br>&nbsp;&nbsp;&nbsp;&nbsp;"valid_face_frames": 4,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frontal_face_frames": 2,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_sampled_frames": 8,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_valid_face_frames": 6,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_frontal_face_frames": 6,<br>&nbsp;&nbsp;&nbsp;&nbsp;"candidate_frame_count": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"low_light_enhanced_frames": 3,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_frame_count": 120,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_fps": 30.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampling_strategy": "uniform_max_24",<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_yaw_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_pitch_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_roll_deg": 40.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frame_diagnostics": []<br>&nbsp;&nbsp;},<br>&nbsp;&nbsp;"model_version": "opencv-sface-2021dec",<br>&nbsp;&nbsp;"match": null<br>} | 촬영 단계에서 실패했으므로 동일인 비교를 수행하지 않음 | 재촬영 안내<br>`match=null`이 정상 |
|  |  |  |  | 얼굴 특징 생성 실패 | 200 | {<br>&nbsp;&nbsp;"decision": "RETRY",<br>&nbsp;&nbsp;"reason_codes": ["FACE_COMPARISON_FAILED"],<br>&nbsp;&nbsp;"similarity": null,<br>&nbsp;&nbsp;"liveness": {<br>&nbsp;&nbsp;&nbsp;&nbsp;"passed": true,<br>&nbsp;&nbsp;&nbsp;&nbsp;"completed_challenges": ["LOOK_STRAIGHT"],<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampled_frames": 24,<br>&nbsp;&nbsp;&nbsp;&nbsp;"valid_face_frames": 20,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frontal_face_frames": 17,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_sampled_frames": 8,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_valid_face_frames": 6,<br>&nbsp;&nbsp;&nbsp;&nbsp;"required_frontal_face_frames": 10,<br>&nbsp;&nbsp;&nbsp;&nbsp;"candidate_frame_count": 3,<br>&nbsp;&nbsp;&nbsp;&nbsp;"low_light_enhanced_frames": 0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_frame_count": 120,<br>&nbsp;&nbsp;&nbsp;&nbsp;"source_fps": 30.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"sampling_strategy": "uniform_max_24",<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_yaw_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_pitch_deg": 25.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"max_abs_roll_deg": 40.0,<br>&nbsp;&nbsp;&nbsp;&nbsp;"frame_diagnostics": []<br>&nbsp;&nbsp;},<br>&nbsp;&nbsp;"model_version": "opencv-sface-2021dec",<br>&nbsp;&nbsp;"match": null<br>} | 촬영 단계는 통과했으나 비교 가능한 특징을 만들지 못한 상태를 별도 구분 | 다른 사람 판정이 아니므로 `NOT_VERIFIED`가 아니라 `RETRY` |
|  |  |  |  | 만료·변조된 챌린지 토큰 | 400 | {<br>&nbsp;&nbsp;"detail": "유효하지 않은 챌린지 토큰입니다."<br>} | 촬영 지시 토큰 위변조와 만료를 거절 | 만료 토큰은 `만료된 챌린지 토큰입니다.` 반환 가능 |
|  |  |  |  | 내부 API 키 불일치 | 401 | {<br>&nbsp;&nbsp;"detail": "invalid internal api key"<br>} | 서비스 간 요청 보호 |  |
|  |  |  |  | 사진 또는 영상 크기 제한 초과 | 413 | {<br>&nbsp;&nbsp;"detail": "업로드 파일이 허용 크기를 초과했습니다."<br>} | 메모리·CPU 보호 | 이미지 10 MiB, 영상 25 MiB 기본값 |
|  |  |  |  | 지원하지 않는 사진 또는 영상 MIME 타입 | 415 | {<br>&nbsp;&nbsp;"detail": "MP4, WebM, MOV 영상만 지원합니다."<br>} | OpenCV가 처리할 수 있는 형식만 허용 | 이미지 오류는 이미지 형식 안내 반환 |
|  |  |  |  | 필수 필드 누락, 빈 파일, 이미지 디코딩 실패 | 422 | {<br>&nbsp;&nbsp;"detail": "빈 파일은 처리할 수 없습니다."<br>} | 잘못된 입력 구분 |  |
|  |  |  |  | YuNet 또는 SFace 모델 사용 불가 | 503 | {<br>&nbsp;&nbsp;"detail": "얼굴 비교 모델이 없습니다: ..."<br>} | 입력 문제가 아닌 서비스 준비 상태 문제 구분 |  |
|  |  |  |  | 처리되지 않은 서버 오류 | 500 | `Internal Server Error` | 예상하지 못한 내부 예외 | 기본 FastAPI 동작에서는 `text/plain`일 수 있음 |

### 라이브 인증 최상위 필드 형식

| 경로 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `decision` | enum | 예 | `VERIFIED`, `RETRY`, `NOT_VERIFIED` |
| `reason_codes` | string[] | 예 | 성공 시 빈 배열 |
| `similarity` | number 또는 null | 예 | 후보별 유사도 중앙값 |
| `liveness` | object | 예 | 프레임·촬영 조건 검사 결과 |
| `model_version` | string | 예 | 얼굴 비교 모델 버전 |
| `match` | object 또는 null | 예 | 동일인 비교 진단. 앞 단계 실패 시 null |

### `liveness` 필드 형식

| 경로 | 타입 | 설명 |
|---|---|---|
| `passed` | boolean | 촬영 조건 통과 여부 |
| `completed_challenges` | string[] | 완료한 촬영 동작 |
| `sampled_frames` | integer | 실제 분석한 프레임 수 |
| `valid_face_frames` | integer | 유효 얼굴이 한 명인 프레임 수 |
| `frontal_face_frames` | integer | 허용 각도 안의 프레임 수 |
| `required_sampled_frames` | integer | 필요한 최소 샘플 수 |
| `required_valid_face_frames` | integer | 필요한 최소 유효 얼굴 프레임 수 |
| `required_frontal_face_frames` | integer | 해당 영상에서 필요한 정면 프레임 수 |
| `candidate_frame_count` | integer | 실제 비교 후보 프레임 수 |
| `low_light_enhanced_frames` | integer | 저조도 보정을 사용한 프레임 수 |
| `source_frame_count` | integer | 원본 영상 전체 프레임 수 |
| `source_fps` | number 또는 null | 원본 영상 FPS |
| `sampling_strategy` | string | `uniform_max_24` |
| `max_abs_yaw_deg` | number | 라이브 yaw 허용 각도 |
| `max_abs_pitch_deg` | number | 라이브 pitch 허용 각도 |
| `max_abs_roll_deg` | number | 라이브 roll 허용 각도 |
| `frame_diagnostics` | object[] | 각 샘플 프레임의 분석 결과 |

### `frame_diagnostics[]` 필드 형식

| 경로 | 타입 | 설명 |
|---|---|---|
| `sample_index` | integer | 샘플 배열 순번 |
| `source_frame_index` | integer | 원본 영상 프레임 번호 |
| `timestamp_ms` | integer 또는 null | 영상 시각(ms) |
| `status` | enum | `NO_FACE`, `MULTIPLE_FACES`, `NON_FRONTAL`, `FRONTAL`, `MATCH_CANDIDATE` |
| `face_count` | integer | 최종 유효 얼굴 수 |
| `detected_face_count` | integer | 처음 검출한 얼굴 수 |
| `masked_face_count` | integer | 허용한 마스킹 얼굴 수 |
| `ignored_background_face_count` | integer | 제외한 작은 배경 얼굴 수 |
| `yaw` | number 또는 null | 좌우 각도 |
| `pitch` | number 또는 null | 상하 각도 |
| `roll` | number 또는 null | 기울기 각도 |
| `blur_variance` | number 또는 null | 얼굴 주변 선명도 |
| `low_light_enhanced` | boolean | 저조도 보정 사용 여부 |
| `candidate_rank` | integer 또는 null | 비교 후보 순위 |

### `match` 필드 형식

| 경로 | 타입 | 설명 |
|---|---|---|
| `threshold` | number | 동일인 판정 임계값, 기본 0.42 |
| `compared_frame_count` | integer | 실제 비교에 성공한 후보 수 |
| `frame_similarities` | number[] | 대표사진과 후보별 코사인 유사도 |
| `aggregation` | string | 현재 `median` |

### 라이브 인증 사유 코드

| 코드 | 의미 | 사용자 안내 |
|---|---|---|
| `LIVENESS_FAILED` | 프레임·얼굴 검출·정면 유지 기준 미달 | 촬영 환경과 자세를 확인하고 재촬영 |
| `FACE_MISMATCH` | 대표사진과 라이브 얼굴 유사도가 임계값 미만 | 대표사진 속 인물과 일치하지 않음 |
| `FACE_COMPARISON_FAILED` | 얼굴은 찾았지만 비교 특징 생성 실패 | 다시 촬영하거나 다른 대표사진 사용 |
| `NO_FACE` | 비교 가능한 얼굴 자체가 없음 | 얼굴이 보이도록 재촬영. 현재 정상 흐름에서는 주로 하위 호환용 |

---

## 5. 헬스 체크

| 요청 | Request method | URL | Body | 설명 | Response status code | Response body | 작성 이유 | 비고 |
|:---:|:---:|---|---|---|:---:|---|---|---|
| AI 서비스 상태 조회 | GET | `/health` | 없음 | 애플리케이션이 HTTP 요청에 응답할 수 있는지 확인 | 200 | {<br>&nbsp;&nbsp;"status": "ok"<br>} | 로드밸런서, 배포 시스템, 모니터링에서 프로세스 상태 확인 | 모델 추론까지 확인하는 readiness 검사는 아님 |
|  |  |  |  | 서버 오류 | 500 | `Internal Server Error` | 애플리케이션 자체 오류 | 기본 FastAPI 동작에서는 `text/plain`일 수 있으며 단순 상태 응답이라 발생 가능성은 낮음 |

---

## 6. 백엔드 최종 처리 규칙

### 대표사진 등록

```text
정면 검사 decision == PASS
AND
AI 생성 검사 decision == CLEAR
→ 대표사진 등록 가능
```

| 정면 검사 | AI 생성 검사 | 백엔드 처리 |
|---|---|---|
| `PASS` | `CLEAR` | 대표사진 저장 또는 확정 |
| `RETRY` | 모든 값 | 정면·화질 사유를 안내하고 다른 사진 요청 |
| `PASS` | `SYNTHETIC_RISK` | AI 생성 위험 안내 후 다른 사진 요청 |
| `PASS` | `CONFIRMED_SYNTHETIC` | AI 생성 확인 안내 후 다른 사진 요청 |

### 라이브 얼굴 인증

| AI 응답 | 백엔드 처리 |
|---|---|
| `VERIFIED` | 요청 사용자, 인증 시도, 현재 대표사진이 모두 일치할 때 인증마크 부여 |
| `RETRY` | 인증마크를 부여하지 않고 재촬영 안내 |
| `NOT_VERIFIED` | 인증마크를 부여하지 않고 대표사진과 불일치 안내 |

대표사진이 변경되면 기존 인증마크를 즉시 해제한다. AI 서비스는 사용자 ID, 인증 시도 ID, 인증마크를 저장하거나 변경하지 않는다.
