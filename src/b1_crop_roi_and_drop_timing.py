import os
import argparse
import cv2
import numpy as np
import pandas as pd
import torch
from ultralytics import YOLO
from pathlib import Path

# =========================
# 설정
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIDEO_PATH   = PROJECT_ROOT / "data" / "raw" / "train_videos" / "slump1.mp4"
MODEL_PATH = PROJECT_ROOT / "runs" / "detect" / "train" / "weights" / "best.pt"

CONF_THRES  = 0.25
DEVICE      = 0 if torch.cuda.is_available() else "cpu"

# 스무딩(박스 흔들림 완화)
EMA_ALPHA   = 0.25
HOLD_FRAMES = 10

# Drop timing(낙하 시작) 검출 파라미터
# - ROI 내부에서 "움직임(Optical Flow magnitude)"가 평소보다 확 커지는 지점을 drop start로 본다.
FLOW_WIN_SEC      = 1.0      # 기준선(이전 평균) 계산에 쓰는 윈도우 길이(초)
FLOW_Z_THRES      = 3.0      # (현재-기준)/표준편차 가 이 값 이상이면 이벤트
FLOW_ABS_THRES    = 0.6      # 절대값(픽셀 기준) 하한. 너무 작은 변화는 무시
MIN_EVENT_GAP_SEC = 2.0      # 이벤트 간 최소 간격(초) - 중복 검출 방지

# =========================
# 유틸
# =========================
def ema_update(prev_box, new_box, alpha):
    if prev_box is None:
        return new_box
    prev = np.array(prev_box, dtype=np.float32)
    new  = np.array(new_box, dtype=np.float32)
    out  = (1 - alpha) * prev + alpha * new
    return tuple(out.tolist())

def pick_two_chutes_xyxy(dets, frame_w):
    left_candidates = []
    right_candidates = []
    for x1, y1, x2, y2, conf in dets:
        cx = 0.5 * (x1 + x2)
        if cx < frame_w / 2:
            left_candidates.append((x1, y1, x2, y2, conf))
        else:
            right_candidates.append((x1, y1, x2, y2, conf))

    left_box = None
    right_box = None
    if left_candidates:
        best = max(left_candidates, key=lambda t: t[4])
        left_box = best[:4]
    if right_candidates:
        best = max(right_candidates, key=lambda t: t[4])
        right_box = best[:4]
    return left_box, right_box

def safe_crop(frame, box):
    """box: (x1,y1,x2,y2) -> crop 이미지 + 정수 좌표 반환"""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(int, box)
    x1 = max(0, min(w-1, x1))
    x2 = max(0, min(w,   x2))
    y1 = max(0, min(h-1, y1))
    y2 = max(0, min(h,   y2))
    if x2 <= x1 or y2 <= y1:
        return None, (x1,y1,x2,y2)
    return frame[y1:y2, x1:x2], (x1,y1,x2,y2)

# =========================
# Drop timing helper
# =========================
class DropDetector:
    """
    ROI의 프레임 간 optical flow magnitude 평균을 기록하고
    기준선 대비 급상승하면 drop start로 판정한다.
    """
    def __init__(self, fps, flow_win_sec=1.0, z_thres=3.0, abs_thres=0.6, min_gap_sec=2.0):
        self.fps = fps
        self.win = max(3, int(flow_win_sec * fps))
        self.z_thres = z_thres
        self.abs_thres = abs_thres
        self.min_gap = int(min_gap_sec * fps)
        self.prev_gray = None
        self.history = []
        self.last_event_frame = -10**9

    def update(self, roi_bgr, frame_idx):
        if roi_bgr is None:
            self.prev_gray = None
            return None, 0.0

        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)

        event = None
        flow_mean = 0.0

        if self.prev_gray is not None and self.prev_gray.shape == gray.shape:
            flow = cv2.calcOpticalFlowFarneback(self.prev_gray, gray, None,
                                                0.5, 3, 15, 3, 5, 1.2, 0)
            mag, _ = cv2.cartToPolar(flow[...,0], flow[...,1])
            flow_mean = float(np.mean(mag))

            self.history.append(flow_mean)

            if len(self.history) > self.win:
                base = np.array(self.history[-self.win-1:-1], dtype=np.float32)
                mu = float(np.mean(base))
                sd = float(np.std(base) + 1e-6)
                z = (flow_mean - mu) / sd

                if (flow_mean >= self.abs_thres) and (z >= self.z_thres):
                    if frame_idx - self.last_event_frame >= self.min_gap:
                        self.last_event_frame = frame_idx
                        event = {
                            "frame": frame_idx,
                            "time_sec": frame_idx / self.fps,
                            "flow_mean": flow_mean,
                            "base_mean": mu,
                            "base_std": sd,
                            "z": z,
                        }

        self.prev_gray = gray
        return event, flow_mean

