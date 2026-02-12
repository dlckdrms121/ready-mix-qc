@echo off
cd /d C:\SmartConstruction_Project\SlumpGuard_Study

REM YOLOv8 chute detector prediction (imgsz=512)
yolo detect predict ^
  model=runs\detect\train\weights\best.pt ^
  source=C:\SmartConstruction_Project\SlumpGuard_Study\data\raw\train_videos\slump1.mp4 ^
  imgsz=512 ^
  conf=0.25 ^
  device=0 ^
  save=True

pause
