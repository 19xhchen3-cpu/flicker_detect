from __future__ import annotations

"""纯逻辑检测模块的单元测试，并与现有检测链路做结果对照。"""

import math
import unittest

import numpy as np

from flicker_detect.detector_config import DetectorParameters
from flicker_detect.fedetect_moudle import (
    build_features,
    evaluate_group_validity,
    finalize_block_detection,
    group_frames,
)
from flicker_detect.moudle1 import HistoryBuffer, detect_window


def make_params(*, block_count: int = 1, min_frames: int = 10, **overrides):
    """构造一份便于纯逻辑测试的检测参数。"""
    defaults = {
        "fps": 30,
        "detection_frequency": 50,
        "group_size": 4,
        "min_frames": min_frames,
        "min_match_groups": 2,
        "min_group_amp_ratio": 0.001,
        "min_amp_pos_ratio": 0.5,
        "same_pattern_ratio": 0.8,
        "slope_max": 10.0,
        "max_group_amp_cv": 0.4,
        "y_max_bit": 14,
        "cols": block_count,
        "rows": 1,
    }
    defaults.update(overrides)
    return DetectorParameters(**defaults)


def run_existing(history_frames, params: DetectorParameters) -> dict:
    """用现有 NumPy 检测链路执行同一历史窗口。"""
    values = np.asarray(history_frames, dtype=np.float64)
    grouped = group_frames(values, params.group_size)
    features = build_features(grouped["groups"])
    validity = evaluate_group_validity(grouped, features, params)
    final = finalize_block_detection(values, validity, params)
    return {
        **final,
        "flip_count": np.asarray(validity["flip_count"]),
    }


def run_pure(history_frames, params: DetectorParameters) -> dict:
    """把 [frame, block] 历史转成 block-major 后执行纯逻辑检测。"""
    H = params.history
    B = params.block_count
    if len(history_frames) != H:
        raise ValueError("test helper expects a full history window.")

    history = [0.0] * (B * H)
    for h, frame in enumerate(history_frames):
        for b, value in enumerate(frame):
            history[b * H + h] = float(value)

    block_flags = [0] * B
    mean_abs_diff = [float("nan")] * B
    diff_cv = [float("nan")] * B
    sign_flip_ratio = [0.0] * B
    flip_count = [0] * B
    k_value = (
        0
        if params.min_amp_pos_ratio <= 0
        else math.ceil(params.position_count * params.min_amp_pos_ratio)
    )
    flicker_block_num = detect_window(
        history,
        list(range(H)),
        H,
        B,
        params.group_size,
        params.y_max * params.min_group_amp_ratio,
        k_value,
        params.slope_max,
        params.min_match_groups,
        params.same_pattern_ratio,
        params.max_group_amp_cv,
        block_flags,
        mean_abs_diff=mean_abs_diff,
        diff_cv=diff_cv,
        sign_flip_ratio=sign_flip_ratio,
        flip_count=flip_count,
    )
    return {
        "flicker_block_num": flicker_block_num,
        "block_flags": block_flags,
        "mean_abs_diff": mean_abs_diff,
        "diff_cv": diff_cv,
        "sign_flip_ratio": sign_flip_ratio,
        "flip_count": flip_count,
    }


def assert_results_equal(test: unittest.TestCase, actual: dict, expected: dict) -> None:
    """比较纯逻辑与现有实现的关键输出，NaN 按相等处理。"""
    test.assertEqual(actual["flicker_block_num"], expected["flicker_block_num"])
    np.testing.assert_array_equal(actual["block_flags"], expected["block_flags"])
    np.testing.assert_allclose(
        np.asarray(actual["mean_abs_diff"], dtype=np.float64),
        np.asarray(expected["mean_abs_diff"], dtype=np.float64),
        equal_nan=True,
    )
    np.testing.assert_allclose(
        np.asarray(actual["diff_cv"], dtype=np.float64),
        np.asarray(expected["diff_cv"], dtype=np.float64),
        equal_nan=True,
    )
    np.testing.assert_allclose(
        np.asarray(actual["sign_flip_ratio"], dtype=np.float64),
        np.asarray(expected["sign_flip_ratio"], dtype=np.float64),
    )
    np.testing.assert_array_equal(actual["flip_count"], expected["flip_count"])


