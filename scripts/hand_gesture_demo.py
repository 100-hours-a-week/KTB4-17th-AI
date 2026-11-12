"""손 모양·고개 각도 프로토타입 — 웹캠 실시간 확인·평가용 클립 녹화·이미지 판정.

웹캠 실시간 확인 (화면은 FE 미리보기처럼 좌우 반전해서 보여주고, 판정은 원본 프레임으로 한다):
    uv run --group vision-demo python scripts/hand_gesture_demo.py

시작 전 얼굴을 중앙 가이드에 맞추고 정면을 유지한다. 준비되면 Enter로 기준을 확정한다.
C: 중앙 맞추기부터 다시, R: 동작 재시작, Q: 종료. 라이브 얼굴 측정은 MediaPipe Face Landmarker다.
손 모양은 안내한 UP/DOWN/SIDEWAYS 방향과 같아야 MATCH다 (좌우·손바닥/손등은 허용).
손 모양·위아래·고개 방향 랜덤 출제 (N: 다음 조합, R: 같은 조합 재시도):
    uv run --group vision-demo python scripts/hand_gesture_demo.py --random
아래로 향한 V를 고정해서 확인:
    uv run --group vision-demo python scripts/hand_gesture_demo.py --gesture TWO --direction DOWN
지정 방향으로 한 번 돌렸다 돌아오는 동작 확인:
    uv run --group vision-demo python scripts/hand_gesture_demo.py --head TURN_LEFT

평가용 클립 녹화 (스페이스바로 5초 녹화 → <dir>/<GESTURE>/<UP|DOWN|SIDEWAYS>/<시각>.mp4, q로 종료):
    uv run --group vision-demo python scripts/hand_gesture_demo.py --record-dir evals/hand_gestures --gesture TWO

이미지 한 장 판정 (랜드마크를 그린 결과를 --out 에 저장):
    uv run --group vision-demo python scripts/hand_gesture_demo.py --image hand.jpg --out hand_annotated.jpg
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.features.face_verification.centering import CENTER_POSITION_STDDEV, centering_hint  # noqa: E402
from app.features.face_verification.challenge import random_challenge  # noqa: E402
from app.features.face_verification.gesture import (  # noqa: E402
    GESTURE_LABELS,
    HandDirection,
    HandGesture,
    matches_gesture,
    read_gesture,
    requires_hand_direction,
)
from app.features.face_verification.hand import HandLandmarker, HandLandmarks  # noqa: E402
from app.features.face_verification.head import (  # noqa: E402
    HEAD_MOVEMENT_LABELS,
    HEAD_RETURN_MAX_DELTA_DEG,
    HEAD_TURN_MIN_DELTA_DEG,
    MIN_TURNED_FRAMES,
    HeadMovement,
    HeadPose,
    movement_delta,
    pose_from,
    read_movement,
)
from app.features.face_verification.live_face import LiveFaceAnalyzer  # noqa: E402
from app.features.primary_photo.analyzer import FaceObservation  # noqa: E402

CLIP_SECONDS = 5.0
FINGER_NAMES = ("T", "I", "M", "R", "P")
CALIBRATION_FRAMES = 15
PANEL_WIDTH = 360
BONES = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)  # fmt: skip


@dataclass
class HeadPreview:
    """데모의 정면 기준과 고개 돌리기 진행 상태. 값은 메모리에만 둔다."""

    target: HeadMovement | None = None
    samples: deque[HeadPose] = field(default_factory=lambda: deque(maxlen=CALIBRATION_FRAMES))
    baseline: HeadPose | None = None
    observation: FaceObservation | None = None
    stage: str = "TURN"
    consecutive: int = 0
    peak: float = 0.0
    center_samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=CALIBRATION_FRAMES))
    history: deque[HeadPose] = field(default_factory=lambda: deque(maxlen=3))
    filtered_pose: HeadPose | None = None

    @property
    def ready(self) -> bool:
        return (
            centering_hint(self.observation) is None
            and len(self.samples) == CALIBRATION_FRAMES
            and len(self.center_samples) == CALIBRATION_FRAMES
            and all(
                statistics.pstdev(getattr(sample, axis) for sample in self.samples) <= 2.0 for axis in ("yaw", "pitch")
            )
            and all(
                statistics.pstdev(sample[axis] for sample in self.center_samples) <= CENTER_POSITION_STDDEV
                for axis in (0, 1)
            )
        )

    def confirm_center(self) -> bool:
        if self.baseline is not None or not self.ready:
            return False
        self.baseline = HeadPose(
            statistics.median(sample.yaw for sample in self.samples),
            statistics.median(sample.pitch for sample in self.samples),
        )
        self.filtered_pose = self.baseline
        self.history.clear()
        self.history.append(self.baseline)
        return True

    def restart(self, *, calibrate: bool = False) -> None:
        self.stage, self.consecutive, self.peak = "TURN", 0, 0.0
        self.history.clear()
        self.filtered_pose = None
        if calibrate:
            self.baseline = None
            self.samples.clear()
            self.center_samples.clear()

    def update(self, observation: FaceObservation) -> None:
        self.observation = observation
        pose = pose_from(observation)
        if pose is None:
            self.samples.clear()
            self.center_samples.clear()
            self.restart()
            return
        if self.baseline is None:
            if centering_hint(observation) is not None:
                self.samples.clear()
                self.center_samples.clear()
                return
            self.samples.append(pose)
            self.center_samples.append((observation.center_x, observation.center_y))
            return
        self.history.append(pose)
        pose = HeadPose(
            statistics.median(sample.yaw for sample in self.history),
            statistics.median(sample.pitch for sample in self.history),
        )
        self.filtered_pose = pose
        if self.target is None or self.stage == "DONE":
            return
        delta = movement_delta(pose, self.baseline, self.target)
        self.peak = max(self.peak, delta)
        if self.stage == "TURN":
            self.consecutive = self.consecutive + 1 if read_movement(pose, self.baseline) == self.target else 0
            if self.consecutive >= MIN_TURNED_FRAMES:
                self.stage = "DONE"


def draw_centering_guide(view: np.ndarray, head: HeadPreview) -> np.ndarray:
    if head.baseline is not None:
        return view
    canvas = view.copy()
    height, width = canvas.shape[:2]
    color = (90, 230, 120) if head.ready else (80, 220, 255)
    center = (width // 2, round(height * 0.48))
    cv2.ellipse(canvas, center, (round(width * 0.19), round(height * 0.32)), 0, 0, 360, color, 2, cv2.LINE_AA)
    cv2.drawMarker(canvas, center, color, cv2.MARKER_CROSS, 20, 1)
    face = head.observation
    if (
        face is not None
        and face.face_count == 1
        and all(value is not None for value in (face.center_x, face.center_y, face.width_ratio, face.height_ratio))
    ):
        # 미리보기는 반전됐으므로 얼굴 위치만 x축 반전해 그린다.
        x1 = round((1 - face.center_x - face.width_ratio / 2) * width)
        x2 = round((1 - face.center_x + face.width_ratio / 2) * width)
        y1 = round((face.center_y - face.height_ratio / 2) * height)
        y2 = round((face.center_y + face.height_ratio / 2) * height)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)
    cv2.rectangle(canvas, (0, 0), (width, 65), (25, 25, 25), -1)
    hint = centering_hint(face)
    instruction = hint or ("READY - PRESS ENTER" if head.ready else "HOLD STILL, LOOK AT CAMERA")
    cv2.putText(canvas, "1. CENTER YOUR FACE", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 1, cv2.LINE_AA)
    cv2.putText(canvas, instruction, (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.60, color, 1, cv2.LINE_AA)
    return canvas


def draw_head_panel(view: np.ndarray, head: HeadPreview) -> np.ndarray:
    """반전된 영상 옆에 패널을 붙여 글자와 방향 안내는 뒤집히지 않게 한다."""
    height, width = view.shape[:2]
    panel = np.full((max(height, 480), PANEL_WIDTH, 3), (32, 29, 26), dtype=np.uint8)
    color = (240, 240, 240)

    def text(line: str, y: int, tint: tuple = color, scale: float = 0.55) -> None:
        cv2.putText(panel, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX, scale, tint, 1, cv2.LINE_AA)

    def gauge(label: str, value: float, y: int, low: str, high: str) -> None:
        text(f"{label}: {value:+.1f} approx. deg", y)
        start, end, center = 30, PANEL_WIDTH - 30, PANEL_WIDTH // 2
        cv2.line(panel, (start, y + 23), (end, y + 23), (110, 110, 110), 2)
        for angle in (-HEAD_TURN_MIN_DELTA_DEG, 0, HEAD_TURN_MIN_DELTA_DEG):
            x = round(center + angle / 30 * (end - center))
            cv2.line(panel, (x, y + 16), (x, y + 30), (160, 160, 160), 1)
        x = round(center + np.clip(value / 30, -1, 1) * (end - center))
        cv2.circle(panel, (x, y + 23), 6, (80, 220, 255), -1)
        text(f"{low:<23}{high}", y + 52, scale=0.46)

    text("FACE CENTERING" if head.baseline is None else "HEAD MOVEMENT", 30, scale=0.7)
    text("FaceMesh | your own left / right", 56, scale=0.48)
    observation = head.observation
    if observation is None or observation.face_count != 1 or not observation.pose_valid:
        if observation is not None and observation.face_count == 1:
            label = "POSE UNAVAILABLE"
        else:
            label = "NO FACE" if observation is None or observation.face_count == 0 else "MULTIPLE FACES"
        text(label, 100, (80, 160, 255))
        text("Keep one face visible", 132)
    else:
        text(f"raw yaw {observation.yaw:+.1f}  pitch {observation.pitch:+.1f}", 86, scale=0.49)
        text(f"roll {observation.roll:+.1f} / estimated deg", 110, scale=0.49)
        if head.baseline is None:
            hint = centering_hint(observation)
            text(hint or ("READY - PRESS ENTER" if head.ready else "HOLD STILL"), 153, (80, 220, 255))
            text(f"Stable frames: {len(head.samples)}/{CALIBRATION_FRAMES}", 182)
            if observation.center_x is not None and observation.center_y is not None:
                text(f"Face center: {observation.center_x:.2f}, {observation.center_y:.2f}", 220, scale=0.49)
            text("Match the oval, face camera", 260, scale=0.49)
            text("Then press Enter to set neutral", 290, scale=0.49)
            text("Pose challenge starts afterward", 320, scale=0.49)
        else:
            pose = head.filtered_pose
            movement = read_movement(pose, head.baseline) if pose else None
            delta = abs(pose.pitch - head.baseline.pitch) if pose else 0
            direction = (
                movement.value
                if movement
                else ("CENTER" if pose and delta <= HEAD_RETURN_MAX_DELTA_DEG else "UNCERTAIN / KEEP STILL")
            )
            text(direction, 145, (90, 230, 120) if movement or direction == "CENTER" else (80, 220, 255))
            # 거울 미리보기 기준: 사용자의 왼쪽은 패널 왼쪽, 위쪽은 UP 끝에 표시한다.
            if pose:
                gauge("Yaw delta", -(pose.yaw - head.baseline.yaw), 178, "LEFT", "RIGHT")
                gauge("Pitch delta", pose.pitch - head.baseline.pitch, 260, "UP", "DOWN")
            text("Turn: 12+ / Return: within 6", 334, scale=0.49)
    if head.target and head.baseline is not None:
        text(f"Target: {head.target.value}", 365)
        state = {"TURN": "Turn to target", "RETURN": "Return to center", "DONE": "DONE - press R to retry"}[head.stage]
        if head.baseline is None:
            state = "Calibrate first"
        elif observation is None or observation.face_count != 1 or not observation.pose_valid:
            state = "Waiting for valid face pose"
        text(state, 393, (90, 230, 120) if head.stage == "DONE" else color)
        text(f"Peak toward target: {head.peak:.1f}", 417, scale=0.49)
    text(
        "Enter: confirm  C: reset  Q: quit" if head.baseline is None else "C: center again  R: retry  Q: quit",
        454,
        scale=0.49,
    )
    canvas = np.zeros((panel.shape[0], width + PANEL_WIDTH, 3), dtype=np.uint8)
    canvas[:height, :width] = view
    canvas[:, width:] = panel
    return canvas


def annotate(
    image: np.ndarray,
    hands: list[HandLandmarks],
    *,
    mirror: bool = False,
    target: HandGesture | None = None,
    target_direction: HandDirection = HandDirection.UP,
) -> np.ndarray:
    """랜드마크·판정을 그린다. mirror=True면 그림까지 그린 뒤 좌우 반전하고 글자만 다시 쓴다."""
    canvas = image.copy()
    labels: list[tuple[np.ndarray, list[str], tuple[int, int, int]]] = []
    for hand in hands:
        reading = read_gesture(hand.image_points)
        points = hand.image_points[:, :2].round().astype(int)
        for start, end in BONES:
            cv2.line(canvas, tuple(points[start]), tuple(points[end]), (255, 200, 0), 2)
        for index, point in enumerate(points):
            cv2.circle(canvas, tuple(point), 4 if index % 4 == 0 else 3, (0, 0, 255), -1)
        fingers = "".join(name if up else "-" for name, up in zip(FINGER_NAMES, reading.fingers, strict=True))
        valid = (
            matches_gesture(reading, target, target_direction)
            if target
            else (
                reading.gesture is not None
                and reading.direction in (HandDirection.UP, HandDirection.DOWN, HandDirection.SIDEWAYS)
            )
        )
        verdict = "MATCH" if target and valid else ("WRONG POSE" if target else ("OK" if valid else "CHECK DIRECTION"))
        lines = [
            f"{pose_name(reading.gesture, reading.direction)} / {reading.direction}",
            verdict,
            f"{fingers} reach={reading.thumb_reach:.2f}",
            "bend " + "/".join(f"{bend:.0f}" for bend in reading.finger_bend_deg),
        ]
        labels.append((hand.box, lines, (90, 230, 120) if valid else (80, 120, 255)))

    if mirror:
        canvas = cv2.flip(canvas, 1)
    width = canvas.shape[1]
    for box, lines, tint in labels:
        x = int(width - box[2]) if mirror else int(box[0])
        max_text_width = max(cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)[0][0] for line in lines)
        x = max(4, min(x, width - max_text_width - 4))
        y = max(int(box[1]) - 10 - 22 * (len(lines) - 1), 20)
        for offset, line in enumerate(lines):
            position = (max(x, 4), y + 22 * offset)
            cv2.putText(canvas, line, position, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(canvas, line, position, cv2.FONT_HERSHEY_SIMPLEX, 0.6, tint, 1, cv2.LINE_AA)
    return canvas


def pose_name(gesture: HandGesture | None, direction: HandDirection) -> str:
    if gesture == HandGesture.THUMBS_UP and direction == HandDirection.DOWN:
        return "THUMBS_DOWN"
    return str(gesture or "UNKNOWN")


def announce_target(gesture: HandGesture | None, direction: HandDirection, head: HeadMovement | None) -> None:
    if gesture:
        name = GESTURE_LABELS[gesture]
        if not requires_hand_direction(gesture, direction):
            print(f"손 안내: {name} — 방향에 상관없이 손 모양을 유지하세요.")
            if head:
                print(f"고개 안내: 정면에서 {HEAD_MOVEMENT_LABELS[head]}.")
            return
        orientation = {
            HandDirection.UP: "위쪽",
            HandDirection.DOWN: "아래쪽",
            HandDirection.SIDEWAYS: "가로(좌우 어느 쪽이든)",
        }[direction]
        guide = "손목에서 손가락 마디 쪽이" if gesture == HandGesture.FIST else "편 손가락이"
        print(f"손 안내: {name} — {guide} {orientation}을 향하도록 유지하세요.")
    if head:
        print(f"고개 안내: 정면에서 {HEAD_MOVEMENT_LABELS[head]}.")


def run_image(
    landmarker: HandLandmarker,
    image_path: Path,
    out: Path | None,
    gesture: HandGesture | None = None,
    direction: HandDirection = HandDirection.UP,
) -> None:
    image = cv2.imread(str(image_path))
    if image is None:
        raise SystemExit(f"이미지를 읽을 수 없습니다: {image_path}")
    hands = landmarker.detect(image)
    if not hands:
        print("손을 찾지 못했습니다.")
    for number, hand in enumerate(hands, start=1):
        reading = read_gesture(hand.image_points)
        print(
            f"hand {number}: gesture={reading.gesture or 'UNKNOWN'} direction={reading.direction} "
            f"direction_tilt={reading.direction_tilt_deg} fingers={reading.fingers} "
            f"bend={reading.finger_bend_deg} thumb_reach={reading.thumb_reach} "
            f"thumb_index={reading.thumb_index_angle_deg} tilt={reading.thumb_tilt_deg} conf={hand.confidence:.3f}"
        )
    if out:
        cv2.imwrite(str(out), annotate(image, hands, target=gesture, target_direction=direction))
        print(f"saved: {out}")


def draw_live_challenge_hud(
    view: np.ndarray,
    frame_width: int,
    head: HeadPreview,
    hands: list[HandLandmarks],
    gesture: HandGesture | None,
    direction: HandDirection,
    *,
    random_mode: bool = False,
    recording_remaining: float | None = None,
) -> None:
    """카메라 화면 상단·하단에 손 인식 여부와 고개 동작 진행률(각도 게이지·단계)을 실시간 표시한다."""
    cv2.rectangle(view, (0, 0), (frame_width, 96), (22, 22, 22), -1)

    hand_matched = (
        any(matches_gesture(read_gesture(hand.image_points), gesture, direction) for hand in hands)
        if gesture
        else bool(hands)
    )
    hand_color = (90, 230, 120) if hand_matched else ((80, 180, 255) if hands else (160, 160, 160))
    hand_state = "MATCH [OK]" if hand_matched else ("WRONG POSE" if hands else "SHOW HAND")
    if gesture:
        req_dir = direction.value if requires_hand_direction(gesture, direction) else "ANY DIR"
        hand_text = f"HAND ({pose_name(gesture, direction)} / {req_dir}): {hand_state}"
    else:
        hand_text = f"HAND: {hand_state}"
    cv2.putText(view, hand_text, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.58, hand_color, 2, cv2.LINE_AA)

    if head.target and head.baseline is not None:
        pose = head.filtered_pose
        current_delta = max(0.0, movement_delta(pose, head.baseline, head.target)) if pose else 0.0
        return_delta = max(abs(pose.yaw - head.baseline.yaw), abs(pose.pitch - head.baseline.pitch)) if pose else 99.0
        if head.stage == "TURN":
            progress = min(1.0, current_delta / HEAD_TURN_MIN_DELTA_DEG)
            head_color = (90, 230, 120) if progress >= 1.0 else (80, 220, 255)
            head_text = (
                f"HEAD [1/2 {head.target.value}]: {current_delta:.1f} / {HEAD_TURN_MIN_DELTA_DEG:.0f} deg "
                f"({'HOLD' if progress >= 1.0 else 'MOVE MORE'})"
            )
        elif head.stage == "RETURN":
            progress = max(0.0, min(1.0, 1.0 - (return_delta - HEAD_RETURN_MAX_DELTA_DEG) / 18.0))
            head_color = (80, 220, 255)
            head_text = (
                f"HEAD [2/2 TURN OK! RETURN CENTER]: delta {return_delta:.1f} deg (<= {HEAD_RETURN_MAX_DELTA_DEG:.0f})"
            )
        else:
            progress = 1.0
            head_color = (90, 230, 120)
            head_text = f"HEAD [{head.target.value}]: COMPLETED [OK]"

        cv2.putText(view, head_text, (12, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.54, head_color, 2, cv2.LINE_AA)
        bar_x1, bar_y1, bar_x2, bar_y2 = 12, 64, frame_width - 12, 76
        cv2.rectangle(view, (bar_x1, bar_y1), (bar_x2, bar_y2), (65, 65, 65), -1)
        fill_w = round((bar_x2 - bar_x1) * progress)
        if fill_w > 0:
            cv2.rectangle(view, (bar_x1, bar_y1), (bar_x1 + fill_w, bar_y2), head_color, -1)

        if hand_matched and head.stage == "DONE":
            cv2.rectangle(view, (0, 96), (frame_width, 132), (35, 140, 60), -1)
            cv2.putText(
                view,
                "ALL PASSED! (HAND + HEAD VERIFIED) - Press N for next",
                (12, 120),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

    controls = (
        "N: new random | R: retry | C: re-center | Q: quit" if random_mode else "R: retry | C: re-center | Q: quit"
    )
    if recording_remaining is not None:
        controls += f" | REC {max(0.0, recording_remaining):.1f}s"
    cv2.putText(view, controls, (12, 91), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (210, 210, 210), 1, cv2.LINE_AA)


def run_camera(
    landmarker: HandLandmarker,
    analyzer: LiveFaceAnalyzer,
    camera: int,
    record_dir: Path | None,
    gesture: HandGesture | None,
    head_target: HeadMovement | None,
    direction: HandDirection = HandDirection.UP,
    random_mode: bool = False,
) -> None:
    target = random_challenge() if random_mode else None
    if target:
        gesture, direction, head_target = target.gesture, target.direction, target.head
    capture = cv2.VideoCapture(camera)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not capture.isOpened():
        raise SystemExit("카메라를 열 수 없습니다.")
    writer: cv2.VideoWriter | None = None
    recording_until = 0.0
    head = HeadPreview(target=head_target)
    print("먼저 타원 가이드 중앙에 얼굴을 맞추고 카메라를 정면으로 보세요.")
    print("READY 표시 후 Enter로 정면 기준을 확정하면 챌린지를 시작합니다. C: 중앙 맞추기부터 다시, Q: 종료")
    print("얼굴 위치·각도는 MediaPipe Face Landmarker로 측정합니다. 방향은 본인 기준입니다.")
    if random_mode:
        print("N: 새 랜덤 조합, R: 같은 조합 재시도. 녹화 중에는 조합을 변경하지 않습니다.")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            now = time.monotonic()
            if writer is not None:
                writer.write(frame)
                if now >= recording_until:
                    writer.release()
                    writer = None
            head.update(analyzer.observe(frame))
            detected_hands: list[HandLandmarks] = []
            if head.baseline is None:
                view = draw_centering_guide(cv2.flip(frame, 1), head)
            else:
                detected_hands = landmarker.detect(frame)
                view = annotate(frame, detected_hands, mirror=True, target=gesture, target_direction=direction)
            view = draw_head_panel(view, head)
            if (gesture or head.target) and head.baseline is not None:
                draw_live_challenge_hud(
                    view,
                    frame.shape[1],
                    head,
                    detected_hands,
                    gesture,
                    direction,
                    random_mode=random_mode,
                    recording_remaining=(recording_until - now) if writer else None,
                )
            cv2.imshow("hand gesture + head movement", view)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key in (10, 13) and head.confirm_center():
                print("정면 기준 확정. 안내한 손 모양을 유지하면서 고개 동작을 수행하세요.")
                announce_target(gesture, direction, head.target)
            if key == ord("c") and writer is None:
                head.restart(calibrate=True)
            if key == ord("r") and writer is None:
                head.restart()
            if key == ord("n") and random_mode and writer is None and head.baseline is not None:
                target = random_challenge(previous=target)
                gesture, direction = target.gesture, target.direction
                head.target = target.head
                head.restart()
                announce_target(gesture, direction, target.head)
            if key == ord(" ") and record_dir and gesture and writer is None and head.baseline is not None:
                clip_path = record_dir / gesture.value / direction.value / f"{datetime.now():%Y%m%d-%H%M%S-%f}.mp4"
                clip_path.parent.mkdir(parents=True, exist_ok=True)
                fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
                height, width = frame.shape[:2]
                writer = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
                recording_until = now + CLIP_SECONDS
                print(f"recording: {clip_path}")
    finally:
        if writer is not None:
            writer.release()
        capture.release()
        cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=Path, help="웹캠 대신 이미지 한 장을 판정")
    parser.add_argument("--out", type=Path, help="--image 결과 그림 저장 경로")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--record-dir", type=Path, help="스페이스바로 녹화한 클립을 저장할 폴더")
    parser.add_argument("--gesture", type=HandGesture, choices=list(HandGesture), help="목표 손 모양")
    parser.add_argument(
        "--direction",
        type=HandDirection,
        choices=[HandDirection.UP, HandDirection.DOWN, HandDirection.SIDEWAYS],
        help="목표 손 방향 (기본 UP)",
    )
    parser.add_argument(
        "--random", action="store_true", help="손 모양·UP/DOWN/SIDEWAYS·고개 방향을 랜덤 출제, N으로 다음 조합"
    )
    parser.add_argument(
        "--head", type=HeadMovement, choices=list(HeadMovement), help="돌렸다 정면 복귀할 목표 방향 (사용자 기준)"
    )
    args = parser.parse_args()

    if args.random and any((args.image, args.gesture, args.direction, args.head)):
        parser.error("--random 은 --image/--gesture/--direction/--head 와 함께 쓸 수 없습니다.")
    if args.direction and not args.gesture:
        parser.error("--direction 을 쓰려면 --gesture 가 필요합니다.")
    if args.record_dir and not (args.gesture or args.random):
        parser.error("--record-dir 를 쓰려면 --gesture 또는 --random 이 필요합니다.")
    direction = args.direction or HandDirection.UP
    landmarker = HandLandmarker(get_settings())
    if args.image:
        run_image(landmarker, args.image, args.out, args.gesture, direction)
        return
    analyzer = LiveFaceAnalyzer(get_settings())
    try:
        run_camera(landmarker, analyzer, args.camera, args.record_dir, args.gesture, args.head, direction, args.random)
    finally:
        analyzer.close()


if __name__ == "__main__":
    main()
