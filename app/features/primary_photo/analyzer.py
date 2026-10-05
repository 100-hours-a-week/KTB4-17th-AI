"""YuNet·LBF 기반 대표사진 정면 및 품질 분석."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import Settings
from app.core.errors import ModelUnavailable
from app.core.media import enhance_low_light

from .schemas import Pose, Quality, ReasonCode


@dataclass(frozen=True)
class FaceObservation:
    face_count: int
    detected_face_count: int = 0
    masked_face_count: int = 0
    ignored_background_face_count: int = 0
    detection_method: str = "yunet"
    low_light_enhanced: bool = False
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    eye_aspect_ratio: float = 0.0
    face_area_ratio: float = 0.0
    face_blur_variance: float | None = None


@dataclass(frozen=True)
class PhotoAnalysis:
    quality: Quality
    reasons: list[ReasonCode]


class FaceAnalyzer:
    """YuNet과 OpenCV LBF는 상태를 가지므로 잠금으로 직렬화해 재사용한다."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        missing = [path for path in (settings.yunet_model_path, settings.lbf_model_path) if not path.exists()]
        if missing:
            raise ModelUnavailable("얼굴 랜드마크 모델이 없습니다: " + ", ".join(str(path) for path in missing))
        self._detector = cv2.FaceDetectorYN.create(str(settings.yunet_model_path), "", (320, 320), 0.7, 0.3, 5000)
        self._facemark = cv2.face.createFacemarkLBF()
        self._facemark.loadModel(str(settings.lbf_model_path))
        self._closeup_detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        self._lock = threading.Lock()

    def observe(
        self,
        image: np.ndarray,
        *,
        allow_masked_secondary: bool = False,
        allow_detection_fallback: bool = False,
        allow_low_light_enhancement: bool = False,
    ) -> FaceObservation:
        with self._lock:
            height, width = image.shape[:2]
            analysis_image = image
            low_light_enhanced = False
            self._detector.setInputSize((width, height))
            _, detected = self._detector.detect(image)
            detection_method = "yunet"
            if detected is None and allow_low_light_enhancement and float(image.mean()) < 80:
                analysis_image = enhance_low_light(image)
                _, detected = self._detector.detect(analysis_image)
                if detected is not None:
                    detection_method = "yunet-low-light"
                    low_light_enhanced = True
            if detected is None and (allow_masked_secondary or allow_detection_fallback):
                detected = self._detect_closeup_faces(analysis_image)
                detection_method = "haar-low-light-fallback" if low_light_enhanced else "haar-closeup-fallback"
            if detected is None:
                return FaceObservation(face_count=0, detected_face_count=0)

            detected_count = len(detected)
            masked_count = 0
            background_count = 0
            if detected_count > 1 and allow_masked_secondary:
                clear_faces: list[tuple[np.ndarray, np.ndarray]] = []
                dominant_area = max(float(candidate[2] * candidate[3]) for candidate in detected)
                for candidate in detected:
                    candidate_area = float(candidate[2] * candidate[3])
                    if candidate_area < dominant_area * self.settings.background_face_max_relative_area:
                        background_count += 1
                        continue
                    landmarks = self._fit_landmarks(analysis_image, candidate)
                    is_dominant = candidate_area == dominant_area
                    if landmarks is not None and (is_dominant or not self._looks_masked(analysis_image, candidate)):
                        clear_faces.append((candidate, landmarks))
                masked_count = detected_count - len(clear_faces) - background_count
                if len(clear_faces) != 1:
                    return FaceObservation(
                        face_count=len(clear_faces),
                        detected_face_count=detected_count,
                        masked_face_count=masked_count,
                        ignored_background_face_count=background_count,
                        detection_method=detection_method,
                        low_light_enhanced=low_light_enhanced,
                    )
                selected, landmarks = clear_faces[0]
            else:
                if detected_count != 1:
                    return FaceObservation(face_count=detected_count, detected_face_count=detected_count)
                selected = detected[0]
                landmarks = self._fit_landmarks(analysis_image, selected)

        if landmarks is None:
            return FaceObservation(face_count=0, detected_face_count=detected_count)

        height, width = image.shape[:2]
        _, _, face_width, face_height = selected[:4]
        area_ratio = max(0.0, min(1.0, float(face_width * face_height / (width * height))))
        face_blur = self._face_blur_variance(analysis_image, selected)
        pose = _estimate_pose(landmarks)
        ear = (
            _eye_aspect_ratio(landmarks, (36, 37, 38, 39, 40, 41))
            + _eye_aspect_ratio(landmarks, (42, 43, 44, 45, 46, 47))
        ) / 2
        return FaceObservation(
            face_count=1,
            detected_face_count=detected_count,
            masked_face_count=masked_count,
            ignored_background_face_count=background_count,
            detection_method=detection_method,
            low_light_enhanced=low_light_enhanced,
            yaw=pose.yaw,
            pitch=pose.pitch,
            roll=pose.roll,
            eye_aspect_ratio=ear,
            face_area_ratio=area_ratio,
            face_blur_variance=face_blur,
        )

    def _detect_closeup_faces(self, image: np.ndarray) -> np.ndarray | None:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        detected = self._closeup_detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(64, 64),
        )
        if len(detected) == 0:
            return None
        return np.asarray(detected, dtype=np.float32)

    def _fit_landmarks(self, image: np.ndarray, detection: np.ndarray) -> np.ndarray | None:
        boxes = np.asarray([detection[:4]], dtype=np.int32)
        success, fitted = self._facemark.fit(image, boxes)
        if not success or not fitted:
            return None
        return np.asarray(fitted[0]).reshape(68, 2)

    def _looks_masked(self, image: np.ndarray, detection: np.ndarray) -> bool:
        height, width = image.shape[:2]
        x, y, face_width, face_height = detection[:4].astype(int)
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(width, x + face_width), min(height, y + face_height)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return True
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        focus = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        bright_neutral_overlay = (hsv[:, :, 1] < 45) & (hsv[:, :, 2] > 190)
        dark_overlay = hsv[:, :, 2] < 25
        overlay_ratio = float(max(bright_neutral_overlay.mean(), dark_overlay.mean()))
        return (
            focus <= self.settings.masked_face_max_blur_variance
            or overlay_ratio >= self.settings.masked_face_min_overlay_ratio
        )

    def _face_blur_variance(self, image: np.ndarray, detection: np.ndarray) -> float:
        height, width = image.shape[:2]
        x, y, face_width, face_height = detection[:4].astype(int)
        padding = round(max(face_width, face_height) * 0.1)
        x1, y1 = max(0, x - padding), max(0, y - padding)
        x2, y2 = min(width, x + face_width + padding), min(height, y + face_height + padding)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def analyze(self, image: np.ndarray) -> PhotoAnalysis:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        global_blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        observation = self.observe(image, allow_masked_secondary=True)
        blur = observation.face_blur_variance if observation.face_blur_variance is not None else global_blur
        reasons: list[ReasonCode] = []

        if min(width, height) < self.settings.min_image_edge_px:
            reasons.append(ReasonCode.IMAGE_TOO_SMALL)
        if blur < self.settings.min_blur_variance:
            reasons.append(ReasonCode.IMAGE_TOO_BLURRY)
        if brightness < self.settings.min_brightness:
            reasons.append(ReasonCode.IMAGE_TOO_DARK)
        if brightness > self.settings.max_brightness:
            reasons.append(ReasonCode.IMAGE_TOO_BRIGHT)
        if observation.face_count == 0:
            reasons.append(ReasonCode.NO_FACE)
        elif observation.face_count > 1:
            reasons.append(ReasonCode.MULTIPLE_FACES)
        else:
            if (
                abs(observation.yaw) > self.settings.max_abs_yaw_deg
                or abs(observation.pitch) > self.settings.max_abs_pitch_deg
                or abs(observation.roll) > self.settings.max_abs_roll_deg
            ):
                reasons.append(ReasonCode.NON_FRONTAL_FACE)

        pose = None
        area_ratio = None
        if observation.face_count == 1:
            pose = Pose(yaw=observation.yaw, pitch=observation.pitch, roll=observation.roll)
            area_ratio = observation.face_area_ratio
        return PhotoAnalysis(
            quality=Quality(
                width=width,
                height=height,
                face_count=observation.face_count,
                detected_face_count=observation.detected_face_count,
                masked_face_count=observation.masked_face_count,
                ignored_background_face_count=observation.ignored_background_face_count,
                detection_method=observation.detection_method,
                face_area_ratio=area_ratio,
                blur_variance=blur,
                global_blur_variance=global_blur,
                brightness=brightness,
                pose=pose,
            ),
            reasons=reasons,
        )


