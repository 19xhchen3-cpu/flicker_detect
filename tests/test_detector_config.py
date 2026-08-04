from __future__ import annotations

import unittest

from flicker_detect.detector_config import (
    PARAMETER_TABLE,
    Y_MAX,
    build_group_size_table,
    group_size_for,
    lookup_detector_parameters,
)


class DetectorConfigTest(unittest.TestCase):
    def test_y_max_is_14_bit_full_scale(self) -> None:
        self.assertEqual(Y_MAX, 16383)

    def test_group_size_for_30fps_50hz_drive(self) -> None:
        self.assertEqual(group_size_for(30, 50), 4)

    def test_zero_apparent_frequency_is_invalid(self) -> None:
        self.assertIsNone(group_size_for(25, 50))

    def test_group_size_table_contains_valid_pairs(self) -> None:
        table = build_group_size_table()

        self.assertEqual(table[(30, 50)], 4)
        self.assertEqual(table[(24, 50)], 7)
        self.assertEqual(table[(48, 60)], 3)
        self.assertNotIn((25, 50), table)

    def test_lookup_returns_derived_group_fields(self) -> None:
        params = lookup_detector_parameters(30, 50)

        self.assertEqual(params.group_stride, 3)
        self.assertEqual(params.position_count, 3)
        self.assertEqual(params.diff_window_size, params.group_size)
        self.assertEqual(params.y_max, Y_MAX)

    def test_unknown_combo_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            lookup_detector_parameters(25, 50)

    def test_parameter_table_matches_group_table_keys(self) -> None:
        self.assertEqual(
            set(PARAMETER_TABLE),
            set(build_group_size_table()),
        )


if __name__ == "__main__":
    unittest.main()
