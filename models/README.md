# Runtime models

ONNX 바이너리는 Git에 저장하지 않습니다. 프로젝트 루트에서 다음 명령으로 내려받습니다.

```bash
python scripts/download_models.py
```

스크립트는 URL을 특정 리비전에 고정하고 SHA-256을 검증합니다.

| 파일 | 용도 | 출처·라이선스 |
| --- | --- | --- |
| `face_detection_yunet_2023mar.onnx` | 얼굴 검출·5점 정렬 | OpenCV Zoo YuNet, MIT |
| `lbfmodel.yaml` | 68점 얼굴 랜드마크 | OpenCV Facemark 공식 튜토리얼 연결 모델 |
| `face_recognition_sface_2021dec.onnx` | 1:1 얼굴 특징 비교 | OpenCV Zoo SFace, Apache-2.0 모델 디렉터리 기준 |
| `community_forensics_vit_int8.onnx` | AI 생성 이미지 위험 점수 | Community Forensics ViT, MIT |

모델 데이터의 상업 이용 적합성과 학습 데이터 출처는 출시 전에 별도 법무 검토가 필요합니다.
