from __future__ import annotations

import unittest

from flicker_app.engine import FlickerEngine, FlickerParams


class FlickerEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FlickerEngine()

    def test_default_parameters(self) -> None:
        result = self.engine.calculate(FlickerParams())

        self.assertEqual(result.num_frames, 30)
        self.assertEqual(len(result.frame_strength), 30)
        self.assertGreater(result.max_min_ratio, 9.0)
        self.assertLessEqual(len(result.i_total_disp), 3001)

    def test_validation_rejects_invalid_exposure(self) -> None:
        error = self.engine.validate(
            FlickerParams(video_frame_rate=30.0, exposure_time=0.05)
        )
        self.assertIn("exceeds frame interval", error)

    def test_validation_rejects_nonpositive_fps(self) -> None:
        error = self.engine.validate(FlickerParams(video_frame_rate=0.0))
        self.assertIn("positive", error)

    def test_flicker_frequency_zero_produces_constant_ac(self) -> None:
        result = self.engine.calculate(
            FlickerParams(
                dc_amplitude=0.0,
                flicker_amplitude=1.0,
                flicker_frequency=0.0,
            )
        )
        self.assertEqual(len(result.i_ac_disp), len(result.i_ac_disp))
        self.assertEqual(min(result.i_ac_disp), max(result.i_ac_disp))


if __name__ == "__main__":
    unittest.main()
