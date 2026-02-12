# tools/video_ab_compare.py
# 목적:
# 1) 원본(raw) vs 가공(processed)에서 "프리즈(정지) 구간" 자동 탐지
# 2) 동일 시각(0,2,4,6,8,10,...) 프레임을 side-by-side 이미지로 저장
# 3) 가공본을 OpenCV로 재인코딩(re-encode)해서 "컨테이너/코덱 문제인지" 빠르게 분기
#
# 변경점(중요):
# - 원본/가공본 파일명이 달라도 됨: --raw_path / --proc_path 로 직접 지정 가능
# - 기존 방식도 유지: --raw_stem/--raw_dir, --proc_stem/--proc_dir 로 탐색 가능

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}


def md5_small_gray(frame_bgr: np.ndarray, size: Tuple[int, int] = (64, 36)) -> str:
    small = cv2.resize(frame_bgr, size, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.md5(gray.tobytes()).hexdigest()


def safe_fps(cap: cv2.VideoCapture, default: float = 30.0) -> float:
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0 or np.isnan(fps):
        return default
    return float(fps)


def find_by_stem(root: Path, stem: str) -> Optional[Path]:
    if not root.exists():
        return None
    cands = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.stem.lower() == stem.lower():
            cands.append(p)
    if not cands:
        return None
    # 가장 큰 파일(대개 본 영상)
    return max(cands, key=lambda x: x.stat().st_size)


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


@dataclass
class FreezeInfo:
    detected: bool
    start_s: Optional[float]
    length_s: Optional[float]
    fps_used: float
    notes: str


def detect_freeze(video_path: Path, max_seconds: float, freeze_seconds: float) -> FreezeInfo:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return FreezeInfo(False, None, None, 0.0, "CAPTURE_OPEN_FAIL")

    fps = safe_fps(cap, default=30.0)
    max_frames = int(max(1, round(fps * max_seconds)))
    thr = int(max(1, round(fps * freeze_seconds)))

    prev_h = None
    same_count = 0
    first_same_idx = None

    freeze_detected = False
    freeze_start_s = None
    freeze_len_s = None

    read_frames = 0
    read_fail = False

    for idx in range(max_frames):
        ok, frame = cap.read()
        if not ok or frame is None:
            read_fail = True
            break

        read_frames += 1
        h = md5_small_gray(frame)

        if prev_h is not None and h == prev_h:
            same_count += 1
            if first_same_idx is None:
                first_same_idx = idx - 1
        else:
            if same_count >= thr and not freeze_detected:
                freeze_detected = True
                start_idx = first_same_idx if first_same_idx is not None else (idx - same_count)
                freeze_start_s = start_idx / fps
                freeze_len_s = same_count / fps
            same_count = 0
            first_same_idx = None

        prev_h = h

    if same_count >= thr and not freeze_detected:
        freeze_detected = True
        start_idx = first_same_idx if first_same_idx is not None else max(0, read_frames - same_count)
        freeze_start_s = start_idx / fps
        freeze_len_s = same_count / fps

    cap.release()

    notes = []
    if read_fail:
        notes.append("READ_FAIL_BEFORE_MAX_SECONDS")
    notes.append(f"read_frames={read_frames}")
    return FreezeInfo(
        detected=freeze_detected,
        start_s=freeze_start_s,
        length_s=freeze_len_s,
        fps_used=fps,
        notes=";".join(notes),
    )


def concat_side_by_side(a: Optional[np.ndarray], b: Optional[np.ndarray], r: Optional[np.ndarray]) -> np.ndarray:
    def ensure(img: Optional[np.ndarray]) -> np.ndarray:
        if img is None:
            return np.zeros((360, 640, 3), dtype=np.uint8)
        return img

    a = ensure(a)
    b = ensure(b)
    r = ensure(r)

    H = min(a.shape[0], b.shape[0], r.shape[0], 720)

    def resize_h(img, H):
        h, w = img.shape[:2]
        if h == H:
            return img
        new_w = int(round(w * (H / h)))
        return cv2.resize(img, (new_w, H), interpolation=cv2.INTER_AREA)

    a2 = resize_h(a, H)
    b2 = resize_h(b, H)
    r2 = resize_h(r, H)

    def put_label(img, text):
        out = img.copy()
        cv2.putText(out, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        return out

    a2 = put_label(a2, "A: RAW")
    b2 = put_label(b2, "B: PROCESSED")
    r2 = put_label(r2, "R: REENCODED(B)")
    return np.concatenate([a2, b2, r2], axis=1)


def reencode_video(in_path: Path, out_path: Path, max_seconds: float) -> Tuple[bool, str]:
    cap = cv2.VideoCapture(str(in_path))
    if not cap.isOpened():
        return False, "CAPTURE_OPEN_FAIL"

    fps = safe_fps(cap, default=30.0)
    ok, first = cap.read()
    if not ok or first is None:
        cap.release()
        return False, "NO_FRAME_READ"

    h, w = first.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
    if not writer.isOpened():
        cap.release()
        return False, "WRITER_OPEN_FAIL(mp4v)"

    writer.write(first)

    max_frames = int(max(1, round(fps * max_seconds))) if max_seconds > 0 else 10**9
    frames_written = 1

    while frames_written < max_frames:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if frame.shape[:2] != (h, w):
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        writer.write(frame)
        frames_written += 1

    cap.release()
    writer.release()
    return True, f"frames_written={frames_written};fps={fps}"


def write_csv(path: Path, rows: List[List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 엑셀 호환 필요 시 utf-8-sig :contentReference[oaicite:3]{index=3}
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)


def sanitize_name(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^\w\-\.]+", "_", s)
    return s[:80] if len(s) > 80 else s


def resolve_video_path(path_str: Optional[str], stem: Optional[str], root: Optional[str], label: str) -> Path:
    if path_str:
        p = Path(path_str)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            raise FileNotFoundError(f"{label}_PATH_NOT_FOUND: {p}")
        return p

    if (stem is None) or (root is None):
        raise ValueError(f"{label}_NEED_EITHER_PATH_OR_(STEM+DIR)")

    root_p = Path(root)
    p = find_by_stem(root_p, stem)
    if p is None:
        raise FileNotFoundError(f"{label}_STEM_NOT_FOUND: stem={stem} in {root_p}")
    return p


def main():
    ap = argparse.ArgumentParser()

    # 신규: 경로 직접 지정
    ap.add_argument("--raw_path", default="", help="원본 영상 경로(절대/상대). 예: data\\raw\\train_videos\\Slump1.mp4")
    ap.add_argument("--proc_path", default="", help="가공 영상 경로(절대/상대). 예: data\\processed\\...\\clip_right.mp4")

    # 기존: stem+dir 탐색(유지)
    ap.add_argument("--raw_stem", default="", help="원본 stem. 예: Slump1")
    ap.add_argument("--raw_dir", default="data\\raw\\train_videos", help="원본 폴더")
    ap.add_argument("--proc_stem", default="", help="가공 stem. 예: clip_right")
    ap.add_argument("--proc_dir", default="data\\processed", help="가공 폴더(배치 폴더까지 지정 권장)")

    ap.add_argument("--out_root", default="data\\processed\\ab_reports", help="리포트 저장 루트")
    ap.add_argument("--out_name", default="", help="출력 폴더명(비우면 자동 생성)")

    ap.add_argument("--sample_times", default="0,2,4,6,8,10,12,15", help="프레임 비교 시각(초) 콤마")
    ap.add_argument("--freeze_max_seconds", type=float, default=20.0, help="프리즈 탐지 검사 구간(초)")
    ap.add_argument("--freeze_seconds", type=float, default=1.0, help="이 초 이상 같은 프레임 반복이면 프리즈")
    ap.add_argument("--do_reencode", type=int, default=1, help="1이면 가공본 재인코딩 생성")
    ap.add_argument("--reencode_max_seconds", type=float, default=20.0, help="재인코딩 길이(초). 0이면 전체")
    args = ap.parse_args()

    # 입력 해석
    raw_path_str = args.raw_path.strip() or None
    proc_path_str = args.proc_path.strip() or None
    raw_stem = args.raw_stem.strip() or None
    proc_stem = args.proc_stem.strip() or None

    # 편의: stem을 비우고 경로도 비웠으면 기본값으로 raw_stem=Slump1 같은 자동 추정은 하지 않음(명시 필요)
    raw_path = resolve_video_path(raw_path_str, raw_stem, args.raw_dir, "RAW")
    proc_path = resolve_video_path(proc_path_str, proc_stem, args.proc_dir, "PRC")

    # 출력 폴더명
    if args.out_name.strip():
        name = sanitize_name(args.out_name)
    else:
        name = sanitize_name(f"{raw_path.stem}__vs__{proc_path.stem}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_root) / f"ab_{ts}" / name
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] RAW = {raw_path}")
    print(f"[INFO] PRC = {proc_path}")
    print(f"[INFO] OUT = {out_dir}")

    # 1) 프리즈 탐지
    raw_freeze = detect_freeze(raw_path, args.freeze_max_seconds, args.freeze_seconds)
    prc_freeze = detect_freeze(proc_path, args.freeze_max_seconds, args.freeze_seconds)

    print(f"[FREEZE][RAW] detected={int(raw_freeze.detected)} start={raw_freeze.start_s} len={raw_freeze.length_s} fps={raw_freeze.fps_used:.2f} note={raw_freeze.notes}")
    print(f"[FREEZE][PRC] detected={int(prc_freeze.detected)} start={prc_freeze.start_s} len={prc_freeze.length_s} fps={prc_freeze.fps_used:.2f} note={prc_freeze.notes}")

    # 2) 재인코딩(가공본) + 재인코딩본 프리즈 재검사
    reencoded_path = out_dir / "processed_reencoded.mp4"
    reenc_ok = False
    reenc_note = "SKIP"
    reenc_freeze = FreezeInfo(False, None, None, 0.0, "SKIP")

    if args.do_reencode == 1:
        ok, note = reencode_video(proc_path, reencoded_path, args.reencode_max_seconds)
        reenc_ok = ok
        reenc_note = note
        print(f"[REENC] ok={int(ok)} out={reencoded_path} note={note}")
        if ok:
            reenc_freeze = detect_freeze(reencoded_path, args.freeze_max_seconds, args.freeze_seconds)
            print(f"[FREEZE][REN] detected={int(reenc_freeze.detected)} start={reenc_freeze.start_s} len={reenc_freeze.length_s} fps={reenc_freeze.fps_used:.2f} note={reenc_freeze.notes}")

    # 3) 동일 시각 프레임 side-by-side 저장 + compare.csv
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

    csv_rows = [["t_s", "raw_ok", "proc_ok", "reenc_ok", "raw_hash", "proc_hash", "reenc_hash", "raw_frame", "proc_frame", "reenc_frame", "sbs_frame"]]

    for t in sample_times_s:
        t_msec = int(round(t * 1000.0))
        raw_fr = grab_frame_at_msec(raw_path, t_msec)
        prc_fr = grab_frame_at_msec(proc_path, t_msec)
        ren_fr = grab_frame_at_msec(reencoded_path, t_msec) if reenc_ok else None

        raw_ok = raw_fr is not None
        prc_ok = prc_fr is not None
        ren_ok = ren_fr is not None

        raw_hash = md5_small_gray(raw_fr) if raw_ok else ""
        prc_hash = md5_small_gray(prc_fr) if prc_ok else ""
        ren_hash = md5_small_gray(ren_fr) if ren_ok else ""

        raw_fn = f"raw_t{t_msec:06d}ms.jpg"
        prc_fn = f"proc_t{t_msec:06d}ms.jpg"
        ren_fn = f"reenc_t{t_msec:06d}ms.jpg"
        sbs_fn = f"sbs_t{t_msec:06d}ms.jpg"

        if raw_ok:
            cv2.imwrite(str(frames_dir / raw_fn), raw_fr)
        if prc_ok:
            cv2.imwrite(str(frames_dir / prc_fn), prc_fr)
        if ren_ok:
            cv2.imwrite(str(frames_dir / ren_fn), ren_fr)

        sbs = concat_side_by_side(raw_fr, prc_fr, ren_fr)
        cv2.imwrite(str(frames_dir / sbs_fn), sbs)

        csv_rows.append([
            f"{t:.3f}",
            str(int(raw_ok)),
            str(int(prc_ok)),
            str(int(ren_ok)),
            raw_hash,
            prc_hash,
            ren_hash,
            raw_fn if raw_ok else "",
            prc_fn if prc_ok else "",
            ren_fn if ren_ok else "",
            sbs_fn,
        ])

    write_csv(out_dir / "compare.csv", csv_rows)

    # 4) summary.txt
    with (out_dir / "summary.txt").open("w", encoding="utf-8") as f:
        f.write(f"RAW={raw_path}\n")
        f.write(f"PROCESSED={proc_path}\n")
        f.write(f"OUT={out_dir}\n\n")

        f.write("[FREEZE_RAW]\n")
        f.write(f"detected={int(raw_freeze.detected)} start_s={raw_freeze.start_s} len_s={raw_freeze.length_s} fps={raw_freeze.fps_used} note={raw_freeze.notes}\n\n")

        f.write("[FREEZE_PROCESSED]\n")
        f.write(f"detected={int(prc_freeze.detected)} start_s={prc_freeze.start_s} len_s={prc_freeze.length_s} fps={prc_freeze.fps_used} note={prc_freeze.notes}\n\n")

        f.write("[REENCODE]\n")
        f.write(f"enabled={args.do_reencode} ok={int(reenc_ok)} out={reencoded_path} note={reenc_note}\n\n")

        f.write("[FREEZE_REENCODED]\n")
        f.write(f"detected={int(reenc_freeze.detected)} start_s={reenc_freeze.start_s} len_s={reenc_freeze.length_s} fps={reenc_freeze.fps_used} note={reenc_freeze.notes}\n")

    print(f"[DONE] compare.csv = {out_dir / 'compare.csv'}")
    print(f"[DONE] frames/     = {frames_dir}")
    if reenc_ok:
        print(f"[DONE] reencoded   = {reencoded_path}")


if __name__ == "__main__":
    main()
