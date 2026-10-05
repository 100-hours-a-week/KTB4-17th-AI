"""고정된 공식 모델을 내려받고 SHA-256을 검증한다."""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Model:
    filename: str
    url: str
    sha256: str


MODELS = (
    Model(
        filename="face_detection_yunet_2023mar.onnx",
        url=(
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
            "47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/"
            "face_detection_yunet_2023mar.onnx"
        ),
        sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    Model(
        filename="face_recognition_sface_2021dec.onnx",
        url=(
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/ba91a3b/models/"
            "face_recognition_sface/face_recognition_sface_2021dec.onnx"
        ),
        sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
    Model(
        filename="lbfmodel.yaml",
        url="https://raw.githubusercontent.com/kurnianggoro/GSOC2017/master/data/lbfmodel.yaml",
        sha256="70dd8b1657c42d1595d6bd13d97d932877b3bed54a95d3c4733a0f740d1fd66b",
    ),
    Model(
        filename="community_forensics_vit_int8.onnx",
        url=(
            "https://huggingface.co/buildborderless/CommunityForensics-DeepfakeDet-ViT/"
            "resolve/0b8f9cb/onnx/model_int8.onnx?download=true"
        ),
        sha256="968f113f1107d58bfc73444b6e87020e2d541780ad43b7a1ac3e3b18b86c2bbd",
    ),
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def download(model: Model, directory: Path) -> None:
    destination = directory / model.filename
    if destination.exists() and digest(destination) == model.sha256:
        print(f"verified: {destination}")
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(model.url, headers={"User-Agent": "profile-trust-model-downloader/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    actual = digest(partial)
    if actual != model.sha256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {model.filename}: {actual}")
    partial.replace(destination)
    print(f"downloaded: {destination}")


def main() -> None:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    directory.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        download(model, directory)


if __name__ == "__main__":
    main()
