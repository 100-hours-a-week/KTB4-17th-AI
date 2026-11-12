# Runtime models

ONNX 바이너리는 Git에 저장하지 않습니다. 프로젝트 루트에서 다음 명령으로 내려받습니다.

```bash
python scripts/download_models.py --demo
```

스크립트는 URL을 특정 리비전에 고정하고 SHA-256을 검증합니다.

| 파일 | 용도 | 출처·라이선스 |
| --- | --- | --- |
| `face_detection_yunet_2023mar.onnx` | 얼굴 검출·5점 정렬 | OpenCV Zoo YuNet, MIT |
| `lbfmodel.yaml` | 68점 얼굴 랜드마크 | OpenCV Facemark 공식 튜토리얼 연결 모델 |
| `face_recognition_sface_2021dec.onnx` | 1:1 얼굴 특징 비교 | OpenCV Zoo SFace, Apache-2.0 모델 디렉터리 기준 |
| `palm_detection_mediapipe_2023feb.onnx` | 손바닥 검출 (능동형 얼굴 인증) | OpenCV Zoo MediaPipe Palm Detection, Apache-2.0 |
| `handpose_estimation_mediapipe_2023feb.onnx` | 21점 손 랜드마크 (능동형 얼굴 인증) | OpenCV Zoo MediaPipe Handpose, Apache-2.0 |
| `face_landmarker.task` | 라이브 얼굴 위치·회전 추정 (능동형 얼굴 인증, `--demo`에 포함) | Google MediaPipe Face Landmarker, 공식 버전 1 |
| `community_forensics_vit_int8.onnx` | AI 생성 이미지 위험 점수 | Community Forensics ViT, MIT |

모델 데이터의 상업 이용 적합성과 학습 데이터 출처는 출시 전에 별도 법무 검토가 필요합니다.

라이브 프로토타입은 `python scripts/download_models.py --demo-only`로 추가 모델을 받고,
`uv run --group vision-demo python scripts/hand_gesture_demo.py --random`으로 실행합니다.
화면 중앙 가이드에 얼굴을 맞춘 뒤 준비 상태에서 Enter를 눌러 정면 기준을 확정합니다.

서비스는 촬영 전 중앙 확인 후 7초 동안 손 모양·위·아래·가로를 유지하며 지정한 고개 동작과 정면 복귀를 검사합니다. 세션마다 한 번 뽑은 동일 조합을 3회 유지하는 서명 계획을 발급하며 BE는 현재 시도 번호를 `attempt_no`로 전달합니다.

배포 순서: 새 AI를 `ACTIVE_LIVENESS_ENABLED=false`로 배포 → 별도 BE 계약 반영·마이그레이션 및 새 서버 → FE(WASM·모델을 자체 정적 경로로 제공) → AI 플래그 true. 기존 v2 세션이 만료되기 전에는 이전 BE/FE로 되돌리지 않습니다. 상세 배포 절차는 [얼굴 인증 연동 및 배포 안내](../FACE_VERIFICATION_ROLLOUT.md)를 참고하세요.
