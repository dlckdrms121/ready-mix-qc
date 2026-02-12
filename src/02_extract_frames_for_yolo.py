import os
from glob import glob
import cv2
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IN_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "train_videos")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "yolo_frames", "images")

# ✅ 초보자용 기본값: 1초에 1장 저장 (영상이 짧으니 이 정도면 충분)
SAVE_EVERY_SECONDS = 1.0

def ensure_dir(path: str):
    if not os.path.exists(path):
        os.makedirs(path)

def main():
    ensure_dir(OUT_DIR)

    video_paths = sorted(glob(os.path.join(IN_DIR, "*.mp4")))
    if not video_paths:
        print(f"[ERROR] mp4가 없습니다: {IN_DIR}")
        return

    total_saved = 0
    print(f"[INFO] videos: {len(video_paths)}")
    print(f"[INFO] output: {OUT_DIR}")
    print(f"[INFO] save every: {SAVE_EVERY_SECONDS} sec\n")

    for vp in video_paths:
        name = os.path.splitext(os.path.basename(vp))[0]  # slump1
        cap = cv2.VideoCapture(vp)
        if not cap.isOpened():
            print(f"[WARN] open fail: {vp}")
            continue

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps * SAVE_EVERY_SECONDS)))

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        saved_this = 0

        # 0, step, 2*step ... 프레임 위치로 이동하며 저장
        for fidx in range(0, frame_count, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
            ret, frame = cap.read()
            if not ret:
                continue

            out_name = f"{name}_f{fidx:06d}.jpg"
            out_path = os.path.join(OUT_DIR, out_name)

            # JPG로 저장
            cv2.imwrite(out_path, frame)
            saved_this += 1
            total_saved += 1

        cap.release()
        print(f"[OK] {os.path.basename(vp)}  fps={fps:.2f}  frames={frame_count}  saved={saved_this}")

    print(f"\n[DONE] total saved images = {total_saved}")
    print("다음 단계: 이 이미지들을 라벨링(Chute 박스)해서 YOLO 학습 데이터셋을 구성합니다.")

if __name__ == "__main__":
    main()
