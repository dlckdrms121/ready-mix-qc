from pathlib import Path
import argparse
import shutil
import cv2
import pandas as pd
import numpy as np
import json

# -----------------------------
# 기본 설정(필요하면 여기만 수정)
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

CLIP_SEC = 10.0             # drop 시작부터 몇 초를 자를지
MAKE_PREVIEW_MP4 = True     # 결과 mp4도 만들지(검사용)
CLEAN_CLIP_DIR = True       # clip_* 폴더를 매번 비우고 새로 만들지

# 중요: "끝나면 종료" 정책 (프리즈 방지)
PAD_TO_LENGTH = False       # 반드시 False (동일 프레임 반복 패딩 금지)

# CSV에 drop 프레임이 없을 때, ROI 이미지에서 자동으로 drop 시작을 감지
AUTO_DETECT_DROP_IF_MISSING = True
BASELINE_SEC = 1.0          # 첫 1초를 baseline으로 사용
MOTION_ABS_THRES = 1.2      # mean abs diff 절대 임계값(필요시 0.8~2.0로 조정)
MOTION_Z_THRES = 3.0        # z-score 임계값(필요시 2.0~4.0)
CONSEC_FRAMES = 3           # 연속 몇 프레임 이상이면 drop 시작으로 판정
ALREADY_DROP_MEDIAN_THRES = 1.2  # baseline 구간의 median이 이 이상이면 "이미 drop 중"으로 보고 start=1

# ROI 이미지 이름 패턴(당신 b1 코드 기준)
LEFT_PREFIX = "left_"
RIGHT_PREFIX = "right_"

# 최신 out_root 자동선택이 헷갈리면 여기 지정(비우면 최신 train_batch_* 자동)
OUT_ROOT_OVERRIDE = ""  # 예: r"C:\SmartConstruction_Project\SlumpGuard_Study\data\processed\train_batch_20260211_114821"


def pick_latest_out_root(processed_dir: Path) -> Path:
    cands = list(processed_dir.glob("train_batch_*"))
    if not cands:
        raise FileNotFoundError(f"train_batch_* 폴더를 찾지 못했습니다: {processed_dir}")
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


def safe_int(x):
    if pd.isna(x):
        return None
    try:
        return int(x)
    except Exception:
        return None


def load_sorted_images(folder: Path, prefix: str) -> list[Path]:
    # left_*.jpg / right_*.jpg만 로드 + 파일명 기준 정렬(ROI 생성 순서 보존)
    return sorted(folder.glob(f"{prefix}*.jpg"), key=lambda p: p.name)


def ensure_clean_dir(p: Path, video_dir: Path):
    """
    안전장치:
    - video_dir 안에 있는 폴더
    - 폴더명이 clip_ 로 시작
    위 조건을 만족할 때만 삭제 허용
    """
    p = p.resolve()
    video_dir = video_dir.resolve()

    if CLEAN_CLIP_DIR and p.exists():
        if (video_dir in p.parents) and p.name.startswith("clip_"):
            shutil.rmtree(p)
        else:
            raise RuntimeError(f"[SAFEGUARD] 삭제 위험 경로: {p}")

    p.mkdir(parents=True, exist_ok=True)


def motion_score(prev_bgr, curr_bgr, down_size=(128, 128)) -> float:
    prev = cv2.cvtColor(prev_bgr, cv2.COLOR_BGR2GRAY)
    curr = cv2.cvtColor(curr_bgr, cv2.COLOR_BGR2GRAY)

    if down_size is not None:
        prev = cv2.resize(prev, down_size, interpolation=cv2.INTER_AREA)
        curr = cv2.resize(curr, down_size, interpolation=cv2.INTER_AREA)

    # 조명 변화 영향 줄이기: 표준화(평균0, 표준편차1)
    prev = prev.astype(np.float32)
    curr = curr.astype(np.float32)
    prev = (prev - prev.mean()) / (prev.std() + 1e-6)
    curr = (curr - curr.mean()) / (curr.std() + 1e-6)

    diff = np.abs(prev - curr)
    return float(np.mean(diff))


