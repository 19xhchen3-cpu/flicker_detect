from __future__ import annotations

"""频闪检测核心算法纯逻辑版本（单函数版）。

该模块按 docs/pure_logic_detector_design.md 实现，目标是可直接翻译成
嵌入式 C++：核心检测链路不依赖 NumPy/OpenCV，只使用显式循环、一维数组和
固定 shape。检测语义与 flicker_detect/fedetect_moudle.py 保持对齐。
"""

from collections.abc import Sequence
from math import ceil, sqrt

from flicker_detect.detector_config import (
    SIGN_ZERO_EPSILON,
    DetectorParameters,
    lookup_detector_parameters,
)


def detect_window(
    history: Sequence[float],
    slot_order: Sequence[int],
    H: int,
    B: int,
    group_size: int,
    min_group_amp: float,
    k_value: int,
    slope_max: float,
    min_match_groups: int,
    same_pattern_ratio: float,
    max_group_amp_cv: float,
    block_flags: list[int],
    mean_abs_diff: list[float] | None = None,
    diff_cv: list[float] | None = None,
    sign_flip_ratio: list[float] | None = None,
    flip_count: list[int] | None = None,
) -> int:
    """对一个完整历史窗口执行全部频闪检测阶段。

    history 是 block-major 的一维亮度历史，逻辑形状为 [B][H]；
    slot_order 把环形缓冲槽位映射成逻辑时间顺序。返回检出块数，并写回
    block_flags 及可选诊断量。
    """
    if group_size < 2:
        raise ValueError("group_size must be at least 2.")
    if H < 0:
        raise ValueError("H must be non-negative.")
    if B < 0:
        raise ValueError("B must be non-negative.")
    if len(history) < B * H:
        raise ValueError(
            f"history length {len(history)} is smaller than B*H={B * H}."
        )
    if len(slot_order) != H:
        raise ValueError(
            f"slot_order length {len(slot_order)} does not match H={H}."
        )
    if any(index < 0 or index >= H for index in slot_order):
        raise ValueError("slot_order values must be valid slot indices.")
    if len(block_flags) < B:
        raise ValueError(
            f"block_flags length {len(block_flags)} is smaller than B={B}."
        )

    for optional in (mean_abs_diff, diff_cv, sign_flip_ratio, flip_count):
        if optional is not None and len(optional) < B:
            raise ValueError(
                f"optional diagnostic length {len(optional)} is smaller than B={B}."
            )

    # 输出数组统一初始化，避免上一次调用残留污染本轮结果。
    for b in range(B):
        block_flags[b] = 0
    if mean_abs_diff is not None:
        for b in range(B):
            mean_abs_diff[b] = float("nan")
    if diff_cv is not None:
        for b in range(B):
            diff_cv[b] = float("nan")
    if sign_flip_ratio is not None:
        for b in range(B):
            sign_flip_ratio[b] = 0.0
    if flip_count is not None:
        for b in range(B):
            flip_count[b] = 0

    P = group_size - 1
    G = (H - 1) // P if H > 0 else 0
    # 与现有 Python 实现一致：K-of-N 禁用时 min_group_amp 同步关闭，
    # 因此 CV 位置过滤也不再使用振幅门槛。
    if k_value == 0:
        min_group_amp = 0.0
    if G <= 0:
        return 0

    # 分组布局：同一函数内只计算一次。
    used_frames = group_size + (G - 1) * P
    drop_count = H - used_frames
    group_starts = [drop_count + g * P for g in range(G)]

    # 预分配固定数组，循环、索引和控制流与后续 C++ 一一对应。
    pattern_count = 3**P
    counts = [0] * pattern_count
    touched = [0] * pattern_count
    block_amp = [0.0] * (G * P)
    block_sign = [0] * (G * P)
    closure = [0.0] * G
    pattern = [0] * G
    valid_group_indices = [0] * G
    dominant_indices = [0] * G
    sum_amp = [0.0] * P
    mean_amp = [0.0] * P
    valid_positions = [0] * P

    flicker_block_num = 0
    # 遍历所有的块
    for b in range(B):
        hbase = b * H
        valid_group_count = 0
        touched_count = 0
        best_size = 0
        best_code = -1

        # ---------- 1. 分组、特征、K-of-N、闭合斜率 ----------
        # 遍历所有的组
        for g in range(G):
            start = group_starts[g]
            tail  = group_starts[g] + P
            pass_count = 0
            amp_reached = False

            # 遍历所有的位置
            for p in range(P):
                # 计算当前帧索引，获取对应的差值并判断对应的符号
                first_slot = slot_order[start + p]
                second_slot = slot_order[start + p + 1]
                d = (
                    float(history[hbase + second_slot])
                    - float(history[hbase + first_slot])
                )
                a = abs(d)
                idx = g * P + p

                block_amp[idx] = a
                if d > SIGN_ZERO_EPSILON:
                    block_sign[idx] = 1
                elif d < -SIGN_ZERO_EPSILON:
                    block_sign[idx] = -1
                else:
                    block_sign[idx] = 0


                # 判断块有效性，K of N 门，有效块数达到，跳过当前块
                if k_value > 0 and not amp_reached and a > min_group_amp:
                    pass_count += 1
                    if pass_count >= k_value:
                        amp_reached = True

            # 计算闭合幅度查，然后算周期斜率，合格就通过
            first_slot = slot_order[start]
            tail_slot = slot_order[tail]

            closure[g] = float(history[hbase + tail_slot]) - float(history[hbase + first_slot])
            amp_group_ok = k_value == 0 or amp_reached or pass_count >= k_value

            slope_ok = amp_group_ok
            if slope_max > 0 and slope_ok:
                slope = closure[g] / P
                slope_ok = abs(slope) <= slope_max

            # 将所有位置使用3进制按位置编码，方便符号编码模式比较
            if slope_ok:
                code = 0
                for p in range(P):
                    # -1 -> 0, 0 -> 1, +1 -> 2
                    code = code * 3 + (block_sign[g * P + p] + 1)
                pattern[g] = code
                valid_group_indices[valid_group_count] = g
                valid_group_count += 1

        # ---------- 2. 主导符号模式 ----------
        # 安全剪枝：best_size 不可能超过 valid_group_count，因此当
        # min_match_groups > 0 且 valid_group_count <= min_match_groups 时，
        # count_pass 必为假，该块不可能通过判定。只有需要精确
        # flip_count / sign_flip_ratio 诊断量时才保留计数；否则计数与
        # CV 一起跳过（CV 在 dominant_ok 为假时本就不会执行）。
        if (
            min_match_groups > 0
            and valid_group_count <= min_match_groups
            and flip_count is None
            and sign_flip_ratio is None
        ):
            block_flags[b] = 0
            continue

        # 遍历全部的有效组，找到出现次数最多的那个组
        for local_index in range(valid_group_count):
            g = valid_group_indices[local_index]
            code = pattern[g]
            if counts[code] == 0:
                touched[touched_count] = code
                touched_count += 1
            counts[code] += 1
            if counts[code] > best_size:
                best_size = counts[code]
                best_code = code

        # 从绝对值和比例值检查这个最大特征组是否合格
        ratio = best_size / valid_group_count if valid_group_count > 0 else 0.0
        count_pass = min_match_groups == 0 or best_size > min_match_groups
        ratio_pass = (
            same_pattern_ratio == 0 or ratio > same_pattern_ratio
        )
        dominant_ok = (
            valid_group_count > 0 and count_pass and ratio_pass
        )

        #记录当前块的一些数据
        if flip_count is not None:
            flip_count[b] = best_size
        if sign_flip_ratio is not None:
            sign_flip_ratio[b] = ratio

        # 只保留主导模式成员，用于跨周期振幅 CV。
        dominant_count = 0
        if dominant_ok:
            for local_index in range(valid_group_count):
                g = valid_group_indices[local_index]
                if pattern[g] == best_code:
                    dominant_indices[dominant_count] = g
                    dominant_count += 1

        # ---------- 3. 跨周期振幅 CV ----------
        block_ok = False
        if dominant_count > 0:
            d_count = dominant_count
            # 第一轮，先计算平均值
            for p in range(P):
                sum_amp[p] = 0.0
            for local_index in range(dominant_count):
                g = dominant_indices[local_index]
                for p in range(P):
                    sum_amp[p] += block_amp[g * P + p]
            for p in range(P):
                mean_amp[p] = sum_amp[p] / d_count

            valid_position_count = 0
            # 第二轮，计算相对均值的差的平方和
            for p in range(P):
                if mean_amp[p] > min_group_amp:
                    valid_positions[valid_position_count] = p
                    valid_position_count += 1

            if valid_position_count > 0:
                max_pos_cv_sq = 0.0
                mean_sum = 0.0

                for vi in range(valid_position_count):
                    p = valid_positions[vi]
                    mean = mean_amp[p]
                    variance_sum = 0.0
                    for local_index in range(dominant_count):
                        g = dominant_indices[local_index]
                        delta = block_amp[g * P + p] - mean
                        variance_sum += delta * delta

                    variance = variance_sum / d_count
                    mean_sum += mean
                    # 先以平方形式累计最大值：pos_cv² = variance / mean²。
                    # sqrt 单调递增，max(sqrt(x)) == sqrt(max(x))，
                    # 因此开平方推迟到最终比较时做一次，省掉每个位置一次 sqrt。
                    pos_cv_sq = variance / (mean * mean)
                    if pos_cv_sq > max_pos_cv_sq:
                        max_pos_cv_sq = pos_cv_sq

                if mean_abs_diff is not None:
                    mean_abs_diff[b] = mean_sum / valid_position_count
                if diff_cv is not None:
                    diff_cv[b] = sqrt(max_pos_cv_sq)

                if max_group_amp_cv == 0:
                    block_ok = dominant_ok
                else:
                    block_ok = (
                        dominant_ok
                        and sqrt(max_pos_cv_sq) <= max_group_amp_cv
                    )
            elif max_group_amp_cv == 0:
                block_ok = dominant_ok

        # ---------- 4. 最终判定 ----------
        block_flags[b] = 1 if block_ok else 0
        flicker_block_num += block_flags[b]

        for i in range(touched_count):
            counts[touched[i]] = 0

    return flicker_block_num


