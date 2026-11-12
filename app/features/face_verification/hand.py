"""MediaPipe 손바닥 검출·21점 손 랜드마크(OpenCV Zoo ONNX) 추론.

OpenCV Zoo의 mp_palmdet.py·mp_handpose.py 전후처리를 옮기되, 두 번의 crop·pad를
한 번의 affine 변환으로 합쳤다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import Settings
from app.core.errors import ModelUnavailable

PALM_INPUT_SIZE = 192
HANDPOSE_INPUT_SIZE = 224
# 손바닥 검출 7점 중 손목과 중지 뿌리. 손을 세로로 세우는 회전 기준이다.
PALM_WRIST = 0
PALM_MIDDLE_BASE = 2
HAND_ROI_SHIFT_Y = -0.4
HAND_ROI_ENLARGE = 3.0
HAND_DUPLICATE_IOU = 0.5


@dataclass(frozen=True)
class PalmDetection:
    box: np.ndarray  # [x1, y1, x2, y2]
    landmarks: np.ndarray  # (7, 2)
    score: float


@dataclass(frozen=True)
class HandLandmarks:
    box: np.ndarray  # 21점을 감싸는 [x1, y1, x2, y2]
    image_points: np.ndarray  # (21, 3) 원본 이미지 좌표 + 손목 기준 상대 깊이
    world_points: np.ndarray  # (21, 3) 손 중심 기준 미터 단위 3D 좌표
    handedness: float  # 0에 가까우면 왼손, 1에 가까우면 오른손 (반전 안 된 영상 기준)
    confidence: float


def _ssd_anchors() -> np.ndarray:
    """MediaPipe palm detection(192px) SSD 앵커. stride 8에 2개, stride 16에 6개씩."""
    anchors: list[tuple[float, float]] = []
    for stride, per_cell in ((8, 2), (16, 6)):
        grid = PALM_INPUT_SIZE // stride
        for y in range(grid):
            for x in range(grid):
                anchors.extend([((x + 0.5) / grid, (y + 0.5) / grid)] * per_cell)
    return np.asarray(anchors, dtype=np.float32)


class HandLandmarker:
    """두 ONNX 세션을 공유하므로 잠금으로 직렬화해 재사용한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        palm_score_threshold: float = 0.5,
        palm_nms_threshold: float = 0.3,
        landmark_confidence_threshold: float = 0.8,
        max_hands: int = 2,
    ) -> None:
        missing = [path for path in (settings.palm_model_path, settings.handpose_model_path) if not path.exists()]
        if missing:
            raise ModelUnavailable("손 랜드마크 모델이 없습니다: " + ", ".join(str(path) for path in missing))
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ModelUnavailable("onnxruntime이 설치되지 않았습니다.") from exc
        providers = ["CPUExecutionProvider"]
        self._palm = ort.InferenceSession(str(settings.palm_model_path), providers=providers)
        self._handpose = ort.InferenceSession(str(settings.handpose_model_path), providers=providers)
        self._anchors = _ssd_anchors()
        self.palm_score_threshold = palm_score_threshold
        self.palm_nms_threshold = palm_nms_threshold
        self.landmark_confidence_threshold = landmark_confidence_threshold
        self.max_hands = max_hands
        self._lock = threading.Lock()

    def detect(self, image: np.ndarray) -> list[HandLandmarks]:
        """BGR 이미지에서 손을 찾아 손바닥 점수가 높은 순으로 돌려준다."""
        hands: list[HandLandmarks] = []
        with self._lock:
            for palm in self._detect_palms(image):
                if len(hands) >= self.max_hands:
                    break
                hand = self._estimate(image, palm)
                # 손바닥 박스가 달라도 같은 손으로 수렴하는 경우가 있어 랜드마크 박스로 한 번 더 거른다.
                if hand is not None and all(_iou(hand.box, kept.box) < HAND_DUPLICATE_IOU for kept in hands):
                    hands.append(hand)
        return hands

    def _detect_palms(self, image: np.ndarray) -> list[PalmDetection]:
        height, width = image.shape[:2]
        ratio = PALM_INPUT_SIZE / max(height, width)
        resized_w, resized_h = round(width * ratio), round(height * ratio)
        resized = cv2.resize(image, (resized_w, resized_h))
        pad_left = (PALM_INPUT_SIZE - resized_w) // 2
        pad_top = (PALM_INPUT_SIZE - resized_h) // 2
        padded = cv2.copyMakeBorder(
            resized,
            pad_top,
            PALM_INPUT_SIZE - resized_h - pad_top,
            pad_left,
            PALM_INPUT_SIZE - resized_w - pad_left,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        blob = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32)[np.newaxis] / 255.0
        regressors, scores = self._palm.run(None, {self._palm.get_inputs()[0].name: blob})

        score = 1.0 / (1.0 + np.exp(-np.clip(scores[0, :, 0].astype(np.float64), -60, 60)))
        deltas = regressors[0] / PALM_INPUT_SIZE
        centers = deltas[:, 0:2] + self._anchors
        sizes = deltas[:, 2:4]
        scale = max(height, width)
        pad_bias = np.array([pad_left, pad_top], dtype=np.float32) / ratio
        top_left = (centers - sizes / 2) * scale - pad_bias
        bottom_right = (centers + sizes / 2) * scale - pad_bias
        keypoints = (deltas[:, 4:].reshape(-1, 7, 2) + self._anchors[:, np.newaxis, :]) * scale - pad_bias

        xywh = np.concatenate([top_left, bottom_right - top_left], axis=1)
        keep = cv2.dnn.NMSBoxes(xywh.tolist(), score.tolist(), self.palm_score_threshold, self.palm_nms_threshold)
        keep = sorted(np.asarray(keep).reshape(-1).tolist(), key=lambda index: -score[index])
        return [
            PalmDetection(
                box=np.concatenate([top_left[index], bottom_right[index]]),
                landmarks=keypoints[index],
                score=float(score[index]),
            )
            for index in keep
        ]

    def _estimate(self, image: np.ndarray, palm: PalmDetection) -> HandLandmarks | None:
        transform = _hand_roi_transform(palm)
        crop = cv2.warpAffine(
            image,
            transform,
            (HANDPOSE_INPUT_SIZE, HANDPOSE_INPUT_SIZE),
            flags=cv2.INTER_AREA,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        blob = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)[np.newaxis] / 255.0
        outputs = {
            meta.name: value
            for meta, value in zip(
                self._handpose.get_outputs(),
                self._handpose.run(None, {self._handpose.get_inputs()[0].name: blob}),
                strict=True,
            )
        }
        confidence = float(outputs["Identity_1"].reshape(-1)[0])
        if confidence < self.landmark_confidence_threshold:
            return None

        crop_points = outputs["Identity"].reshape(21, 3).astype(np.float64)
        inverse = cv2.invertAffineTransform(transform)
        xy = np.c_[crop_points[:, :2], np.ones(21)] @ inverse.T
        depth_scale = float(np.sqrt(abs(np.linalg.det(inverse[:, :2]))))
        image_points = np.c_[xy, crop_points[:, 2] * depth_scale]
        # world 좌표는 crop(손을 세운 상태) 기준이므로 화면 기준으로 되돌린다.
        rotation = inverse[:, :2] / depth_scale
        world = outputs["Identity_3"].reshape(21, 3).astype(np.float64)
        world_points = np.c_[world[:, :2] @ rotation.T, world[:, 2]]
        return HandLandmarks(
            box=np.concatenate([xy.min(axis=0), xy.max(axis=0)]),
            image_points=image_points,
            world_points=world_points,
            handedness=float(outputs["Identity_2"].reshape(-1)[0]),
            confidence=confidence,
        )


