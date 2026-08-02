from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

CHUNK_SAMPLES = 1_000_000
DISPLAY_LIMIT = 3000
ROW_COUNT = 2160


@dataclass
class FlickerParams:
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

    def calculate(self, params: FlickerParams) -> FlickerResult:
        fps = params.video_frame_rate
        max_exposure = 1.0 / fps
        exposure_time = min(params.exposure_time, max_exposure)

        sample_freq = fps * 1e5
        dt = 1.0 / sample_freq
        num_samples = int(math.floor(params.simulation_time / dt))
        num_frames = int(math.floor(params.simulation_time * fps))

        downsample = max(1, num_samples // DISPLAY_LIMIT)
        display_positions = np.arange(0, num_samples, downsample)

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
        for chunk_start in range(0, num_samples, CHUNK_SAMPLES):
            chunk_end = min(num_samples, chunk_start + CHUNK_SAMPLES)
            t = np.arange(chunk_start, chunk_end, dtype=np.float64) * dt

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
            max_val = float(np.max(rounded_frames[valid_frames]))
            min_val = float(np.min(rounded_frames[valid_frames]))
            max_min_ratio = max_val / min_val if min_val > 1e-12 else 0.0
        else:
            max_min_ratio = 0.0

        if num_frames:
            stripe_frame_index = min(num_frames - 1, max(0, num_frames // 2))
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
