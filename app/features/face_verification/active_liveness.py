"""서명된 고개·손 챌린지를 서버 영상에서 검증한다."""

from __future__ import annotations

import tempfile

import cv2
import numpy as np

from .centering import CENTER_POSITION_MAX_DRIFT, centering_hint
from .gesture import judge_hold, read_gesture
from .hand import HandLandmarker, HandLandmarks
from .head import BASELINE_FRAMES, judge_head_turn, pose_from
from .live_face import LiveFaceAnalyzer
from .liveness import LivenessEvaluation
from .protocol import decode_target
from .schemas import Challenge, FrameDiagnostic, FrameStatus, LivenessResult, ReasonCode

MAX_ACTIVE_FRAMES = 48
MIN_ACTIVE_FRAMES = 12
MAX_VIDEO_SECONDS = 12.0
MAX_DECODED_FRAMES = 1440


def hand_overlaps_face(hand: HandLandmarks, face, shape: tuple[int, int]) -> bool:
    if face.center_x is None or face.center_y is None or face.width_ratio is None or face.height_ratio is None:
        return True
    height, width = shape
    x1 = (face.center_x - face.width_ratio / 2) * width
    x2 = (face.center_x + face.width_ratio / 2) * width
    y1 = (face.center_y - face.height_ratio / 2) * height
    y2 = (face.center_y + face.height_ratio / 2) * height
    hx1, hy1, hx2, hy2 = hand.box
    intersection = max(0, min(x2, hx2) - max(x1, hx1)) * max(0, min(y2, hy2) - max(y1, hy1))
    return intersection > max((x2 - x1) * (y2 - y1), 1) * 0.10


