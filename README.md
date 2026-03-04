# SlumpGuard Quality Web App

FastAPI web app for ready-mix concrete pouring quality analysis.
The app reuses existing legacy logic in this repo:

- Chute detection/ROI smoothing: `src/b1_crop_roi_and_drop_timing.py`
- Optical-flow speed estimation: `src/b0_track_and_smooth_batch_10videos.py` (`lk_flow_score`)
- Motion fallback score: `src/b2_make_10s_clips.py` (`motion_score`)

## 1) Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open: `http://localhost:8000`
- Realtime page: `http://localhost:8000/realtime`

## 3) API usage example

```bash
curl -X POST "http://localhost:8000/api/jobs" \
  -F "file=@data/raw/train_videos/slump1.mp4" \
  -F "roi_x=100" -F "roi_y=120" -F "roi_w=320" -F "roi_h=260"
```

Then check status:

```bash
curl "http://localhost:8000/api/jobs/<job_id>"
curl "http://localhost:8000/api/jobs/<job_id>/result"
curl -OJ "http://localhost:8000/api/jobs/<job_id>/report"
```

Realtime session example:

```bash
curl -X POST "http://localhost:8000/api/realtime/sessions" \
  -F "file=@data/raw/train_videos/slump1.mp4"

curl "http://localhost:8000/api/realtime/sessions/<session_id>"
```

## 4) Output locations

- Upload video: `data/uploads/{job_id}.mp4`
- Job outputs: `data/outputs/{job_id}/`
  - `result.json`
  - `speed_plot.png`
  - `overlay.mp4`
  - `snapshots/*.jpg`
- PDF report: `data/reports/{job_id}.pdf`

## 5) Threshold and smoothing config

Edit `configs/quality_thresholds.yaml`:

- `detection`: model path, confidence, ROI smoothing
- `speed`: smoothing method/window
- `quality`: thresholds for avg speed, stop count, cv, coverage

## 6) Web flow

1. Upload mp4 at `/`
2. Job is created (`POST /api/jobs`)
3. Background analysis runs
4. Monitor at `/jobs/{job_id}`
5. Download report/overlay and inspect JSON result

Realtime flow:

1. Open `/realtime`
2. Upload mp4 and start realtime session
3. Live frame + ROI detection + speed + pouring time update on `/realtime/{session_id}`
