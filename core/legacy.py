from __future__ import annotations

import importlib.util
import os
from functools import lru_cache
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_YOLO_CONFIG_DIR = PROJECT_ROOT / ".cache" / "ultralytics"
_YOLO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(_YOLO_CONFIG_DIR))


@lru_cache(maxsize=16)
def load_legacy_module(module_path: str, alias: str | None = None) -> ModuleType:
    """Load existing script modules in src/ by file path to reuse legacy code as-is."""
    full_path = PROJECT_ROOT / module_path
    if not full_path.exists():
        raise FileNotFoundError(f"Legacy module not found: {full_path}")

    module_name = alias or full_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(full_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec: {full_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
