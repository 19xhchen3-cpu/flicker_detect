from __future__ import annotations

"""统一检测模块中分组功能的单元测试。"""

import unittest

import numpy as np

from flicker_detect.fedetect_moudle import build_features, group_frames


class FedetectMoudleGroupingTest(unittest.TestCase):
    def test_group_layout_for_ten_frames(self) -> None:
        result = group_frames(np.zeros((10, 1)), group_size=4)
        layout = result["layout"]

        self.assertEqual(layout["group_stride"], 3)
        self.assertEqual(layout["position_count"], 3)
        self.assertEqual(layout["group_count"], 3)
        self.assertEqual(layout["used_frames"], 10)
        self.assertEqual(layout["drop_count"], 0)

    def test_drop_oldest_frames_to_align_latest(self) -> None:
        result = group_frames(np.zeros((12, 1)), group_size=4)
        layout = result["layout"]

        self.assertEqual(layout["group_count"], 3)
        self.assertEqual(layout["used_frames"], 10)
        self.assertEqual(layout["drop_count"], 2)

    def test_insufficient_frames_returns_empty_groups(self) -> None:
        result = group_frames(np.zeros((3, 1)), group_size=4)

        self.assertEqual(result["layout"]["group_count"], 0)
        self.assertEqual(result["groups"], [])

    def test_windows_overlap_and_keep_frame_ids(self) -> None:
        history = np.arange(10, dtype=np.float64)
        frame_ids = np.arange(100, 110, dtype=np.int64)
        result = group_frames(history, group_size=4, frame_ids=frame_ids)

        self.assertEqual(result["groups"][0]["frame_indices"], (0, 1, 2, 3))
        self.assertEqual(result["groups"][1]["frame_indices"], (3, 4, 5, 6))
        self.assertEqual(result["groups"][2]["frame_indices"], (6, 7, 8, 9))
        self.assertEqual(result["groups"][0]["frame_ids"], (100, 101, 102, 103))
        self.assertEqual(result["groups"][1]["frame_ids"], (103, 104, 105, 106))

    def test_group_size_must_be_at_least_two(self) -> None:
        with self.assertRaises(ValueError):
            group_frames(np.zeros((10, 1)), group_size=1)

    def test_build_features_returns_diff_amp_sign(self) -> None:
        history = np.array(
            [0.0, 10.0, 0.0, 10.0, 0.0, 10.0, 0.0, 10.0, 0.0, 10.0]
        ).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])

        self.assertEqual(features["diff"].shape, (3, 3, 1))
        self.assertEqual(features["amp"].shape, (3, 3, 1))
        self.assertEqual(features["sign"].shape, (3, 3, 1))
        self.assertTrue(np.all(features["amp"] == 10.0))
        self.assertEqual(features["sign"][0, 0, 0], 1)
        self.assertEqual(features["sign"][0, 1, 0], -1)

    def test_build_features_handles_empty_groups(self) -> None:
        features = build_features([])

        self.assertEqual(features["diff"].shape, (0, 0, 0))
        self.assertEqual(features["amp"].shape, (0, 0, 0))
        self.assertEqual(features["sign"].shape, (0, 0, 0))

    def test_build_features_treats_tiny_diff_as_zero(self) -> None:
        history = np.array(
            [0.0, 5e-5, 0.0, 5e-5, 0.0, 5e-5, 0.0, 5e-5, 0.0, 5e-5]
        ).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])

        self.assertTrue(np.all(features["sign"] == 0))


if __name__ == "__main__":
    unittest.main()
