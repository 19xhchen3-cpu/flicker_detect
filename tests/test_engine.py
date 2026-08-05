from __future__ import annotations

"""频闪仿真引擎的单元测试。"""

import unittest

from flicker_app.engine import FlickerEngine, FlickerParams


class FlickerEngineTest(unittest.TestCase):
    """覆盖仿真参数校验、帧强度和卷帘条纹计算。"""

    def setUp(self) -> None:
        # 每个测试使用独立引擎实例，避免状态互相影响。
        self.engine = FlickerEngine()

    def test_default_parameters(self) -> None:
        """默认参数应生成 30 帧、2160 行条纹和合理的最大最小比值。"""
        result = self.engine.calculate(FlickerParams())

        self.assertEqual(result.num_frames, 30)
        self.assertEqual(len(result.frame_strength), 30)
        self.assertGreater(result.max_min_ratio, 9.0)
        self.assertLessEqual(len(result.i_total_disp), 3001)
        self.assertEqual(result.row_count, 2160)
        self.assertEqual(len(result.stripe_rows), 2160)
        self.assertGreater(max(result.stripe_rows) - min(result.stripe_rows), 0)

    def test_validation_rejects_invalid_exposure(self) -> None:
        """曝光时间超过帧周期时应当返回错误。"""
        error = self.engine.validate(
            FlickerParams(video_frame_rate=30.0, exposure_time=0.05)
        )
        self.assertIn("exceeds frame interval", error)

    def test_validation_rejects_nonpositive_fps(self) -> None:
        """帧率非正数时应当返回错误。"""
        error = self.engine.validate(FlickerParams(video_frame_rate=0.0))
        self.assertIn("positive", error)

    def test_flicker_frequency_zero_produces_constant_ac(self) -> None:
        """零频闪频率对应恒定 AC 光强。"""
        result = self.engine.calculate(
            FlickerParams(
                dc_amplitude=0.0,
                flicker_amplitude=1.0,
                flicker_frequency=0.0,
            )
        )
        self.assertEqual(len(result.i_ac_disp), len(result.i_ac_disp))
        self.assertEqual(min(result.i_ac_disp), max(result.i_ac_disp))

    def test_selected_stripe_frame(self) -> None:
        """stripe_frame_index 应被正确使用并在越界时钳制。"""
        params = FlickerParams()

        first = self.engine.calculate(params, stripe_frame_index=0)
        self.assertEqual(first.stripe_frame_index, 0)

        last = self.engine.calculate(params, stripe_frame_index=29)
        self.assertEqual(last.stripe_frame_index, 29)

        clamped = self.engine.calculate(params, stripe_frame_index=999)
        self.assertEqual(clamped.stripe_frame_index, 29)


if __name__ == "__main__":
    unittest.main()