def detect_drop_start_from_images(img_paths: list[Path], fps: float) -> tuple[int, dict]:
    """
    ROI 이미지 시퀀스(정렬된 Path 리스트)에서 drop 시작 프레임(1-based, ROI-order)을 추정.
    반환: (start_frame_1b, diag_dict)
    """
    diag = {
        "method": "auto_motion",
        "scores_n": 0,
        "base_mu": None,
        "base_sd": None,
        "base_median": None,
        "first_trigger_frame": None,
        "reason": "",
    }

    if not img_paths or len(img_paths) < 2:
        diag["reason"] = "too_few_images"
        return 1, diag

    # 연속 점수 계산(프레임1->2가 score[0])
    scores = []
    prev = cv2.imread(str(img_paths[0]))
    if prev is None:
        diag["reason"] = "first_image_read_fail"
        return 1, diag

    # 점수는 필요 이상 전부 계산해도 되지만, 빠르게 하려면 트리거 나오는 즉시 중단 가능
    # 여기서는 baseline 계산을 위해 최소 baseline_n 만큼은 만들고, 그 이후는 트리거 나오면 중단
    baseline_n = max(1, int(round(BASELINE_SEC * fps)))

    consec = 0
    run_start_idx = None

    for i in range(1, len(img_paths)):
        curr = cv2.imread(str(img_paths[i]))
        if curr is None:
            continue

        s = motion_score(prev, curr)
        scores.append(s)
        prev = curr

        # baseline 통계가 아직 없으면 계속 쌓기만
        if len(scores) < baseline_n:
            continue

        # baseline 계산(한 번만)
        if diag["base_mu"] is None:
            base = np.array(scores[:baseline_n], dtype=np.float32)
            mu = float(base.mean())
            sd = float(base.std())
            if sd < 1e-6:
                sd = 1e-6
            med = float(np.median(base))
            diag["base_mu"] = mu
            diag["base_sd"] = sd
            diag["base_median"] = med

            # 이미 drop 중이면 바로 시작(사용자 요구)
            if med >= ALREADY_DROP_MEDIAN_THRES:
                diag["reason"] = "already_dropping_at_start"
                diag["scores_n"] = len(scores)
                return 1, diag

        # 트리거 판정: abs + zscore
        z = (s - diag["base_mu"]) / diag["base_sd"]
        if (s >= MOTION_ABS_THRES) and (z >= MOTION_Z_THRES):
            if consec == 0:
                run_start_idx = len(scores) - 1  # score index
            consec += 1
            if consec >= CONSEC_FRAMES:
                # score index k는 (k+1)번째 프레임에 해당
                first_score_idx = run_start_idx
                start_frame = (first_score_idx + 1)  # 1-based (변화 직전부터 포함)
                diag["first_trigger_frame"] = start_frame
                diag["reason"] = "motion_abs+z_trigger"
                diag["scores_n"] = len(scores)
                return max(1, int(start_frame)), diag
        else:
            consec = 0
            run_start_idx = None

    # 여기까지 못 찾으면: 그래도 “이미 drop일 가능성”을 한 번 더 완화 체크(ABS만)
    if diag["base_mu"] is not None:
        consec2 = 0
        run2 = None
        for k, s in enumerate(scores):
            if s >= MOTION_ABS_THRES:
                if consec2 == 0:
                    run2 = k
                consec2 += 1
                if consec2 >= CONSEC_FRAMES:
                    start_frame = run2 + 1
                    diag["first_trigger_frame"] = start_frame
                    diag["reason"] = "motion_abs_only_trigger"
                    diag["scores_n"] = len(scores)
                    return max(1, int(start_frame)), diag
            else:
                consec2 = 0
                run2 = None

    diag["scores_n"] = len(scores)
    if diag["base_mu"] is None:
        diag["reason"] = "baseline_not_formed_default1"
    else:
       diag["reason"] = "not_found_default1"
    return 1, diag


