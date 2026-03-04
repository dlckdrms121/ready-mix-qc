from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from core.legacy import load_legacy_module


@dataclass
class SpeedConfig:
    smoothing_method: str
    smoothing_window: int


class SpeedEstimator:
    """Reuses legacy optical-flow speed functions from src scripts."""

    def __init__(self, config: SpeedConfig):
        self.config = config

        legacy_b0 = load_legacy_module(
            "src/b0_track_and_smooth_batch_10videos.py", alias="legacy_b0_speed_module"
        )
        legacy_b2 = load_legacy_module("src/b2_make_10s_clips.py", alias="legacy_b2_speed_module")

        self.lk_flow_score = legacy_b0.lk_flow_score
        self.motion_score = legacy_b2.motion_score

    def smooth(self, values: list[float]) -> list[float]:
        if not values:
            return []

        win = max(1, int(self.config.smoothing_window))
        if win == 1:
            return list(values)

        arr = np.array(values, dtype=np.float32)
        out = []
        for i in range(len(arr)):
            lo = max(0, i - win + 1)
            seg = arr[lo : i + 1]
            if self.config.smoothing_method == "median":
                out.append(float(np.median(seg)))
            else:
                out.append(float(np.mean(seg)))
        return out

    def estimate_pair(self, prev_roi: np.ndarray, curr_roi: np.ndarray, fps: float) -> float:
        if prev_roi.shape[:2] != curr_roi.shape[:2]:
            # Legacy LK flow requires same frame size.
            prev_h, prev_w = prev_roi.shape[:2]
            curr_roi = cv2.resize(curr_roi, (prev_w, prev_h), interpolation=cv2.INTER_AREA)

        prev_gray = cv2.cvtColor(prev_roi, cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(curr_roi, cv2.COLOR_BGR2GRAY)

        lk_out = self.lk_flow_score(prev_gray, curr_gray)
        flow_mag = float(lk_out[0]) if isinstance(lk_out, tuple) else float(lk_out)
        speed_px_s = float(flow_mag * fps)
        if speed_px_s <= 0.0:
            motion = self.motion_score(prev_roi, curr_roi)
            speed_px_s = float(motion * fps)
        return speed_px_s

    def estimate(
        self,
        roi_frames: list[np.ndarray | None],
        fps: float,
        mm_per_pixel: float | None = None,
    ) -> dict[str, list[float] | None]:
        speeds_px_s: list[float] = []

        prev = None
        for curr in roi_frames:
            if prev is None or curr is None:
                speeds_px_s.append(0.0)
                prev = curr
                continue
            speeds_px_s.append(self.estimate_pair(prev, curr, fps))
            prev = curr

        smooth_px_s = self.smooth(speeds_px_s)

        speeds_m_s = None
        if mm_per_pixel is not None and mm_per_pixel > 0:
            scale = float(mm_per_pixel) / 1000.0
            speeds_m_s = [float(v * scale) for v in smooth_px_s]

        return {
            "speed_px_s": speeds_px_s,
            "speed_smoothed_px_s": smooth_px_s,
            "speed_m_s": speeds_m_s,
        }


def build_speed_config(config: dict[str, Any]) -> SpeedConfig:
    speed = config.get("speed", {})
    return SpeedConfig(
        smoothing_method=str(speed.get("smoothing_method", "moving_average")),
        smoothing_window=int(speed.get("smoothing_window", 5)),
    )
