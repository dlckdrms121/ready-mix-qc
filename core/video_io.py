from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import cv2
import numpy as np


@dataclass
class VideoMeta:
    path: Path
    fps: float
    frame_count: int
    width: int
    height: int


class VideoReader:
    def __init__(self, video_path: str | Path, fps_fallback: float = 30.0):
        self.video_path = Path(video_path)
        self.fps_fallback = fps_fallback
        self.cap: cv2.VideoCapture | None = None
        self.meta: VideoMeta | None = None

    def __enter__(self) -> "VideoReader":
        self.cap = cv2.VideoCapture(str(self.video_path))
        if not self.cap.isOpened():
            raise RuntimeError(f"Failed to open video: {self.video_path}")

        fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0.0:
            fps = self.fps_fallback

        frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        self.meta = VideoMeta(
            path=self.video_path,
            fps=fps,
            frame_count=frame_count,
            width=width,
            height=height,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.cap is not None:
            self.cap.release()

    def iter_frames(self) -> Generator[tuple[int, float, np.ndarray], None, None]:
        if self.cap is None or self.meta is None:
            raise RuntimeError("VideoReader is not opened")

        idx = 0
        while True:
            ok, frame = self.cap.read()
            if not ok:
                break
            idx += 1
            yield idx, idx / self.meta.fps, frame
