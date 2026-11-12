"""손 모양 판정 인식률 평가.

라벨 폴더 구조: <root>/<GESTURE>/<UP|DOWN|SIDEWAYS>/<파일> (예: TWO/SIDEWAYS/clip.mp4)
  - 영상(.mp4/.webm/.mov): 라이브니스와 같이 최대 24프레임을 고르게 뽑아 '모양 유지' 판정까지 본다.
  - 이미지(.jpg/.jpeg/.png): 프레임 하나로 센다.
기존 <root>/<GESTURE>/<파일> 형식은 UP으로 읽는다. 안내한 방향과 반대면 불일치로 센다.
클립은 hand_gesture_demo.py --record-dir 로 녹화할 수 있다.

사용법:
    uv run python scripts/eval_hand_gestures.py evals/hand_gestures [--out evals/results/hand-gestures.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.features.face_verification.gesture import (  # noqa: E402
    GestureReading,
    HandDirection,
    HandGesture,
    judge_hold,
    matches_gesture,
    read_gesture,
)
from app.features.face_verification.hand import HandLandmarker  # noqa: E402

VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
MAX_SAMPLED_FRAMES = 24


def labeled_files(root: Path) -> Iterator[tuple[Path, HandGesture, HandDirection]]:
    for gesture in HandGesture:
        parent = root / gesture.value
        for folder, direction in (
            (parent, HandDirection.UP),  # 예전 녹화 파일
            (parent / "UP", HandDirection.UP),
            (parent / "DOWN", HandDirection.DOWN),
            (parent / "SIDEWAYS", HandDirection.SIDEWAYS),
        ):
            if folder.is_dir():
                for path in sorted(folder.iterdir()):
                    if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
                        yield path, gesture, direction


def sample_frames(path: Path) -> list[np.ndarray]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        image = cv2.imread(str(path))
        return [] if image is None else [image]
    capture = cv2.VideoCapture(str(path))
    frames: list[np.ndarray] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        capture.release()
    if len(frames) <= MAX_SAMPLED_FRAMES:
        return frames
    indices = np.linspace(0, len(frames) - 1, MAX_SAMPLED_FRAMES, dtype=int)
    return [frames[index] for index in indices]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    landmarker = HandLandmarker(get_settings())
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    clips: list[dict] = []
    for path, gesture, direction in labeled_files(args.root):
        expected_label = gesture.value if direction == HandDirection.UP else f"{gesture}:{direction}"
        frames: list[list[GestureReading]] = []
        for frame in sample_frames(path):
            readings = [read_gesture(hand.image_points) for hand in landmarker.detect(frame)]
            frames.append(readings)
            label = readings[0].label if readings else "NO_HAND"
            if any(matches_gesture(reading, gesture, direction) for reading in readings):
                label = expected_label
            confusion[expected_label][label] += 1
        hold = judge_hold(frames, gesture, expected_direction=direction)
        clips.append(
            {
                "file": str(path.relative_to(args.root)),
                "expected": gesture.value,
                "expected_direction": hold.expected_direction.value,
                "expected_label": expected_label,
                "sampled_frames": hold.sampled_frames,
                "hand_frames": hold.hand_frames,
                "matched_frames": hold.matched_frames,
                "observed": hold.observed,
                "passed": hold.passed,
            }
        )
        mark = "PASS" if hold.passed else "FAIL"
        print(
            f"[{mark}] {path.relative_to(args.root)}: "
            f"{hold.matched_frames}/{hold.hand_frames} matched, hand {hold.hand_frames}/{hold.sampled_frames}, "
            f"observed={hold.observed}"
        )

    if not clips:
        raise SystemExit(f"평가할 파일이 없습니다: {args.root}/<GESTURE>/")

    print("\n손 모양·방향별 결과 (프레임 일치율 / 클립 통과율)")
    summary: dict[str, dict] = {}
    for expected_label, counts in confusion.items():
        if not counts:
            continue
        total = sum(counts.values())
        gesture_clips = [clip for clip in clips if clip["expected_label"] == expected_label]
        passed = sum(clip["passed"] for clip in gesture_clips)
        summary[expected_label] = {
            "frames": total,
            "frame_accuracy": round(counts[expected_label] / total, 3),
            "clips": len(gesture_clips),
            "clip_pass_rate": round(passed / len(gesture_clips), 3),
            "confused_with": dict((label, count) for label, count in counts.most_common() if label != expected_label),
        }
        print(
            f"  {expected_label:<20} frame {counts[expected_label]:>4}/{total:<4} "
            f"({summary[expected_label]['frame_accuracy']:.0%})  clip {passed}/{len(gesture_clips)}  "
            f"confused={summary[expected_label]['confused_with']}"
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"summary": summary, "clips": clips}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nsaved: {args.out}")


if __name__ == "__main__":
    main()
