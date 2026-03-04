from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Any

from core.legacy import PROJECT_ROOT

_MPL_CONFIG_DIR = PROJECT_ROOT / ".cache" / "matplotlib"
_MPL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPL_CONFIG_DIR))

import matplotlib.pyplot as plt
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def generate_speed_plot(times: list[float], speeds: list[float], out_png_path: Path) -> Path:
    out_png_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 4))
    plt.plot(times, speeds, color="#116699", linewidth=1.8)
    plt.title("Speed Time Series")
    plt.xlabel("Time (s)")
    plt.ylabel("Speed (px/s)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_png_path, dpi=150)
    plt.close()
    return out_png_path


def generate_yolo_process_plot(
    times: list[float],
    yolo_counts: list[int],
    yolo_best_conf: list[float],
    out_png_path: Path,
) -> Path:
    out_png_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 4))
    ax1 = plt.gca()
    ax1.plot(times, yolo_counts, color="#1f77b4", linewidth=1.8, label="YOLO count")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Detections", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.grid(alpha=0.3)

    ax2 = ax1.twinx()
    ax2.plot(times, yolo_best_conf, color="#d62728", linewidth=1.6, label="Best conf")
    ax2.set_ylabel("Best confidence", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")
    ax2.set_ylim(0.0, 1.0)

    plt.title("YOLO Detection Process")
    plt.tight_layout()
    plt.savefig(out_png_path, dpi=150)
    plt.close()
    return out_png_path


def generate_confidence_heatmap(
    yolo_best_conf: list[float],
    out_png_path: Path,
) -> Path:
    out_png_path.parent.mkdir(parents=True, exist_ok=True)
    if not yolo_best_conf:
        yolo_best_conf = [0.0]

    arr = np.array([yolo_best_conf], dtype=np.float32)

    plt.figure(figsize=(10, 2.4))
    ax = plt.gca()
    im = ax.imshow(arr, aspect="auto", cmap="inferno", vmin=0.0, vmax=1.0)
    ax.set_yticks([])
    ax.set_xlabel("Frame Index")
    ax.set_title("YOLO Confidence Heatmap (best conf per frame)")
    plt.colorbar(im, ax=ax, fraction=0.03, pad=0.02, label="confidence")
    plt.tight_layout()
    plt.savefig(out_png_path, dpi=160)
    plt.close()
    return out_png_path


def generate_pdf_report(
    result: dict[str, Any],
    out_pdf_path: Path,
    speed_plot_path: Path,
    snapshot_paths: list[Path],
    yolo_process_plot_path: Path | None = None,
    confidence_heatmap_path: Path | None = None,
) -> Path:
    out_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    story = []

    doc = SimpleDocTemplate(
        str(out_pdf_path),
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    job_id = result.get("job_id", "")
    input_video = result.get("input_video", "")
    grade = result.get("quality_grade", "")
    reasons = result.get("reasons", [])
    metrics = result.get("metrics", {})

    story.append(Paragraph("SlumpGuard Pouring Quality Report", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Video: {input_video}", styles["Normal"]))
    story.append(Paragraph(f"Analysis Time: {datetime.now().isoformat(timespec='seconds')}", styles["Normal"]))
    story.append(Paragraph(f"Job ID: {job_id}", styles["Normal"]))
    story.append(Paragraph("Version: webapp-v1", styles["Normal"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Summary", styles["Heading2"]))
    story.append(Paragraph(f"Final Grade: <b>{grade}</b>", styles["Normal"]))
    for reason in reasons[:5]:
        story.append(Paragraph(f"- {reason}", styles["Normal"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Core Metrics", styles["Heading2"]))
    metric_rows = [["Metric", "Value"]]
    for key in [
        "avg_speed",
        "median_speed",
        "std_speed",
        "cv_speed",
        "stop_count",
        "max_speed",
        "min_speed",
        "coverage_ratio",
    ]:
        val = metrics.get(key, "")
        if isinstance(val, float):
            metric_rows.append([key, f"{val:.6f}"])
        else:
            metric_rows.append([key, str(val)])

    tbl = Table(metric_rows, colWidths=[7 * cm, 7 * cm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Speed Graph", styles["Heading2"]))
    story.append(Image(str(speed_plot_path), width=17 * cm, height=6.5 * cm))
    story.append(Spacer(1, 10))

    if yolo_process_plot_path is not None and yolo_process_plot_path.exists():
        story.append(Paragraph("YOLO Process Graph", styles["Heading2"]))
        story.append(Image(str(yolo_process_plot_path), width=17 * cm, height=6.5 * cm))
        story.append(Spacer(1, 10))

    if confidence_heatmap_path is not None and confidence_heatmap_path.exists():
        story.append(Paragraph("YOLO Confidence Heatmap", styles["Heading2"]))
        story.append(Image(str(confidence_heatmap_path), width=17 * cm, height=4.0 * cm))
        story.append(Spacer(1, 10))

    if snapshot_paths:
        story.append(Paragraph("Detection Snapshots", styles["Heading2"]))
        for snap in snapshot_paths[:5]:
            story.append(Paragraph(snap.name, styles["Italic"]))
            story.append(Image(str(snap), width=17 * cm, height=9 * cm))
            story.append(Spacer(1, 6))

    doc.build(story)
    return out_pdf_path
