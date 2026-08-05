from __future__ import annotations

"""频闪仿真计算引擎，负责生成光源曲线、帧强度和卷帘快门条纹。"""

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

# 分块计算时的最大采样点数，避免一次生成过大的中间数组。
CHUNK_SAMPLES = 1_000_000
# 前端显示曲线时的最大点数，超出后按间隔抽样。
DISPLAY_LIMIT = 3000
# 4K 卷帘快门仿真使用的行数。
ROW_COUNT = 2160


@dataclass
class FlickerParams:
    """频闪仿真参数。"""

    video_frame_rate: float = 30.0
    exposure_time: float = 1e-3
    simulation_time: float = 1.0
    dc_amplitude: float = 5.0
    dc_frequency: float = 0.25
    dc_initial_phase: float = 0.0
    flicker_amplitude: float = 1.0
    flicker_frequency: float = 50.0
    ac_initial_phase: float = 1.0


DEFAULT_PARAMS = FlickerParams()


@dataclass
class FlickerResult:
    """频闪仿真结果，包含曲线、帧强度、比值和单帧条纹。"""

    time_disp: list[float]
    i_dc_disp: list[float]
    i_ac_disp: list[float]
    i_total_disp: list[float]
    frame_strength: list[float]
    max_min_ratio: float
    num_samples: int
    num_frames: int
    stripe_rows: list[float]
    stripe_frame_index: int
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FlickerEngine:
    @staticmethod
    def validate(params: FlickerParams) -> str:
        """校验仿真参数，返回空字符串表示参数有效。"""
        if params.video_frame_rate <= 0.0:
            return "Video frame rate must be positive."
        if params.simulation_time <= 0.0:
            return "Simulation time must be positive."
        if params.exposure_time <= 0.0:
            return "Exposure time must be positive."

        max_exposure = 1.0 / params.video_frame_rate
        if params.exposure_time > max_exposure:
            return (
                f"Exposure time ({params.exposure_time:g}s) exceeds "
                f"frame interval ({max_exposure:g}s)."
            )
        return ""

    def _compute_stripe_rows(
        self,
        params: FlickerParams,
        dt: float,
        num_samples: int,
        frame_index: int,
        max_exposure: float,
        exposure_time: float,
    ) -> list[float]:
        """计算指定帧内每一行曝光窗口的亮度累加值，形成卷帘条纹。"""
        row_starts_rel = np.arange(ROW_COUNT) * max_exposure / ROW_COUNT
        row_starts = frame_index * max_exposure + row_starts_rel
        starts = np.round(row_starts / dt).astype(np.int64)
        ends = np.round((row_starts + exposure_time) / dt).astype(np.int64)
        np.minimum(ends, num_samples, out=ends)

        if num_samples <= 0 or int(np.max(ends)) <= int(np.min(starts)):
            return [0.0] * ROW_COUNT

        start_sample = int(np.min(starts))
        end_sample = int(np.max(ends))
        t = np.arange(start_sample, end_sample, dtype=np.float64) * dt

        dc = params.dc_amplitude * np.abs(
            np.sin(2.0 * math.pi * params.dc_frequency * t + params.dc_initial_phase)
        )
        ac = params.flicker_amplitude * np.abs(
            np.sin(2.0 * math.pi * params.flicker_frequency * t + params.ac_initial_phase)
        )
        total = dc + ac

        row_sums = np.zeros(ROW_COUNT, dtype=np.float64)
        for row in range(ROW_COUNT):
            row_start = int(starts[row]) - start_sample
            row_end = int(ends[row]) - start_sample
            if row_end > row_start:
                row_sums[row] = float(np.sum(total[row_start:row_end]))

        return np.round(row_sums, 3).tolist()

    def calculate(
        self,
        params: FlickerParams,
        stripe_frame_index: int | None = None,
    ) -> FlickerResult:
        """执行仿真并返回曲线、帧强度和可选的单帧卷帘条纹。"""
        fps = params.video_frame_rate
        max_exposure = 1.0 / fps
        exposure_time = min(params.exposure_time, max_exposure)

        # 用远高于帧率的采样率逼近连续光强，再按帧曝光窗口累加。
        sample_freq = fps * 1e5
        dt = 1.0 / sample_freq
        num_samples = int(math.floor(params.simulation_time / dt))
        num_frames = int(math.floor(params.simulation_time * fps))

        downsample = max(1, num_samples // DISPLAY_LIMIT)
        display_positions = np.arange(0, num_samples, downsample)

        # 每个视频帧对应一段曝光窗口，窗口内光强累加得到帧强度。
        frame_starts = np.round(
            np.arange(num_frames, dtype=np.float64) * max_exposure / dt
        ).astype(np.int64)
        frame_ends = np.round(
            (
                np.arange(num_frames, dtype=np.float64) * max_exposure
                + exposure_time
            )
            / dt
        ).astype(np.int64)
        np.minimum(frame_ends, num_samples, out=frame_ends)

        time_disp: list[float] = []
        i_dc_disp: list[float] = []
        i_ac_disp: list[float] = []
        i_total_disp: list[float] = []
        frame_sums = np.zeros(num_frames, dtype=np.float64)

        two_pi = 2.0 * math.pi
        # 分块生成正弦曲线，减少大时间序列对内存的占用。
        for chunk_start in range(0, num_samples, CHUNK_SAMPLES):
            chunk_end = min(num_samples, chunk_start + CHUNK_SAMPLES)
            t = np.arange(chunk_start, chunk_end, dtype=np.float64) * dt

            # abs(sin) 模拟整流后的光强波形，光强基频为参数频率的 2 倍。
            dc = params.dc_amplitude * np.abs(
                np.sin(two_pi * params.dc_frequency * t + params.dc_initial_phase)
            )
            ac = params.flicker_amplitude * np.abs(
                np.sin(two_pi * params.flicker_frequency * t + params.ac_initial_phase)
            )
            total = dc + ac

            in_chunk = (display_positions >= chunk_start) & (
                display_positions < chunk_end
            )
            positions = display_positions[in_chunk]
            rel = positions - chunk_start
            time_disp.extend((positions * dt).tolist())
            i_dc_disp.extend(dc[rel].tolist())
            i_ac_disp.extend(ac[rel].tolist())
            i_total_disp.extend(total[rel].tolist())

            if num_frames:
                # 按采样点所属帧做加权累加，得到每一帧的总光强。
                sample_idx = np.arange(chunk_start, chunk_end, dtype=np.int64)
                frame_idx = (
                    np.searchsorted(frame_starts, sample_idx, side="right") - 1
                )
                valid = (frame_idx >= 0) & (sample_idx < frame_ends[frame_idx])
                if np.any(valid):
                    frame_sums += np.bincount(
                        frame_idx[valid],
                        weights=total[valid],
                        minlength=num_frames,
                    )

        rounded_frames = np.round(frame_sums, 3)
        valid_frames = frame_ends > frame_starts
        if num_frames and np.any(valid_frames):
            # 最大/最小帧强度比用于快速观察频闪幅度。
            max_val = float(np.max(rounded_frames[valid_frames]))
            min_val = float(np.min(rounded_frames[valid_frames]))
            max_min_ratio = max_val / min_val if min_val > 1e-12 else 0.0
        else:
            max_min_ratio = 0.0

        if num_frames:
            # 默认显示中间帧的条纹，用户也可通过 stripe_frame_index 指定。
            requested_frame = (
                num_frames // 2 if stripe_frame_index is None else stripe_frame_index
            )
            stripe_frame_index = min(num_frames - 1, max(0, requested_frame))
        else:
            stripe_frame_index = 0
        stripe_rows = self._compute_stripe_rows(
            params,
            dt,
            num_samples,
            stripe_frame_index,
            max_exposure,
            exposure_time,
        )

        return FlickerResult(
            time_disp=time_disp,
            i_dc_disp=i_dc_disp,
            i_ac_disp=i_ac_disp,
            i_total_disp=i_total_disp,
            frame_strength=rounded_frames.tolist(),
            max_min_ratio=max_min_ratio,
            num_samples=num_samples,
            num_frames=num_frames,
            stripe_rows=stripe_rows,
            stripe_frame_index=stripe_frame_index,
            row_count=ROW_COUNT,
        )
