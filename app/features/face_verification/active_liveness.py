"""서명된 고개·손 챌린지를 서버 영상에서 검증한다."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from hashlib import blake2b

import cv2
import numpy as np

from .centering import MAX_FRONTAL_ANGLE, centering_hint, is_frontal
from .continuous_hold import HOLD_SECONDS, find_hold
from .gesture import judge_hold, matches_gesture, read_gesture
from .hand import HandLandmarker, HandLandmarks
from .head import HEAD_TURN_MIN_DELTA_DEG, HeadPose, movement_delta, pose_from
from .live_face import LiveFaceAnalyzer
from .liveness import LivenessEvaluation
from .protocol import decode_target
from .schemas import Challenge, FrameDiagnostic, FrameStatus, LivenessResult, ReasonCode

MAX_ACTIVE_FRAMES = 48
MIN_ACTIVE_FRAMES = 12
MAX_VIDEO_SECONDS = 18.0
MAX_DECODED_FRAMES = 2160
MAX_FRAME_EDGE_PX = 720


def _skip_frame(capture) -> bool:
    grab = getattr(capture, "grab", None)
    if callable(grab):
        return bool(grab())
    ok, _ = capture.read()
    return bool(ok)


def _downscale_frame(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    if max(height, width) <= MAX_FRAME_EDGE_PX:
        return frame
    scale = MAX_FRAME_EDGE_PX / max(height, width)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


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
    return intersection > max((x2 - x1) * (y2 - y1), 1) * 0.20


class ActiveLivenessAnalyzer:
    def __init__(self, face_analyzer: LiveFaceAnalyzer, hand_analyzer: HandLandmarker) -> None:
        self.face_analyzer = face_analyzer
        self.hand_analyzer = hand_analyzer

    def evaluate(self, video: bytes, suffix: str, challenges: list[Challenge]) -> LivenessEvaluation:
        target = decode_target(challenges)
        faces, readings, records, candidates = [], [], [], []
        fingerprints, clear_frontal, presentation_times, uncovered = [], [], [], []
        invalid_video = False
        source_fps, reported = 0.0, 0
        frontal_captured = False
        next_sample_time = 0.0
        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(video)
            handle.flush()
            capture = cv2.VideoCapture(handle.name)
            try:
                raw_count, raw_fps = capture.get(cv2.CAP_PROP_FRAME_COUNT), capture.get(cv2.CAP_PROP_FPS)
                reported = int(raw_count) if np.isfinite(raw_count) and raw_count > 0 else 0
                source_fps = float(raw_fps) if np.isfinite(raw_fps) and raw_fps > 0 else 0.0
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
                    source_index = index
                    sample_time = source_index / source_fps if source_fps else capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
                    if (indices is not None and source_index not in indices) or (
                        indices is None and sample_time + 1e-9 < next_sample_time
                    ):
                        if not _skip_frame(capture):
                            break
                        index += 1
                        continue
                    ok, frame = capture.read()
                    if not ok:
                        break
                    index += 1
                    if indices is None and (len(faces) >= MAX_ACTIVE_FRAMES or sample_time > MAX_VIDEO_SECONDS):
                        # 헤더 없이 앞부분만 잘라 인증하지 않는다.
                        invalid_video = True
                        break
                    next_sample_time = sample_time + MAX_VIDEO_SECONDS / (MAX_ACTIVE_FRAMES - 1)
                    frame = _downscale_frame(frame)
                    face = self.face_analyzer.observe(frame)
                    hands = self.hand_analyzer.detect(frame)
                    faces.append(face)
                    fingerprints.append(blake2b(memoryview(np.ascontiguousarray(frame)), digest_size=16).digest())
                    readings.append([read_gesture(hand.image_points) for hand in hands])
                    sample_index = len(faces) - 1
                    presentation_times.append(capture.get(cv2.CAP_PROP_POS_MSEC))
                    pose = pose_from(face)
                    frontal = is_frontal(face)
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
                    uncovered.append(not any(hand_overlaps_face(hand, face, frame.shape[:2]) for hand in hands))
                    unobscured = frontal and uncovered[-1]
                    clear_frontal.append(unobscured and centering_hint(face) is None)
                    # 정면 구간 확보 전에는 최근 후보 세 장만 보관한다. 이후 픽셀 복사를 멈춘다.
                    if not frontal_captured and clear_frontal[-1]:
                        if len(candidates) == 3:
                            candidates.pop(0)
                        candidates.append((0, sample_index, frame.copy()))
                    current_times = (
                        [value / 1000 for value in presentation_times]
                        if len(presentation_times) >= 2 and (np.diff(presentation_times) > 0).all()
                        else [record.source_frame_index / source_fps for record in records]
                        if source_fps
                        else []
                    )
                    if not frontal_captured:
                        current = find_hold(clear_frontal, current_times, HOLD_SECONDS)
                        if current.seconds >= HOLD_SECONDS:
                            candidates = [item for item in candidates if item[1] in current.indices]
                            frontal_captured = True
                if reported and abs(index - reported) > 1:
                    invalid_video = True
            finally:
                capture.release()

        hold = judge_hold(
            readings, target.gesture, expected_direction=target.direction, min_hand_ratio=0.8, min_match_ratio=0.8
        )
        valid = sum(pose_from(face) is not None for face in faces)
        reasons = []
        if (
            invalid_video
            or len(faces) < MIN_ACTIVE_FRAMES
            or valid < MIN_ACTIVE_FRAMES
            or any(face.face_count > 1 for face in faces)
        ):
            reasons.append(ReasonCode.LIVENESS_FAILED)
        adaptive = target.head is None
        frontal_window = pose_window = head_window = None
        media_times = (
            len(presentation_times) >= 2
            and np.isfinite(presentation_times).all()
            and min(presentation_times) >= 0
            and (np.diff(presentation_times) > 0).all()
        )
        times = (
            [value / 1000 for value in presentation_times]
            if media_times
            else [record.source_frame_index / source_fps for record in records]
            if source_fps
            else []
        )
        if media_times:
            for record, timestamp in zip(records, presentation_times, strict=True):
                record.timestamp_ms = round(timestamp)
        # v2/v3는 요구 동작만 다르다. 모두 정면 → 손 → 선택 고개를 각각 3초 검증한다.
        frontal_window = find_hold(clear_frontal, times, HOLD_SECONDS)
        centered = frontal_window.seconds >= HOLD_SECONDS
        pose_matches = [
            clear and any(matches_gesture(reading, target.gesture, target.direction) for reading in frame)
            for clear, frame in zip(clear_frontal, readings, strict=True)
        ]
        pose_window = find_hold(
            pose_matches,
            times,
            HOLD_SECONDS,
            start=frontal_window.indices[-1] + 1 if centered else len(faces),
        )
        pose_passed = pose_window.seconds >= HOLD_SECONDS
        baseline = None
        head_peak = None
        if centered:
            baseline = HeadPose(
                yaw=float(np.median([faces[index].yaw for index in frontal_window.indices])),
                pitch=float(np.median([faces[index].pitch for index in frontal_window.indices])),
            )
        if target.head and baseline:
            deltas = [
                movement_delta(pose_from(face), baseline, target.head) if pose_from(face) else 0 for face in faces
            ]
            head_peak = max(deltas, default=0)
            head_matches = [
                pose_from(face) is not None
                and face.face_in_frame
                and centering_hint(replace(face, yaw=0, pitch=0, roll=0)) is None
                and abs(face.yaw) <= 35
                and abs(face.roll) <= 30
                and clear
                and delta >= HEAD_TURN_MIN_DELTA_DEG
                for face, delta, clear in zip(faces, deltas, uncovered, strict=True)
            ]
            head_window = find_hold(
                head_matches,
                times,
                HOLD_SECONDS,
                start=pose_window.indices[-1] + 1 if pose_passed else len(faces),
            )
        if not centered:
            reasons.append(ReasonCode.FACE_NOT_CENTERED)
        if centered and not pose_passed:
            after = frontal_window.indices[-1] + 1
            face_window = find_hold(clear_frontal, times, HOLD_SECONDS, start=after)
            if face_window.seconds < HOLD_SECONDS:
                reasons.append(ReasonCode.FACE_NOT_CENTERED)
            else:
                reasons.append(
                    ReasonCode.HAND_NOT_DETECTED
                    if sum(bool(frame) for frame in readings[after:]) < 2
                    else ReasonCode.HAND_GESTURE_MISMATCH
                )
        if target.head and (head_window is None or head_window.seconds < HOLD_SECONDS):
            reasons.append(ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED)
        initial = [item for item in candidates if item[1] in frontal_window.indices]
        if len(initial) < 2:
            reasons.append(ReasonCode.FACE_COMPARISON_FAILED)
        passed = not reasons
        selected = initial if passed else []
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
                required_valid_face_frames=MIN_ACTIVE_FRAMES,
                required_frontal_face_frames=max(2, len(frontal_window.indices)),
                candidate_frame_count=len(candidate_frames),
                source_frame_count=reported or index,
                source_fps=source_fps or None,
                sampling_strategy="uniform_max_48_adaptive_v3" if adaptive else "uniform_max_48_active_v2",
                max_abs_yaw_deg=MAX_FRONTAL_ANGLE,
                max_abs_pitch_deg=MAX_FRONTAL_ANGLE,
                max_abs_roll_deg=MAX_FRONTAL_ANGLE,
                frame_diagnostics=records,
                reason_codes=reasons,
                hand_frames=hold.hand_frames,
                matched_hand_frames=hold.matched_frames,
                expected_hand_direction=target.direction,
                head_peak_delta_deg=head_peak,
                head_returned=(head_window is not None and head_window.seconds >= HOLD_SECONDS)
                if target.head
                else None,
                head_hold_seconds=round(head_window.seconds, 3) if head_window else None,
                frontal_hold_seconds=round(frontal_window.seconds, 3) if frontal_window else None,
                pose_hold_seconds=round(pose_window.seconds, 3) if pose_window else None,
                repeated_frame_ratio=1 - len(set(fingerprints)) / len(fingerprints) if fingerprints else 0,
            ),
            best_frame=candidate_frames[0] if candidate_frames else None,
            candidate_frames=candidate_frames,
        )
