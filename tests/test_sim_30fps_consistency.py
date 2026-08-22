from __future__ import annotations

"""30fps 仿真数据 vs 检测链路的一致性验证。

用频闪仿真引擎生成已知频闪的光强序列（30fps 下有效驱动频率
50/100/125/200/500 Hz 全部混叠到 10Hz 视在频率，group_size=4），
构造 [frame, block] 亮度历史，分别跑 fedetect_moudle 全链路和
moudle1 纯逻辑版，验证：

1. 两个实现的结果一致（block_flags / flicker_block_num / 诊断量）；
2. 与已知真值一致（有频闪全部块检出，无频闪全部不检出）。

范围：只验证 30fps。
"""

import math
import unittest

import numpy as np

from flicker_app.engine import FlickerEngine, FlickerParams
from flicker_detect.detector_config import lookup_detector_parameters
from flicker_detect.fedetect_moudle import (
    build_features,
    evaluate_group_validity,
    finalize_block_detection,
    group_frames,
)
from flicker_detect.moudle1 import (
    create_incremental_state,
    detect_window_incremental,
)

# 30fps 下参数表有效（group_size=4）的驱动频率。
VALID_30FPS_DRIVE_HZ = (50, 100, 125, 200, 500)
FRAMES = 30
# 短曝光近似瞬时采样，避免曝光窗口覆盖整周期把频闪积分抹平。
EXPOSURE_TIME = 1e-5


def simulate_frame_strength(
    drive_hz: float,
    flicker_amplitude: float = 0.5,
    dc_amplitude: float = 0.5,
    dc_frequency: float = 0.0,
    dc_initial_phase: float = math.pi / 2.0,
) -> np.ndarray:
    """用仿真引擎生成 30fps 连续 30 帧的光强积分序列。

    恒定 DC + 整流 AC（光强频率 = 2 × drive_hz），使帧强度呈现已知
    视在频率的周期波形；驱动频率不同但 30fps 下视在频率均为 10Hz。
    通过参数可生成无 AC（恒定光）、视在频率为 0（fa=0）或缓慢 DC 漂移
    等不应检出的数据。
    """
    params = FlickerParams(
        video_frame_rate=30.0,
        exposure_time=EXPOSURE_TIME,
        simulation_time=FRAMES / 30.0 + 1e-6,
        dc_amplitude=dc_amplitude,
        dc_frequency=dc_frequency,
        dc_initial_phase=dc_initial_phase,
        flicker_amplitude=flicker_amplitude,
        flicker_frequency=drive_hz,
        ac_initial_phase=1.0,
    )
    result = FlickerEngine().calculate(params)
    return np.asarray(result.frame_strength[:FRAMES], dtype=np.float64)


def scale_to_14bit(frames: np.ndarray) -> np.ndarray:
    """把光强积分缩放到 14bit 满量程 0..16383。"""
    peak = float(np.max(frames))
    if peak <= 0:
        return np.zeros_like(frames)
    return frames / peak * 16383.0


def build_block_history(frame_strength: np.ndarray, block_count: int) -> np.ndarray:
    """把逐帧光强复制到全部块，构造 [frame, block] 亮度历史。"""
    return np.tile(frame_strength[:, None], (1, block_count))


def run_fedetect(history: np.ndarray, params) -> dict:
    """跑 fedetect_moudle 全链路，返回最终输出并附上 flip_count。"""
    grouped = group_frames(history, params.group_size)
    features = build_features(grouped["groups"])
    validity = evaluate_group_validity(grouped, features, params)
    final = finalize_block_detection(history, validity, params)
    return {**final, "flip_count": np.asarray(validity["flip_count"])}


def run_pure(history: np.ndarray, params) -> dict:
    """用 moudle1 增量纯逻辑版跑同一段历史。"""
    state = create_incremental_state(params)
    result = None
    for frame_id in range(history.shape[0]):
        result = detect_window_incremental(
            state, history[frame_id].tolist(), frame_id=frame_id
        )
    assert result is not None, "history should be ready after filling the buffer"
    return result


