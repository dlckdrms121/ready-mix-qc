from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_no_optional_labels_in_user_pages() -> None:
    targets = [
        "app/templates/index.html",
        "app/templates/realtime.html",
        "docs/index.html",
        "docs/realtime.html",
    ]
    for rel in targets:
        text = _read(rel).lower()
        assert "optional" not in text, f"'optional' found in {rel}"


def test_roi_input_fields_are_removed_from_pages() -> None:
    targets = [
        "app/templates/index.html",
        "app/templates/realtime.html",
        "docs/index.html",
        "docs/realtime.html",
    ]
    for rel in targets:
        text = _read(rel)
        assert "ROI x" not in text, f"'ROI x' found in {rel}"
        assert "ROI y" not in text, f"'ROI y' found in {rel}"
        assert "ROI w" not in text, f"'ROI w' found in {rel}"
        assert "ROI h" not in text, f"'ROI h' found in {rel}"


def test_realtime_features_are_exposed_on_main_pages() -> None:
    app_index = _read("app/templates/index.html")
    docs_index = _read("docs/index.html")

    assert "Realtime Core Features" in app_index
    assert "Realtime Core Features" in docs_index
    assert "Open Realtime Analyzer" in app_index
    assert "Open Realtime Analyzer" in docs_index


def test_pages_default_api_base_is_set() -> None:
    config = _read("docs/config.js")
    assert 'apiBaseUrl: "https://ready-mix-qc-api.onrender.com"' in config