def _iou(first: np.ndarray, second: np.ndarray) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    overlap = width * height
    union = (first[2] - first[0]) * (first[3] - first[1]) + (second[2] - second[0]) * (second[3] - second[1]) - overlap
    return float(overlap / union) if union > 0 else 0.0


def _hand_roi_transform(palm: PalmDetection) -> np.ndarray:
    """원본 이미지 → 손을 세운 224×224 입력으로 가는 2×3 affine."""
    wrist = palm.landmarks[PALM_WRIST]
    middle = palm.landmarks[PALM_MIDDLE_BASE]
    radians = np.pi / 2 - np.arctan2(-(middle[1] - wrist[1]), middle[0] - wrist[0])
    radians -= 2 * np.pi * np.floor((radians + np.pi) / (2 * np.pi))
    center = (palm.box[:2] + palm.box[2:]) / 2
    rotation = cv2.getRotationMatrix2D((float(center[0]), float(center[1])), float(np.degrees(radians)), 1.0)

    rotated = np.c_[palm.landmarks, np.ones(len(palm.landmarks))] @ rotation.T
    low, high = rotated.min(axis=0), rotated.max(axis=0)
    size = high - low
    roi_center = (low + high) / 2 + np.array([0.0, HAND_ROI_SHIFT_Y]) * size
    side = max(float(size.max()) * HAND_ROI_ENLARGE, 1.0)

    scale = HANDPOSE_INPUT_SIZE / side
    to_crop = np.array(
        [
            [scale, 0.0, -scale * (roi_center[0] - side / 2)],
            [0.0, scale, -scale * (roi_center[1] - side / 2)],
        ]
    )
    return to_crop @ np.vstack([rotation, [0.0, 0.0, 1.0]])
