from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import pandas as pd

from core.detection import ChuteConcreteDetector, build_detection_config
from core.overlay import draw_overlay_frame, init_video_writer
from core.speed import SpeedEstimator, build_speed_config
from core.video_io import VideoReader


RealtimeCallback = Callable[[dict[str, Any]], None]


def _encode_frame_b64(frame: np.ndarray, max_width: int = 960) -> str:
    h, w = frame.shape[:2]
    if w > max_width:
        nh = int(round((max_width / float(w)) * h))
        frame = cv2.resize(frame, (max_width, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _window_smooth(values: list[float], method: str, window: int) -> float:
    if not values:
        return 0.0
    win = max(1, int(window))
    seg = values[-win:]
    arr = np.array(seg, dtype=np.float32)
    if method == "median":
        return float(np.median(arr))
    return float(np.mean(arr))


def run_realtime_session(
    session_id: str,
    input_video_path: Path,
    output_dir: Path,
    config: dict[str, Any],
    manual_roi,
    update_cb: RealtimeCallback,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    overlay_path = output_dir / "realtime_overlay.mp4"
    trace_csv_path = output_dir / "realtime_trace.csv"

    detector = ChuteConcreteDetector(build_detection_config(config), manual_roi=manual_roi)
    speed_estimator = SpeedEstimator(build_speed_config(config))

    realtime_cfg = config.get("realtime", {})
    pouring_speed_th = float(realtime_cfg.get("pouring_speed_px_s_threshold", 30.0))
    start_speed_th = float(realtime_cfg.get("start_speed_px_s_threshold", 35.0))
    live_fps = float(realtime_cfg.get("live_update_fps", 5.0))
    playback_speed = float(realtime_cfg.get("playback_speed", 1.0))
    max_live_width = int(realtime_cfg.get("max_live_width", 960))

    fps_fallback = float(config.get("video", {}).get("fps_fallback", 30.0))
    frame_stride = max(1, int(config.get("video", {}).get("frame_stride", 1)))

    speed_series: list[float] = []
    smoothed_series: list[float] = []

    trace_rows: list[dict[str, Any]] = []

    pour_started = False
    pour_start_sec: float | None = None
    pouring_time_sec = 0.0
    dt = 0.0

    prev_roi = None
    last_push = 0.0

    with VideoReader(input_video_path, fps_fallback=fps_fallback) as vr:
        meta = vr.meta
        assert meta is not None

        dt = 1.0 / float((meta.fps if meta.fps > 0 else 30.0) / float(frame_stride))
        writer = init_video_writer(overlay_path, meta.fps, meta.width, meta.height)

        for frame_idx, time_sec, frame in vr.iter_frames():
            if frame_stride > 1 and (frame_idx % frame_stride) != 0:
                continue

            det = detector.detect(frame)
            chute_bbox = det["chute_bbox"]
            concrete_bbox = det["concrete_bbox"]
            coverage_ratio = float(det["coverage_ratio"])
            roi_frame = det["roi_frame"]

            yolo_candidates = list(det.get("debug", {}).get("yolo_candidates", []))
            yolo_best_conf = float(det.get("debug", {}).get("yolo_best_conf", 0.0))
            chosen_raw_bbox = det.get("debug", {}).get("chosen_raw_bbox")
            active_side = det.get("debug", {}).get("active_side")

            if prev_roi is not None and roi_frame is not None:
                speed = speed_estimator.estimate_pair(prev_roi, roi_frame, meta.fps / float(frame_stride))
            else:
                speed = 0.0
            prev_roi = roi_frame

            speed_series.append(float(speed))
            smoothed = _window_smooth(
                speed_series,
                method=speed_estimator.config.smoothing_method,
                window=speed_estimator.config.smoothing_window,
            )
            smoothed_series.append(smoothed)

            if (not pour_started) and (smoothed >= start_speed_th):
                pour_started = True
                pour_start_sec = float(time_sec)

            if smoothed >= pouring_speed_th:
                pouring_time_sec += dt

            overlay = draw_overlay_frame(
                frame=frame,
                chute_bbox=chute_bbox,
                concrete_bbox=concrete_bbox,
                coverage_ratio=coverage_ratio,
                speed_px_s=smoothed,
                yolo_candidates=yolo_candidates,
                chosen_raw_bbox=chosen_raw_bbox,
                active_side=active_side,
            )
            writer.write(overlay)

            trace_rows.append(
                {
                    "frame_idx": int(frame_idx),
                    "time_sec": float(time_sec),
                    "speed_px_s": float(speed),
                    "speed_smoothed_px_s": float(smoothed),
                    "yolo_count": int(len(yolo_candidates)),
                    "yolo_best_conf": float(yolo_best_conf),
                    "coverage_ratio": float(coverage_ratio),
                    "chute_bbox": str(chute_bbox) if chute_bbox is not None else "",
                    "concrete_bbox": str(concrete_bbox) if concrete_bbox is not None else "",
                    "pouring_time_sec": float(pouring_time_sec),
                }
            )

            progress = int(round((frame_idx / max(1, meta.frame_count)) * 100.0)) if meta.frame_count > 0 else 0
            now = time.time()
            interval = 1.0 / max(1.0, live_fps)
            should_push = (now - last_push) >= interval or frame_idx == meta.frame_count
            if should_push:
                last_push = now
                update_cb(
                    {
                        "status": "running",
                        "progress": int(max(0, min(99, progress))),
                        "message": f"Realtime analyzing frame {frame_idx}/{meta.frame_count}",
                        "frame_jpeg_b64": _encode_frame_b64(overlay, max_width=max_live_width),
                        "stats": {
                            "frame_idx": int(frame_idx),
                            "video_time_sec": float(time_sec),
                            "current_speed_px_s": float(speed),
                            "smoothed_speed_px_s": float(smoothed),
                            "pour_started": bool(pour_started),
                            "pour_start_sec": float(pour_start_sec) if pour_start_sec is not None else None,
                            "pouring_time_sec": float(pouring_time_sec),
                            "yolo_count": int(len(yolo_candidates)),
                            "yolo_best_conf": float(yolo_best_conf),
                            "coverage_ratio": float(coverage_ratio),
                            "active_side": active_side,
                        },
                    }
                )

            # near-real-time pacing
            if playback_speed > 0:
                time.sleep(max(0.0, dt / playback_speed))

        writer.release()

    pd.DataFrame(trace_rows).to_csv(trace_csv_path, index=False, encoding="utf-8-sig")

    final_summary = {
        "session_id": session_id,
        "status": "done",
        "progress": 100,
        "message": "Realtime analysis completed",
        "stats": {
            "avg_speed_px_s": float(np.mean(np.array(smoothed_series, dtype=np.float32))) if smoothed_series else 0.0,
            "max_speed_px_s": float(np.max(np.array(smoothed_series, dtype=np.float32))) if smoothed_series else 0.0,
            "pour_started": bool(pour_started),
            "pour_start_sec": float(pour_start_sec) if pour_start_sec is not None else None,
            "pouring_time_sec": float(pouring_time_sec),
            "total_frames": len(smoothed_series),
        },
        "artifacts": {
            "overlay_video": f"/data/outputs/{session_id}/realtime_overlay.mp4",
            "trace_csv": f"/data/outputs/{session_id}/realtime_trace.csv",
        },
    }
    return final_summary
