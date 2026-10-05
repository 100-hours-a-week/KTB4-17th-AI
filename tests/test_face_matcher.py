from __future__ import annotations

import numpy as np

from app.core.config import Settings
from app.features.face_verification.matcher import FaceMatcher


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


def test_feature_uses_haar_crop_when_yunet_misses_a_closeup_face():
    matcher = object.__new__(FaceMatcher)
    matcher.detector_path = Settings().yunet_model_path
    matcher.recognizer_path = Settings().sface_model_path

    class MissingDetector:
        def setInputSize(self, _size):
            pass

        def detect(self, _image):
            return None, None

    class FallbackDetector:
        def detectMultiScale(self, _gray, **_kwargs):
            return np.array([[15, 10, 90, 100]], dtype=np.int32)

    class FakeRecognizer:
        received_shape = None

        def feature(self, aligned):
            self.received_shape = aligned.shape
            return np.zeros((1, 128), dtype=np.float32)

    matcher._detector = MissingDetector()
    matcher._fallback_detector = FallbackDetector()
    matcher._recognizer = FakeRecognizer()

    feature = matcher._feature(np.full((140, 140, 3), 100, dtype=np.uint8))

    assert feature.shape == (1, 128)
    assert matcher._recognizer.received_shape == (112, 112, 3)
