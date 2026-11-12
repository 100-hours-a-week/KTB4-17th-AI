"""라이브 얼굴 위치·방향 측정. MediaPipe Face Landmarker를 CPU에서 실행한다."""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from app.core.config import Settings
from app.core.errors import ModelUnavailable
from app.features.primary_photo.analyzer import FaceObservation
from app.features.primary_photo.schemas import Pose


def pose_from_matrix(matrix: np.ndarray) -> Pose | None:
    """MediaPipe의 y-up/z-toward-camera 좌표를 영상 좌표로 변환한다."""
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        return None
    axes = np.diag([1.0, -1.0, -1.0])
    rotation = axes @ matrix[:3, :3] @ axes
    u, scales, vt = np.linalg.svd(rotation)
    if min(scales) <= 1e-8 or np.linalg.det(u @ vt) <= 0:
        return None
    pitch, yaw, roll = cv2.RQDecomp3x3(u @ vt)[0]
    return Pose(yaw=round(-yaw, 2), pitch=round(pitch, 2), roll=round(roll, 2))


def observation_from_result(result) -> FaceObservation:
    count = len(result.face_landmarks)
    if count != 1:
        return FaceObservation(face_count=count, detected_face_count=count, pose_valid=False, pose_method="face_mesh")
    points = np.array([(point.x, point.y) for point in result.face_landmarks[0]], dtype=np.float64)
    if points.size == 0 or not np.isfinite(points).all():
        return FaceObservation(face_count=1, detected_face_count=1, pose_valid=False, pose_method="face_mesh")
    low, high = points.min(axis=0), points.max(axis=0)
    center = (low + high) / 2
    width, height = high - low
    pose = pose_from_matrix(result.facial_transformation_matrixes[0]) if result.facial_transformation_matrixes else None
    return FaceObservation(
        face_count=1,
        detected_face_count=1,
        detection_method="mediapipe-face-landmarker",
        pose_valid=pose is not None,
        yaw=pose.yaw if pose else 0.0,
        pitch=pose.pitch if pose else 0.0,
        roll=pose.roll if pose else 0.0,
        face_area_ratio=float(width * height),
        center_x=float(center[0]),
        center_y=float(center[1]),
        width_ratio=float(width),
        height_ratio=float(height),
        face_in_frame=bool(np.all(low >= 0) and np.all(high <= 1)),
        pose_method="face_mesh",
    )


class LiveFaceAnalyzer:
    def __init__(self, settings: Settings, *, video: bool = True) -> None:
        try:
            import mediapipe as mp
        except ImportError as exc:
            raise ModelUnavailable("라이브 얼굴 모델 의존성이 없습니다. uv sync --locked 로 설치하세요.") from exc
        if not settings.face_landmarker_model_path.exists():
            raise ModelUnavailable("라이브 얼굴 모델이 없습니다. python scripts/download_models.py --demo 로 받으세요.")
        self._mp = mp
        self._video = video
        self._timestamp = -1
        self._lock = threading.Lock()
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(settings.face_landmarker_model_path), delegate=mp.tasks.BaseOptions.Delegate.CPU
            ),
            running_mode=mp.tasks.vision.RunningMode.VIDEO if video else mp.tasks.vision.RunningMode.IMAGE,
            num_faces=2,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_facial_transformation_matrixes=True,
        )
        self._model = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    def observe(self, image: np.ndarray) -> FaceObservation:
        with self._lock:
            return self._observe(image)

    def _observe(self, image: np.ndarray) -> FaceObservation:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        frame = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        if self._video:
            self._timestamp = max(self._timestamp + 1, time.monotonic_ns() // 1_000_000)
            result = self._model.detect_for_video(frame, self._timestamp)
        else:
            result = self._model.detect(frame)
        return observation_from_result(result)

    def close(self) -> None:
        self._model.close()
