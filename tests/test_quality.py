import unittest

from core.quality import compute_metrics, judge_quality


class TestQuality(unittest.TestCase):
    def test_quality_warning_when_speed_low_and_stops_high(self):
        speed_series = [0.0, 2.0, 4.0, 8.0, 6.0, 3.0, 2.0, 12.0]
        coverage = [0.1] * len(speed_series)
        cfg = {
            "stop_speed_px_s": 5.0,
            "stop_min_duration_sec": 0.1,
            "avg_speed_px_s": {
                "bad_low": 1.0,
                "warning_low": 10.0,
                "warning_high": 300.0,
                "bad_high": 500.0,
            },
            "stop_count": {"warning": 1, "bad": 10},
            "cv_speed_warning": 0.7,
            "coverage_ratio_warning_low": 0.01,
        }

        metrics = compute_metrics(speed_series, coverage, fps=30.0, quality_cfg=cfg)
        grade, reasons, thresholds = judge_quality(metrics, cfg)

        self.assertIn(grade, {"WARNING", "BAD"})
        self.assertGreaterEqual(len(reasons), 1)
        self.assertIn("avg_speed_px_s", thresholds)


if __name__ == "__main__":
    unittest.main()
