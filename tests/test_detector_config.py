from __future__ import annotations

"""检测配置模块的单元测试。"""

import unittest

from flicker_detect.detector_config import (
    DETECTION_FREQUENCIES_HZ,
    FPS_TIERS,
    PARAMETER_TABLE,
    Y_MAX,
    DetectorParameters,
    build_group_size_table,
    group_size_for,
    lookup_detector_parameters,
)


class DetectorConfigTest(unittest.TestCase):
    """覆盖配置矩阵、group_size 推导、参数查表和派生属性。"""

    def test_y_max_is_14_bit_full_scale(self) -> None:
        """14 bit 满量程应为 16383。"""
        self.assertEqual(Y_MAX, 16383)

    def test_y_max_bit_derived_from_full_scale(self) -> None:
        """满量程 16383 应反推出 14 bit。"""
        params = lookup_detector_parameters(30, 50)
        self.assertEqual(params.y_max_bit, 14)

    def test_group_size_for_30fps_50hz_drive(self) -> None:
        """30fps、50Hz 驱动频率对应 group_size 4。"""
        self.assertEqual(group_size_for(30, 50), 4)

    def test_zero_apparent_frequency_is_invalid(self) -> None:
        """混叠为 0 的频率组合应判定为无效。"""
        self.assertIsNone(group_size_for(25, 50))

    def test_group_size_table_contains_valid_pairs(self) -> None:
        """自动推导表只应包含整数视在周期的组合。"""
        table = build_group_size_table()

        self.assertEqual(table[(30, 50)], 4)
        self.assertEqual(table[(24, 50)], 7)
        self.assertEqual(table[(48, 60)], 3)
        self.assertNotIn((25, 50), table)

    def test_lookup_returns_filled_parameters(self) -> None:
        """已填写参数应原样返回。"""
        params = lookup_detector_parameters(30, 50)

        self.assertEqual(params.group_size, 4)
        self.assertEqual(params.min_frames, 30)
        self.assertEqual(params.history, 30)
        self.assertEqual(params.diff_window_size, 30)
        self.assertEqual(params.min_match_groups, 2)
        self.assertEqual(params.min_group_amp_ratio, 0.001)
        self.assertEqual(params.min_amp_pos_ratio, 0.5)
        self.assertEqual(params.same_pattern_ratio, 0.8)
        self.assertEqual(params.slope_max, 10.0)
        self.assertEqual(params.max_group_amp_cv, 0.4)
        self.assertEqual(params.y_max, Y_MAX)
        self.assertEqual(params.cols, 16)
        self.assertEqual(params.rows, 12)

    def test_unfilled_cell_keeps_group_size_zero(self) -> None:
        """未填写 group_size 的格子应保持 0。"""
        params = lookup_detector_parameters(24, 50)

        self.assertEqual(params.group_size, 0)
        self.assertEqual(params.min_frames, 30)
        self.assertEqual(params.history, 30)
        self.assertEqual(params.min_group_amp_ratio, 0.001)
        self.assertEqual(params.same_pattern_ratio, 0.8)
        self.assertEqual(params.y_max, Y_MAX)

    def test_unknown_combo_raises_key_error(self) -> None:
        """不在配置矩阵中的组合应抛出 KeyError。"""
        with self.assertRaises(KeyError):
            lookup_detector_parameters(23, 50)

    def test_parameter_table_contains_all_fps_frequency_pairs(self) -> None:
        """配置表应覆盖所有 fps 与目标频率组合。"""
        expected_pairs = {
            (fps, detection_frequency)
            for fps in FPS_TIERS
            for detection_frequency in DETECTION_FREQUENCIES_HZ
        }

        self.assertEqual(set(PARAMETER_TABLE), expected_pairs)
        self.assertEqual(
            len(PARAMETER_TABLE),
            len(FPS_TIERS) * len(DETECTION_FREQUENCIES_HZ),
        )

    def test_block_count_derived_from_cols_and_rows(self) -> None:
        """分块总数应为 cols 乘 rows。"""
        params = DetectorParameters(
            fps=30,
            detection_frequency=50,
            group_size=4,
            min_frames=0,
            min_match_groups=0,
            min_group_amp_ratio=0.0,
            min_amp_pos_ratio=0.0,
            same_pattern_ratio=0.0,
            slope_max=0.0,
            max_group_amp_cv=0.0,
            cols=8,
            rows=6,
        )

        self.assertEqual(params.block_count, 48)

    def test_history_and_diagnostic_window_reuse_min_frames(self) -> None:
        """历史容量和诊断窗口应复用 min_frames。"""
        params = DetectorParameters(
            fps=30,
            detection_frequency=50,
            group_size=4,
            min_frames=7,
            min_match_groups=1,
            min_group_amp_ratio=0.01,
            min_amp_pos_ratio=0.5,
            same_pattern_ratio=0.5,
            slope_max=0.0,
            max_group_amp_cv=0.5,
            cols=8,
            rows=6,
        )

        self.assertEqual(params.history, 7)
        self.assertEqual(params.diff_window_size, 7)
        self.assertNotEqual(params.diff_window_size, params.group_size)


if __name__ == "__main__":
    unittest.main()