class PureLogicDetectorTest(unittest.TestCase):
    def test_detect_window_marks_periodic_block_only(self) -> None:
        params = make_params(block_count=2, slope_max=1.0)
        H = params.history
        periodic = [20.0, 40.0, 0.0, 20.0, 40.0, 0.0, 20.0, 40.0, 0.0, 20.0]
        monotonic = [float(value) for value in range(10)]
        history = [
            [periodic[h], monotonic[h]]
            for h in range(H)
        ]

        pure = run_pure(history, params)
        existing = run_existing(history, params)

        assert_results_equal(self, pure, existing)
        self.assertEqual(pure["block_flags"], [1, 0])
        self.assertEqual(pure["flicker_block_num"], 1)

    def test_detect_window_matches_existing_pipeline_randomly(self) -> None:
        rng = np.random.RandomState(20260812)
        configs = [
            make_params(block_count=3),
            make_params(
                block_count=3,
                min_amp_pos_ratio=0.0,
                slope_max=0.0,
                min_match_groups=0,
                same_pattern_ratio=0.0,
                max_group_amp_cv=0.0,
            ),
        ]

        for params in configs:
            for _ in range(5):
                history = rng.normal(1000.0, 200.0, (params.history, 3))
                history[:, 0] += np.tile([20.0, 40.0, 0.0], params.history // 3 + 1)[
                    : params.history
                ]

                pure = run_pure(history, params)
                existing = run_existing(history, params)
                assert_results_equal(self, pure, existing)

    def test_safe_pruning_skips_guaranteed_failures_only(self) -> None:
        # H=30、group_size=4 时最多可形成 G=9 个有效组。
        # 块 0 只构造 2 个有效组（等于 min_match_groups），数量门槛必不通过，
        # 无诊断量时整块跳过计数与 CV；块 1 有 9 个同模式有效组，走完整路径。
        params = make_params(block_count=2, min_frames=30)
        H = params.history

        # 块 0：帧 2..8 震荡（组 0/1 有效），帧 9..29 恒定（组 2..8 被 K-of-N 拒绝）。
        block0 = [1000.0] * H
        for h, value in zip(
            range(2, 9),
            [1000.0, 1020.0, 980.0, 1000.0, 1020.0, 980.0, 1000.0],
        ):
            block0[h] = value
        # 块 1：完整周期信号，9 个有效组全部同模式。
        block1 = [20.0, 40.0, 0.0] * (H // 3) + [20.0]
        history = [[block0[h], block1[h]] for h in range(H)]

        # 完整路径（带诊断）：计数必须照常执行，flip_count 反映真实 best_size。
        pure_full = run_pure(history, params)
        existing_full = run_existing(history, params)
        assert_results_equal(self, pure_full, existing_full)
        self.assertEqual(pure_full["block_flags"], [0, 1])
        self.assertEqual(pure_full["flicker_block_num"], 1)
        self.assertEqual(pure_full["flip_count"], [2, 9])
        self.assertEqual(pure_full["sign_flip_ratio"], [1.0, 1.0])

        # 剪枝路径（无诊断）：跳过计数与 CV，判定结果与完整路径一致；
        # 块 0 被剪枝后不残留计数状态，块 1 仍能正常检出。
        B = params.block_count
        history_flat = [0.0] * (B * H)
        for h, frame in enumerate(history):
            for b, value in enumerate(frame):
                history_flat[b * H + h] = float(value)

        block_flags = [0] * B
        flicker_block_num = detect_window(
            history_flat,
            list(range(H)),
            H,
            B,
            params.group_size,
            params.y_max * params.min_group_amp_ratio,
            2,  # k_value = ceil(3 * 0.5)
            params.slope_max,
            params.min_match_groups,
            params.same_pattern_ratio,
            params.max_group_amp_cv,
            block_flags,
        )
        self.assertEqual(block_flags, [0, 1])
        self.assertEqual(flicker_block_num, 1)

    def test_cv_sqrt_deferred_to_final_compare_matches_existing_pipeline(self) -> None:
        # 块 0：振幅稳定，CV≈0，通过；块 1：符号模式一致但振幅逐组漂移，
        # max_pos_cv≈0.49 > 0.4，CV 门必须拒绝。平方累计 + 末尾开平方
        # 的判定与诊断必须和现有链路一致。
        params = make_params(block_count=2, min_frames=30)
        H = params.history

        block0 = [20.0, 40.0, 0.0] * (H // 3)
        block1 = [1000.0] * H
        for g in range(9):
            s = 2 + 3 * g
            up = 50.0 + 40.0 * g
            down = 100.0 + 80.0 * g
            block1[s] = 1000.0
            block1[s + 1] = 1000.0 + up
            block1[s + 2] = 1000.0 + up - down
            block1[s + 3] = 1000.0
        history = [[block0[h], block1[h]] for h in range(H)]

        pure = run_pure(history, params)
        existing = run_existing(history, params)
        assert_results_equal(self, pure, existing)
        self.assertEqual(pure["block_flags"], [1, 0])
        self.assertGreater(pure["diff_cv"][1], params.max_group_amp_cv)
        self.assertFalse(math.isnan(pure["diff_cv"][0]))

    def test_history_buffer_ring_matches_sliding_windows(self) -> None:
        params = make_params(block_count=2)
        rng = np.random.RandomState(7)
        total_frames = params.history + 5
        frames = rng.normal(1000.0, 100.0, (total_frames, 2))
        frames[:, 0] += np.tile([30.0, 0.0, 0.0, 30.0], total_frames // 4 + 1)[
            :total_frames
        ]

        buffer = HistoryBuffer(params.block_count, params.history)
        for frame_id, frame in enumerate(frames):
            buffer.push_frame(frame, frame_id=frame_id)
            if frame_id < params.history - 1:
                self.assertIsNone(buffer.detect(params))
                continue

            result = buffer.detect(params)
            self.assertIsNotNone(result)
            window = frames[frame_id - params.history + 1 : frame_id + 1]
            pure = run_pure(window, params)
            existing = run_existing(window, params)
            assert_results_equal(self, result, pure)
            assert_results_equal(self, result, existing)

    def test_detect_window_returns_zero_when_no_full_group(self) -> None:
        H = 3
        B = 2
        history = [0.0] * (B * H)
        block_flags = [0] * B
        mean_abs_diff = [float("nan")] * B
        diff_cv = [float("nan")] * B
        sign_flip_ratio = [0.0] * B
        flip_count = [0] * B

        count = detect_window(
            history,
            list(range(H)),
            H,
            B,
            4,
            16.0,
            2,
            10.0,
            2,
            0.8,
            0.4,
            block_flags,
            mean_abs_diff=mean_abs_diff,
            diff_cv=diff_cv,
            sign_flip_ratio=sign_flip_ratio,
            flip_count=flip_count,
        )

        self.assertEqual(count, 0)
        self.assertEqual(block_flags, [0, 0])
        self.assertTrue(all(math.isnan(value) for value in mean_abs_diff))
        self.assertTrue(all(math.isnan(value) for value in diff_cv))
        self.assertEqual(sign_flip_ratio, [0.0, 0.0])
        self.assertEqual(flip_count, [0, 0])

    def test_detect_window_initializes_diagnostics_for_non_candidate(self) -> None:
        params = make_params(slope_max=1.0)
        history = [[float(value)] for value in range(10)]

        pure = run_pure(history, params)
        existing = run_existing(history, params)

        assert_results_equal(self, pure, existing)
        self.assertEqual(pure["block_flags"], [0])
        self.assertTrue(math.isnan(pure["mean_abs_diff"][0]))
        self.assertTrue(math.isnan(pure["diff_cv"][0]))
        self.assertEqual(pure["sign_flip_ratio"], [0.0])
        self.assertEqual(pure["flip_count"], [0])

    def test_detect_window_validates_inputs(self) -> None:
        history = [0.0] * 6
        flags = [0, 0]

        with self.assertRaises(ValueError):
            detect_window(
                history,
                [0, 1, 2],
                3,
                2,
                1,
                16.0,
                2,
                10.0,
                2,
                0.8,
                0.4,
                flags,
            )
        with self.assertRaises(ValueError):
            detect_window(
                [0.0] * 5,
                [0, 1, 2],
                3,
                2,
                4,
                16.0,
                2,
                10.0,
                2,
                0.8,
                0.4,
                flags,
            )
        with self.assertRaises(ValueError):
            detect_window(
                history,
                [0, 1],
                3,
                2,
                4,
                16.0,
                2,
                10.0,
                2,
                0.8,
                0.4,
                flags,
            )
        with self.assertRaises(ValueError):
            detect_window(
                history,
                [0, 1, 3],
                3,
                2,
                4,
                16.0,
                2,
                10.0,
                2,
                0.8,
                0.4,
                flags,
            )

    def test_history_buffer_validates_blocks_and_reset(self) -> None:
        params = make_params(block_count=2)
        buffer = HistoryBuffer(params.block_count, params.history)

        with self.assertRaises(ValueError):
            buffer.push_frame([1.0])

        for frame_id in range(params.history):
            buffer.push_frame([1.0, 2.0], frame_id=frame_id)
        self.assertIsNotNone(buffer.detect(params))

        buffer.reset()
        self.assertIsNone(buffer.detect(params))
        self.assertIsNone(buffer.latest_frame_id)


if __name__ == "__main__":
    unittest.main()
