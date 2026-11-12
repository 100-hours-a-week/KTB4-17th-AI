"""고정된 공식 모델을 내려받고 SHA-256을 검증한다."""

from __future__ import annotations

import argparse
import hashlib
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
        filename="palm_detection_mediapipe_2023feb.onnx",
        url=(
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/8de3653/models/"
            "palm_detection_mediapipe/palm_detection_mediapipe_2023feb.onnx"
        ),
        sha256="78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c",
    ),
    Model(
        filename="handpose_estimation_mediapipe_2023feb.onnx",
        url=(
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/56cef36/models/"
            "handpose_estimation_mediapipe/handpose_estimation_mediapipe_2023feb.onnx"
        ),
        sha256="db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c",
    ),
    Model(
        filename="community_forensics_vit_int8.onnx",
        url=(
            "https://huggingface.co/buildborderless/CommunityForensics-DeepfakeDet-ViT/"
            "resolve/0b8f9cb/onnx/model_int8.onnx?download=true"
        ),
        sha256="968f113f1107d58bfc73444b6e87020e2d541780ad43b7a1ac3e3b18b86c2bbd",
    ),
    Model(
        filename="kure_v1_int8/model_quantized.onnx",
        url=(
            "https://huggingface.co/hrunj1230/kure-v1-onnx-int8/resolve/"
            "a380db33787422c47175b5cc0ba4325811695141/model_quantized.onnx"
        ),
        sha256="aaf2788bdea5e9cc6bb03d3e7bb546ae73dccee2a5489825000e316876853a61",
    ),
    Model(
        filename="kure_v1_int8/tokenizer.json",
        url=(
            "https://huggingface.co/hrunj1230/kure-v1-onnx-int8/resolve/"
            "a380db33787422c47175b5cc0ba4325811695141/tokenizer.json"
        ),
        sha256="fb3c3b93c46fd5a8634e262e1b7de7da11a18b527aa2282b312952b692781dfd",
    ),
)

DEMO_MODELS = (
    Model(
        filename="face_landmarker.task",
        url="https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        sha256="64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
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
    destination.parent.mkdir(parents=True, exist_ok=True)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("models"))
    parser.add_argument("--demo", action="store_true", help="라이브 얼굴 프로토타입 모델도 내려받기")
    parser.add_argument("--demo-only", action="store_true", help="라이브 얼굴 프로토타입 모델만 내려받기")
    args = parser.parse_args()
    directory = args.directory
    directory.mkdir(parents=True, exist_ok=True)
    models = DEMO_MODELS if args.demo_only else MODELS + (DEMO_MODELS if args.demo else ())
    for model in models:
        download(model, directory)


if __name__ == "__main__":
    main()
