from __future__ import annotations

"""频闪检测配置：fps 与目标频率对应的参数二维表、查表和校验。"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Final

# 14 bit 输入亮度的满量程值。
Y_MAX: Final[int] = (1 << 14) - 1

# 可修改：符号矩阵判定“不变”的绝对差分阈值。
SIGN_ZERO_EPSILON: Final[float] = 1e-4

# 支持检测的 fps 挡位，按从小到大排列。
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

# 支持检测的目标驱动频率，按从小到大排列。
DETECTION_FREQUENCIES_HZ: Final[tuple[int, ...]] = (
    50,
    60,
    100,
    120,
    125,
    200,
    240,
    500,
)


# 参数说明：
# group_size: 一个周期组包含的帧数，等于混频周期 + 1；0 表示未配置或不可用。
# min_frames: 启动检测所需的最少有效历史帧数；history 和诊断滑动窗口复用该值。
# min_match_groups: 主导模式必须超过的最少匹配组数。
# min_group_amp_ratio: 最低有效振幅占 y_max 的比例，用于生成绝对振幅门槛。
# min_amp_pos_ratio: 周期组内必须通过振幅门的位置比例。
# same_pattern_ratio: 主导符号模式所需达到的最低复现比例。
# slope_max: 周期首尾允许的最大平均变化率，单位是亮度/帧。
# max_group_amp_cv: 跨周期允许的最大振幅变异系数。
# y_max_bit: 输入亮度位深，例如 14bit 对应满量程 16383。
# cols: 画面分块列数；rows: 画面分块行数；block_count = cols * rows。
PARAMETER_MATRIX: dict[tuple[int, int], dict[str, int | float]] = {
    (24, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (24, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (24, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (24, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (24, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (24, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (24, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (24, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (25, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 50): dict(group_size=4, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 100): dict(group_size=4, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 125): dict(group_size=4, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 200): dict(group_size=4, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (30, 500): dict(group_size=4, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (48, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (50, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (60, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (100, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (120, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (200, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 50): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 60): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 100): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 120): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 125): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 200): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 240): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
    (240, 500): dict(group_size=0, min_frames=30, min_match_groups=2, min_group_amp_ratio=0.001, min_amp_pos_ratio=0.5, same_pattern_ratio=0.8, slope_max=10.0, max_group_amp_cv=0.4, y_max_bit=14, cols=16, rows=12),
}


@dataclass(frozen=True)
class DetectorParameters:
    """单个 fps 与检测频率组合下的完整检测参数。"""

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
    y_max_bit: int = 0
    cols: int = 0
    rows: int = 0

    @property
    def group_stride(self) -> int:
        """相邻周期组起点之间的帧数。"""
        return self.group_size - 1

    @property
    def position_count(self) -> int:
        """一个周期组内的差分位置数量。"""
        return self.group_size - 1

    @property
    def history(self) -> int:
        """历史缓存容量，与 min_frames 保持一致。"""
        return self.min_frames

    @property
    def diff_window_size(self) -> int:
        """诊断滑动窗口大小，复用历史缓存长度。"""
        return self.history

    @property
    def block_count(self) -> int:
        """画面分块总数。"""
        return self.cols * self.rows

    @property
    def y_max(self) -> int:
        """由位深换算输入亮度满量程，例如 14bit 即 16383。"""
        return (1 << self.y_max_bit) - 1 if self.y_max_bit > 0 else 0


def group_size_for(fps: int, detection_frequency: int) -> int | None:
    """计算指定 fps 与检测频率对应的整数 group_size。

    无效组合返回 None。无效指光强频率混叠为 0，或视在周期不是整数帧。
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
    """生成 (fps, 检测频率) -> group_size 的自动推导表。"""
    table: dict[tuple[int, int], int] = {}
    for fps in fps_tiers:
        for detection_frequency in detection_frequencies:
            size = group_size_for(fps, detection_frequency)
            if size is not None:
                table[(fps, detection_frequency)] = size
    return table


PARAMETER_TABLE = {
    (fps, detection_frequency): DetectorParameters(
        fps=fps,
        detection_frequency=detection_frequency,
        **values,
    )
    for (fps, detection_frequency), values in PARAMETER_MATRIX.items()
}


def lookup_detector_parameters(
    fps: int,
    detection_frequency: int,
) -> DetectorParameters:
    """按 fps 和检测频率查表，返回完整检测参数。"""
    try:
        return PARAMETER_TABLE[(fps, detection_frequency)]
    except KeyError as exc:
        raise KeyError(
            f"No configured detector parameters for fps={fps}, "
            f"detection_frequency={detection_frequency}."
        ) from exc


def print_config_tables() -> None:
    """打印 group_size 表和当前二维参数矩阵，便于人工检查。"""
    group_table = build_group_size_table()
    print("group_size table (fps, detection_frequency -> group_size):")
    for fps in FPS_TIERS:
        for detection_frequency in DETECTION_FREQUENCIES_HZ:
            size = group_table.get((fps, detection_frequency))
            print(
                f"  fps={fps:>3} detection_frequency={detection_frequency:>3} "
                f"group_size={size if size is not None else 'invalid'}"
            )
    print("\nparameter matrix cells:")
    for (fps, detection_frequency), params in PARAMETER_TABLE.items():
        print(
            f"  ({fps:>3}, {detection_frequency:>3}): "
            f"group_size={params.group_size} min_frames={params.min_frames} "
            f"history={params.history} "
            f"min_match_groups={params.min_match_groups} "
            f"min_group_amp_ratio={params.min_group_amp_ratio} "
            f"min_amp_pos_ratio={params.min_amp_pos_ratio} "
            f"same_pattern_ratio={params.same_pattern_ratio} "
            f"slope_max={params.slope_max} "
            f"max_group_amp_cv={params.max_group_amp_cv} "
            f"y_max={params.y_max} cols={params.cols} rows={params.rows}"
        )


if __name__ == "__main__":
    print(f"Y_MAX = {Y_MAX}")
    print_config_tables()
