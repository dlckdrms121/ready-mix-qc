from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from core.config import load_config
from core.pipeline import run_analysis_job
from core.realtime import run_realtime_session
from core.schemas import JobCreateResponse, JobResultResponse, JobStatusResponse, ROIInput
from core.utils import OUTPUTS_DIR, REPORTS_DIR, UPLOADS_DIR, new_job_id


logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
executor = ThreadPoolExecutor(max_workers=2)

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}

_rt_lock = threading.Lock()
_rt_sessions: dict[str, dict[str, Any]] = {}


def _set_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        if job_id not in _jobs:
            _jobs[job_id] = {"job_id": job_id}
        _jobs[job_id].update(kwargs)


def _get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


def _set_rt(session_id: str, **kwargs: Any) -> None:
    with _rt_lock:
        if session_id not in _rt_sessions:
            _rt_sessions[session_id] = {"session_id": session_id}
        _rt_sessions[session_id].update(kwargs)


def _get_rt(session_id: str) -> dict[str, Any]:
    with _rt_lock:
        session = _rt_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Realtime session not found: {session_id}")
    return session


def _parse_manual_roi(
    roi_x: int | None,
    roi_y: int | None,
    roi_w: int | None,
    roi_h: int | None,
) -> ROIInput | None:
    vals = [roi_x, roi_y, roi_w, roi_h]
    if all(v is None for v in vals):
        return None
    if any(v is None for v in vals):
        raise HTTPException(status_code=400, detail="manual ROI requires x,y,w,h all together")
    try:
        return ROIInput(x=int(roi_x), y=int(roi_y), w=int(roi_w), h=int(roi_h))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid ROI: {exc}") from exc


def _parse_optional_int(value: str | None, field_name: str) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be an integer") from exc


def _parse_optional_float(value: str | None, field_name: str) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a number") from exc


def _run_job(
    job_id: str,
    input_video_path: Path,
    manual_roi: ROIInput | None,
    mm_per_pixel: float | None,
    chute_width_mm: float | None,
) -> None:
    try:
        _set_job(job_id, status="running", progress=1, message="Job started")

        cfg = load_config()

        def progress_cb(progress: int, message: str) -> None:
            _set_job(job_id, status="running", progress=int(progress), message=message)

        result_payload = run_analysis_job(
            job_id=job_id,
            input_video_path=input_video_path,
            config=cfg,
            manual_roi=manual_roi,
            mm_per_pixel=mm_per_pixel,
            chute_width_mm=chute_width_mm,
            progress_cb=progress_cb,
        )

        _set_job(
            job_id,
            status="done",
            progress=100,
            message="Completed",
            result=result_payload,
        )

    except Exception as exc:
        logger.exception("job failed: %s", job_id)
        _set_job(
            job_id,
            status="failed",
            progress=100,
            message=str(exc),
            error=str(exc),
        )


def _run_realtime(
    session_id: str,
    input_video_path: Path,
    manual_roi: ROIInput | None,
) -> None:
    try:
        _set_rt(session_id, status="running", progress=1, message="Realtime session started")
        cfg = load_config()

        def update_cb(payload: dict[str, Any]) -> None:
            _set_rt(session_id, **payload)

        final_payload = run_realtime_session(
            session_id=session_id,
            input_video_path=input_video_path,
            output_dir=OUTPUTS_DIR / session_id,
            config=cfg,
            manual_roi=manual_roi,
            update_cb=update_cb,
        )
        clean_payload = dict(final_payload)
        clean_payload.pop("session_id", None)
        _set_rt(session_id, **clean_payload)

    except Exception as exc:
        logger.exception("realtime session failed: %s", session_id)
        _set_rt(
            session_id,
            status="failed",
            progress=100,
            message=str(exc),
            error=str(exc),
        )


def shutdown_executor() -> None:
    executor.shutdown(wait=False, cancel_futures=True)


@router.post("/api/jobs", response_model=JobCreateResponse)
async def create_job(
    file: UploadFile = File(...),
    roi_x: str | None = Form(default=None),
    roi_y: str | None = Form(default=None),
    roi_w: str | None = Form(default=None),
    roi_h: str | None = Form(default=None),
    mm_per_pixel: str | None = Form(default=None),
    chute_width_mm: str | None = Form(default=None),
) -> JobCreateResponse:
    ext = Path(file.filename or "").suffix.lower()
    if ext != ".mp4":
        raise HTTPException(status_code=400, detail="Only .mp4 upload is supported")

    parsed_roi_x = _parse_optional_int(roi_x, "roi_x")
    parsed_roi_y = _parse_optional_int(roi_y, "roi_y")
    parsed_roi_w = _parse_optional_int(roi_w, "roi_w")
    parsed_roi_h = _parse_optional_int(roi_h, "roi_h")
    parsed_mm_per_pixel = _parse_optional_float(mm_per_pixel, "mm_per_pixel")
    parsed_chute_width_mm = _parse_optional_float(chute_width_mm, "chute_width_mm")

    manual_roi = _parse_manual_roi(parsed_roi_x, parsed_roi_y, parsed_roi_w, parsed_roi_h)

    job_id = new_job_id()
    upload_path = UPLOADS_DIR / f"{job_id}.mp4"

    upload_path.parent.mkdir(parents=True, exist_ok=True)
    with upload_path.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    _set_job(
        job_id,
        status="pending",
        progress=0,
        message="Queued",
        input_video=str(upload_path),
        result=None,
        error=None,
    )

    executor.submit(
        _run_job,
        job_id,
        upload_path,
        manual_roi,
        parsed_mm_per_pixel,
        parsed_chute_width_mm,
    )
    return JobCreateResponse(job_id=job_id, status="pending")


