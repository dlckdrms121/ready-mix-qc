@echo off
cd /d C:\SmartConstruction_Project\SlumpGuard_Study

REM YOLOv8 chute detector training (imgsz=512)
yolo detect train ^
  model=yolov8n.pt ^
  data=C:\SmartConstruction_Project\SlumpGuard_Study\data\yolo_dataset\data.yaml ^
  imgsz=512 ^
  epochs=50 ^
  batch=16 ^
  device=0 ^
  workers=0

pause
