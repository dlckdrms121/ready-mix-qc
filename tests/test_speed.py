import unittest

from core.speed import build_speed_config


class TestSpeed(unittest.TestCase):
    def test_speed_config_defaults_exist(self):
        cfg = build_speed_config({"speed": {}})
        self.assertGreaterEqual(cfg.smoothing_window, 1)
        self.assertIn(cfg.smoothing_method, {"moving_average", "median"})


if __name__ == "__main__":
    unittest.main()