class Sim30FpsConsistencyTest(unittest.TestCase):
    """30fps 仿真数据下检测结果一致性与真值核对。"""

    def assert_both_zero(self, history: np.ndarray, params, label: str) -> None:
        """断言两个实现都零检出且逐块一致，用于防误检对照。"""
        fed = run_fedetect(history, params)
        pure = run_pure(history, params)
        self.assertEqual(
            fed["flicker_block_num"], 0, f"{label}: fedetect 误检"
        )
        self.assertEqual(
            pure["flicker_block_num"], 0, f"{label}: moudle1 误检"
        )
        np.testing.assert_array_equal(
            fed["block_flags"],
            pure["block_flags"],
            err_msg=f"{label}: block_flags 不一致",
        )

    def test_known_flicker_all_blocks_detected_and_implementations_agree(self) -> None:
        """有效驱动频率下应全部块检出，且两个实现逐块一致。"""
        for drive_hz in VALID_30FPS_DRIVE_HZ:
            with self.subTest(drive_hz=drive_hz):
                params = lookup_detector_parameters(30, drive_hz)
                strength = simulate_frame_strength(drive_hz)
                history = build_block_history(
                    scale_to_14bit(strength), params.block_count
                )

                fed = run_fedetect(history, params)
                pure = run_pure(history, params)

                # 已知真值：整段为固定视在频率的干净周期信号，全部块应检出。
                self.assertEqual(
                    fed["flicker_block_num"],
                    params.block_count,
                    f"drive_hz={drive_hz}: fedetect 未检出全部块",
                )

                # 两个实现逐块一致。
                np.testing.assert_array_equal(
                    fed["block_flags"],
                    pure["block_flags"],
                    err_msg=f"drive_hz={drive_hz}: block_flags 不一致",
                )
                self.assertEqual(
                    fed["flicker_block_num"],
                    pure["flicker_block_num"],
                    f"drive_hz={drive_hz}: flicker_block_num 不一致",
                )
                np.testing.assert_allclose(
                    fed["flip_count"],
                    pure["flip_count"],
                    err_msg=f"drive_hz={drive_hz}: flip_count 不一致",
                )
                np.testing.assert_allclose(
                    fed["sign_flip_ratio"],
                    pure["sign_flip_ratio"],
                    err_msg=f"drive_hz={drive_hz}: sign_flip_ratio 不一致",
                )
                np.testing.assert_allclose(
                    fed["mean_abs_diff"],
                    pure["mean_abs_diff"],
                    equal_nan=True,
                    err_msg=f"drive_hz={drive_hz}: mean_abs_diff 不一致",
                )
                np.testing.assert_allclose(
                    fed["diff_cv"],
                    pure["diff_cv"],
                    equal_nan=True,
                    err_msg=f"drive_hz={drive_hz}: diff_cv 不一致",
                )

    def test_no_flicker_cases(self) -> None:
        """无频闪或不应检出的数据：恒定光、fa=0、缓慢漂移都应零检出。"""
        params = lookup_detector_parameters(30, 50)
        cases = {
            # 恒定光强：完全没有 AC 成分。
            "constant_light": simulate_frame_strength(50, flicker_amplitude=0.0),
            # 30fps 采样下视在频率为 0（2×fd 为帧率的整数倍），帧间恒定。
            "drive_15hz_fa_zero": simulate_frame_strength(15),
            "drive_30hz_fa_zero": simulate_frame_strength(30),
            "drive_45hz_fa_zero": simulate_frame_strength(45),
            "drive_60hz_fa_zero": simulate_frame_strength(60),
            # 缓慢 DC 漂移（单调变化），闭合斜率门应剔除。
            "slow_dc_drift": simulate_frame_strength(
                50,
                flicker_amplitude=0.0,
                dc_amplitude=1.0,
                dc_frequency=0.25,
                dc_initial_phase=0.0,
            ),
        }
        for label, strength in cases.items():
            with self.subTest(case=label):
                history = build_block_history(
                    scale_to_14bit(strength), params.block_count
                )
                self.assert_both_zero(history, params, label)

    def test_no_flicker_random_noise(self) -> None:
        """随机噪声不是周期频闪，两个实现都应零检出。"""
        params = lookup_detector_parameters(30, 50)
        rng = np.random.default_rng(20260822)
        noise = 8192.0 + rng.normal(0.0, 200.0, size=FRAMES)
        history = build_block_history(noise, params.block_count)
        self.assert_both_zero(history, params, "random_noise")


if __name__ == "__main__":
    unittest.main()
