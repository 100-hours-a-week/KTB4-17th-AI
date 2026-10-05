"""라이브니스와 대표사진 동일인 확인 유스케이스."""

import statistics

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
    ) -> VerificationResponse:
        challenges = self.signer.verify(challenge_token)
        reference = decode_image(primary_photo)
        liveness = self.liveness.evaluate(
            live_video,
            video_suffix(live_video_content_type),
            challenges,
        )
        if not liveness.result.passed or liveness.best_frame is None:
            return VerificationResponse(
                decision=VerificationDecision.RETRY,
                reason_codes=[ReasonCode.LIVENESS_FAILED],
                liveness=liveness.result,
                model_version=MODEL_VERSION,
            )
        similarities: list[float] = []
        frames = liveness.candidate_frames or (liveness.best_frame,)
        for frame in frames:
            try:
                _, similarity = self.matcher.compare(reference, frame)
                similarities.append(similarity)
            except FaceNotFound:
                continue
        if not similarities:
            return VerificationResponse(
                decision=VerificationDecision.RETRY,
                reason_codes=[ReasonCode.FACE_COMPARISON_FAILED],
                liveness=liveness.result,
                model_version=MODEL_VERSION,
            )
        similarity = float(statistics.median(similarities))
        threshold = self.matcher.threshold
        matched = similarity >= threshold
        match_diagnostics = MatchDiagnostics(
            threshold=threshold,
            compared_frame_count=len(similarities),
            frame_similarities=[round(item, 6) for item in similarities],
        )
        return VerificationResponse(
            decision=VerificationDecision.VERIFIED if matched else VerificationDecision.NOT_VERIFIED,
            reason_codes=[] if matched else [ReasonCode.FACE_MISMATCH],
            similarity=round(similarity, 6),
            liveness=liveness.result,
            model_version=MODEL_VERSION,
            match=match_diagnostics,
        )
