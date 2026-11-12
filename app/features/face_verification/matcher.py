"""YuNet 검출·정렬과 SFace 임베딩을 이용한 1:1 얼굴 비교."""

import threading
from hashlib import blake2b

import cv2
import numpy as np

from app.core.config import Settings
from app.core.errors import ModelUnavailable
from app.core.media import enhance_low_light

MODEL_VERSION = "opencv-sface-2021dec"
MAX_DETECTION_EDGE_PX = 1024


class FaceNotFound(ValueError):
    pass


class FaceMatcher:
    def __init__(self, settings: Settings) -> None:
        self.detector_path = settings.yunet_model_path
        self.recognizer_path = settings.sface_model_path
        self.threshold = settings.face_match_threshold
        self._detector = None
        self._recognizer = None
        self._fallback_detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        self._lock = threading.Lock()
        # 원본·좌우 반전 대표 사진을 번갈아 비교하므로 최근 두 장의 특징을 보관한다.
        self._cached_ref_features: dict[bytes, np.ndarray] = {}

    def compare(self, reference: np.ndarray, live: np.ndarray) -> tuple[bool, float]:
        ref_key = blake2b(memoryview(np.ascontiguousarray(reference)), digest_size=16).digest()
        with self._lock:
            self._load()
            reference_feature = self._cached_ref_features.get(ref_key)
            if reference_feature is None:
                reference_feature = self._feature(reference)
                if len(self._cached_ref_features) >= 2:
                    self._cached_ref_features.pop(next(iter(self._cached_ref_features)))
                self._cached_ref_features[ref_key] = reference_feature
            live_feature = self._feature(live)
            similarity = float(self._recognizer.match(reference_feature, live_feature, cv2.FaceRecognizerSF_FR_COSINE))
        return similarity >= self.threshold, similarity

    def _load(self) -> None:
        if self._detector is not None:
            return
        missing = [path for path in (self.detector_path, self.recognizer_path) if not path.exists()]
        if missing:
            raise ModelUnavailable("얼굴 비교 모델이 없습니다: " + ", ".join(str(path) for path in missing))
        self._detector = cv2.FaceDetectorYN.create(str(self.detector_path), "", (320, 320), 0.6, 0.3, 5000)
        self._recognizer = cv2.FaceRecognizerSF.create(str(self.recognizer_path), "")

    def _detect(self, image: np.ndarray) -> np.ndarray | None:
        height, width = image.shape[:2]
        detection_image = image
        if max(height, width) > MAX_DETECTION_EDGE_PX:
            scale = MAX_DETECTION_EDGE_PX / max(height, width)
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            detection_image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        det_h, det_w = detection_image.shape[:2]
        self._detector.setInputSize((det_w, det_h))
        _, faces = self._detector.detect(detection_image)
        if faces is not None and len(faces) > 0 and detection_image is not image:
            faces = faces.copy()
            faces[:, 0:14:2] *= width / det_w
            faces[:, 1:14:2] *= height / det_h
        return faces

    def _feature(self, image: np.ndarray) -> np.ndarray:
        analysis_image = image
        faces = self._detect(analysis_image)
        if (faces is None or len(faces) == 0) and float(image.mean()) < 90:
            analysis_image = enhance_low_light(image)
            faces = self._detect(analysis_image)
        if faces is None or len(faces) == 0:
            return self._fallback_feature(analysis_image)
        primary_face = max(faces, key=lambda face: float(face[2] * face[3]))
        aligned = self._recognizer.alignCrop(analysis_image, primary_face)
        return self._recognizer.feature(aligned)

    def _fallback_feature(self, image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self._fallback_detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(64, 64),
        )
        if len(faces) == 0:
            raise FaceNotFound("얼굴 비교 특징을 만들 수 없습니다.")
        x, y, width, height = max(faces, key=lambda face: int(face[2] * face[3]))
        center_x = x + width / 2
        center_y = y + height / 2
        crop_size = max(width, height) * 1.25
        image_height, image_width = image.shape[:2]
        x1 = max(0, round(center_x - crop_size / 2))
        y1 = max(0, round(center_y - crop_size / 2))
        x2 = min(image_width, round(center_x + crop_size / 2))
        y2 = min(image_height, round(center_y + crop_size / 2))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            raise FaceNotFound("얼굴 비교 특징을 만들 수 없습니다.")
        # Haar는 재검출할 영역만 찾는다. 특징 추출에는 반드시 YuNet의 5점 정렬을 쓴다.
        landmarks = self._detect(crop)
        if landmarks is None or len(landmarks) == 0:
            raise FaceNotFound("얼굴 비교를 위한 정렬점을 찾을 수 없습니다.")
        primary = max(landmarks, key=lambda face: float(face[2] * face[3]))
        aligned = self._recognizer.alignCrop(crop, primary)
        return self._recognizer.feature(aligned)