class HistoryBuffer:
    """把逐帧 block 亮度写入 block-major 环形历史，供 detect_window 使用。"""

    def __init__(self, B: int, H: int) -> None:
        if B <= 0:
            raise ValueError("B must be positive.")
        if H <= 0:
            raise ValueError("H must be positive.")
        self.block_count = B
        self.capacity = H
        self.history = [0] * (B * H)
        self.slot_order = list(range(H))
        self.write_index = 0
        self.valid_count = 0
        self.latest_frame_id: int | None = None

    def reset(self) -> None:
        """清空历史，供丢帧、VFR 大间隔或场景突变时重建窗口。"""
        self.history = [0] * (self.block_count * self.capacity)
        self.slot_order = list(range(self.capacity))
        self.write_index = 0
        self.valid_count = 0
        self.latest_frame_id = None

    def push_frame(
        self,
        blocks: Sequence[float],
        frame_id: int | None = None,
    ) -> None:
        """写入一帧块亮度，满窗后自动更新逻辑帧顺序。"""
        if len(blocks) != self.block_count:
            raise ValueError(
                f"blocks length {len(blocks)} does not match "
                f"block_count {self.block_count}."
            )

        for b, value in enumerate(blocks):
            self.history[b * self.capacity + self.write_index] = value

        self.write_index = (self.write_index + 1) % self.capacity
        if self.valid_count < self.capacity:
            self.valid_count += 1

        if self.valid_count == self.capacity:
            oldest = self.write_index
            for h in range(self.capacity):
                self.slot_order[h] = (oldest + h) % self.capacity

        if frame_id is not None:
            self.latest_frame_id = frame_id

    def detect(
        self,
        params: DetectorParameters,
        mean_abs_diff: list[float] | None = None,
        diff_cv: list[float] | None = None,
        sign_flip_ratio: list[float] | None = None,
        flip_count: list[int] | None = None,
    ) -> dict | None:
        """历史满窗时执行检测，未满窗返回 None。"""
        if self.valid_count < self.capacity:
            return None

        B = self.block_count
        if mean_abs_diff is None:
            mean_abs_diff = [float("nan")] * B
        if diff_cv is None:
            diff_cv = [float("nan")] * B
        if sign_flip_ratio is None:
            sign_flip_ratio = [0.0] * B
        if flip_count is None:
            flip_count = [0] * B

        block_flags = [0] * B
        min_group_amp = params.y_max * params.min_group_amp_ratio
        if params.min_amp_pos_ratio <= 0:
            k_value = 0
        else:
            k_value = ceil(params.position_count * params.min_amp_pos_ratio)

        flicker_block_num = detect_window(
            self.history,
            self.slot_order,
            self.capacity,
            B,
            params.group_size,
            min_group_amp,
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


def main() -> None:
    """用 30fps/50Hz 合成周期信号演示纯逻辑检测脚本。"""
    params = lookup_detector_parameters(30, 50)
    if params.group_size <= 0:
        raise ValueError("configured parameter cell has group_size=0.")

    buffer = HistoryBuffer(params.block_count, params.history)
    levels = [6000, 9000, 5000]
    for frame_id in range(params.history):
        level = levels[frame_id % params.position_count]
        buffer.push_frame([level] * params.block_count, frame_id=frame_id)

    result = buffer.detect(params)
    if result is None:
        raise RuntimeError("history should be ready after filling the buffer.")
    print(
        f"pure logic detect: flicker_block_num={result['flicker_block_num']} "
        f"block_count={params.block_count}"
    )


if __name__ == "__main__":
    main()
