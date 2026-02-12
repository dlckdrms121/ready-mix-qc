# tools/video_freeze_report.py
# 핵심 변경:
# - read_fail을 "EOF(정상 종료)" vs "조기 실패"로 분리(eof_reached, fail_early)
# - segments_count>0이면 첫 세그먼트(start/end/len)를 콘솔에 요약 출력
# - 샘플 프레임:
#   (1) 고정 시각(at_XXXXms.jpg)
#   (2) 첫 프리즈 세그먼트 경계(before/start/mid/end).jpg

import argparse
import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class Segment:
    start_frame: int
    end_frame: int
    start_s: float
    end_s: float
    length_s: float
    reason: str  # "hash" or "motion"
    metric: float


def md5_small_gray(frame_bgr: np.ndarray, size: Tuple[int, int] = (64, 36)) -> str:
    small = cv2.resize(frame_bgr, size, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.md5(gray.tobytes()).hexdigest()


def safe_fps(cap: cv2.VideoCapture, default: float = 30.0) -> float:
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0 or np.isnan(fps):
        return default
    return float(fps)


def write_csv(path: Path, header: List[str], rows: List[List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def grab_frame_at_msec(video_path: Path, t_msec: int) -> Optional[np.ndarray]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, float(t_msec))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    return frame


def grab_frame_at_frame(video_path: Path, frame_idx: int) -> Optional[np.ndarray]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, float(frame_idx))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    return frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video_path", required=True, help="분석할 영상 경로(절대/상대)")
    ap.add_argument("--out_root", default="data\\processed\\freeze_reports", help="리포트 저장 루트")
    ap.add_argument("--max_seconds", type=float, default=0.0, help="0이면 전체, 아니면 앞부분만(초)")
    ap.add_argument("--freeze_seconds", type=float, default=1.0, help="이 초 이상 연속 정지면 프리즈로 간주")
    ap.add_argument("--motion_eps", type=float, default=0.6, help="mean abs diff <= eps면 정지(0~255)")
    ap.add_argument("--save_samples", type=int, default=1, help="1이면 샘플 프레임 저장")
    ap.add_argument("--sample_times", default="0,2,4,6,8,10,12,15", help="샘플 저장 시각(초) 콤마")
    args = ap.parse_args()

    vp = Path(args.video_path)
    if not vp.is_absolute():
        vp = (Path.cwd() / vp).resolve()
    if not vp.exists():
        print(f"[FAIL] VIDEO_NOT_FOUND: {vp}")
        return

    cap = cv2.VideoCapture(str(vp))
    if not cap.isOpened():
        print(f"[FAIL] CAPTURE_OPEN_FAIL: {vp}")
        return

    fps = safe_fps(cap, default=30.0)
    frame_count_meta = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_est = (frame_count_meta / fps) if (frame_count_meta > 0 and fps > 0) else 0.0

    max_frames = 10**12
    if args.max_seconds and args.max_seconds > 0:
        max_frames = int(max(1, round(fps * args.max_seconds)))

    thr = int(max(1, round(fps * args.freeze_seconds)))

    prev_hash = None
    prev_gray = None

    still_run = 0
    run_start = None
    run_reason = None
    run_metric_last = 0.0

    segments: List[Segment] = []
    unique_hashes = set()

    frames_read = 0
    read_fail = False
    last_good_pos_msec = -1.0

    while frames_read < max_frames:
        ok, frame = cap.read()
        if not ok or frame is None:
            read_fail = True
            break

        pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0
        last_good_pos_msec = pos_msec

        h = md5_small_gray(frame)
        unique_hashes.add(h)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        is_still = False
        reason = ""
        metric = 0.0

        if prev_hash is not None and h == prev_hash:
            is_still = True
            reason = "hash"
            metric = 0.0

        if prev_gray is not None:
            diff = cv2.absdiff(gray, prev_gray)
            mad = float(np.mean(diff))
            if mad <= args.motion_eps:
                is_still = True
                reason = "motion"
                metric = mad

        if is_still:
            if still_run == 0:
                run_start = max(0, frames_read - 1)
            still_run += 1
            run_reason = reason
            run_metric_last = metric
        else:
            if still_run >= thr and run_start is not None and run_reason is not None:
                start_f = run_start
                end_f = frames_read - 1
                start_s = start_f / fps
                end_s = end_f / fps
                segments.append(Segment(start_f, end_f, start_s, end_s, (end_s - start_s), run_reason, run_metric_last))
            still_run = 0
            run_start = None
            run_reason = None
            run_metric_last = 0.0

        prev_hash = h
        prev_gray = gray
        frames_read += 1

    if still_run >= thr and run_start is not None and run_reason is not None:
        start_f = run_start
        end_f = frames_read - 1
        start_s = start_f / fps
        end_s = end_f / fps
        segments.append(Segment(start_f, end_f, start_s, end_s, (end_s - start_s), run_reason, run_metric_last))

    cap.release()

    # EOF vs 조기 실패 분리
    eof_reached = 0
    fail_early = 0
    if read_fail:
        if frame_count_meta > 0 and frames_read >= max(0, frame_count_meta - 1):
            eof_reached = 1
        else:
            # max_seconds로 앞부분만 스캔하다가 read_fail이면 조기 실패로 볼 여지가 큼
            fail_early = 1 if (args.max_seconds and args.max_seconds > 0) else 1

    unique_ratio = (len(unique_hashes) / frames_read) if frames_read > 0 else 0.0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_root) / f"freeze_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    report_header = [
        "video_path", "fps_used", "frame_count_meta", "duration_est_s",
        "frames_read", "max_seconds", "freeze_seconds", "thr_frames",
        "segments_count", "unique_hashes", "unique_ratio",
        "motion_eps", "read_fail", "eof_reached", "fail_early", "last_good_pos_msec"
    ]
    report_rows = [[
        str(vp), f"{fps:.3f}", str(frame_count_meta), f"{duration_est:.3f}",
        str(frames_read), f"{args.max_seconds:.3f}", f"{args.freeze_seconds:.3f}", str(thr),
        str(len(segments)), str(len(unique_hashes)), f"{unique_ratio:.6f}",
        f"{args.motion_eps:.3f}", str(int(read_fail)), str(eof_reached), str(fail_early), f"{last_good_pos_msec:.3f}",
    ]]
    write_csv(out_dir / "report.csv", report_header, report_rows)

    seg_header = ["idx", "start_frame", "end_frame", "start_s", "end_s", "length_s", "reason", "metric"]
    seg_rows = []
    for i, s in enumerate(segments):
        seg_rows.append([str(i), str(s.start_frame), str(s.end_frame),
                         f"{s.start_s:.3f}", f"{s.end_s:.3f}", f"{s.length_s:.3f}",
                         s.reason, f"{s.metric:.6f}"])
    write_csv(out_dir / "segments.csv", seg_header, seg_rows)

    # debug.txt
    with (out_dir / "debug.txt").open("w", encoding="utf-8") as f:
        f.write(f"video={vp}\n")
        f.write(f"fps={fps}\n")
        f.write(f"frame_count_meta={frame_count_meta}\n")
        f.write(f"duration_est={duration_est}\n")
        f.write(f"frames_read={frames_read}\n")
        f.write(f"read_fail={read_fail}\n")
        f.write(f"eof_reached={eof_reached}\n")
        f.write(f"fail_early={fail_early}\n")
        f.write(f"last_good_pos_msec={last_good_pos_msec}\n")
        f.write(f"segments_count={len(segments)}\n")

    # 샘플 저장
    saved_n = 0
    if args.save_samples == 1:
        samples_dir = out_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        # (1) 고정 시각 샘플
        times = []
        for x in args.sample_times.split(","):
            x = x.strip()
            if not x:
                continue
            try:
                times.append(float(x))
            except ValueError:
                pass
        if not times:
            times = [0, 2, 4, 6, 8, 10]

        for t in times:
            t_msec = int(round(t * 1000.0))
            fr = grab_frame_at_msec(vp, t_msec)
            out_jpg = samples_dir / f"at_{t_msec:06d}ms.jpg"
            if fr is not None:
                cv2.imwrite(str(out_jpg), fr)
                saved_n += 1

        # (2) 첫 세그먼트 경계 샘플
        if len(segments) > 0:
            s0 = segments[0]
            idxs = {
                "seg_before": max(0, s0.start_frame - 1),
                "seg_start": s0.start_frame,
                "seg_mid": int(round((s0.start_frame + s0.end_frame) * 0.5)),
                "seg_end": s0.end_frame,
            }
            for name, fi in idxs.items():
                fr = grab_frame_at_frame(vp, fi)
                if fr is not None:
                    out_jpg = samples_dir / f"{name}_f{fi:06d}.jpg"
                    cv2.imwrite(str(out_jpg), fr)
                    saved_n += 1

    # 콘솔 요약
    print(f"[DONE] out_dir        = {out_dir}")
    print(f"[DONE] report.csv     = {out_dir / 'report.csv'}")
    print(f"[DONE] segments.csv   = {out_dir / 'segments.csv'}")
    print(f"[DONE] debug.txt      = {out_dir / 'debug.txt'}")
    if args.save_samples == 1:
        print(f"[DONE] samples/       = {out_dir / 'samples'} (saved={saved_n})")

    print(f"[INFO] read_fail={int(read_fail)} eof_reached={eof_reached} fail_early={fail_early} frames_read={frames_read} meta_frames={frame_count_meta} fps={fps:.3f} segments_count={len(segments)}")
    if len(segments) > 0:
        s0 = segments[0]
        print(f"[INFO] first_segment: start_s={s0.start_s:.3f} end_s={s0.end_s:.3f} len_s={s0.length_s:.3f} reason={s0.reason}")

if __name__ == "__main__":
    main()

