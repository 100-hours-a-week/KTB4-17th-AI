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


def test_every_check_logs_scores_without_the_image(caplog):
    caplog.set_level("INFO", logger="app.features.synthetic_detection.service")
    small = np.zeros((32, 32, 3), dtype=np.uint8)
    small[::2, ::2] = [255, 190, 30]
    small[1::2, 1::2] = [40, 180, 255]
    pixel_art = np.asarray(Image.fromarray(small).resize((512, 384), Image.Resampling.NEAREST))

    detector().inspect(image_bytes(pixel_art), "image/jpeg")

    message = caplog.records[-1].getMessage()
    assert message.startswith("synthetic check decision=SYNTHETIC_RISK probability=0.010 threshold=0.700 ")
    assert "signals=PIXEL_ART_OR_ILLUSTRATION provenance=none size=512x384 " in message
    assert "axis_ratio=" in message and "edge_density=" in message


def test_clear_and_confirmed_checks_are_logged(caplog):
    caplog.set_level("INFO", logger="app.features.synthetic_detection.service")
    texture = np.random.default_rng(11).integers(0, 256, (300, 400, 3), dtype=np.uint8)
    service = detector()

    service.inspect(image_bytes(texture), "image/jpeg")
    assert "decision=CLEAR probability=0.010 threshold=0.700 signals=- provenance=none size=400x300" in (
        caplog.records[-1].getMessage()
    )

    service.provenance.inspect = lambda *_args: "confirmed-ai"
    service.inspect(image_bytes(texture), "image/jpeg")
    assert "decision=CONFIRMED_SYNTHETIC probability=- threshold=0.700 signals=TRUSTED_AI_PROVENANCE" in (
        caplog.records[-1].getMessage()
    )
