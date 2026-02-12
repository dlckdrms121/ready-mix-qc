import os
import cv2
from glob import glob
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "videos")

def guess_stereo(width, height):
    """
    사이드-바이-사이드 스테레오(좌/우 붙은 형태) 추정.
    대표: 3840x1080(=1920x1080*2), 2560x720(=1280x720*2) 등
    """
    if width % 2 != 0:
        return False
    half_w = width // 2
    # 흔한 단일 프레임 폭 후보들(필요하면 추가 가능)
    common_single_widths = {640, 960, 1280, 1920, 2048}
    if half_w in common_single_widths and height in {480, 720, 1080, 1200}:
        return True
    # 보조 규칙: 가로가 세로보다 매우 큰 경우(대략 2.5배 이상)
    if height > 0 and (width / height) >= 2.5:
        return True
    return False

def inspect_video(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (frame_count / fps) if fps and fps > 0 else 0.0
    stereo = guess_stereo(width, height)
    cap.release()

    return {
        "file": os.path.basename(path),
        "width": width,
        "height": height,
        "fps": fps,
        "frames": frame_count,
        "duration_s": duration,
        "stereo_guess": stereo
    }

def main():
    video_paths = sorted(glob(os.path.join(RAW_DIR, "*.mp4")))
    if not video_paths:
        print(f"[ERROR] mp4 파일이 없습니다: {RAW_DIR}")
        return

    print(f"[INFO] Found {len(video_paths)} videos in: {RAW_DIR}\n")
    print(f"{'idx':>3} | {'file':<40} | {'WxH':>11} | {'fps':>6} | {'frames':>8} | {'sec':>8} | {'stereo':>6}")
    print("-"*105)

    for i, vp in enumerate(video_paths, 1):
        info = inspect_video(vp)
        if info is None:
            print(f"{i:>3} | {os.path.basename(vp):<40} | {'OPEN_FAIL':>11}")
            continue

        wh = f"{info['width']}x{info['height']}"
        print(f"{i:>3} | {info['file']:<40} | {wh:>11} | {info['fps']:>6.2f} | {info['frames']:>8} | {info['duration_s']:>8.1f} | {str(info['stereo_guess']):>6}")

if __name__ == "__main__":
    main()