def _estimate_pose(landmarks: np.ndarray) -> Pose:
    left_eye = landmarks[36]
    right_eye = landmarks[45]
    eye_mid = (left_eye + right_eye) / 2
    mouth_mid = (landmarks[48] + landmarks[54]) / 2
    nose = landmarks[30]
    eye_distance = float(np.linalg.norm(right_eye - left_eye))
    eye_to_mouth = float(mouth_mid[1] - eye_mid[1])
    if eye_distance <= 1 or abs(eye_to_mouth) <= 1:
        return Pose(yaw=0.0, pitch=0.0, roll=0.0)

    yaw = float((nose[0] - eye_mid[0]) / eye_distance * 50.0)
    pitch = (float((nose[1] - eye_mid[1]) / eye_to_mouth) - 0.50) * 90.0
    roll = math.degrees(math.atan2(right_eye[1] - left_eye[1], right_eye[0] - left_eye[0]))
    return Pose(yaw=round(yaw, 2), pitch=round(pitch, 2), roll=round(roll, 2))


def _eye_aspect_ratio(landmarks: np.ndarray, indexes: tuple[int, int, int, int, int, int]) -> float:
    points = np.array([landmarks[i] for i in indexes], dtype=np.float64)
    vertical_a = np.linalg.norm(points[1] - points[5])
    vertical_b = np.linalg.norm(points[2] - points[4])
    horizontal = np.linalg.norm(points[0] - points[3])
    return float((vertical_a + vertical_b) / (2 * horizontal)) if horizontal > 0 else 0.0
