#!/usr/bin/env bash
set -e

# 1) venv 생성
[ -d .venv ] || python3 -m venv .venv

# 2) 활성화
source .venv/bin/activate

# 3) 기본 도구 업그레이드
python -m pip install -U pip setuptools wheel

# 4) 잠금 의존성 설치(재현성 핵심)
pip install -r requirements.lock.txt
