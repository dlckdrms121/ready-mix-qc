from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Any

import numpy as np
import pandas as pd

from core.detection import ChuteConcreteDetector, build_detection_config
from core.overlay import draw_overlay_frame, init_video_writer, save_snapshot
from core.quality import compute_metrics, judge_quality
from core.report import (
    generate_confidence_heatmap,
    generate_pdf_report,
    generate_speed_plot,
    generate_yolo_process_plot,
)
from core.schemas import ROIInput
from core.speed import SpeedEstimator, build_speed_config
from core.utils import OUTPUTS_DIR, REPORTS_DIR, write_json
from core.video_io import VideoReader


logger = logging.getLogger(__name__)


ProgressCallback = Callable[[int, str], None]


def _snapshot_targets(frame_count: int) -> list[int]:
    if frame_count <= 0:
        return [1]
    raw = [1, frame_count // 4, frame_count // 2, (3 * frame_count) // 4, frame_count]
    out = sorted({max(1, int(v)) for v in raw})
    return out


def run_analysis_job(
    job_id: str,
    input_video_path: Path,
    config: dict[str, Any],
    manual_roi: ROIInput | None,
    mm_per_pixel: float | None,
    chute_width_mm: float | None,
    progress_cb: ProgressCallback | None = None,
) -> dict[str, Any]:
    def update(progress: int, message: str) -> None:
        if progress_cb is not None:
            progress_cb(progress, message)

    output_dir = OUTPUTS_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    overlay_path = output_dir / "overlay.mp4"
    speed_plot_path = output_dir / "speed_plot.png"
    yolo_process_plot_path = output_dir / "yolo_process.png"
    yolo_conf_heatmap_path = output_dir / "yolo_conf_heatmap.png"
    yolo_trace_csv_path = output_dir / "yolo_trace.csv"
    snapshots_dir = output_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    result_json_path = output_dir / "result.json"
    report_pdf_path = REPORTS_DIR / f"{job_id}.pdf"

    update(5, "Initializing detector and speed estimator")
    detector = ChuteConcreteDetector(build_detection_config(config), manual_roi=manual_roi)
    speed_estimator = SpeedEstimator(build_speed_config(config))

    fps_fallback = float(config.get("video", {}).get("fps_fallback", 30.0))

    times: list[float] = []
    speed_raw: list[float] = []
    speed_smoothed: list[float] = []
    speed_m_s: list[float] | None = None
    coverage_series: list[float] = []
    chute_width_px_series: list[float] = []
    yolo_count_series: list[int] = []
    yolo_best_conf_series: list[float] = []
    yolo_trace_rows: list[dict[str, Any]] = []

    snapshot_paths: list[Path] = []
    snapshot_target_set: set[int] = set()

    prev_roi = None
    valid_roi_pair_count = 0
    chute_detect_count = 0

    update(10, "Reading video frames")

    with VideoReader(input_video_path, fps_fallback=fps_fallback) as vr:
        meta = vr.meta
        assert meta is not None

        snapshot_target_set = set(_snapshot_targets(meta.frame_count))

        writer = init_video_writer(overlay_path, meta.fps, meta.width, meta.height)

        for frame_idx, time_sec, frame in vr.iter_frames():
            det = detector.detect(frame)

            chute_bbox = det["chute_bbox"]
            concrete_bbox = det["concrete_bbox"]
            coverage_ratio = float(det["coverage_ratio"])
            roi_frame = det["roi_frame"]
            yolo_candidates = list(det.get("debug", {}).get("yolo_candidates", []))
            yolo_best_conf = float(det.get("debug", {}).get("yolo_best_conf", 0.0))
            chosen_raw_bbox = det.get("debug", {}).get("chosen_raw_bbox", None)
            active_side = det.get("debug", {}).get("active_side", None)

            if chute_bbox is not None:
                chute_detect_count += 1
                x1, _y1, x2, _y2 = chute_bbox
                chute_width_px_series.append(float(max(1, x2 - x1)))

            if prev_roi is not None and roi_frame is not None:
                speed = speed_estimator.estimate_pair(prev_roi, roi_frame, meta.fps)
                valid_roi_pair_count += 1
            else:
                speed = 0.0

            prev_roi = roi_frame
            times.append(time_sec)
            speed_raw.append(float(speed))
            coverage_series.append(coverage_ratio)
            yolo_count_series.append(int(len(yolo_candidates)))
            yolo_best_conf_series.append(yolo_best_conf)
            yolo_trace_rows.append(
                {
                    "frame_idx": int(frame_idx),
                    "time_sec": float(time_sec),
                    "yolo_count": int(len(yolo_candidates)),
                    "yolo_best_conf": float(yolo_best_conf),
                    "active_side": active_side if active_side is not None else "",
                    "chosen_raw_bbox": str(chosen_raw_bbox) if chosen_raw_bbox is not None else "",
                    "smoothed_chute_bbox": str(chute_bbox) if chute_bbox is not None else "",
                    "concrete_bbox": str(concrete_bbox) if concrete_bbox is not None else "",
                    "coverage_ratio": float(coverage_ratio),
                    "speed_px_s": float(speed),
                }
            )

            overlay_frame = draw_overlay_frame(
                frame=frame,
                chute_bbox=chute_bbox,
                concrete_bbox=concrete_bbox,
                coverage_ratio=coverage_ratio,
                speed_px_s=speed,
                yolo_candidates=yolo_candidates,
                chosen_raw_bbox=chosen_raw_bbox,
                active_side=active_side,
            )
            writer.write(overlay_frame)

            if frame_idx in snapshot_target_set:
                snap_path = snapshots_dir / f"frame_{frame_idx:06d}.jpg"
                save_snapshot(overlay_frame, snap_path)
                snapshot_paths.append(snap_path)

            if meta.frame_count > 0 and frame_idx % 10 == 0:
                p = int(10 + (70.0 * frame_idx / float(meta.frame_count)))
                update(min(80, p), f"Processing frames {frame_idx}/{meta.frame_count}")

        writer.release()

        fps = meta.fps

    if manual_roi is None and chute_detect_count == 0:
        raise RuntimeError("Chute detection failed: no chute bbox was found")

    if valid_roi_pair_count == 0:
        raise RuntimeError("Speed calculation unavailable: no valid consecutive ROI pair")

    speed_smoothed = speed_estimator.smooth(speed_raw)

    scale_source = "none"
    if mm_per_pixel is None and chute_width_mm is not None and chute_width_px_series:
        mean_width_px = float(np.mean(np.array(chute_width_px_series, dtype=np.float32)))
        if mean_width_px > 1e-6:
            mm_per_pixel = float(chute_width_mm) / mean_width_px
            scale_source = "chute_width_mm"

    if mm_per_pixel is not None and mm_per_pixel > 0:
        scale_source = "mm_per_pixel"
        speed_m_s = [float(v * mm_per_pixel / 1000.0) for v in speed_smoothed]

    update(84, "Computing metrics and quality grade")
    quality_cfg = config.get("quality", {})
    metrics = compute_metrics(speed_smoothed, coverage_series, fps, quality_cfg)
    grade, reasons, thresholds_used = judge_quality(metrics, quality_cfg)

    update(90, "Generating speed graph")
    generate_speed_plot(times, speed_smoothed, speed_plot_path)
    generate_yolo_process_plot(times, yolo_count_series, yolo_best_conf_series, yolo_process_plot_path)
    generate_confidence_heatmap(yolo_best_conf_series, yolo_conf_heatmap_path)
    pd.DataFrame(yolo_trace_rows).to_csv(yolo_trace_csv_path, index=False, encoding="utf-8-sig")

    artifacts = {
        "result_json": f"/data/outputs/{job_id}/result.json",
        "speed_plot": f"/data/outputs/{job_id}/speed_plot.png",
        "yolo_process_plot": f"/data/outputs/{job_id}/yolo_process.png",
        "yolo_conf_heatmap": f"/data/outputs/{job_id}/yolo_conf_heatmap.png",
        "yolo_trace_csv": f"/data/outputs/{job_id}/yolo_trace.csv",
        "report_pdf": f"/api/jobs/{job_id}/report",
        "overlay_video": f"/api/jobs/{job_id}/overlay_video",
        "snapshots": [f"/data/outputs/{job_id}/snapshots/{p.name}" for p in snapshot_paths],
    }

    result_payload: dict[str, Any] = {
        "job_id": job_id,
        "input_video": input_video_path.name,
        "fps": fps,
        "quality_grade": grade,
        "reasons": reasons,
        "metrics": metrics,
        "thresholds_used": thresholds_used,
        "speed_series_px_s": speed_smoothed,
        "speed_series_m_s": speed_m_s,
        "yolo_detection_series": {
            "count": yolo_count_series,
            "best_conf": yolo_best_conf_series,
        },
        "scale": {
            "mm_per_pixel": mm_per_pixel,
            "source": scale_source,
        },
        "artifacts": artifacts,
    }

    write_json(result_json_path, result_payload)

    update(94, "Generating PDF report")
    generate_pdf_report(
        result_payload,
        report_pdf_path,
        speed_plot_path,
        snapshot_paths,
        yolo_process_plot_path=yolo_process_plot_path,
        confidence_heatmap_path=yolo_conf_heatmap_path,
    )

    update(100, "Analysis done")
    logger.info("job done: %s", job_id)
    return result_payload
