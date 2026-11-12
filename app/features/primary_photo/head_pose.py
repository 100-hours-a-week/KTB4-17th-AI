"""라이브 고개 동작용 6점 PnP 자세 추정. 카메라 내부 파라미터는 영상 크기로 근사한다."""

from __future__ import annotations

import cv2
import numpy as np

from .schemas import Pose

# 코끝, 턱끝, 양쪽 눈 바깥꼬리, 양쪽 입꼬리. x 오른쪽/y 아래/z 카메라에서 먼 쪽.
# 평균 얼굴 비율의 임의 길이 단위다. 절대 각도보다 보정한 정면 대비 변화량을 사용한다.
LANDMARK_IDS = (30, 8, 36, 45, 48, 54)
FACE_POINTS_3D = np.array(
    [(0, 0, -22), (0, 62, -2), (-43, -32, 0), (43, -32, 0), (-26, 30, -6), (26, 30, -6)],
    dtype=np.float64,
)


def estimate_head_pose(landmarks: np.ndarray, image_shape: tuple[int, int]) -> Pose | None:
    """프레임 원본 좌표로 추정한다. pitch는 아래로 숙일 때 +, yaw는 사용자 왼쪽으로 돌릴 때 +다."""
    height, width = image_shape
    image_points = np.ascontiguousarray(np.asarray(landmarks, dtype=np.float64)[list(LANDMARK_IDS), :2])
    if min(height, width) <= 0 or not np.isfinite(image_points).all():
        return None
    if cv2.contourArea(cv2.convexHull(image_points.astype(np.float32))) < 1:
        return None
    focal = float(max(height, width))
    camera = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]], dtype=np.float64)
    distortion = np.zeros(4)
    try:
        success, rotation, translation = cv2.solvePnP(
            FACE_POINTS_3D, image_points, camera, distortion, flags=cv2.SOLVEPNP_SQPNP
        )
        if not success:
            return None
        rotation, translation = cv2.solvePnPRefineLM(
            FACE_POINTS_3D, image_points, camera, distortion, rotation, translation
        )
        matrix = cv2.Rodrigues(rotation)[0]
        if not np.isfinite(matrix).all() or not np.isfinite(translation).all():
            return None
        if np.min((matrix @ FACE_POINTS_3D.T + translation)[2]) <= 0:
            return None
        pitch, yaw, roll = cv2.RQDecomp3x3(matrix)[0]
    except cv2.error:
        return None
    if not np.isfinite([pitch, yaw, roll]).all():
        return None
    return Pose(yaw=round(-yaw, 2), pitch=round(pitch, 2), roll=round(roll, 2))
