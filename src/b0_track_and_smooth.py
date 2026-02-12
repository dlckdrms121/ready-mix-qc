import os
import cv2
import numpy as np
from ultralytics import YOLO
from pathlib import Path

# =========================
# 설정
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIDEO_PATH   = os.path.join(PROJECT_ROOT, r"data\raw\train_videos\slump1.mp4")

# 학습된 모델 경로 (train, train2 등일 수 있으니 본인 폴더에 맞게 바꾸세요)
MODEL_PATH   = os.path.join(PROJECT_ROOT, r"runs\detect\train\weights\best.pt")

OUT_DIR      = os.path.join(PROJECT_ROOT, r"final_results")
os.makedirs(OUT_DIR, exist_ok=True)
OUT_VIDEO    = os.path.join(OUT_DIR, "B0_tracked_smoothed.mp4")

CONF_THRES   = 0.25
DEVICE       = 0

# 스무딩(EMA) 강도: 0에 가까울수록 더 부드럽고(느림), 1에 가까울수록 즉각 반응(덜 부드러움)
EMA_ALPHA    = 0.25

# 탐지 실패 시 직전 박스를 유지하는 프레임 수(너무 길면 드리프트가 생김)
HOLD_FRAMES  = 10

# =========================
# 유틸
# =========================
def iou_xyxy(a, b):
    """a,b: (x1,y1,x2,y2)"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    iw = max(0, inter_x2 - inter_x1)
    ih = max(0, inter_y2 - inter_y1)
    inter = iw * ih
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter + 1e-9
    return inter / union

def ema_update(prev_box, new_box, alpha):
    if prev_box is None:
        return new_box
    prev = np.array(prev_box, dtype=np.float32)
    new  = np.array(new_box, dtype=np.float32)
    out  = (1 - alpha) * prev + alpha * new
    return tuple(out.tolist())

def pick_two_chutes_xyxy(dets, frame_w):
    """
    dets: list of (x1,y1,x2,y2,conf)
    반환: left_box, right_box (없으면 None)
    전략:
      - 중심 x 기준으로 좌/우 분리
      - 각 그룹에서 conf 최고 1개 선택
    """
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

# =========================
# 메인
# =========================
def main():
    model = YOLO(MODEL_PATH)

    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        raise FileNotFoundError(f"영상 열기 실패: {VIDEO_PATH}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(OUT_VIDEO, fourcc, fps, (w, h))

    # 좌/우 박스 트랙 상태
    left_smoothed = None
    right_smoothed = None
    left_hold = 0
    right_hold = 0

    frame_idx = 0
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

        # 좌/우 chute 후보 선택
        left_new, right_new = pick_two_chutes_xyxy(dets, w)

        # 탐지 실패 처리 + EMA 스무딩
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

        # 시각화(박스)
        vis = frame.copy()

        def draw_box(box, color, label):
            if box is None:
                return
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
            cv2.putText(vis, label, (x1, max(0, y1-8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        draw_box(left_smoothed,  (0, 255, 255), "LEFT chute (smoothed)")
        draw_box(right_smoothed, (255, 255, 0), "RIGHT chute (smoothed)")

        cv2.putText(vis, f"frame={frame_idx}", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        writer.write(vis)

    cap.release()
    writer.release()
    print(f"[DONE] saved: {OUT_VIDEO}")

if __name__ == "__main__":
    main()
