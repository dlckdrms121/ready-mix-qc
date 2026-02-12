#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate

TS="$(date +%Y%m%d_%H%M%S)"
OUT_ROOT="data/processed/repro_${TS}"
mkdir -p "$OUT_ROOT"

# b1: 드롭 이벤트 CSV 생성 + ROI 산출물 생성
python src/b1_crop_roi_and_drop_timing.py --out_root "$OUT_ROOT" | tee "$OUT_ROOT/b1_log.txt"

# 필수 산출물 체크
test -f "$OUT_ROOT/logs/b1_drop_events.csv" || (echo "Missing $OUT_ROOT/logs/b1_drop_events.csv" && exit 1)

# b2: b1 결과를 읽어 10초 클립 생성
python src/b2_make_10s_clips.py --out_root "$OUT_ROOT" --clip_sec 10.0 | tee "$OUT_ROOT/b2_log.txt"

echo "DONE. out_root=$OUT_ROOT"
