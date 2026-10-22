from __future__ import annotations

import numpy as np
import pytest

from app.core.config import Settings
from app.features.face_verification.matcher import FaceMatcher, FaceNotFound


def test_feature_uses_largest_face_when_background_face_is_also_detected():
    matcher = object.__new__(FaceMatcher)
    matcher.detector_path = Settings().yunet_model_path
    matcher.recognizer_path = Settings().sface_model_path

    small = np.array([5, 5, 20, 20, *([0] * 11)], dtype=np.float32)
    primary = np.array([30, 20, 80, 90, *([0] * 11)], dtype=np.float32)

    class FakeDetector:
        def setInputSize(self, _size):
            pass

        def detect(self, _image):
            return None, np.stack([small, primary])

    class FakeRecognizer:
        selected = None

        def alignCrop(self, _image, face):
            self.selected = face
            return np.zeros((112, 112, 3), dtype=np.uint8)

        def feature(self, _aligned):
            return np.zeros((1, 128), dtype=np.float32)

    matcher._detector = FakeDetector()
    matcher._recognizer = FakeRecognizer()

    matcher._feature(np.zeros((160, 160, 3), dtype=np.uint8))

    assert np.array_equal(matcher._recognizer.selected, primary)


def test_feature_retries_with_low_light_enhancement():
    matcher = object.__new__(FaceMatcher)
    matcher.detector_path = Settings().yunet_model_path
    matcher.recognizer_path = Settings().sface_model_path
    face = np.array([20, 20, 80, 90, *([0] * 11)], dtype=np.float32)
    detector_means = []

    class FakeDetector:
        def setInputSize(self, _size):
            pass

        def detect(self, image):
            detector_means.append(float(image.mean()))
            return (None, np.stack([face])) if image.mean() >= 40 else (None, None)

    class FakeRecognizer:
        def alignCrop(self, _image, _face):
            return np.zeros((112, 112, 3), dtype=np.uint8)

        def feature(self, _aligned):
            return np.zeros((1, 128), dtype=np.float32)

    matcher._detector = FakeDetector()
    matcher._recognizer = FakeRecognizer()

    matcher._feature(np.full((120, 120, 3), 15, dtype=np.uint8))

    assert detector_means[0] == 15
    assert detector_means[1] >= 40


def test_haar_roi_retries_yunet_and_keeps_landmark_alignment():
    matcher = object.__new__(FaceMatcher)
    matcher.detector_path = Settings().yunet_model_path
    matcher.recognizer_path = Settings().sface_model_path

    class MissingDetector:
        def setInputSize(self, _size):
            pass

        def detect(self, image):
            if image.shape[:2] == (140, 140):
                return None, None
            return None, np.array([[10, 10, 70, 80, *([0] * 11)]], dtype=np.float32)

    class FallbackDetector:
        def detectMultiScale(self, _gray, **_kwargs):
            return np.array([[15, 10, 90, 100]], dtype=np.int32)

    class FakeRecognizer:
        received_shape = None
        aligned = False

        def alignCrop(self, image, face):
            self.aligned = True
            return np.zeros((112, 112, 3), dtype=np.uint8)

        def feature(self, aligned):
            self.received_shape = aligned.shape
            return np.zeros((1, 128), dtype=np.float32)

    matcher._detector = MissingDetector()
    matcher._fallback_detector = FallbackDetector()
    matcher._recognizer = FakeRecognizer()

    feature = matcher._feature(np.full((140, 140, 3), 100, dtype=np.uint8))

    assert feature.shape == (1, 128)
    assert matcher._recognizer.received_shape == (112, 112, 3)
    assert matcher._recognizer.aligned
    matcher._detect = lambda image: None
    with pytest.raises(FaceNotFound):
        matcher._fallback_feature(np.full((140, 140, 3), 100, dtype=np.uint8))


def test_matcher_downscales_high_res_detection_and_caches_reference_feature():
    import threading

    matcher = object.__new__(FaceMatcher)
    matcher.detector_path = Settings().yunet_model_path
    matcher.recognizer_path = Settings().sface_model_path
    matcher.threshold = 0.42
    matcher._lock = threading.Lock()
    matcher._cached_ref_key = None
    matcher._cached_ref_feature = None

    det_sizes = []
    detect_calls = []

    class FakeDetector:
        def setInputSize(self, size):
            det_sizes.append(size)

        def detect(self, image):
            detect_calls.append(image.shape[:2])
            face = np.array([10, 20, 100, 120, *([0] * 11)], dtype=np.float32)
            return None, np.stack([face])

    class FakeRecognizer:
        selected_boxes = []

        def alignCrop(self, _image, face):
            self.selected_boxes.append(face[:4].copy())
            return np.zeros((112, 112, 3), dtype=np.uint8)

        def feature(self, _aligned):
            return np.ones((1, 128), dtype=np.float32)

        def match(self, _ref, _live, _mode):
            return 0.85

    matcher._detector = FakeDetector()
    matcher._recognizer = FakeRecognizer()

    ref = np.full((1536, 2048, 3), 120, dtype=np.uint8)
    live1 = np.full((480, 640, 3), 90, dtype=np.uint8)
    live2 = np.full((480, 640, 3), 95, dtype=np.uint8)

    matched1, sim1 = matcher.compare(ref, live1)
    matched2, sim2 = matcher.compare(ref, live2)

    assert matched1 and matched2
    assert sim1 == 0.85 and sim2 == 0.85
    # 2048x1536 reference image is downscaled to 1024x768 for detection, and bbox is scaled back 2x
    assert det_sizes[0] == (1024, 768)
    assert np.allclose(matcher._recognizer.selected_boxes[0], [20, 40, 200, 240])
    # Reference feature is computed once and reused on second compare()
    assert len(detect_calls) == 3
