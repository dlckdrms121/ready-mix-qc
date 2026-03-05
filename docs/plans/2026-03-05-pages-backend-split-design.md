# GitHub Pages + FastAPI Split Deployment Design

Date: 2026-03-05

## Goal
Deploy the UI on GitHub Pages while keeping model inference, video processing, and report generation on a real backend runtime.

## Problem Summary
The deployed Pages site was serving FastAPI templates as static HTML. That caused:
- broken absolute asset paths (`/static/...`) under `/ready-mix-qc/`
- no API runtime for `/api/...` endpoints
- functional mismatch versus the original app

## Selected Architecture
- Frontend: static pages in `docs/` (GitHub Pages source)
- Backend: existing FastAPI app (`app/main.py`, `app/api.py`) on Render/Railway/VM
- Communication: CORS-enabled HTTPS API calls from Pages origin to backend domain

## Key Design Decisions
1. Keep core analysis logic untouched.
   - Reuse existing detection/speed/quality/report modules as-is.
2. Add backend CORS middleware with configurable origins via `SLUMPGUARD_CORS_ORIGINS`.
3. Build Pages-specific frontend files under `docs/`.
   - no server-side routing assumptions
   - query-string based detail pages:
     - `job.html?job_id=...`
     - `realtime_session.html?session_id=...`
4. Add API base abstraction in frontend (`docs/static/common.js`).
   - supports `docs/config.js`, query override, localStorage fallback
   - normalizes artifact links returned as relative `/data/...` paths

## Error Handling and Compatibility
- API health check on frontend startup (`/api/health`)
- explicit missing `job_id` / `session_id` messages
- backend default CORS includes local dev and current GitHub origin
- `.nojekyll` added for predictable Pages static serving

## Verification
- `python -m py_compile app/main.py app/api.py core/*.py`
- `python -m unittest discover -s tests -p "test_*.py"`
- CORS preflight check confirms `access-control-allow-origin: https://dlckdrms121.github.io`

## Deployment Checklist
1. Deploy backend and set `SLUMPGUARD_CORS_ORIGINS`
2. Set GitHub Pages source to `/docs`
3. Set `docs/config.js` `apiBaseUrl` to backend URL
4. Validate upload, polling, realtime, report download on Pages URL
