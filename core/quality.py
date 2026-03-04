from __future__ import annotations

from typing import Any

import numpy as np


def _count_stop_segments(values: list[float], stop_thres: float, min_frames: int) -> int:
    run = 0
    count = 0
    for v in values:
        if v < stop_thres:
            run += 1
        else:
            if run >= min_frames:
                count += 1
            run = 0
    if run >= min_frames:
        count += 1
    return count


def compute_metrics(
    speed_smoothed_px_s: list[float],
    coverage_ratio_series: list[float],
    fps: float,
    quality_cfg: dict[str, Any],
) -> dict[str, float]:
    vals = [float(v) for v in speed_smoothed_px_s if np.isfinite(v)]
    if not vals:
        vals = [0.0]

    arr = np.array(vals, dtype=np.float32)
    avg_speed = float(np.mean(arr))
    median_speed = float(np.median(arr))
    std_speed = float(np.std(arr))
    cv_speed = float(std_speed / avg_speed) if avg_speed > 1e-6 else float("inf")
    max_speed = float(np.max(arr))
    min_speed = float(np.min(arr))

    stop_thres = float(quality_cfg.get("stop_speed_px_s", 5.0))
    stop_min_sec = float(quality_cfg.get("stop_min_duration_sec", 0.5))
    stop_min_frames = max(1, int(round(stop_min_sec * fps)))
    stop_count = float(_count_stop_segments(vals, stop_thres, stop_min_frames))

    cov = [float(c) for c in coverage_ratio_series if np.isfinite(c)]
    coverage_ratio = float(np.mean(cov)) if cov else 0.0

    return {
        "avg_speed": avg_speed,
        "median_speed": median_speed,
        "std_speed": std_speed,
        "cv_speed": cv_speed,
        "stop_count": stop_count,
        "max_speed": max_speed,
        "min_speed": min_speed,
        "coverage_ratio": coverage_ratio,
    }


def judge_quality(metrics: dict[str, float], quality_cfg: dict[str, Any]) -> tuple[str, list[str], dict[str, Any]]:
    thresholds_used = {
        "avg_speed_px_s": quality_cfg.get("avg_speed_px_s", {}),
        "stop_count": quality_cfg.get("stop_count", {}),
        "cv_speed_warning": quality_cfg.get("cv_speed_warning", 0.6),
        "coverage_ratio_warning_low": quality_cfg.get("coverage_ratio_warning_low", 0.05),
    }

    reasons: list[str] = []
    has_bad = False
    has_warning = False

    avg_cfg = thresholds_used["avg_speed_px_s"]
    avg = metrics["avg_speed"]
    if avg < float(avg_cfg.get("bad_low", 15.0)):
        has_bad = True
        reasons.append(
            f"avg_speed {avg:.2f}px/s < bad_low {float(avg_cfg.get('bad_low', 15.0)):.2f}: cold-joint risk"
        )
    elif avg < float(avg_cfg.get("warning_low", 30.0)):
        has_warning = True
        reasons.append(
            f"avg_speed {avg:.2f}px/s < warning_low {float(avg_cfg.get('warning_low', 30.0)):.2f}: continuity risk"
        )

    if avg > float(avg_cfg.get("bad_high", 350.0)):
        has_bad = True
        reasons.append(
            f"avg_speed {avg:.2f}px/s > bad_high {float(avg_cfg.get('bad_high', 350.0)):.2f}: segregation risk"
        )
    elif avg > float(avg_cfg.get("warning_high", 250.0)):
        has_warning = True
        reasons.append(
            f"avg_speed {avg:.2f}px/s > warning_high {float(avg_cfg.get('warning_high', 250.0)):.2f}: instability risk"
        )

    stop_cfg = thresholds_used["stop_count"]
    stop_count = metrics["stop_count"]
    if stop_count >= float(stop_cfg.get("bad", 4.0)):
        has_bad = True
        reasons.append(
            f"stop_count {stop_count:.0f} >= bad {float(stop_cfg.get('bad', 4.0)):.0f}: frequent interruptions"
        )
    elif stop_count >= float(stop_cfg.get("warning", 2.0)):
        has_warning = True
        reasons.append(
            f"stop_count {stop_count:.0f} >= warning {float(stop_cfg.get('warning', 2.0)):.0f}: pause/restart observed"
        )

    cv_warn = float(thresholds_used["cv_speed_warning"])
    if metrics["cv_speed"] > cv_warn:
        has_warning = True
        reasons.append(
            f"cv_speed {metrics['cv_speed']:.2f} > {cv_warn:.2f}: unstable flow"
        )

    cov_warn = float(thresholds_used["coverage_ratio_warning_low"])
    if metrics["coverage_ratio"] < cov_warn:
        has_warning = True
        reasons.append(
            f"coverage_ratio {metrics['coverage_ratio']:.3f} < {cov_warn:.3f}: low concrete visibility"
        )

    if has_bad:
        grade = "BAD"
    elif has_warning:
        grade = "WARNING"
    else:
        grade = "GOOD"
        reasons.append("All monitored metrics are within configured thresholds")

    return grade, reasons, thresholds_used
