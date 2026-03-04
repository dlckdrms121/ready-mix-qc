from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class ROIInput(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)

    def to_xyxy(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.w, self.y + self.h


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str


class JobResultResponse(BaseModel):
    job_id: str
    status: str
    metrics: dict[str, float]
    quality_grade: str
    reasons: list[str]
    thresholds_used: dict[str, Any]
    artifacts: dict[str, Any]


@dataclass
class DetectionFrame:
    frame_idx: int
    time_sec: float
    chute_bbox: tuple[int, int, int, int] | None
    concrete_bbox: tuple[int, int, int, int] | None
    coverage_ratio: float
    speed_px_s: float = 0.0
    speed_smoothed_px_s: float = 0.0
    speed_m_s: float | None = None
    roi_bbox: tuple[int, int, int, int] | None = None
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisArtifacts:
    result_json: str
    speed_plot_png: str
    report_pdf: str
    overlay_video: str | None
    snapshots: list[str]
