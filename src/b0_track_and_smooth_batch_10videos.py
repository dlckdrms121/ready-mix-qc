import os
from pathlib import Path
from datetime import datetime
import cv2
import numpy as np
import pandas as pd
from ultralytics import YOLO


# -----------------------------
# 설정(필요하면 여기만 바꾸면 됨)
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_TRAIN_DIR = PROJECT_ROOT / "data" / "raw" / "train_videos"
RUN_ID  = datetime.now().strftime("%Y%m%d_%H%M%S")   # 예: 20260211_193012
OUT_ROOT = PROJECT_ROOT / "data" / "processed" / f"train_batch_{RUN_ID}"
LOG_DIR  = OUT_ROOT / "logs"

YOLO_WEIGHTS  = PROJECT_ROOT / "runs" / "detect" / "train" / "weights" / "best.pt"

ROI_SIZE = 512              # ROI 저장 크기 (512x512)
CONF_THRES = 0.25           # YOLO confidence threshold
IOU_THRES  = 0.45

# bbox smoothing (YOLO 박스 흔들림 줄이기)
SMOOTH_ALPHA = 0.7          # 0~1 (클수록 이전 박스를 더 믿음)

# Drop 이벤트 탐지(Optical Flow 기반)
BASELINE_SEC = 1.0          # 초반 1초를 "기준(정상 상태)"로 보고 평균/표준편차 계산
FLOW_ABS_THRES = 0.6        # flow 절대값 기준 (너무 안 잡히면 0.4~0.2로 낮추기)
FLOW_Z_THRES   = 3.0        # z-score 기준 (너무 안 잡히면 2.5~2.0로 낮추기)
CONSEC_FRAMES  = 3          # 연속 몇 프레임 이상 튀면 drop 시작으로 판정

# ROI crop에 여유(margin) 주기: 박스 주변을 약간 넓혀 자르기
MARGIN = 0.10               # 10%


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def sort_boxes_left_right(boxes_xyxy):
    """
    boxes_xyxy: (N,4) in xyxy
    x center 기준으로 왼쪽/오른쪽을 정렬
    """
    x_centers = (boxes_xyxy[:, 0] + boxes_xyxy[:, 2]) / 2.0
    order = np.argsort(x_centers)
    return boxes_xyxy[order]


def smooth_box(prev, new, alpha=0.7):
    """
    prev, new: (4,) xyxy
    EMA 형태로 박스를 부드럽게 만들기(smooth)
    """
    if prev is None:
        return new
    return alpha * prev + (1 - alpha) * new


def expand_box(x1, y1, x2, y2, w, h, margin=0.10):
    bw = x2 - x1
    bh = y2 - y1
    x1n = int(max(0, x1 - bw * margin))
    y1n = int(max(0, y1 - bh * margin))
    x2n = int(min(w - 1, x2 + bw * margin))
    y2n = int(min(h - 1, y2 + bh * margin))
    return x1n, y1n, x2n, y2n


def crop_and_resize(frame, box_xyxy, out_size=512):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box_xyxy.astype(int)
    x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, w, h, margin=MARGIN)
    roi = frame[y1:y2, x1:x2].copy()
    roi = cv2.resize(roi, (out_size, out_size), interpolation=cv2.INTER_AREA)
    return roi


def lk_flow_score(prev_gray, curr_gray):
    """
    Lucas-Kanade optical flow로 "움직임 크기" 점수 계산
    - 특징점 추출 -> 다음 프레임으로 추적 -> 이동량(magnitude) 중앙값을 점수로 사용
    """
    p0 = cv2.goodFeaturesToTrack(prev_gray, maxCorners=200, qualityLevel=0.01, minDistance=5)
    if p0 is None:
        return 0.0

    p1, st, err = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, p0, None,
                                           winSize=(21, 21), maxLevel=3,
                                           criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    if p1 is None or st is None:
        return 0.0

    good0 = p0[st.flatten() == 1]
    good1 = p1[st.flatten() == 1]
    if len(good0) < 10:
        return 0.0

    disp = (good1 - good0).reshape(-1, 2)   # (N,1,2) -> (N,2)
    mag = np.linalg.norm(disp, axis=1)      # 각 점의 이동거리
    return float(np.median(mag))



def detect_drop_frame(flow_series, fps):
    """
    flow_series: 프레임별 flow 점수 리스트
    - 초반 BASELINE_SEC 구간으로 평균/표준편차 -> z-score 계산
    - (flow > FLOW_ABS_THRES) & (z > FLOW_Z_THRES)가 CONSEC_FRAMES 연속이면 drop 시작으로 판정
    """
    n = len(flow_series)
    if n == 0:
        return None

    baseline_n = int(max(1, BASELINE_SEC * fps))
    base = np.array(flow_series[:baseline_n], dtype=np.float32)
    mu = float(base.mean())
    sd = float(base.std()) if float(base.std()) > 1e-6 else 1e-6

    consec = 0
    for i, v in enumerate(flow_series):
        z = (v - mu) / sd
        if (v >= FLOW_ABS_THRES) and (z >= FLOW_Z_THRES):
            consec += 1
            if consec >= CONSEC_FRAMES:
                return i - CONSEC_FRAMES + 1
        else:
            consec = 0
    return None


