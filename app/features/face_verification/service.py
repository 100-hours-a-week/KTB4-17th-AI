"""라이브니스와 대표사진 동일인 확인 유스케이스."""

import logging
import statistics

import cv2

from app.core.media import decode_image

from .liveness import ChallengeSigner, LivenessAnalyzer, video_suffix
from .matcher import MODEL_VERSION, FaceMatcher, FaceNotFound
from .schemas import (
    MatchDiagnostics,
    ReasonCode,
    VerificationChallengeResponse,
    VerificationDecision,
    VerificationResponse,
)

logger = logging.getLogger(__name__)


class FaceVerificationService:
    def __init__(
        self,
        matcher: FaceMatcher,
        signer: ChallengeSigner,
        liveness: LivenessAnalyzer,
    ) -> None:
        self.matcher = matcher
        self.signer = signer
        self.liveness = liveness

    def create_challenge(self) -> VerificationChallengeResponse:
        return self.signer.create()

    def complete(
        self,
        challenge_token: str,
        primary_photo: bytes,
        live_video: bytes,
        live_video_content_type: str | None,
        attempt_no: int = 1,
    ) -> VerificationResponse:
        challenges = self.signer.verify(challenge_token, attempt_no)
        reference = decode_image(primary_photo)
        liveness = self.liveness.evaluate(
            live_video,
            video_suffix(live_video_content_type),
            challenges,
        )
        if not liveness.result.passed or liveness.best_frame is None:
            return VerificationResponse(
                decision=VerificationDecision.RETRY,
                reason_codes=liveness.result.reason_codes or [ReasonCode.LIVENESS_FAILED],
                liveness=liveness.result,
                model_version=MODEL_VERSION,
            )
        active = liveness.result.sampling_strategy in ("uniform_max_48_active_v2", "uniform_max_48_adaptive_v3")
        frames = liveness.candidate_frames or (liveness.best_frame,)
        # 아이폰은 전면 카메라 셀카를 거울 방향으로 저장하므로 대표 사진 원본과 좌우 반전본을 모두 비교하고,
        # 유사도 중앙값이 높은 방향 하나로 판정한다(프레임마다 섞어 고르지 않는다).
        by_orientation: dict[str, list[float]] = {}
        for orientation, photo in (("original", reference), ("mirrored", cv2.flip(reference, 1))):
            scores = []
            for frame in frames:
                try:
                    _, score = self.matcher.compare(photo, frame)
                    scores.append(score)
                except FaceNotFound:
                    continue
            if scores and (not active or len(scores) >= 2):
                by_orientation[orientation] = scores
        if not by_orientation:
            return VerificationResponse(
                decision=VerificationDecision.RETRY,
                reason_codes=[ReasonCode.FACE_COMPARISON_FAILED],
                liveness=liveness.result,
                model_version=MODEL_VERSION,
            )
        orientation, similarities = max(by_orientation.items(), key=lambda item: statistics.median(item[1]))
        similarity = float(statistics.median(similarities))
        threshold = self.matcher.threshold
        matched = similarity >= threshold
        match_diagnostics = MatchDiagnostics(
            threshold=threshold,
            compared_frame_count=len(similarities),
            frame_similarities=[round(item, 6) for item in similarities],
            aggregation=("median_initial_frontal" if active else "median")
            + ("_mirrored" if orientation == "mirrored" else ""),
        )
        # 사진·영상은 남기지 않고 동일인 비교 수치만 기록한다. 기준에 아깝게 못 미친 건지 판단하는 근거다.
        logger.info(
            "face match decision=%s orientation=%s similarity=%.3f threshold=%.3f frames=%s other=%s",
            "VERIFIED" if matched else "NOT_VERIFIED",
            orientation,
            similarity,
            threshold,
            ",".join(f"{item:.3f}" for item in similarities),
            ",".join(
                f"{name}:{statistics.median(scores):.3f}"
                for name, scores in by_orientation.items()
                if name != orientation
            )
            or "-",
        )
        # 기본 판정을 만족한 경계 사례만 추가 확인한다. 동일인 비교 실패를 우회하지 않는다.
        if (
            len(challenges) == 3
            and matched
            and (
                similarity < threshold + self.signer.step_up_match_margin or liveness.result.repeated_frame_ratio >= 0.8
            )
        ):
            return VerificationResponse(
                decision=VerificationDecision.RETRY,
                reason_codes=[ReasonCode.ADDITIONAL_CHALLENGE_REQUIRED],
                similarity=round(similarity, 6),
                liveness=liveness.result,
                model_version=MODEL_VERSION,
                match=match_diagnostics,
                next_challenge=self.signer.step_up(challenge_token),
            )
        return VerificationResponse(
            decision=VerificationDecision.VERIFIED if matched else VerificationDecision.NOT_VERIFIED,
            reason_codes=[] if matched else [ReasonCode.FACE_MISMATCH],
            similarity=round(similarity, 6),
            liveness=liveness.result,
            model_version=MODEL_VERSION,
            match=match_diagnostics,
        )
