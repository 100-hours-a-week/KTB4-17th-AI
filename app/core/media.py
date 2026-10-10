"""업로드 미디어 공통 검증과 이미지 디코딩."""

from __future__ import annotations

import io

import cv2
import numpy as np
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

SUPPORTED_IMAGES = {"image/jpeg", "image/png"}
SUPPORTED_VIDEOS = {"video/mp4", "video/webm", "video/quicktime"}


class InvalidImage(ValueError):
    pass


def decode_image(data: bytes) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.verify()
        with Image.open(io.BytesIO(data)) as source:
            rgb = ImageOps.exif_transpose(source).convert("RGB")
            array = np.asarray(rgb)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("지원되는 JPEG/JPG 또는 PNG 이미지가 아닙니다.") from exc
    if array.ndim != 3 or array.shape[2] != 3:
        raise InvalidImage("RGB 이미지로 변환할 수 없습니다.")
    return cv2.cvtColor(array, cv2.COLOR_RGB2BGR)


def enhance_low_light(image: np.ndarray) -> np.ndarray:
    """검출 재시도용으로만 사용하는 제한적 밝기 보정."""
    mean_brightness = max(1.0, float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean()))
    gain = min(4.0, 75.0 / mean_brightness)
    return cv2.convertScaleAbs(image, alpha=gain, beta=4)


async def read_image(file: UploadFile, limit: int) -> tuple[bytes, str]:
    content_type = file.content_type or "application/octet-stream"
    if content_type not in SUPPORTED_IMAGES:
        raise HTTPException(415, "JPEG/JPG, PNG 이미지만 지원합니다.")
    return await read_limited(file, limit), content_type


async def read_video(file: UploadFile, limit: int) -> bytes:
    if file.content_type not in SUPPORTED_VIDEOS:
        raise HTTPException(415, "MP4, WebM, MOV 영상만 지원합니다.")
    return await read_limited(file, limit)


async def read_limited(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "업로드 파일이 허용 크기를 초과했습니다.")
    if not data:
        raise HTTPException(422, "빈 파일은 처리할 수 없습니다.")
    return data