def main():
    ensure_dir(OUT_ROOT)
    print(f"[INFO] OUT_ROOT = {OUT_ROOT}")
    ensure_dir(LOG_DIR)

    if not YOLO_WEIGHTS.exists():
        print(f"[ERROR] YOLO weight not found: {YOLO_WEIGHTS}")
        print("        YOLO 학습이 끝난 폴더(runs/detect/train/weights/best.pt)를 확인하세요.")
        return

    model = YOLO(str(YOLO_WEIGHTS))

    video_files = sorted([p for p in RAW_TRAIN_DIR.glob("*.mp4")])
    print(f"[INFO] Found {len(video_files)} videos in: {RAW_TRAIN_DIR}")

    rows = []

    for vp in video_files:
        stem = vp.stem
        print(f"\n[INFO] Processing: {vp.name}")

        out_video_dir = OUT_ROOT / stem
        out_left_dir  = out_video_dir / "roi_left"
        out_right_dir = out_video_dir / "roi_right"
        ensure_dir(out_left_dir)
        ensure_dir(out_right_dir)

        cap = cv2.VideoCapture(str(vp))
        if not cap.isOpened():
            print(f"[WARN] cannot open video: {vp}")
            continue

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps is None or fps < 1:
            fps = 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        prev_left_box = None
        prev_right_box = None

        flow_left = []
        flow_right = []
        prev_left_gray = None
        prev_right_gray = None

        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            h, w = frame.shape[:2]

            # YOLO로 chute 박스 예측
            pred = model.predict(frame, conf=CONF_THRES, iou=IOU_THRES, verbose=False)
            boxes = pred[0].boxes

            left_box = None
            right_box = None

            if boxes is not None and boxes.xyxy is not None and len(boxes.xyxy) >= 2:
                b = boxes.xyxy.cpu().numpy()
                conf = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(b), dtype=np.float32)

                # confidence 상위 2개만 선택
                top2 = np.argsort(-conf)[:2]
                b = b[top2]

                # 그 다음 좌/우 정렬
                b = sort_boxes_left_right(b)
                left_box = b[0]
                right_box = b[1]


            # 박스 스무딩(=smooth): 프레임마다 박스가 살짝 흔들리는 걸 줄여 ROI를 안정화
            if left_box is not None:
                prev_left_box = smooth_box(prev_left_box, left_box, alpha=SMOOTH_ALPHA)
            if right_box is not None:
                prev_right_box = smooth_box(prev_right_box, right_box, alpha=SMOOTH_ALPHA)

            # 박스가 없는 프레임은 직전 박스를 사용(탐지 누락 대비)
            if prev_left_box is None or prev_right_box is None:
                frame_idx += 1
                continue

            roi_left = crop_and_resize(frame, prev_left_box, out_size=ROI_SIZE)
            roi_right = crop_and_resize(frame, prev_right_box, out_size=ROI_SIZE)

            # ROI 저장
            cv2.imwrite(str(out_left_dir / f"left_{frame_idx+1:06d}.jpg"), roi_left)
            cv2.imwrite(str(out_right_dir / f"right_{frame_idx+1:06d}.jpg"), roi_right)

            # optical flow 점수 계산
            left_gray = cv2.cvtColor(roi_left, cv2.COLOR_BGR2GRAY)
            right_gray = cv2.cvtColor(roi_right, cv2.COLOR_BGR2GRAY)

            if prev_left_gray is not None:
                flow_left.append(lk_flow_score(prev_left_gray, left_gray))
            if prev_right_gray is not None:
                flow_right.append(lk_flow_score(prev_right_gray, right_gray))

            prev_left_gray = left_gray
            prev_right_gray = right_gray

            frame_idx += 1

        cap.release()

        # flow는 prev 프레임이 있어야 계산되므로 길이가 (frames-1)일 수 있음
        drop_left = detect_drop_frame(flow_left, fps)   # 0-based on flow series
        drop_right = detect_drop_frame(flow_right, fps)

        # flow index를 원래 프레임 기준으로 맞추기(+1)
        drop_left_frame = (drop_left + 1) if drop_left is not None else None
        drop_right_frame = (drop_right + 1) if drop_right is not None else None

        rows.append({
            "video": vp.name,
            "fps": fps,
            "frames_saved": frame_idx,
            "drop_left_frame": drop_left_frame,
            "drop_right_frame": drop_right_frame,
            "drop_left_sec": (drop_left_frame / fps) if drop_left_frame else None,
            "drop_right_sec": (drop_right_frame / fps) if drop_right_frame else None,
        })

        print(f"[DONE] saved frames: {frame_idx} | drop_left={drop_left_frame} | drop_right={drop_right_frame}")

    df = pd.DataFrame(rows)
    out_csv = LOG_DIR / "b1_drop_events.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[INFO] drop events saved to: {out_csv}")
    print("[NEXT] 이제 이 CSV를 기반으로 'drop 시작 기준 10초 클립'을 만들면 됩니다.")


if __name__ == "__main__":
    main()
