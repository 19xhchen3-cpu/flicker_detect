from __future__ import annotations

"""频闪检测核心算法纯逻辑版本（流式增量版）。

该模块按 docs/pure_logic_detector_design.md 的目标实现，可直接翻译成
嵌入式 C++：核心检测链路不依赖 NumPy/OpenCV，只使用显式循环、一维数组和
固定 shape。检测语义与 flicker_detect/fedetect_moudle.py 保持对齐。

detect_window_incremental 采用相位锁定 + 跳帧的流式增量实现
（见 docs/moudle1_optimization.md 4.1）：分组锚定到绝对帧号，每前进
P 帧新增一组，只在组完成帧重扫判定，其余帧复用上一结论。
"""

from collections.abc import Sequence
from math import ceil, sqrt

from flicker_detect.detector_config import (
    SIGN_ZERO_EPSILON,
    DetectorParameters,
    lookup_detector_parameters,
)


def create_incremental_state(params: DetectorParameters) -> dict:
    """创建增量检测的持久状态（纯 dict，等价 C++ 结构体/成员数组）。

    上层每帧把该状态传给 detect_window_incremental() 原地更新；阈值
    换算与 fedetect_moudle 链路保持一致（K-of-N 禁用时振幅门同步关闭）。
    """
    if params.group_size < 2:
        raise ValueError("group_size must be at least 2.")
    if params.history <= 0:
        raise ValueError("history must be positive.")
    if params.block_count <= 0:
        raise ValueError("block_count must be positive.")

    B = params.block_count
    H = params.history
    P = params.group_size - 1            # 组内位置数（差分个数）
    G = (H - 1) // P                     # 同时活跃的组数

    min_group_amp = params.y_max * params.min_group_amp_ratio
    k_value = (
        0
        if params.min_amp_pos_ratio <= 0
        else ceil(params.position_count * params.min_amp_pos_ratio)
    )
    if k_value == 0:
        min_group_amp = 0.0

    return {
        "B": B,
        "H": H,
        "P": P,
        "G": G,
        # 组特征循环槽容量 G+1，保证"正在滑出"的旧组数据仍在槽内。
        "group_cap": G + 1,
        # 环形帧历史（block-major）：绝对帧 f 存放于槽 f % H。
        "history": [0.0] * (B * H),
        # 组振幅缓存（(G+1)*B*P）；组模式编码缓存（(G+1)*B，-1 无效）。
        "group_amp": [0.0] * ((G + 1) * B * P),
        "group_pattern": [0] * ((G + 1) * B),
        "min_group_amp": min_group_amp,
        "k_value": k_value,
        "slope_max": params.slope_max,
        "min_match_groups": params.min_match_groups,
        "same_pattern_ratio": params.same_pattern_ratio,
        "max_group_amp_cv": params.max_group_amp_cv,
        "pattern_count": 3**P,
        "group_count": 0,          # 已完成组数（达到 G 后窗口就绪）
        "phase_base": None,        # 相位基准：首个 frame_id（组号从此起算）
        "window_start": None,      # 最近结论的活跃窗口起始绝对帧
        "window_end": None,        # 最近结论的活跃窗口结束绝对帧
        "result": None,            # 上一结论缓存（中间帧复用）
        "next_frame_id": 0,        # frame_id 缺省时的自增序号
        "latest_frame_id": None,
    }


def reset_incremental_state(state: dict) -> None:
    """清空增量状态，供丢帧、VFR 大间隔或场景突变时重建窗口。"""
    state["history"] = [0.0] * (state["B"] * state["H"])
    state["group_amp"] = [0.0] * (state["group_cap"] * state["B"] * state["P"])
    state["group_pattern"] = [0] * (state["group_cap"] * state["B"])
    state["group_count"] = 0
    state["phase_base"] = None
    state["window_start"] = None
    state["window_end"] = None
    state["result"] = None
    state["next_frame_id"] = 0
    state["latest_frame_id"] = None