# =========================
# 메인
# =========================
def parse_args():
    ap = argparse.ArgumentParser(description="B1: ROI 크롭 저장 + drop timing 검출")
    ap.add_argument(
        "--out_root",
        default="",
        help='비우면 PROJECT_ROOT/"data"/"processed" 사용',
    )
    return ap.parse_args()

def main():
    args = parse_args()
    out_root = Path(args.out_root) if args.out_root else (PROJECT_ROOT / "data" / "processed")
    out_left_dir = out_root / "roi_left"
    out_right_dir = out_root / "roi_right"
    log_dir = out_root / "logs"
    out_left_dir.mkdir(parents=True, exist_ok=True)
    out_right_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(MODEL_PATH)

    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        raise FileNotFoundError(f"영상 열기 실패: {VIDEO_PATH}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    # 좌/우 박스 상태
    left_smoothed = None
    right_smoothed = None
    left_hold = 0
    right_hold = 0

    # drop detector
    drop_left  = DropDetector(fps, FLOW_WIN_SEC, FLOW_Z_THRES, FLOW_ABS_THRES, MIN_EVENT_GAP_SEC)
    drop_right = DropDetector(fps, FLOW_WIN_SEC, FLOW_Z_THRES, FLOW_ABS_THRES, MIN_EVENT_GAP_SEC)

    logs = []
    frame_idx = 0

    print("[INFO] B-1 시작: ROI 크롭 저장 + drop timing 검출")

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1

        # YOLO 추론
        results = model(frame, device=DEVICE, conf=CONF_THRES, verbose=False)
        boxes = results[0].boxes

        dets = []
        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            conf = boxes.conf.cpu().numpy()
            for (x1, y1, x2, y2), c in zip(xyxy, conf):
                dets.append((float(x1), float(y1), float(x2), float(y2), float(c)))

        left_new, right_new = pick_two_chutes_xyxy(dets, w)

        # 스무딩 + 홀드
        if left_new is not None:
            left_smoothed = ema_update(left_smoothed, left_new, EMA_ALPHA)
            left_hold = 0
        else:
            left_hold += 1
            if left_hold > HOLD_FRAMES:
                left_smoothed = None

        if right_new is not None:
            right_smoothed = ema_update(right_smoothed, right_new, EMA_ALPHA)
            right_hold = 0
        else:
            right_hold += 1
            if right_hold > HOLD_FRAMES:
                right_smoothed = None

        # ROI 크롭 저장(좌/우 각각)
        left_roi, left_xyxy = (None, None)
        right_roi, right_xyxy = (None, None)

        if left_smoothed is not None:
            left_roi, left_xyxy = safe_crop(frame, left_smoothed)
            if left_roi is not None:
                cv2.imwrite(str(out_left_dir / f"left_{frame_idx:06d}.jpg"), left_roi)

        if right_smoothed is not None:
            right_roi, right_xyxy = safe_crop(frame, right_smoothed)
            if right_roi is not None:
                cv2.imwrite(str(out_right_dir / f"right_{frame_idx:06d}.jpg"), right_roi)

        # Drop timing 업데이트
        ev_l, flow_l = drop_left.update(left_roi, frame_idx)
        ev_r, flow_r = drop_right.update(right_roi, frame_idx)

        if ev_l is not None:
            print(f"[DROP-LEFT] frame={ev_l['frame']} time={ev_l['time_sec']:.2f}s z={ev_l['z']:.2f} flow={ev_l['flow_mean']:.3f}")
            logs.append({"side":"left", **ev_l})

        if ev_r is not None:
            print(f"[DROP-RIGHT] frame={ev_r['frame']} time={ev_r['time_sec']:.2f}s z={ev_r['z']:.2f} flow={ev_r['flow_mean']:.3f}")
            logs.append({"side":"right", **ev_r})

    cap.release()

    # 로그 저장
    df = pd.DataFrame(
        logs,
        columns=["side", "frame", "time_sec", "flow_mean", "base_mean", "base_std", "z"],
    )
    out_csv = log_dir / "b1_drop_events.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    print(f"[DONE] ROI saved to:")
    print(f"  - {out_left_dir}")
    print(f"  - {out_right_dir}")
    print(f"[DONE] Drop events saved: {out_csv}")
    print(f"[DONE] total events = {len(df)}")

if __name__ == "__main__":
    main()
