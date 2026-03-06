from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CACHE_ROOT = PROJECT_ROOT / ".cache"
(_CACHE_ROOT / "ultralytics").mkdir(parents=True, exist_ok=True)
(_CACHE_ROOT / "matplotlib").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(_CACHE_ROOT / "ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(_CACHE_ROOT / "matplotlib"))

import torch
from ultralytics import YOLO

from core.legacy import PROJECT_ROOT, load_legacy_module
from core.schemas import ROIInput


@dataclass
class DetectionConfig:
    model_path: Path
    conf_thres: float
    imgsz: int
    max_det: int
    ema_alpha: float
    hold_frames: int
    concrete_diff_threshold: int


class ChuteConcreteDetector:
    """
    Reuses existing chute detection helpers from src/b1_crop_roi_and_drop_timing.py.
    Concrete mask is a minimal fallback based on frame-diff in ROI.
    """

    def __init__(self, config: DetectionConfig, manual_roi: ROIInput | None = None):
        self.config = config
        self.manual_roi = manual_roi

        legacy_b1 = load_legacy_module("src/b1_crop_roi_and_drop_timing.py", alias="legacy_b1_module")
        self.pick_two_chutes_xyxy = legacy_b1.pick_two_chutes_xyxy
        self.ema_update = legacy_b1.ema_update
        self.safe_crop = legacy_b1.safe_crop

        model_path = config.model_path
        if not model_path.is_absolute():
            model_path = PROJECT_ROOT / model_path
        if not model_path.exists() and manual_roi is None:
            raise FileNotFoundError(f"YOLO weight not found: {model_path}")

        self.model = None if manual_roi is not None else YOLO(str(model_path))
        self.device = 0 if torch.cuda.is_available() else "cpu"
        torch.set_grad_enabled(False)
        try:
            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)
        except Exception:
            pass

        self.smoothed_box: tuple[float, float, float, float] | None = None
        self.hold_count = 0
        self.active_side: str | None = None
        self.prev_roi_gray: np.ndarray | None = None

    def _choose_box(
        self,
        left_box: tuple[float, float, float, float] | None,
        right_box: tuple[float, float, float, float] | None,
    ) -> tuple[float, float, float, float] | None:
        if left_box is None and right_box is None:
            return None
        if left_box is None:
            self.active_side = "right"
            return right_box
        if right_box is None:
            self.active_side = "left"
            return left_box

        if self.active_side == "left":
            return left_box
        if self.active_side == "right":
            return right_box

        def area(box: tuple[float, float, float, float]) -> float:
            x1, y1, x2, y2 = box
            return max(0.0, (x2 - x1) * (y2 - y1))

        left_area = area(left_box)
        right_area = area(right_box)
        if left_area >= right_area:
            self.active_side = "left"
            return left_box
        self.active_side = "right"
        return right_box

    def _concrete_mask(
        self,
        roi_bgr: np.ndarray,
        roi_xyxy: tuple[int, int, int, int],
    ) -> tuple[np.ndarray, tuple[int, int, int, int] | None, float]:
        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)

        if self.prev_roi_gray is None or self.prev_roi_gray.shape != gray.shape:
            self.prev_roi_gray = gray
            empty = np.zeros_like(gray, dtype=np.uint8)
            return empty, None, 0.0

        diff = cv2.absdiff(gray, self.prev_roi_gray)
        self.prev_roi_gray = gray

        # Minimal fallback for concrete-motion mask when dedicated model is absent.
        otsu_thr, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        thr = max(float(self.config.concrete_diff_threshold), float(otsu_thr))
        _, mask = cv2.threshold(diff, thr, 255, cv2.THRESH_BINARY)

        kernel = np.ones((3, 3), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel)

        nonzero = np.argwhere(mask > 0)
        coverage = float(nonzero.shape[0]) / float(mask.size) if mask.size > 0 else 0.0

        if nonzero.shape[0] == 0:
            return mask, None, coverage

        y_min, x_min = nonzero.min(axis=0)
        y_max, x_max = nonzero.max(axis=0)

        rx1, ry1, _rx2, _ry2 = roi_xyxy
        concrete_bbox = (
            int(rx1 + x_min),
            int(ry1 + y_min),
            int(rx1 + x_max),
            int(ry1 + y_max),
        )
        return mask, concrete_bbox, coverage

    def detect(
        self,
        frame: np.ndarray,
    ) -> dict[str, Any]:
        h, w = frame.shape[:2]
        dets: list[tuple[float, float, float, float, float]] = []
        yolo_candidates: list[dict[str, Any]] = []

        if self.manual_roi is None and self.model is not None:
            results = self.model(
                frame,
                device=self.device,
                conf=self.config.conf_thres,
                imgsz=self.config.imgsz,
                max_det=self.config.max_det,
                verbose=False,
            )
            boxes = results[0].boxes
            if boxes is not None and len(boxes) > 0:
                xyxy = boxes.xyxy.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                for (x1, y1, x2, y2), c in zip(xyxy, confs):
                    dets.append((float(x1), float(y1), float(x2), float(y2), float(c)))
                    cx = 0.5 * (float(x1) + float(x2))
                    yolo_candidates.append(
                        {
                            "bbox": [int(x1), int(y1), int(x2), int(y2)],
                            "conf": float(c),
                            "side": "left" if cx < (w / 2.0) else "right",
                        }
                    )

        chosen_raw_box: tuple[float, float, float, float] | None = None
        if self.manual_roi is not None:
            x1, y1, x2, y2 = self.manual_roi.to_xyxy()
            x1 = max(0, min(w - 1, x1))
            x2 = max(0, min(w, x2))
            y1 = max(0, min(h - 1, y1))
            y2 = max(0, min(h, y2))
            chute_box = (float(x1), float(y1), float(x2), float(y2))
            self.smoothed_box = chute_box
            chosen_raw_box = chute_box
        else:
            left_new, right_new = self.pick_two_chutes_xyxy(dets, w)
            chosen_new = self._choose_box(left_new, right_new)
            chosen_raw_box = chosen_new

            if chosen_new is not None:
                self.smoothed_box = self.ema_update(self.smoothed_box, chosen_new, self.config.ema_alpha)
                self.hold_count = 0
            else:
                self.hold_count += 1
                if self.hold_count > self.config.hold_frames:
                    self.smoothed_box = None

        chute_bbox: tuple[int, int, int, int] | None = None
        concrete_bbox: tuple[int, int, int, int] | None = None
        concrete_mask: np.ndarray | None = None
        coverage_ratio = 0.0
        roi_frame: np.ndarray | None = None

        if self.smoothed_box is not None:
            roi_frame, chute_bbox = self.safe_crop(frame, self.smoothed_box)
            if roi_frame is not None and chute_bbox is not None:
                concrete_mask, concrete_bbox, coverage_ratio = self._concrete_mask(roi_frame, chute_bbox)
            else:
                self.prev_roi_gray = None
        else:
            self.prev_roi_gray = None

        return {
            "chute_bbox": chute_bbox,
            "concrete_bbox": concrete_bbox,
            "concrete_mask": concrete_mask,
            "coverage_ratio": coverage_ratio,
            "roi_frame": roi_frame,
            "debug": {
                "detections": len(dets),
                "active_side": self.active_side,
                "manual_roi": self.manual_roi.model_dump() if self.manual_roi else None,
                "yolo_candidates": yolo_candidates,
                "yolo_best_conf": max([float(c[4]) for c in dets], default=0.0),
                "chosen_raw_bbox": [int(v) for v in chosen_raw_box] if chosen_raw_box is not None else None,
            },
        }


def build_detection_config(config: dict[str, Any]) -> DetectionConfig:
    det = config.get("detection", {})
    model_path = Path(det.get("model_path", "runs/detect/train/weights/best.pt"))
    return DetectionConfig(
        model_path=model_path,
        conf_thres=float(det.get("conf_thres", 0.25)),
        imgsz=int(det.get("imgsz", 320)),
        max_det=int(det.get("max_det", 4)),
        ema_alpha=float(det.get("ema_alpha", 0.25)),
        hold_frames=int(det.get("hold_frames", 10)),
        concrete_diff_threshold=int(det.get("concrete_diff_threshold", 20)),
    )
