from __future__ import annotations

import io

import numpy as np
from PIL import Image

from app.core.config import Settings
from app.features.synthetic_detection.schemas import SyntheticDecision
from app.features.synthetic_detection.service import SyntheticDetectionService


def image_bytes(array: np.ndarray) -> bytes:
    output = io.BytesIO()
    Image.fromarray(array).save(output, format="JPEG", quality=95)
    return output.getvalue()


def detector() -> SyntheticDetectionService:
    service = SyntheticDetectionService(Settings())
    service.provenance.inspect = lambda *_args: "none"
    service._predict = lambda _data: 0.01
    return service


def test_pixel_art_signal_blocks_low_model_probability():
    small = np.zeros((32, 32, 3), dtype=np.uint8)
    small[::2, ::2] = [255, 190, 30]
    small[1::2, 1::2] = [40, 180, 255]
    pixel_art = np.asarray(Image.fromarray(small).resize((512, 512), Image.Resampling.NEAREST))

    result = detector().inspect(image_bytes(pixel_art), "image/jpeg")

    assert result.decision is SyntheticDecision.SYNTHETIC_RISK
    assert result.signals == ["PIXEL_ART_OR_ILLUSTRATION"]


def test_random_texture_is_not_classified_as_pixel_art():
    texture = np.random.default_rng(11).integers(0, 256, (512, 512, 3), dtype=np.uint8)

    result = detector().inspect(image_bytes(texture), "image/jpeg")

    assert result.decision is SyntheticDecision.CLEAR
    assert result.signals == []
