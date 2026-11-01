from __future__ import annotations

import threading

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.features.primary_photo.analyzer import EyeClosureEstimator, FaceAnalyzer, FaceObservation
from app.features.primary_photo.schemas import ReasonCode


def analyzer_without_models() -> FaceAnalyzer:
    analyzer = object.__new__(FaceAnalyzer)
    analyzer.settings = Settings(min_image_edge_px=64, min_blur_variance=0)
    return analyzer


@pytest.mark.parametrize(
    ("shape", "detector_size"),
    [((1800, 1800), (1024, 1024)), ((1801, 2400), (1024, 768)), ((2400, 1801), (768, 1024)), ((480, 640), (640, 480))],
)
def test_detection_preserves_original_face_geometry_and_quality(shape, detector_size):
    analyzer = analyzer_without_models()
    analyzer._lock = threading.Lock()
    image = np.full((*shape, 3), 128, dtype=np.uint8)
    fitted = []
    sharpness_inputs = []

    class Detector:
        def setInputSize(self, size):
            assert size == detector_size

        def detect(self, detector_image):
            assert detector_image.shape[:2] == detector_size[::-1]
            width, height = detector_size
            face = np.array(
                [
                    width * 0.2,
                    height * 0.1,
                    width * 0.5,
                    height * 0.6,
                    width * 0.3,
                    height * 0.3,
                    width * 0.6,
                    height * 0.3,
                    width * 0.45,
                    height * 0.4,
                    width * 0.35,
                    height * 0.5,
                    width * 0.55,
                    height * 0.5,
                    0.95,
                ],
                dtype=np.float32,
            )
            return None, face[None, :]

    def fit(original, detection):
        assert original is image
        fitted.append(detection.copy())
        return np.zeros((68, 2))

    def sharpness(original, detection):
        assert original is image
        sharpness_inputs.append(detection.copy())
        return 17.5

    analyzer._detector = Detector()
    analyzer._fit_landmarks = fit
    analyzer._face_blur_variance = sharpness
    result = analyzer.analyze(image)

    height, width = shape
    expected = np.array(
        [
            width * 0.2,
            height * 0.1,
            width * 0.5,
            height * 0.6,
            width * 0.3,
            height * 0.3,
            width * 0.6,
            height * 0.3,
            width * 0.45,
            height * 0.4,
            width * 0.35,
            height * 0.5,
            width * 0.55,
            height * 0.5,
            0.95,
        ]
    )
    np.testing.assert_allclose(fitted[0], expected, rtol=1e-6)
    np.testing.assert_allclose(sharpness_inputs[0], expected, rtol=1e-6)
    assert result.quality.face_area_ratio == pytest.approx(0.3)
    assert (result.quality.height, result.quality.width) == shape
    assert result.quality.blur_variance == 17.5


def test_closeup_fallback_uses_original_coordinates_after_scaled_detection():
    analyzer = analyzer_without_models()
    analyzer._lock = threading.Lock()
    image = np.full((1800, 1800, 3), 128, dtype=np.uint8)
    face = np.array([[100, 200, 400, 500]], dtype=np.float32)

    class Detector:
        def setInputSize(self, size):
            assert size == (1024, 1024)

        def detect(self, detector_image):
            return None, None

    def fallback(original):
        assert original is image
        return face

    def fit(original, detection):
        assert original is image
        np.testing.assert_array_equal(detection, face[0])
        return np.zeros((68, 2))

    analyzer._detector = Detector()
    analyzer._detect_closeup_faces = fallback
    analyzer._fit_landmarks = fit
    observation = analyzer.observe(image, allow_detection_fallback=True)

    assert observation.face_count == 1
    assert observation.center_x == pytest.approx(300 / 1800)
    assert observation.center_y == pytest.approx(450 / 1800)


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


@pytest.mark.parametrize("edge", [120, 1800])
def test_low_light_retry_enhances_detector_input(edge):
    analyzer = analyzer_without_models()
    analyzer._lock = threading.Lock()
    face = np.array([20, 20, 80, 80], dtype=np.float32)
    detector_means = []

    class StubDetector:
        def setInputSize(self, size):
            assert size == (min(edge, 1024), min(edge, 1024))

        def detect(self, image):
            assert image.shape[:2] == (min(edge, 1024), min(edge, 1024))
            detector_means.append(float(image.mean()))
            return (None, np.stack([face])) if image.mean() >= 40 else (None, None)

    analyzer._detector = StubDetector()
    analyzer._fit_landmarks = lambda _image, _face: np.zeros((68, 2), dtype=np.float64)

    observation = analyzer.observe(
        np.full((edge, edge, 3), 15, dtype=np.uint8),
        allow_detection_fallback=True,
        allow_low_light_enhancement=True,
    )

    assert observation.face_count == 1
    assert observation.low_light_enhanced
    assert detector_means[1] >= 40


class FakeEyes:
    def __init__(self, scores):
        self.scores = scores

    def blink_scores(self, _image):
        return self.scores


def frontal_single_face(analyzer):
    analyzer.observe = lambda _image, **_kwargs: FaceObservation(
        face_count=1, detected_face_count=1, face_area_ratio=0.2
    )


@pytest.mark.parametrize(
    ("scores", "rejected"),
    [
        ((0.53, 0.50), True),  # 눈 감고 웃는 사진 실측값
        ((0.01, 0.01), False),  # 눈 뜬 셀카 실측값
        ((0.80, 0.10), False),  # 한쪽 눈 윙크
        (None, False),  # 측정 모델이 얼굴을 못 찾음
    ],
)
def test_photo_with_both_eyes_closed_is_rejected_as_non_frontal(scores, rejected):
    # BE가 허용하는 기존 사유 코드(NON_FRONTAL_FACE)로 돌려준다.
    analyzer = analyzer_without_models()
    frontal_single_face(analyzer)
    analyzer._eyes = FakeEyes(scores)

    result = analyzer.analyze(np.full((200, 200, 3), 128, dtype=np.uint8))

    assert (ReasonCode.NON_FRONTAL_FACE in result.reasons) is rejected


def test_eye_check_is_skipped_without_the_landmark_model():
    analyzer = analyzer_without_models()
    frontal_single_face(analyzer)

    result = analyzer.analyze(np.full((200, 200, 3), 128, dtype=np.uint8))

    assert result.reasons == []


def test_eye_closure_threshold_is_configurable():
    analyzer = analyzer_without_models()
    analyzer.settings = Settings(min_image_edge_px=64, min_blur_variance=0, max_eye_blink_score=0.6)
    frontal_single_face(analyzer)
    analyzer._eyes = FakeEyes((0.53, 0.50))

    assert analyzer.analyze(np.full((200, 200, 3), 128, dtype=np.uint8)).reasons == []


def test_eye_closure_estimator_finds_no_face_in_blank_image():
    path = Settings().face_landmarker_model_path
    if not path.exists():
        pytest.skip("face_landmarker.task 없음")
    assert EyeClosureEstimator(path).blink_scores(np.full((240, 320, 3), 128, dtype=np.uint8)) is None