@router.post("/api/realtime/sessions")
async def create_realtime_session(
    file: UploadFile = File(...),
    roi_x: str | None = Form(default=None),
    roi_y: str | None = Form(default=None),
    roi_w: str | None = Form(default=None),
    roi_h: str | None = Form(default=None),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext != ".mp4":
        raise HTTPException(status_code=400, detail="Only .mp4 upload is supported")

    parsed_roi_x = _parse_optional_int(roi_x, "roi_x")
    parsed_roi_y = _parse_optional_int(roi_y, "roi_y")
    parsed_roi_w = _parse_optional_int(roi_w, "roi_w")
    parsed_roi_h = _parse_optional_int(roi_h, "roi_h")
    manual_roi = _parse_manual_roi(parsed_roi_x, parsed_roi_y, parsed_roi_w, parsed_roi_h)

    session_id = f"rt_{new_job_id()}"
    upload_path = UPLOADS_DIR / f"{session_id}.mp4"

    upload_path.parent.mkdir(parents=True, exist_ok=True)
    with upload_path.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    _set_rt(
        session_id,
        status="pending",
        progress=0,
        message="Queued",
        frame_jpeg_b64="",
        stats={},
        artifacts={},
        video_url=f"/data/uploads/{session_id}.mp4",
    )

    executor.submit(_run_realtime, session_id, upload_path, manual_roi)
    return JSONResponse(
        content={
            "session_id": session_id,
            "status": "pending",
            "viewer_url": f"/realtime/{session_id}",
        }
    )


@router.get("/api/realtime/sessions/{session_id}")
def get_realtime_session(session_id: str):
    return JSONResponse(content=_get_rt(session_id))


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    job = _get_job(job_id)
    return JobStatusResponse(
        job_id=job_id,
        status=str(job.get("status", "pending")),
        progress=int(job.get("progress", 0)),
        message=str(job.get("message", "")),
    )


@router.get("/api/jobs/{job_id}/result")
def get_job_result(job_id: str):
    job = _get_job(job_id)
    status = str(job.get("status", "pending"))

    if status != "done":
        return JSONResponse(
            status_code=200,
            content={
                "job_id": job_id,
                "status": status,
                "progress": int(job.get("progress", 0)),
                "message": str(job.get("message", "")),
            },
        )

    result = job.get("result", {}) or {}
    response = JobResultResponse(
        job_id=job_id,
        status="done",
        metrics=result.get("metrics", {}),
        quality_grade=result.get("quality_grade", ""),
        reasons=result.get("reasons", []),
        thresholds_used=result.get("thresholds_used", {}),
        artifacts=result.get("artifacts", {}),
    )
    payload = response.model_dump()
    payload["speed_series_px_s"] = result.get("speed_series_px_s", [])
    payload["speed_series_m_s"] = result.get("speed_series_m_s", None)
    payload["scale"] = result.get("scale", {})
    return JSONResponse(content=payload)


@router.get("/api/jobs/{job_id}/report")
def download_report(job_id: str):
    job = _get_job(job_id)
    if str(job.get("status")) != "done":
        raise HTTPException(status_code=400, detail="Report is available only when job is done")

    pdf_path = REPORTS_DIR / f"{job_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    return FileResponse(path=pdf_path, media_type="application/pdf", filename=f"{job_id}.pdf")


@router.get("/api/jobs/{job_id}/overlay_video")
def download_overlay_video(job_id: str):
    job = _get_job(job_id)
    if str(job.get("status")) != "done":
        raise HTTPException(status_code=400, detail="Overlay is available only when job is done")

    overlay_path = OUTPUTS_DIR / job_id / "overlay.mp4"
    if not overlay_path.exists():
        raise HTTPException(status_code=404, detail="Overlay video not found")

    return FileResponse(path=overlay_path, media_type="video/mp4", filename=f"{job_id}_overlay.mp4")


@router.get("/")
def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/realtime")
def realtime_page(request: Request):
    return templates.TemplateResponse("realtime.html", {"request": request})


@router.get("/realtime/{session_id}")
def realtime_view_page(request: Request, session_id: str):
    return templates.TemplateResponse("realtime_session.html", {"request": request, "session_id": session_id})


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    return templates.TemplateResponse("job.html", {"request": request, "job_id": job_id})
