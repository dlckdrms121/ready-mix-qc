# tools/video_sanity_check.py
# 목적: 원본/가공 영상의 "프리즈(정지) + 모션 + 샘플 프레임" sanity check 리포트 생성

import argparse
import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}


@dataclass
class VideoScanResult:
    video_path: str
    opened: bool
    fps: float
    frame_count: int
    duration_s_est: float
    read_ok: bool
    freeze_detected: bool
    freeze_start_s: Optional[float]
    freeze_len_s: Optional[float]
    avg_motion: Optional[float]
    notes: str


def md5_small_frame(frame_bgr: np.ndarray, size: Tuple[int, int] = (64, 36)) -> str:
    small = cv2.resize(frame_bgr, size, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.md5(gray.tobytes()).hexdigest()


def list_videos(root: Path) -> List[Path]:
    if not root.exists():
        return []
    vids = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    return sorted(vids)


def safe_get(cap: cv2.VideoCapture, prop: int, default: float = 0.0) -> float:
    v = cap.get(prop)
    if v is None or np.isnan(v) or v <= 0:
        return default
    return float(v)


def scan_video(
    video_path: Path,
    out_snap_dir: Path,
    max_seconds: float,
    freeze_seconds: float,
    sample_times_s: List[float],
) -> VideoScanResult:
    notes = []
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return VideoScanResult(str(video_path), False, 0.0, 0, 0.0, False, False, None, None, None, "CAPTURE_OPEN_FAIL")

    fps = safe_get(cap, cv2.CAP_PROP_FPS, default=0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s_est = (frame_count / fps) if (fps and frame_count) else 0.0

    freeze_frames_threshold = int(max(1, round((fps if fps > 0 else 30.0) * freeze_seconds)))
    max_frames = int(max(1, round((fps if fps > 0 else 30.0) * max_seconds)))

    prev_hash = None
    same_count = 0
    first_same_idx = None
    freeze_detected = False
    freeze_start_s = None
    freeze_len_s = None

    motion_sum = 0.0
    motion_n = 0
    prev_gray = None

    frame_idx = 0
    while frame_idx < max_frames:
        ok, frame = cap.read()
        if not ok or frame is None:
            notes.append(f"READ_FAIL_AT_FRAME_{frame_idx}")
            break

        h = md5_small_frame(frame)
        if prev_hash is not None and h == prev_hash:
            same_count += 1
            if first_same_idx is None:
                first_same_idx = frame_idx - 1
        else:
            if same_count >= freeze_frames_threshold and not freeze_detected:
                freeze_detected = True
                start_idx = first_same_idx if first_same_idx is not None else (frame_idx - same_count)
                denom = (fps if fps > 0 else 30.0)
                freeze_start_s = start_idx / denom
                freeze_len_s = same_count / denom
            same_count = 0
            first_same_idx = None
        prev_hash = h

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            diff = cv2.absdiff(gray, prev_gray)
            motion_sum += float(np.mean(diff))
            motion_n += 1
        prev_gray = gray

        frame_idx += 1

    if same_count >= freeze_frames_threshold and not freeze_detected:
        freeze_detected = True
        start_idx = first_same_idx if first_same_idx is not None else max(0, frame_idx - same_count)
        denom = (fps if fps > 0 else 30.0)
        freeze_start_s = start_idx / denom
        freeze_len_s = same_count / denom

    cap.release()
    avg_motion = (motion_sum / motion_n) if motion_n > 0 else None

    # 샘플 프레임 저장(시킹)
    out_snap_dir.mkdir(parents=True, exist_ok=True)
    cap2 = cv2.VideoCapture(str(video_path))
    if cap2.isOpened():
        for t in sample_times_s:
            cap2.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
            ok, fr = cap2.read()
            if not ok or fr is None:
                continue
            out_path = out_snap_dir / f"t{int(t*1000):06d}ms.jpg"
            cv2.imwrite(str(out_path), fr)
    cap2.release()

    read_ok = True if frame_idx > 0 else False
    if fps <= 0:
        notes.append("FPS_UNKNOWN(assume30)")
    if frame_count <= 0:
        notes.append("FRAME_COUNT_UNKNOWN")
    if not read_ok:
        notes.append("NO_FRAME_READ")

    return VideoScanResult(
        str(video_path),
        True,
        fps,
        frame_count,
        duration_s_est,
        read_ok,
        freeze_detected,
        freeze_start_s,
        freeze_len_s,
        avg_motion,
        ";".join(notes) if notes else "",
    )


def write_report_csv(report_path: Path, rows: List[VideoScanResult]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "video_path",
            "opened",
            "fps",
            "frame_count",
            "duration_s_est",
            "read_ok",
            "freeze_detected",
            "freeze_start_s",
            "freeze_len_s",
            "avg_motion",
            "notes",
        ])
        for r in rows:
            w.writerow([
                r.video_path,
                int(r.opened),
                f"{r.fps:.3f}",
                r.frame_count,
                f"{r.duration_s_est:.3f}",
                int(r.read_ok),
                int(r.freeze_detected),
                "" if r.freeze_start_s is None else f"{r.freeze_start_s:.3f}",
                "" if r.freeze_len_s is None else f"{r.freeze_len_s:.3f}",
                "" if r.avg_motion is None else f"{r.avg_motion:.3f}",
                r.notes,
            ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan_dir", action="append", required=True, help="스캔할 폴더(복수 지정 가능)")
    ap.add_argument("--report_root", default="data\\processed\\sanity_reports", help="리포트 저장 루트")
    ap.add_argument("--max_seconds", type=float, default=15.0, help="앞부분 검사 최대 초")
    ap.add_argument("--freeze_seconds", type=float, default=1.0, help="같은 프레임 반복이 이 초 이상이면 프리즈로 간주")
    ap.add_argument("--sample_times", default="0,2,4,6,8,10,12,15", help="샘플 프레임 시각(초) 콤마 구분")
    args = ap.parse_args()

    scan_dirs = [Path(p) for p in args.scan_dir]
    report_root = Path(args.report_root)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = report_root / f"sanity_{ts}"
    snaps_root = out_dir / "snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    sample_times_s = []
    for x in args.sample_times.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            sample_times_s.append(float(x))
        except ValueError:
            pass
    if not sample_times_s:
        sample_times_s = [0, 2, 4, 6, 8, 10]

    all_rows: List[VideoScanResult] = []
    print(f"[INFO] scan_dirs = {[str(d) for d in scan_dirs]}")
    print(f"[INFO] out_dir   = {out_dir}")

    for d in scan_dirs:
        vids = list_videos(d)
        print(f"[SCAN] {d} -> {len(vids)} videos")
        for vp in vids:
            try:
                rel = vp.relative_to(d)
            except Exception:
                rel = Path(vp.name)
            snap_dir = snaps_root / d.name / rel.parent / vp.stem

            r = scan_video(vp, snap_dir, args.max_seconds, args.freeze_seconds, sample_times_s)
            all_rows.append(r)

            tag = "OK"
            if (not r.opened) or (not r.read_ok):
                tag = "FAIL"
            elif r.freeze_detected:
                tag = "WARN_FREEZE"

            print(
                f"[{tag}] {vp.name} | fps={r.fps:.2f} frames={r.frame_count} dur_est={r.duration_s_est:.1f}s "
                f"| freeze={int(r.freeze_detected)} start={r.freeze_start_s} len={r.freeze_len_s} | motion={r.avg_motion}"
            )

    report_csv = out_dir / "report.csv"
    write_report_csv(report_csv, all_rows)

    print(f"[DONE] report_csv = {report_csv}")
    print(f"[DONE] snapshots  = {snaps_root}")


if __name__ == "__main__":
    main()
