"""C2PA 출처와 CPU ONNX 모델을 결합한 합성 이미지 검사."""

from __future__ import annotations

import io
import json
import math
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.core.config import Settings
from app.core.errors import ModelUnavailable
from app.core.media import decode_image

from .schemas import SyntheticDecision, SyntheticResult

MODEL_VERSION = "community-forensics-vit-v1.1-int8"
AI_SOURCE_MARKERS = (
    "trainedalgorithmicmedia",
    "compositewithtrainedalgorithmicmedia",
    "compositesynthetic",
    "algorithmicmedia",
)


class ProvenanceVerifier:
    def __init__(self, trust_anchors_path: Path | None) -> None:
        self.trust_anchors_path = trust_anchors_path

    def inspect(self, data: bytes, content_type: str) -> str:
        try:
            import c2pa
        except ImportError:
            return "unavailable"

        settings: dict = {"verify": {"remote_manifest_fetch": False, "ocsp_fetch": False}}
        if self.trust_anchors_path and self.trust_anchors_path.exists():
            settings["trust"] = {"trust_anchors": self.trust_anchors_path.read_text(encoding="utf-8")}
        try:
            with c2pa.Context.from_dict(settings) as context:
                with c2pa.Reader(content_type, io.BytesIO(data), context=context) as reader:
                    store = json.loads(reader.json())
                    state = str(reader.get_validation_state()).lower()
        except Exception:
            return "none"

        active = store.get("manifests", {}).get(store.get("active_manifest"), {})
        serialized = json.dumps(active.get("assertions", []), ensure_ascii=False).lower()
        declares_ai = any(marker in serialized for marker in AI_SOURCE_MARKERS)
        trusted = "trusted" in state or state.endswith("valid") or state == "valid"
        if declares_ai and trusted:
            return "confirmed-ai"
        return "untrusted-ai" if declares_ai else "verified-non-ai"


class SyntheticDetectionService:
    def __init__(self, settings: Settings) -> None:
        self.model_path = settings.synthetic_model_path
        self.threshold = settings.synthetic_risk_threshold
        self.pixel_art_min_axis_ratio = settings.pixel_art_min_axis_ratio
        self.pixel_art_min_edge_density = settings.pixel_art_min_edge_density
        self.provenance = ProvenanceVerifier(settings.c2pa_trust_anchors_path)
        self._session = None
        self._lock = threading.Lock()

    def inspect(self, data: bytes, content_type: str) -> SyntheticResult:
        decode_image(data)
        provenance = self.provenance.inspect(data, content_type)
        if provenance == "confirmed-ai":
            return SyntheticResult(
                decision=SyntheticDecision.CONFIRMED_SYNTHETIC,
                probability=None,
                provenance=provenance,
                model_version=MODEL_VERSION,
                signals=["TRUSTED_AI_PROVENANCE"],
            )

        probability = self._predict(data)
        signals = self._visual_signals(data)
        decision = (
            SyntheticDecision.SYNTHETIC_RISK if probability >= self.threshold or signals else SyntheticDecision.CLEAR
        )
        return SyntheticResult(
            decision=decision,
            probability=round(probability, 6),
            provenance=provenance,
            model_version=MODEL_VERSION,
            signals=signals,
        )

    def _visual_signals(self, data: bytes) -> list[str]:
        with Image.open(io.BytesIO(data)) as source:
            image = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
        height, width = image.shape[:2]
        scale = min(1.0, 512 / max(width, height))
        if scale < 1:
            image = cv2.resize(
                image,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        magnitude = np.hypot(gradient_x, gradient_y)
        strong = magnitude > 30
        if int(strong.sum()) < 100:
            return []

        angles = np.abs(np.arctan2(gradient_y[strong], gradient_x[strong]))
        axis_distance = np.minimum.reduce([angles, np.abs(angles - np.pi / 2), np.abs(angles - np.pi)])
        axis_ratio = float((axis_distance < np.deg2rad(8)).mean())
        edge_density = float((cv2.Canny(gray, 50, 150) > 0).mean())
        if axis_ratio >= self.pixel_art_min_axis_ratio and edge_density >= self.pixel_art_min_edge_density:
            return ["PIXEL_ART_OR_ILLUSTRATION"]
        return []

    def _predict(self, data: bytes) -> float:
        session = self._get_session()
        image = _preprocess(data)
        input_name = session.get_inputs()[0].name
        with self._lock:
            raw = session.run(None, {input_name: image})[0]
        logit = float(np.asarray(raw).reshape(-1)[0])
        return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, logit))))

    def _get_session(self):
        if self._session is not None:
            return self._session
        if not self.model_path.exists():
            raise ModelUnavailable(f"합성 이미지 모델이 없습니다: {self.model_path}")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ModelUnavailable("onnxruntime이 설치되지 않았습니다.") from exc
        self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        return self._session


def _preprocess(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    scale = 440 / min(width, height)
    image = image.resize((round(width * scale), round(height * scale)), Image.Resampling.BICUBIC)
    left = (image.width - 384) // 2
    top = (image.height - 384) // 2
    image = image.crop((left, top, left + 384, top + 384))
    array = np.asarray(image, dtype=np.float32) / 255.0
    mean = np.array([0.4815, 0.4578, 0.4082], dtype=np.float32)
    std = np.array([0.2686, 0.2613, 0.2758], dtype=np.float32)
    return np.expand_dims(((array - mean) / std).transpose(2, 0, 1), axis=0).astype(np.float32)
