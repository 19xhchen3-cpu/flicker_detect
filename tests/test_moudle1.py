from __future__ import annotations

"""纯逻辑增量检测模块（moudle1.detect_window_incremental）的单元测试。"""

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
from flicker_detect.moudle1 import (
    create_incremental_state,
    detect_window_incremental,
    reset_incremental_state,
)


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


def run_incremental(
    history_frames,
    params: DetectorParameters,
    base: int = 0,
) -> dict | None:
    """把 [frame, block] 历史逐帧喂给增量检测，返回就绪后的结论。

    H=10（drop=0）时结论窗口与输入完全一致；H=30 时结论窗口为相位
    对齐区间，调用方需按注释传入对应的绝对帧片段。
    """
    state = create_incremental_state(params)
    result = None
    for i, frame in enumerate(history_frames):
        result = detect_window_incremental(
            state, [float(value) for value in frame], frame_id=base + i
        )
    return result


def assert_results_equal(test: unittest.TestCase, actual: dict, expected: dict) -> None:
    """比较增量实现与现有实现的关键输出，NaN 按相等处理。"""
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


class IncrementalDetectorTest(unittest.TestCase):
    """detect_window_incremental 的行为测试与 fedetect 链路对照。"""

    def test_marks_periodic_block_only(self) -> None:
        """H=10 窗口（drop=0）下与现有链路一致，只标记周期块。"""
        params = make_params(block_count=2, slope_max=1.0)
        H = params.history
        periodic = [20.0, 40.0, 0.0] * (H // 3) + [20.0]
        monotonic = [float(value) for value in range(H)]
        history = [[periodic[h], monotonic[h]] for h in range(H)]

        pure = run_incremental(history, params)
        existing = run_existing(history, params)
        assert pure is not None
        assert_results_equal(self, pure, existing)
        self.assertEqual(pure["block_flags"], [1, 0])
        self.assertEqual(pure["flicker_block_num"], 1)

    def test_matches_existing_pipeline_randomly(self) -> None:
        """H=10 随机数据与现有链路一致（含全门禁关闭配置）。"""
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
                history[:, 0] += np.tile(
                    [20.0, 40.0, 0.0], params.history // 3 + 1
                )[: params.history]

                pure = run_incremental(history.tolist(), params)
                existing = run_existing(history, params)
                assert pure is not None
                assert_results_equal(self, pure, existing)

    def test_safe_pruning_skips_guaranteed_failures_only(self) -> None:
        """H=30：相位锁定窗口 [3,30] 下剪枝行为与 fedetect 一致。"""
        params = make_params(block_count=2, min_frames=30)
        H = params.history

        # 块 0：帧 3..9 震荡（组 0/1 有效且同模式），其余恒定（无效）。
        block0 = [1000.0] * (H + 1)
        for h, value in zip(
            range(3, 10),
            [1000.0, 1020.0, 980.0, 1000.0, 1020.0, 980.0, 1000.0],
        ):
            block0[h] = value
        # 块 1：完整周期信号，9 个有效组全部同模式。
        block1 = ([20.0, 40.0, 0.0] * ((H + 1) // 3 + 1))[: H + 1]
        history = [[block0[h], block1[h]] for h in range(H + 1)]

        # 增量在帧 30 出结论，窗口 [3,30]；fedetect 输入取绝对帧 1..30，
        # 使其相对起点 2,5,...,26 映射到绝对 3,6,...,27。
        pure = run_incremental(history, params)
        existing = run_existing(history[1:31], params)
        assert pure is not None
        assert_results_equal(self, pure, existing)
        self.assertEqual(pure["block_flags"], [0, 1])
        self.assertEqual(pure["flicker_block_num"], 1)
        self.assertEqual(pure["flip_count"], [2, 9])
        self.assertEqual(pure["sign_flip_ratio"], [1.0, 1.0])

    def test_cv_sqrt_matches_existing_pipeline(self) -> None:
        """H=30：CV 门在相位锁定窗口 [3,30] 下与 fedetect 一致。"""
        params = make_params(block_count=2, min_frames=30)
        H = params.history

        block0 = ([20.0, 40.0, 0.0] * ((H + 1) // 3 + 1))[: H + 1]
        block1 = [1000.0] * (H + 1)
        for g in range(9):
            s = 3 + 3 * g
            up = 50.0 + 40.0 * g
            down = 100.0 + 80.0 * g
            block1[s] = 1000.0
            block1[s + 1] = 1000.0 + up
            block1[s + 2] = 1000.0 + up - down
            block1[s + 3] = 1000.0
        history = [[block0[h], block1[h]] for h in range(H + 1)]

        pure = run_incremental(history, params)
        existing = run_existing(history[1:31], params)
        assert pure is not None
        assert_results_equal(self, pure, existing)
        self.assertEqual(pure["block_flags"], [1, 0])
        self.assertGreater(pure["diff_cv"][1], params.max_group_amp_cv)
        self.assertFalse(math.isnan(pure["diff_cv"][0]))

    def test_returns_none_before_ready(self) -> None:
        """H=30：窗口凑齐 G 组（帧 27）前返回 None。"""
        params = make_params(block_count=2, min_frames=30)
        frame = [6000.0, 9000.0, 5000.0]
        state = create_incremental_state(params)
        for i in range(27):
            self.assertIsNone(
                detect_window_incremental(state, [frame[i % 3]] * 2, frame_id=i)
            )
        self.assertIsNotNone(
            detect_window_incremental(state, [frame[27 % 3]] * 2, frame_id=27)
        )

    def test_initializes_diagnostics_for_non_candidate(self) -> None:
        """无有效组时诊断量初始化正确（mean/cv 为 NaN，ratio/flip 为 0）。"""
        params = make_params(slope_max=1.0)
        history = [[float(value)] for value in range(10)]

        pure = run_incremental(history, params)
        existing = run_existing(history, params)
        assert pure is not None
        assert_results_equal(self, pure, existing)
        self.assertEqual(pure["block_flags"], [0])
        self.assertTrue(math.isnan(pure["mean_abs_diff"][0]))
        self.assertTrue(math.isnan(pure["diff_cv"][0]))
        self.assertEqual(pure["sign_flip_ratio"], [0.0])
        self.assertEqual(pure["flip_count"], [0])

    def test_phase_base_non_zero_alignment(self) -> None:
        """起始帧号非 P 倍数：相位锚定到首帧，结论与 fedetect 对齐窗口一致。"""
        params = make_params(block_count=3)
        rng = np.random.RandomState(42)
        frames = rng.normal(8192.0, 200.0, (12, 3))
        frames[:, 0] += np.tile([30.0, 0.0, 0.0], 4)[:12]

        pure = run_incremental(frames.tolist(), params, base=2)
        # 结论窗口为绝对帧 [2,11]（rel=9）；base=2 时帧号 = 行号 + 2，
        # 故 fedetect 输入取 rows 0..9（对应帧号 2..11）。
        existing = run_existing(frames[0:10], params)
        assert pure is not None
        assert_results_equal(self, pure, existing)

    def test_jump_semantics_and_group_count(self) -> None:
        """每跳 P 帧恰好新增一组；中间帧复用同一结论对象。"""
        params = make_params(block_count=2, min_frames=30)
        frame = [6000.0, 9000.0, 5000.0]
        state = create_incremental_state(params)
        for i in range(30):
            detect_window_incremental(state, [frame[i % 3]] * 2, frame_id=i)
        self.assertEqual(state["group_count"], 9)  # 0..29 完成 9 组
        self.assertIsNotNone(state["result"])
        cached = state["result"]
        for i in (31, 32):
            detect_window_incremental(state, [frame[i % 3]] * 2, frame_id=i)
        self.assertEqual(state["group_count"], 9)  # 中间帧不新增组
        self.assertIs(state["result"], cached)     # 中间帧复用结论对象
        detect_window_incremental(state, [frame[0]] * 2, frame_id=33)
        self.assertEqual(state["group_count"], 10)  # f=33 新增一组
        self.assertIsNot(state["result"], cached)
        self.assertEqual(state["window_end"] - state["window_start"] + 1, 28)

    def test_validates_inputs(self) -> None:
        """非法参数与输入应抛出 ValueError。"""
        with self.assertRaises(ValueError):
            create_incremental_state(make_params(group_size=1))
        with self.assertRaises(ValueError):
            create_incremental_state(make_params(min_frames=0))
        with self.assertRaises(ValueError):
            create_incremental_state(make_params(block_count=0))

        params = make_params(block_count=2)
        state = create_incremental_state(params)
        with self.assertRaises(ValueError):
            detect_window_incremental(state, [1.0], frame_id=0)
        with self.assertRaises(ValueError):
            detect_window_incremental(state, [1.0, 2.0], frame_id=-1)

    def test_reset_clears_state(self) -> None:
        """reset 后回到未就绪状态。"""
        params = make_params(block_count=2, min_frames=30)
        frame = [6000.0, 9000.0, 5000.0]
        state = create_incremental_state(params)
        for i in range(28):
            detect_window_incremental(state, [frame[i % 3]] * 2, frame_id=i)
        self.assertIsNotNone(state["result"])
        reset_incremental_state(state)
        self.assertIsNone(state["result"])
        self.assertIsNone(
            detect_window_incremental(state, [frame[0]] * 2, frame_id=0)
        )


if __name__ == "__main__":
    unittest.main()