def write_preview_mp4(img_paths, out_mp4: Path, fps: float):
    if not img_paths:
        return False

    first = cv2.imread(str(img_paths[0]))
    if first is None:
        return False

    h, w = first.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(out_mp4), fourcc, float(fps), (w, h))

    written = 0
    for p in img_paths:
        im = cv2.imread(str(p))
        if im is None:
            continue
        if im.shape[:2] != (h, w):
            im = cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA)
        vw.write(im)
        written += 1

    vw.release()
    return written > 0


def choose_side_and_start(df_row, fps, out_root: Path, stem: str):
    """
    우선순위:
    1) CSV drop 프레임이 있으면(좌/우 중 더 이른 것) 그걸 사용
    2) 둘 다 없으면(AUTO_DETECT_DROP_IF_MISSING=True) 좌/우 ROI에서 자동 감지 후 더 이른 쪽 선택
    3) 그래도 못 하면 left, start=1
    반환: (side, start_frame_1b, reason, diag_dict)
    """
    drop_l = safe_int(df_row.get("drop_left_frame"))
    drop_r = safe_int(df_row.get("drop_right_frame"))

    video_dir = out_root / stem
    roi_left_dir = video_dir / "roi_left"
    roi_right_dir = video_dir / "roi_right"

    diag = {"csv_drop_l": drop_l, "csv_drop_r": drop_r}

    # 1) CSV drop 우선
    if drop_l is not None or drop_r is not None:
        if drop_l is not None and drop_r is None:
            return "left", max(1, drop_l), "csv_drop_left", diag
        if drop_r is not None and drop_l is None:
            return "right", max(1, drop_r), "csv_drop_right", diag
        # 둘 다 있으면 더 이른 쪽
        if drop_l <= drop_r:
            return "left", max(1, drop_l), "csv_drop_left_earlier", diag
        return "right", max(1, drop_r), "csv_drop_right_earlier", diag

    # 2) CSV drop이 없으면 자동 감지
    if AUTO_DETECT_DROP_IF_MISSING:
        cand = []
        det_pack = {}  # 좌/우 모두 진단 저장

        if roi_left_dir.exists():
            imgs_l = load_sorted_images(roi_left_dir, LEFT_PREFIX)
            if len(imgs_l) >= 2:
                s_l, d_l = detect_drop_start_from_images(imgs_l, fps)
                det_pack["left"] = d_l
                cand.append(("left", s_l, d_l, len(imgs_l)))

        if roi_right_dir.exists():
            imgs_r = load_sorted_images(roi_right_dir, RIGHT_PREFIX)
            if len(imgs_r) >= 2:
                s_r, d_r = detect_drop_start_from_images(imgs_r, fps)
                det_pack["right"] = d_r
                cand.append(("right", s_r, d_r, len(imgs_r)))

        if cand:
            cand.sort(key=lambda x: (x[1], -x[3]))
            side, start, _det_diag, _n = cand[0]
            diag["auto_detect"] = det_pack
            return side, max(1, int(start)), "auto_detect_drop", diag

    # 3) fallback
    return "left", 1, "fallback_start1", diag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out_root", default="", help=r"예: C:\...\data\processed\train_batch_20260211_114821 (비우면 설정/최신 자동)")
    ap.add_argument("--clip_sec", type=float, default=CLIP_SEC)
    args = ap.parse_args()

    if args.out_root:
        out_root = Path(args.out_root)
    elif OUT_ROOT_OVERRIDE:
        out_root = Path(OUT_ROOT_OVERRIDE)
    else:
        out_root = pick_latest_out_root(PROCESSED_DIR)

    if not out_root.exists():
        raise FileNotFoundError(f"OUT_ROOT가 없습니다: {out_root}")

    log_csv = out_root / "logs" / "b1_drop_events.csv"
    print(f"[INFO] Using OUT_ROOT: {out_root}")
    print(f"[INFO] Reading CSV: {log_csv}")

    if not log_csv.exists():
        raise FileNotFoundError(f"b1_drop_events.csv가 없습니다: {log_csv}")

    df = pd.read_csv(log_csv, encoding="utf-8-sig")
    results = []

    for _, row in df.iterrows():
        video = row.get("video", "")
        if not isinstance(video, str) or not video:
            continue

        stem = Path(video).stem
        fps = float(row["fps"]) if ("fps" in row and not pd.isna(row["fps"])) else 30.0
        clip_len = int(round(float(args.clip_sec) * fps))

        # side/start 결정
        side, start_frame, reason, diag = choose_side_and_start(row, fps, out_root, stem)

        video_dir = out_root / stem
        roi_dir = video_dir / f"roi_{side}"
        if not roi_dir.exists():
            print(f"[WARN] ROI 폴더 없음: {roi_dir} (skip)")
            continue

        prefix = LEFT_PREFIX if side == "left" else RIGHT_PREFIX
        imgs = load_sorted_images(roi_dir, prefix)
        n = len(imgs)
        if n == 0:
            print(f"[WARN] ROI 이미지 0개: {roi_dir} (skip)")
            continue

        # start_frame(1-based, ROI-order)이 범위를 벗어나면 보정
        if start_frame < 1:
            start_frame = 1
        if start_frame > n:
            start_frame = n

        clip_dir = video_dir / f"clip_{side}"
        ensure_clean_dir(clip_dir, video_dir)

        # 클립 저장(끝나면 즉시 종료: 패딩 금지)
        saved_paths = []
        for i in range(clip_len):
            src_idx = (start_frame - 1) + i  # 0-based
            if src_idx >= n:
                if PAD_TO_LENGTH:
                    # 정책상 PAD_TO_LENGTH는 False가 정답이지만, 혹시 True로 바꾸더라도 동일 프레임 반복이 발생함(권장X)
                    src_idx = n - 1
                else:
                    break

            src_path = imgs[src_idx]
            dst_path = clip_dir / f"clip_{i+1:06d}.jpg"
            shutil.copy2(src_path, dst_path)
            saved_paths.append(dst_path)

        is_short = (len(saved_paths) < clip_len)

        # 검사용 mp4 생성(저장된 clip jpg만으로 만들기)
        out_mp4 = None
        mp4_ok = False
        if MAKE_PREVIEW_MP4 and saved_paths:
            out_mp4 = video_dir / f"clip_{side}.mp4"
            mp4_ok = write_preview_mp4(saved_paths, out_mp4, fps=fps)

        status = "ok" if not is_short else f"short({len(saved_paths)}/{clip_len})"

        print(
            f"[DONE] {video} | side={side} | start_frame={start_frame} | "
            f"roi_n={n} | clip={len(saved_paths)}/{clip_len} | {status} | reason={reason} | mp4={'ok' if mp4_ok else 'no'}"
        )

        results.append({
            "video": video,
            "stem": stem,
            "fps": fps,
            "clip_sec": float(args.clip_sec),
            "clip_len_frames": clip_len,

            "chosen_side": side,
            "start_frame": start_frame,
            "start_reason": reason,

            "roi_dir": str(roi_dir),
            "roi_images": n,

            "clip_frames_saved": len(saved_paths),
            "clip_status": status,
            "is_short": int(is_short),

            "clip_dir": str(clip_dir),
            "preview_mp4": str(out_mp4) if out_mp4 else "",
            "diag": json.dumps(diag, ensure_ascii=False),
        })

    out_index = out_root / "logs" / "b2_clip_index.csv"
    pd.DataFrame(results).to_csv(out_index, index=False, encoding="utf-8-sig")
    print(f"\n[INFO] Saved index: {out_index}")
    print("[NEXT] clip_*/ (drop 기준 10초, 끝나면 종료)로 데이터셋을 만들 수 있습니다.")


if __name__ == "__main__":
    main()
