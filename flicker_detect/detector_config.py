from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

Y_MAX: Final[int] = (1 << 14) - 1

FPS_TIERS: Final[tuple[int, ...]] = (
    24,
    25,
    30,
    48,
    50,
    60,
    100,
    120,
    200,
    240,
)

# Initial detection targets. Extend this list after the original parameter
# table values are confirmed.
DETECTION_FREQUENCIES_HZ: Final[tuple[int, ...]] = (50, 60)


@dataclass(frozen=True)
class DetectorParameters:
    fps: int
    detection_frequency: int
    group_size: int
    min_frames: int
    min_match_groups: int
    min_group_amp_ratio: float
    min_amp_pos_ratio: float
    same_pattern_ratio: float
    slope_max: float
    max_group_amp_cv: float
    y_max: int = Y_MAX

    @property
    def group_stride(self) -> int:
        return self.group_size - 1

    @property
    def position_count(self) -> int:
        return self.group_size - 1

    @property
    def diff_window_size(self) -> int:
        return self.group_size


def group_size_for(fps: int, detection_frequency: int) -> int | None:
    """Return the integer group_size for a configured fps/frequency pair.

    Invalid pairs return None. A pair is invalid when the intensity flicker
    aliases to zero or when the apparent period is not an integer number of
    frames.
    """
    if fps <= 0 or detection_frequency <= 0:
        raise ValueError("fps and detection_frequency must be positive.")

    fps_fraction = Fraction(fps)
    intensity_frequency = 2 * Fraction(detection_frequency)
    alias_count = round(intensity_frequency / fps_fraction)
    apparent_frequency = abs(
        intensity_frequency - alias_count * fps_fraction
    )

    if apparent_frequency == 0:
        return None

    period_frames = fps_fraction / apparent_frequency
    if period_frames.denominator != 1:
        return None

    return period_frames.numerator + 1


def build_group_size_table(
    fps_tiers: tuple[int, ...] = FPS_TIERS,
    detection_frequencies: tuple[int, ...] = DETECTION_FREQUENCIES_HZ,
) -> dict[tuple[int, int], int]:
    table: dict[tuple[int, int], int] = {}
    for fps in fps_tiers:
        for detection_frequency in detection_frequencies:
            size = group_size_for(fps, detection_frequency)
            if size is not None:
                table[(fps, detection_frequency)] = size
    return table


# Placeholder tuning values. Replace these after the original parameter table
# is provided; the structure and derived group fields are the stable part.
def _build_parameters(
    fps: int,
    detection_frequency: int,
    group_size: int,
) -> DetectorParameters:
    min_frames = 2 * group_size - 1
    min_match_groups = 1
    return DetectorParameters(
        fps=fps,
        detection_frequency=detection_frequency,
        group_size=group_size,
        min_frames=min_frames,
        min_match_groups=min_match_groups,
        min_group_amp_ratio=0.01,
        min_amp_pos_ratio=0.5,
        same_pattern_ratio=0.5,
        slope_max=0.0,
        max_group_amp_cv=0.5,
    )


def build_parameter_table(
    group_table: dict[tuple[int, int], int] | None = None,
) -> dict[tuple[int, int], DetectorParameters]:
    table = build_group_size_table() if group_table is None else group_table
    return {
        (fps, detection_frequency): _build_parameters(
            fps,
            detection_frequency,
            group_size,
        )
        for (fps, detection_frequency), group_size in table.items()
    }


PARAMETER_TABLE = build_parameter_table()


def lookup_detector_parameters(
    fps: int,
    detection_frequency: int,
) -> DetectorParameters:
    try:
        return PARAMETER_TABLE[(fps, detection_frequency)]
    except KeyError as exc:
        raise KeyError(
            f"No configured detector parameters for fps={fps}, "
            f"detection_frequency={detection_frequency}."
        ) from exc


def print_config_tables() -> None:
    group_table = build_group_size_table()
    print("group_size table (fps, detection_frequency -> group_size):")
    for fps in FPS_TIERS:
        for detection_frequency in DETECTION_FREQUENCIES_HZ:
            size = group_table.get((fps, detection_frequency))
            print(
                f"  fps={fps:>3} detection_frequency={detection_frequency:>3} "
                f"group_size={size if size is not None else 'invalid'}"
            )


if __name__ == "__main__":
    print(f"Y_MAX = {Y_MAX}")
    print_config_tables()
