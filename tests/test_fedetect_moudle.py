from __future__ import annotations

"""统一检测模块中分组功能的单元测试。"""

import unittest

import numpy as np

from flicker_detect.detector_config import DetectorParameters
from flicker_detect.fedetect_moudle import (
    build_features,
    evaluate_group_validity,
    finalize_block_detection,
    group_frames,
)


def make_params(*, block_count: int = 1, **overrides) -> DetectorParameters:
    """构造一份便于单元测试的检测参数。"""
    defaults = {
        "fps": 30,
        "detection_frequency": 50,
        "group_size": 4,
        "min_frames": 10,
        "min_match_groups": 1,
        "min_group_amp_ratio": 0.001,
        "min_amp_pos_ratio": 0.5,
        "same_pattern_ratio": 0.5,
        "slope_max": 10.0,
        "max_group_amp_cv": 0.4,
        "y_max": 16383,
        "cols": block_count,
        "rows": 1,
    }
    defaults.update(overrides)
    return DetectorParameters(**defaults)


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

    def test_evaluate_group_validity_marks_periodic_groups_dominant(self) -> None:
        history = np.array(
            [20.0, 40.0, 0.0, 20.0, 40.0, 0.0, 20.0, 40.0, 0.0, 20.0]
        ).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(result, features, make_params())

        self.assertTrue(np.all(validity["amp_pass_count"] == 3))
        self.assertTrue(np.all(validity["amp_group_ok"]))
        self.assertTrue(np.all(validity["slope_group_ok"]))
        self.assertTrue(np.all(validity["group_valid"]))
        self.assertEqual(validity["best_candidate"][0], 0)
        self.assertEqual(validity["flip_count"][0], 3)
        self.assertAlmostEqual(validity["sign_flip_ratio"][0], 1.0)
        self.assertTrue(validity["dominant_ok"][0])
        self.assertTrue(np.all(validity["dominant_group_ok"]))
        self.assertTrue(validity["cv_ok"][0])
        self.assertAlmostEqual(validity["max_pos_cv"][0], 0.0)
        np.testing.assert_array_equal(
            validity["dominant_pattern"][:, 0],
            [1, -1, 1],
        )

    def test_evaluate_group_validity_k_of_n_allows_platform_position(self) -> None:
        history = np.array(
            [0.0, 0.0, 20.0, 0.0, 0.0, 20.0, 0.0, 0.0, 20.0, 0.0]
        ).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(result, features, make_params())

        self.assertEqual(validity["k_value"], 2)
        self.assertTrue(np.all(validity["amp_pass_count"] == 2))
        self.assertTrue(np.all(validity["amp_group_ok"]))
        self.assertTrue(np.all(validity["dominant_group_ok"]))
        self.assertTrue(validity["cv_ok"][0])
        np.testing.assert_array_equal(
            validity["dominant_pattern"][:, 0],
            [0, 1, -1],
        )

    def test_evaluate_group_validity_rejects_monotonic_slope(self) -> None:
        history = np.arange(0.0, 200.0, 20.0).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(
            result,
            features,
            make_params(slope_max=1.0),
        )

        self.assertTrue(np.all(validity["amp_group_ok"]))
        self.assertFalse(np.all(validity["slope_group_ok"]))
        self.assertFalse(np.all(validity["group_valid"]))
        self.assertEqual(validity["best_candidate"][0], -1)
        self.assertFalse(validity["dominant_ok"][0])
        self.assertFalse(np.any(validity["dominant_group_ok"]))
        self.assertFalse(validity["cv_ok"][0])
        self.assertTrue(np.isnan(validity["max_pos_cv"][0]))

    def test_evaluate_group_validity_is_independent_per_block(self) -> None:
        history = np.zeros((10, 2), dtype=np.float64)
        history[:, 0] = [20.0, 40.0, 0.0, 20.0, 40.0, 0.0, 20.0, 40.0, 0.0, 20.0]
        history[:, 1] = np.arange(0.0, 200.0, 20.0)

        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(
            result,
            features,
            make_params(block_count=2, slope_max=1.0),
        )

        self.assertTrue(validity["dominant_ok"][0])
        self.assertFalse(validity["dominant_ok"][1])
        self.assertTrue(np.all(validity["dominant_group_ok"][:, 0]))
        self.assertFalse(np.any(validity["dominant_group_ok"][:, 1]))
        self.assertTrue(validity["cv_ok"][0])
        self.assertFalse(validity["cv_ok"][1])

    def test_evaluate_group_validity_zero_min_amp_pos_ratio_disables_k_of_n(
        self,
    ) -> None:
        history = np.zeros((10, 1), dtype=np.float64)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(
            result,
            features,
            make_params(min_amp_pos_ratio=0.0),
        )

        self.assertEqual(validity["k_value"], 0)
        self.assertTrue(np.all(validity["amp_pass_count"] == 3))
        self.assertTrue(np.all(validity["amp_group_ok"]))
        self.assertTrue(np.all(validity["group_valid"]))
        self.assertTrue(validity["dominant_ok"][0])
        self.assertFalse(validity["cv_ok"][0])
        self.assertTrue(np.isnan(validity["max_pos_cv"][0]))

        disabled = evaluate_group_validity(
            result,
            features,
            make_params(min_amp_pos_ratio=0.0, max_group_amp_cv=0.0),
        )
        self.assertTrue(disabled["cv_ok"][0])

    def test_evaluate_group_validity_zero_slope_max_disables_slope_gate(
        self,
    ) -> None:
        history = np.arange(0.0, 200.0, 20.0).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(
            result,
            features,
            make_params(slope_max=0.0),
        )

        self.assertTrue(np.all(validity["slope_group_ok"]))
        self.assertTrue(np.all(validity["group_valid"]))
        self.assertTrue(np.all(np.isnan(validity["slope"])))
        self.assertTrue(validity["dominant_ok"][0])
        self.assertTrue(validity["cv_ok"][0])

    def test_evaluate_group_validity_cv_rejects_unstable_amplitude(self) -> None:
        history = np.array(
            [20.0, 40.0, 0.0, 20.0, 60.0, 0.0, 20.0, 100.0, 0.0, 20.0]
        ).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(result, features, make_params())

        self.assertTrue(validity["dominant_ok"][0])
        self.assertTrue(np.all(validity["dominant_group_ok"]))
        self.assertEqual(validity["mean_amp"].shape, (3, 1))
        self.assertEqual(validity["pos_cv"].shape, (3, 1))
        self.assertEqual(validity["max_pos_cv"].shape, (1,))
        self.assertTrue(validity["max_pos_cv"][0] > 0.4)
        self.assertFalse(validity["cv_ok"][0])

    def test_evaluate_group_validity_zero_max_group_amp_cv_disables_cv_gate(
        self,
    ) -> None:
        history = np.array(
            [20.0, 40.0, 0.0, 20.0, 60.0, 0.0, 20.0, 100.0, 0.0, 20.0]
        ).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(
            result,
            features,
            make_params(max_group_amp_cv=0.0),
        )

        self.assertTrue(validity["dominant_ok"][0])
        self.assertTrue(validity["max_pos_cv"][0] > 0.4)
        self.assertTrue(validity["cv_ok"][0])

    def test_finalize_block_detection_returns_flags_and_diagnostics(self) -> None:
        history = np.array(
            [20.0, 40.0, 10.0, 20.0, 40.0, 10.0, 20.0, 40.0, 10.0, 20.0]
        ).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(result, features, make_params())
        final = finalize_block_detection(history, validity, make_params())

        self.assertEqual(final["block_flags"].tolist(), [1])
        self.assertEqual(final["flicker_block_num"], 1)
        self.assertEqual(final["mean_abs_diff"].shape, (1,))
        self.assertEqual(final["diff_cv"].shape, (1,))
        self.assertEqual(final["sign_flip_ratio"].shape, (1,))
        self.assertAlmostEqual(final["avg_ratio_diff"][0], 4.0)
        self.assertAlmostEqual(final["avg_delta_diff"][0], 30.0)

    def test_finalize_block_detection_keeps_non_candidate_at_zero(self) -> None:
        history = np.arange(0.0, 200.0, 20.0).reshape(-1, 1)
        result = group_frames(history, group_size=4)
        features = build_features(result["groups"])
        validity = evaluate_group_validity(
            result,
            features,
            make_params(slope_max=1.0),
        )
        final = finalize_block_detection(
            history,
            validity,
            make_params(slope_max=1.0),
        )

        self.assertEqual(final["block_flags"].tolist(), [0])
        self.assertEqual(final["flicker_block_num"], 0)

    def test_finalize_block_detection_handles_short_history(self) -> None:
        history = np.array([[10.0], [20.0], [30.0]])
        validity = {
            "cv_ok": np.array([0], dtype=bool),
            "mean_abs_diff": np.array([np.nan]),
            "diff_cv": np.array([np.nan]),
            "sign_flip_ratio": np.array([0.0]),
        }
        final = finalize_block_detection(history, validity, make_params())

        self.assertEqual(final["block_flags"].tolist(), [0])
        self.assertTrue(np.isnan(final["avg_ratio_diff"][0]))
        self.assertTrue(np.isnan(final["avg_delta_diff"][0]))


if __name__ == "__main__":
    unittest.main()