class ActiveLivenessAnalyzer:
    def __init__(self, face_analyzer: LiveFaceAnalyzer, hand_analyzer: HandLandmarker) -> None:
        self.face_analyzer = face_analyzer
        self.hand_analyzer = hand_analyzer

    def evaluate(self, video: bytes, suffix: str, challenges: list[Challenge]) -> LivenessEvaluation:
        target = decode_target(challenges)
        faces, readings, records, candidates = [], [], [], []
        invalid_video = False
        source_fps, reported = 0.0, 0
        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(video)
            handle.flush()
            capture = cv2.VideoCapture(handle.name)
            try:
                raw_count, raw_fps = capture.get(cv2.CAP_PROP_FRAME_COUNT), capture.get(cv2.CAP_PROP_FPS)
                reported = int(raw_count) if np.isfinite(raw_count) and raw_count > 0 else 0
                source_fps = float(raw_fps) if np.isfinite(raw_fps) and raw_fps > 0 else 0.0
                if reported <= 1:
                    reported = 0
                    # 브라우저 WebM은 프레임 수 헤더가 없을 수 있다. 저장하지 않고 한 번 세고 다시 연다.
                    while reported <= MAX_DECODED_FRAMES:
                        ok, _ = capture.read()
                        if not ok:
                            break
                        reported += 1
                    capture.release()
                    capture = cv2.VideoCapture(handle.name)
                if reported > MAX_DECODED_FRAMES or (source_fps and reported / source_fps > MAX_VIDEO_SECONDS):
                    invalid_video = True
                indices = (
                    set(np.linspace(0, reported - 1, min(MAX_ACTIVE_FRAMES, reported), dtype=int)) if reported else None
                )
                index = 0
                while not invalid_video and capture.isOpened():
                    if index >= MAX_DECODED_FRAMES:
                        invalid_video = True
                        break
                    ok, frame = capture.read()
                    if not ok:
                        break
                    source_index = index
                    index += 1
                    if indices is not None and source_index not in indices:
                        continue
                    if indices is None and len(faces) >= MAX_ACTIVE_FRAMES:
                        # 헤더 없이 앞부분만 잘라 인증하지 않는다.
                        invalid_video = True
                        break
                    face = self.face_analyzer.observe(frame)
                    hands = self.hand_analyzer.detect(frame)
                    faces.append(face)
                    readings.append([read_gesture(hand.image_points) for hand in hands])
                    sample_index = len(faces) - 1
                    pose = pose_from(face)
                    frontal = (
                        pose is not None and abs(face.yaw) <= 20 and abs(face.pitch) <= 20 and abs(face.roll) <= 20
                    )
                    records.append(
                        FrameDiagnostic(
                            sample_index=sample_index,
                            source_frame_index=source_index,
                            timestamp_ms=round(source_index / source_fps * 1000) if source_fps else None,
                            status=FrameStatus.FRONTAL
                            if frontal
                            else (FrameStatus.NO_FACE if face.face_count == 0 else FrameStatus.NON_FRONTAL),
                            face_count=face.face_count,
                            detected_face_count=face.detected_face_count,
                            yaw=face.yaw if pose else None,
                            pitch=face.pitch if pose else None,
                            roll=face.roll if pose else None,
                        )
                    )
                    if frontal and not any(hand_overlaps_face(hand, face, frame.shape[:2]) for hand in hands):
                        candidates.append(
                            (abs(face.yaw) + abs(face.pitch) + abs(face.roll), sample_index, frame.copy())
                        )
                if reported and abs(index - reported) > 1:
                    invalid_video = True
            finally:
                capture.release()

        hold = judge_hold(
            readings, target.gesture, expected_direction=target.direction, min_hand_ratio=0.8, min_match_ratio=0.8
        )
        head = judge_head_turn([pose_from(face) for face in faces], target.head)
        centered = len(faces) >= BASELINE_FRAMES and all(
            centering_hint(face) is None for face in faces[:BASELINE_FRAMES]
        )
        if centered:
            centered = all(
                max(getattr(face, axis) for face in faces[:BASELINE_FRAMES])
                - min(getattr(face, axis) for face in faces[:BASELINE_FRAMES])
                <= tolerance
                for axis, tolerance in (
                    ("yaw", 6),
                    ("pitch", 6),
                    ("center_x", CENTER_POSITION_MAX_DRIFT),
                    ("center_y", CENTER_POSITION_MAX_DRIFT),
                )
            )
        valid = sum(pose_from(face) is not None for face in faces)
        reasons = []
        if (
            invalid_video
            or len(faces) < MIN_ACTIVE_FRAMES
            or valid < len(faces) * 0.8
            or any(face.face_count > 1 for face in faces)
        ):
            reasons.append(ReasonCode.LIVENESS_FAILED)
        if not centered:
            reasons.append(ReasonCode.FACE_NOT_CENTERED)
        if not hold.passed:
            reasons.append(
                ReasonCode.HAND_NOT_DETECTED
                if hold.hand_frames < len(faces) * 0.8
                else ReasonCode.HAND_GESTURE_MISMATCH
            )
        ended_centered = (
            head.baseline is not None
            and len(faces) >= 2
            and all(
                (pose := pose_from(face)) is not None
                and max(abs(pose.yaw - head.baseline.yaw), abs(pose.pitch - head.baseline.pitch)) <= 6
                for face in faces[-2:]
            )
        )
        if not head.passed or not ended_centered:
            reasons.append(ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED)
        if not candidates:
            reasons.append(ReasonCode.FACE_COMPARISON_FAILED)
        passed = not reasons
        # 시작과 끝의 얼굴을 둘 다 비교한다. 초기 정면 프레임만 골라 동일인을 판단하지 않는다.
        selected = []
        if passed:
            temporal = sorted(candidates, key=lambda item: item[1])
            first, last = temporal[0], temporal[-1]
            selected = [first, last] if first[1] != last[1] else [first]
        for rank, (_, sample, _) in enumerate(selected, 1):
            records[sample].status, records[sample].candidate_rank = FrameStatus.MATCH_CANDIDATE, rank
        candidate_frames = tuple(frame for _, _, frame in selected)
        return LivenessEvaluation(
            result=LivenessResult(
                passed=passed,
                completed_challenges=challenges if passed else [],
                sampled_frames=len(faces),
                valid_face_frames=valid,
                frontal_face_frames=sum(
                    record.status in (FrameStatus.FRONTAL, FrameStatus.MATCH_CANDIDATE) for record in records
                ),
                required_sampled_frames=MIN_ACTIVE_FRAMES,
                required_valid_face_frames=max(MIN_ACTIVE_FRAMES, int(np.ceil(len(faces) * 0.8))),
                required_frontal_face_frames=BASELINE_FRAMES,
                candidate_frame_count=len(candidate_frames),
                source_frame_count=reported,
                source_fps=source_fps or None,
                sampling_strategy="uniform_max_48_active_v2",
                max_abs_yaw_deg=20,
                max_abs_pitch_deg=20,
                max_abs_roll_deg=20,
                frame_diagnostics=records,
                reason_codes=reasons,
                hand_frames=hold.hand_frames,
                matched_hand_frames=hold.matched_frames,
                expected_hand_direction=target.direction,
                head_peak_delta_deg=head.peak_delta_deg,
                head_returned=head.returned and ended_centered,
            ),
            best_frame=candidate_frames[0] if candidate_frames else None,
            candidate_frames=candidate_frames,
        )
