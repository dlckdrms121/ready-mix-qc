from __future__ import annotations

import gc
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
executor = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("SLUMPGUARD_MAX_WORKERS", "1"))))

RUNTIME_STATE_DIR = OUTPUTS_DIR / "_runtime_state"
JOB_STATE_DIR = RUNTIME_STATE_DIR / "jobs"
RT_STATE_DIR = RUNTIME_STATE_DIR / "realtime"
for _d in (JOB_STATE_DIR, RT_STATE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}

_rt_lock = threading.Lock()
_rt_sessions: dict[str, dict[str, Any]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(kind: str, item_id: str) -> Path:
    root = JOB_STATE_DIR if kind == "job" else RT_STATE_DIR
    return root / f"{item_id}.json"


def _persist_state(kind: str, item_id: str, payload: dict[str, Any]) -> None:
    path = _state_path(kind, item_id)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _load_state(kind: str, item_id: str) -> dict[str, Any] | None:
    path = _state_path(kind, item_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        logger.exception("failed to read state file: %s", path)
    return None


def _recover_job_from_artifacts(job_id: str) -> dict[str, Any] | None:
    result_path = OUTPUTS_DIR / job_id / "result.json"
    if result_path.exists():
        try:
            with result_path.open("r", encoding="utf-8") as f:
                result_payload = json.load(f)
            return {
                "job_id": job_id,
                "status": "done",
                "progress": 100,
                "message": "Completed (recovered from artifacts)",
                "result": result_payload,
                "recovered": True,
                "updated_at": _utc_now_iso(),
            }
        except Exception:
            logger.exception("failed to recover job result: %s", result_path)

    upload_path = UPLOADS_DIR / f"{job_id}.mp4"
    if upload_path.exists():
        return {
            "job_id": job_id,
            "status": "failed",
            "progress": 100,
            "message": "Task state was lost after server restart. Please submit again.",
            "error": "state_lost_after_restart",
            "recovered": True,
            "updated_at": _utc_now_iso(),
        }
    return None


def _recover_rt_from_artifacts(session_id: str) -> dict[str, Any] | None:
    output_dir = OUTPUTS_DIR / session_id
    overlay_path = output_dir / "realtime_overlay.mp4"
    trace_path = output_dir / "realtime_trace.csv"
    upload_path = UPLOADS_DIR / f"{session_id}.mp4"

    if overlay_path.exists() and trace_path.exists():
        return {
            "session_id": session_id,
            "status": "done",
            "progress": 100,
            "message": "Realtime analysis completed (recovered from artifacts)",
            "frame_jpeg_b64": "",
            "stats": {},
            "artifacts": {
                "overlay_video": f"/data/outputs/{session_id}/realtime_overlay.mp4",
                "trace_csv": f"/data/outputs/{session_id}/realtime_trace.csv",
            },
            "video_url": f"/data/uploads/{session_id}.mp4",
            "recovered": True,
            "updated_at": _utc_now_iso(),
        }

    if upload_path.exists():
        return {
            "session_id": session_id,
            "status": "failed",
            "progress": 100,
            "message": "Realtime state was lost after server restart. Please submit again.",
            "error": "state_lost_after_restart",
            "frame_jpeg_b64": "",
            "stats": {},
            "artifacts": {},
            "video_url": f"/data/uploads/{session_id}.mp4",
            "recovered": True,
            "updated_at": _utc_now_iso(),
        }
    return None


def _reconcile_stale_states_on_boot() -> None:
    """
    If the process restarted while work was in-memory, pending/running tasks
    become orphaned. Mark them failed instead of returning 404 forever.
    """
    for root in (JOB_STATE_DIR, RT_STATE_DIR):
        kind = "job" if root == JOB_STATE_DIR else "rt"
        for p in root.glob("*.json"):
            try:
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                status = str(data.get("status", "")).lower()
                if status in {"pending", "running"}:
                    data["status"] = "failed"
                    data["progress"] = 100
                    data["error"] = "worker_terminated"
                    data["message"] = "Service restarted while processing. Please submit again."
                    data["updated_at"] = _utc_now_iso()
                    item_id = str(data.get("job_id") or data.get("session_id") or p.stem)
                    _persist_state("job" if kind == "job" else "rt", item_id, data)
            except Exception:
                logger.exception("failed to reconcile stale state: %s", p)


_reconcile_stale_states_on_boot()


def _set_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        if job_id not in _jobs:
            _jobs[job_id] = {"job_id": job_id, "created_at": _utc_now_iso()}
        _jobs[job_id].update(kwargs)
        _jobs[job_id]["updated_at"] = _utc_now_iso()
        snapshot = dict(_jobs[job_id])
    _persist_state("job", job_id, snapshot)


def _get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        loaded = _load_state("job", job_id)
        if loaded is not None:
            with _jobs_lock:
                _jobs[job_id] = loaded
            job = loaded
    if job is None:
        recovered = _recover_job_from_artifacts(job_id)
        if recovered is not None:
            with _jobs_lock:
                _jobs[job_id] = recovered
            _persist_state("job", job_id, recovered)
            job = recovered
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


def _set_rt(session_id: str, **kwargs: Any) -> None:
    with _rt_lock:
        if session_id not in _rt_sessions:
            _rt_sessions[session_id] = {"session_id": session_id, "created_at": _utc_now_iso()}
        _rt_sessions[session_id].update(kwargs)
        _rt_sessions[session_id]["updated_at"] = _utc_now_iso()
        snapshot = dict(_rt_sessions[session_id])
    _persist_state("rt", session_id, snapshot)


def _get_rt(session_id: str) -> dict[str, Any]:
    with _rt_lock:
        session = _rt_sessions.get(session_id)
    if session is None:
        loaded = _load_state("rt", session_id)
        if loaded is not None:
            with _rt_lock:
                _rt_sessions[session_id] = loaded
            session = loaded
    if session is None:
        recovered = _recover_rt_from_artifacts(session_id)
        if recovered is not None:
            with _rt_lock:
                _rt_sessions[session_id] = recovered
            _persist_state("rt", session_id, recovered)
            session = recovered
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
    finally:
        gc.collect()


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
    finally:
        gc.collect()


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


@router.get("/api/health")
def health_check():
    return JSONResponse(content={"status": "ok"})


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
