from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def draw_overlay_frame(
    frame: np.ndarray,
    chute_bbox: tuple[int, int, int, int] | None,
    concrete_bbox: tuple[int, int, int, int] | None,
    coverage_ratio: float,
    speed_px_s: float,
    yolo_candidates: list[dict] | None = None,
    chosen_raw_bbox: list[int] | None = None,
    active_side: str | None = None,
) -> np.ndarray:
    out = frame.copy()
    yolo_candidates = yolo_candidates or []

    # YOLO raw detections
    for idx, cand in enumerate(yolo_candidates):
        bbox = cand.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = [int(v) for v in bbox]
        conf = float(cand.get("conf", 0.0))
        side = str(cand.get("side", "?"))
        color = (255, 128, 0) if side == "left" else (180, 90, 255)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 1)
        cv2.putText(
            out,
            f"YOLO {side[0].upper()} {conf:.2f}",
            (x1, max(16, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

    if chosen_raw_bbox is not None and len(chosen_raw_bbox) == 4:
        cx1, cy1, cx2, cy2 = [int(v) for v in chosen_raw_bbox]
        cv2.rectangle(out, (cx1, cy1), (cx2, cy2), (255, 255, 255), 2)
        cv2.putText(
            out,
            "YOLO selected",
            (cx1, max(20, cy1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    if chute_bbox is not None:
        x1, y1, x2, y2 = chute_bbox
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 255), 2)
        cv2.putText(
            out,
            "chute",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

    if concrete_bbox is not None:
        x1, y1, x2, y2 = concrete_bbox
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
        cv2.putText(
            out,
            "concrete",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 200, 0),
            2,
            cv2.LINE_AA,
        )

    cv2.putText(
        out,
        f"speed={speed_px_s:.2f}px/s  coverage={coverage_ratio:.3f}  yolo_n={len(yolo_candidates)} side={active_side or '-'}",
        (20, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return out


def init_video_writer(out_path: Path, fps: float, width: int, height: int) -> cv2.VideoWriter:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    return cv2.VideoWriter(str(out_path), fourcc, float(fps), (int(width), int(height)))


def save_snapshot(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