def detect_window_incremental(
    state: dict,
    blocks: Sequence[float],
    frame_id: int | None = None,
) -> dict | None:
    """单函数入口：每帧调用一次，写入该帧并（必要时）刷新结论。

    全部计算都收敛在本函数内，用显式循环和固定数组，便于直接翻译成
    C++；state 由 create_incremental_state() 创建，本函数原地更新。

    组数耦合（跳帧语义）：分组相位锚定在首个帧号 phase_base 上，第 k 组
    覆盖绝对帧 [phase_base + k*P, phase_base + k*P + P]。每前进 P 帧
    （"实际增量"）恰好**新增一组**：新组 k 进窗、最旧组 k-G 出窗，活跃
    组数恒为 G。因此只在组完成帧（rel = frame_id - phase_base 是 P 的
    倍数）计算新组特征并重扫判定；中间帧只写数据、复用上一份结论。
    数据必须逐帧调用本函数；帧号大间隔跳变（丢帧）需先
    reset_incremental_state()。
    """
    B = state["B"]
    H = state["H"]
    P = state["P"]
    G = state["G"]
    history = state["history"]
    group_amp = state["group_amp"]
    group_pattern = state["group_pattern"]
    group_cap = state["group_cap"]
    min_group_amp = state["min_group_amp"]
    k_value = state["k_value"]
    slope_max = state["slope_max"]
    min_match_groups = state["min_match_groups"]
    same_pattern_ratio = state["same_pattern_ratio"]
    max_group_amp_cv = state["max_group_amp_cv"]
    pattern_count = state["pattern_count"]

    if len(blocks) != B:
        raise ValueError(
            f"blocks length {len(blocks)} does not match "
            f"block_count {B}."
        )
    if frame_id is None:
        frame_id = state["next_frame_id"]
    state["next_frame_id"] = frame_id + 1
    if frame_id < 0:
        raise ValueError("frame_id must be non-negative.")
    state["latest_frame_id"] = frame_id

    # 相位基准取首个帧号，组号从 0 起算，避免起始帧号非 P 倍数时错位。
    if state["phase_base"] is None:
        state["phase_base"] = frame_id
    rel = frame_id - state["phase_base"]

    # ---------- 写帧到环形历史（绝对帧 f 存放于槽 f % H） ----------
    slot = frame_id % H
    for b, value in enumerate(blocks):
        history[b * H + slot] = float(value)

    # 第 k 组在帧 f = phase_base + (k+1)*P 时完成；中间帧只写数据，
    # 复用上一结论（跳帧：每 P 帧才新增一组）。
    if rel < P or rel % P != 0:
        return state["result"]

    new_k = (rel - P) // P
    state["group_count"] += 1

    # ---------- 1. 新组特征（差分/振幅/符号编码/K-of-N/闭合斜率） ----------
    # 只有新组需要计算，其余 G-1 组复用循环槽中的缓存。
    start = state["phase_base"] + new_k * P
    group_slot = (new_k % group_cap) * B
    for b in range(B):
        hbase = b * H
        code = 0
        pass_count = 0
        amp_reached = False
        for p in range(P):
            first_slot = (start + p) % H
            second_slot = (start + p + 1) % H
            d = history[hbase + second_slot] - history[hbase + first_slot]
            a = abs(d)
            group_amp[(group_slot + b) * P + p] = a
            if d > SIGN_ZERO_EPSILON:
                digit = 2          # 符号 +1
            elif d < -SIGN_ZERO_EPSILON:
                digit = 0          # 符号 -1
            else:
                digit = 1          # 符号 0
            code = code * 3 + digit
            if k_value > 0 and not amp_reached and a > min_group_amp:
                pass_count += 1
                if pass_count >= k_value:
                    amp_reached = True

        # 闭合斜率门；不合格组记 -1，不参与计数与 CV。
        amp_group_ok = k_value == 0 or amp_reached or pass_count >= k_value
        valid = False
        if amp_group_ok:
            if slope_max <= 0:
                valid = True
            else:
                closure = (
                    history[hbase + (start + P) % H]
                    - history[hbase + start % H]
                )
                valid = abs(closure / P) <= slope_max
        group_pattern[group_slot + b] = code if valid else -1

    # 窗口未就绪（不足 G 组）时不出结论。
    if state["group_count"] < G:
        return None

    # 记录该结论对应的活跃窗口（绝对帧区间），供上层组装一致的诊断。
    # 活跃组为 [new_k-G+1, new_k]，窗口为 [首个组起点, 最后组终点]。
    state["window_start"] = state["phase_base"] + (new_k - G + 1) * P
    state["window_end"] = state["phase_base"] + (new_k + 1) * P

    # ---------- 2/3/4. 逐块判定：计数扫描 + 主导模式 + 振幅 CV ----------
    block_flags = [0] * B
    mean_abs_diff = [float("nan")] * B
    diff_cv = [float("nan")] * B
    sign_flip_ratio = [0.0] * B
    flip_count = [0] * B

    # 当前活跃组：序号 [new_k-G+1, new_k]。
    active_slots = [(new_k - G + 1 + g) % group_cap * B for g in range(G)]

    for b in range(B):
        # 扫描活跃组构建计数（G 很小，按需重扫），平局规则与 fedetect_moudle
        # 一致：取组扫描顺序中先达到最大计数的编码。
        counts = [0] * pattern_count
        valid_group_count = 0
        best_size = 0
        best_code = -1
        for active_slot in active_slots:
            code = group_pattern[active_slot + b]
            if code < 0:
                continue
            valid_group_count += 1
            counts[code] += 1
            if counts[code] > best_size:
                best_size = counts[code]
                best_code = code

        ratio = (
            best_size / valid_group_count if valid_group_count > 0 else 0.0
        )
        count_pass = (
            min_match_groups == 0 or best_size > min_match_groups
        )
        ratio_pass = (
            same_pattern_ratio == 0 or ratio > same_pattern_ratio
        )
        dominant_ok = valid_group_count > 0 and count_pass and ratio_pass

        flip_count[b] = best_size
        sign_flip_ratio[b] = ratio

        # 跨周期振幅 CV（只对主导组，用缓存的振幅按需重算）。
        block_ok = False
        if dominant_ok:
            # 第一趟：主导组各位置振幅均值。
            sum_amp = [0.0] * P
            dominant_count = 0
            for active_slot in active_slots:
                if group_pattern[active_slot + b] == best_code:
                    dominant_count += 1
                    for p in range(P):
                        sum_amp[p] += group_amp[(active_slot + b) * P + p]
            if dominant_count > 0:
                mean_amp = [s / dominant_count for s in sum_amp]
                valid_positions = [
                    p for p in range(P) if mean_amp[p] > min_group_amp
                ]
                if valid_positions:
                    # 第二趟：平方偏差累计，pos_cv² = var / mean²。
                    max_pos_cv_sq = 0.0
                    mean_sum = 0.0
                    for p in valid_positions:
                        variance_sum = 0.0
                        for active_slot in active_slots:
                            if group_pattern[active_slot + b] == best_code:
                                delta = (
                                    group_amp[(active_slot + b) * P + p]
                                    - mean_amp[p]
                                )
                                variance_sum += delta * delta
                        variance = variance_sum / dominant_count
                        mean_sum += mean_amp[p]
                        pos_cv_sq = variance / (mean_amp[p] * mean_amp[p])
                        max_pos_cv_sq = max(max_pos_cv_sq, pos_cv_sq)

                    mean_abs_diff[b] = mean_sum / len(valid_positions)
                    diff_cv[b] = sqrt(max_pos_cv_sq)
                    if (
                        max_group_amp_cv == 0
                        or sqrt(max_pos_cv_sq) <= max_group_amp_cv
                    ):
                        block_ok = True
                elif max_group_amp_cv == 0:
                    block_ok = True
        block_flags[b] = 1 if block_ok else 0

    state["result"] = {
        "flicker_block_num": sum(block_flags),
        "block_flags": block_flags,
        "mean_abs_diff": mean_abs_diff,
        "diff_cv": diff_cv,
        "sign_flip_ratio": sign_flip_ratio,
        "flip_count": flip_count,
    }
    return state["result"]


def main() -> None:
    """用 30fps/50Hz 合成周期信号演示纯逻辑检测脚本。"""
    params = lookup_detector_parameters(30, 50)
    if params.group_size <= 0:
        raise ValueError("configured parameter cell has group_size=0.")

    state = create_incremental_state(params)
    levels = [6000, 9000, 5000]
    for frame_id in range(params.history):
        level = levels[frame_id % params.position_count]
        detect_window_incremental(
            state, [level] * params.block_count, frame_id=frame_id
        )

    result = state["result"]
    if result is None:
        raise RuntimeError("history should be ready after filling the buffer.")
    print(
        f"pure logic detect: flicker_block_num={result['flicker_block_num']} "
        f"block_count={params.block_count}"
    )


if __name__ == "__main__":
    main()
