from __future__ import annotations

import threading

import cv2
import numpy as np

from app.core.config import Settings
from app.features.primary_photo.analyzer import FaceAnalyzer, FaceObservation
from app.features.primary_photo.schemas import ReasonCode


def analyzer_without_models() -> FaceAnalyzer:
    analyzer = object.__new__(FaceAnalyzer)
    analyzer.settings = Settings(min_image_edge_px=64, min_blur_variance=0)
    return analyzer


def test_masked_secondary_faces_are_allowed():
    analyzer = analyzer_without_models()
    analyzer.observe = lambda _image, **_kwargs: FaceObservation(
        face_count=1,
        detected_face_count=3,
        masked_face_count=2,
        face_area_ratio=0.08,
    )

    result = analyzer.analyze(np.full((200, 200, 3), 128, dtype=np.uint8))

    assert ReasonCode.MULTIPLE_FACES not in result.reasons
    assert result.quality.detected_face_count == 3
    assert result.quality.masked_face_count == 2


def test_tiny_background_faces_are_ignored():
    analyzer = analyzer_without_models()
    analyzer.observe = lambda _image, **_kwargs: FaceObservation(
        face_count=1,
        detected_face_count=4,
        ignored_background_face_count=3,
        face_area_ratio=0.08,
    )

    result = analyzer.analyze(np.full((200, 200, 3), 128, dtype=np.uint8))

    assert ReasonCode.MULTIPLE_FACES not in result.reasons
    assert result.quality.ignored_background_face_count == 3


def test_multiple_clear_faces_are_rejected():
    analyzer = analyzer_without_models()
    analyzer.observe = lambda _image, **_kwargs: FaceObservation(
        face_count=2,
        detected_face_count=2,
    )

    result = analyzer.analyze(np.full((200, 200, 3), 128, dtype=np.uint8))

    assert ReasonCode.MULTIPLE_FACES in result.reasons


def test_blurred_face_crop_is_treated_as_masked():
    analyzer = analyzer_without_models()
    blurred = np.full((100, 100, 3), 128, dtype=np.uint8)
    sharp = np.random.default_rng(7).integers(0, 256, (100, 100, 3), dtype=np.uint8)
    detection = np.array([0, 0, 100, 100], dtype=np.float32)

    assert analyzer._looks_masked(blurred, detection)
    assert not analyzer._looks_masked(sharp, detection)


def test_white_scribble_overlay_is_treated_as_masked_even_when_edges_are_sharp():
    analyzer = analyzer_without_models()
    scribbled = np.full((100, 100, 3), 255, dtype=np.uint8)
    scribbled[::5, :] = 0
    detection = np.array([0, 0, 100, 100], dtype=np.float32)

    assert cv2.Laplacian(scribbled, cv2.CV_64F).var() > analyzer.settings.masked_face_max_blur_variance
    assert analyzer._looks_masked(scribbled, detection)


def test_closeup_fallback_converts_detected_boxes_to_float_array():
    analyzer = analyzer_without_models()

    class StubDetector:
        def detectMultiScale(self, *_args, **_kwargs):
            return np.array([[10, 20, 80, 90]], dtype=np.int32)

    analyzer._closeup_detector = StubDetector()
    detected = analyzer._detect_closeup_faces(np.zeros((200, 200, 3), dtype=np.uint8))

    assert detected is not None
    assert detected.dtype == np.float32
    assert detected.tolist() == [[10.0, 20.0, 80.0, 90.0]]


def test_face_sharpness_is_used_instead_of_smooth_background():
    analyzer = analyzer_without_models()
    analyzer.settings = Settings(min_image_edge_px=64, min_blur_variance=8)
    analyzer.observe = lambda _image, **_kwargs: FaceObservation(
        face_count=1,
        detected_face_count=1,
        face_area_ratio=0.1,
        face_blur_variance=17.5,
    )

    result = analyzer.analyze(np.full((200, 200, 3), 128, dtype=np.uint8))

    assert ReasonCode.IMAGE_TOO_BLURRY not in result.reasons
    assert result.quality.blur_variance == 17.5
    assert result.quality.global_blur_variance == 0


def test_dominant_face_is_not_discarded_as_masked_in_low_light():
    analyzer = analyzer_without_models()
    analyzer._lock = threading.Lock()
    dominant = np.array([20, 20, 100, 100], dtype=np.float32)
    secondary = np.array([140, 20, 40, 40], dtype=np.float32)

    class StubDetector:
        def setInputSize(self, _size):
            pass

        def detect(self, _image):
            return None, np.stack([dominant, secondary])

    analyzer._detector = StubDetector()
    analyzer._fit_landmarks = lambda _image, _face: np.zeros((68, 2), dtype=np.float64)
    analyzer._looks_masked = lambda _image, _face: True

    observation = analyzer.observe(
        np.full((180, 200, 3), 35, dtype=np.uint8),
        allow_masked_secondary=True,
    )

    assert observation.face_count == 1
    assert observation.masked_face_count == 1


def test_low_light_retry_enhances_detector_input():
    analyzer = analyzer_without_models()
    analyzer._lock = threading.Lock()
    face = np.array([20, 20, 80, 80], dtype=np.float32)
    detector_means = []

    class StubDetector:
        def setInputSize(self, _size):
            pass

        def detect(self, image):
            detector_means.append(float(image.mean()))
            return (None, np.stack([face])) if image.mean() >= 40 else (None, None)

    analyzer._detector = StubDetector()
    analyzer._fit_landmarks = lambda _image, _face: np.zeros((68, 2), dtype=np.float64)

    observation = analyzer.observe(
        np.full((120, 120, 3), 15, dtype=np.uint8),
        allow_detection_fallback=True,
        allow_low_light_enhancement=True,
    )

    assert observation.face_count == 1
    assert observation.low_light_enhanced
    assert detector_means[1] >= 40
