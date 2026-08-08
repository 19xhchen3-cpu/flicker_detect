from __future__ import annotations

"""统一检测功能模块。

后续检测算法功能都按独立函数写在这里；
视频预处理继续使用独立的 preprocess.py。
"""

# 标准库工具：命令行参数与类型声明。
import argparse
from typing import Sequence

# 数值数组处理。
import numpy as np

# 检测配置查表与独立视频预处理模块。
from flicker_detect.detector_config import (
    DetectorParameters,
    SIGN_ZERO_EPSILON,
    lookup_detector_parameters,
)
from flicker_detect.preprocess import inspect_video, iter_video_frames


# 分组功能：根据 group_size 对历史帧做重叠周期分组。
def group_frames(
    history: Sequence[np.ndarray] | np.ndarray,
    group_size: int,
    frame_ids: Sequence[int] | np.ndarray | None = None,
) -> dict:
    """全部分组功能收敛于此，返回布局和重叠周期组。

    history 的物理形状为 [frame, block]，frame_ids 可选。
    有效帧不足一个完整周期时返回空分组。
    """
    # group_size 至少为 2，才能产生一个差分位置。
    if group_size < 2:
        raise ValueError("group_size must be at least 2.")

    # 统一转成 numpy 数组，便于按帧索引切片。
    values = np.asarray(history)
    # history 转成数组后形状为 [frame, block]；
    # 后面 values[start:end] 取到的是 [group_size, block]，所有块仍保留在最后一维。
    if values.ndim < 1:
        raise ValueError("history must have at least one dimension.")

    # group_stride 是相邻组起点间隔，也是组内差分位置数量。
    valid_count = values.shape[0]
    group_stride = group_size - 1
    position_count = group_stride

    # 有效帧不足时不会形成完整周期组。
    if valid_count <= 0:
        group_count = 0
    else:
        group_count = (valid_count - 1) // group_stride

    # 只保留能组成完整周期组的帧数，其余帧作为最旧丢弃帧。
    if group_count > 0:
        used_frames = group_size + (group_count - 1) * group_stride
    else:
        used_frames = 0

    # 分组布局包含所有后续特征计算需要的派生量。
    layout = {
        "group_size": group_size,
        "group_stride": group_stride,
        "position_count": position_count,
        "group_count": group_count,
        "used_frames": used_frames,
        "drop_count": valid_count - used_frames,
    }

    # frame_ids 用于让分组结果与真实帧索引对齐。
    if frame_ids is not None:
        ids = np.asarray(frame_ids)
        if ids.shape[0] != valid_count:
            raise ValueError(
                f"frame_ids length {ids.shape[0]} does not match "
                f"valid_count {valid_count}."
            )
    else:
        ids = None

    # 每组共享上一组的边界帧，起点每隔 group_stride 推进。
    groups: list[dict] = []
    for group_index in range(layout["group_count"]):
        start = layout["drop_count"] + group_index * layout["group_stride"]
        end = start + layout["group_size"]
        group_ids = (
            tuple(int(value) for value in ids[start:end])
            if ids is not None
            else None
        )
        groups.append(
            {
                "group_index": group_index,
                "start_frame_index": start,
                "frame_indices": tuple(range(start, end)),
                "frame_ids": group_ids,
                # brightness 形状为 [group_size, block_count]，
                # block 维没有被展开，供 build_features 继续向量化处理。
                "brightness": values[start:end],
            }
        )

    # 返回统一分组结果，包含布局和周期组列表。
    return {
        "layout": layout,
        "groups": groups,
    }


# 特征构建功能：从周期组计算 diff/amp/sign 矩阵。
def build_features(groups: Sequence[dict]) -> dict:
    """根据分组结果构建特征矩阵。

    返回 diff、amp、sign 三个矩阵，形状均为
    [group_count, position_count, block_count]。
    """
    # 没有周期组时返回空特征矩阵。
    if not groups:
        return {
            "diff": np.empty((0, 0, 0), dtype=np.float64),
            "amp": np.empty((0, 0, 0), dtype=np.float64),
            "sign": np.empty((0, 0, 0), dtype=np.int8),
            "group_frame_ids": [],
        }

    # 把每个组的亮度窗口堆叠为 [group_count, group_size, block_count]。
    # 这里没有显式遍历 block，因为 block 作为最后一个维度一起参与运算。
    brightness = np.stack(
        [
            np.asarray(group["brightness"], dtype=np.float64)
            for group in groups
        ]
    )

    # 组内相邻帧差分。
    diff = np.diff(brightness, axis=1)
    # 振幅取差分绝对值。
    amp = np.abs(diff)
    # 符号表示亮度上升、下降或不变；abs(diff) < 1e-4 时视为不变。
    sign = np.zeros(diff.shape, dtype=np.int8)
    sign[diff > SIGN_ZERO_EPSILON] = 1
    sign[diff < -SIGN_ZERO_EPSILON] = -1

    return {
        "diff": diff,
        "amp": amp,
        "sign": sign,
        "group_frame_ids": [group.get("frame_ids") for group in groups],
    }


# 组有效性功能：组合 K-of-N 振幅门、周期闭合斜率门、主导符号模式和跨周期振幅 CV 门。
def evaluate_group_validity(
    groups: dict,
    features: dict,
    params: DetectorParameters,
) -> dict:
    """标记每个块内哪些周期组通过振幅门、闭合斜率门，并进入主导模式和 CV 门。

    groups 来自 group_frames()，features 来自 build_features()；
    amp/sign 形状为 [group_count, position_count, block_count]。
    返回的组级布尔矩阵形状统一为 [group_count, block_count]。
    """
    # 从分组布局取派生量，避免重新计算 group_size 相关参数。
    layout = groups["layout"]
    group_count = layout["group_count"]
    group_size = layout["group_size"]
    group_stride = layout["group_stride"]
    position_count = layout["position_count"]

    # 特征矩阵应保留 block 作为最后一个维度；block_count 优先以配置为准。
    amp = np.asarray(features["amp"], dtype=np.float64)
    sign = np.asarray(features["sign"], dtype=np.int8)
    block_count = params.block_count
    if amp.ndim != 3:
        raise ValueError("features['amp'] must have shape [group, position, block].")
    if group_count > 0:
        if amp.shape[0] != group_count or amp.shape[1] != position_count:
            raise ValueError("features['amp'] does not match group layout.")
        if block_count == 0:
            block_count = amp.shape[2]
        elif amp.shape[2] != block_count:
            raise ValueError(
                "features['amp'] block_count does not match params.block_count."
            )

    # 先把所有周期组的亮度窗口堆叠成 [group_count, group_size, block_count]，
    # 这样闭合斜率计算也可以一次作用于所有块。
    if group_count > 0:
        brightness = np.stack(
            [
                np.asarray(group["brightness"], dtype=np.float64)
                for group in groups["groups"]
            ]
        )
        if brightness.shape != (group_count, group_size, block_count):
            raise ValueError("groups brightness does not match features block_count.")
    else:
        brightness = np.empty((0, group_size, block_count), dtype=np.float64)

    # K-of-N：min_group_amp 是绝对亮度差门槛，K 是至少通过的位置数量。
    # min_amp_pos_ratio 为 0 时视为禁用 K-of-N，所有组直接通过。
    amp_gate_enabled = params.min_amp_pos_ratio > 0
    if amp_gate_enabled:
        min_group_amp = params.y_max * params.min_group_amp_ratio
        k_value = int(np.ceil(position_count * params.min_amp_pos_ratio))

        # amp 形状为 [group, position, block]，沿 position 统计通过振幅门的位置数。
        amp_pass_count = np.count_nonzero(amp > min_group_amp, axis=1)
        # K-of-N 的计数量通过条件为 count >= K，与严格 > 比较的阈值不同。
        amp_group_ok = amp_pass_count >= k_value
    else:
        min_group_amp = 0.0
        k_value = 0
        amp_pass_count = np.full(
            (group_count, block_count),
            position_count,
            dtype=np.int64,
        )
        amp_group_ok = np.ones((group_count, block_count), dtype=bool)

    # 闭合斜率按渐进式顺序执行：只对已通过 K-of-N 的组计算首尾斜率，
    # 未进入该阶段的组用 NaN 标记为“未判定”，不会进入后续主导模式。
    # slope_max 为 0 时视为禁用闭合斜率门，所有组直接通过。
    slope = np.full((group_count, block_count), np.nan, dtype=np.float64)
    if params.slope_max > 0:
        slope_group_ok = np.zeros((group_count, block_count), dtype=bool)
        for block in range(block_count):
            amp_valid = np.flatnonzero(amp_group_ok[:, block])
            if amp_valid.size == 0:
                continue
            valid_slope = (
                brightness[amp_valid, -1, block]
                - brightness[amp_valid, 0, block]
            ) / group_stride
            slope[amp_valid, block] = valid_slope
            # 剔除 abs(slope) > slope_max 的持续变亮、变暗或曝光趋势。
            slope_group_ok[amp_valid, block] = (
                np.abs(valid_slope) <= params.slope_max
            )
    else:
        # slope_max=0 只禁用闭合斜率门，K-of-N 振幅门仍然生效。
        slope_group_ok = amp_group_ok.copy()

    # 渐进式前提下，slope_group_ok 只可能对通过 K-of-N 的组为 True，
    # 因此它本身就是“同时通过两个门”的候选组集合。
    group_valid = slope_group_ok
    valid_group_count = np.count_nonzero(group_valid, axis=0)

    # 初始化每个块的主导致结果；无有效组或无主导模式时保持空值/0。
    class_id = np.full((group_count, block_count), -1, dtype=np.int64)
    dominant_group_ok = np.zeros((group_count, block_count), dtype=bool)
    best_candidate = np.full(block_count, -1, dtype=np.int64)
    flip_count = np.zeros(block_count, dtype=np.int64)
    sign_flip_ratio = np.zeros(block_count, dtype=np.float64)
    dominant_pattern = np.zeros((position_count, block_count), dtype=np.int8)
    dominant_ok = np.zeros(block_count, dtype=bool)

    # 主导模式按块独立判定，因此这里显式遍历 block。
    for block in range(block_count):
        valid_indices = np.flatnonzero(group_valid[:, block])
        valid_count = valid_indices.size
        if valid_count == 0:
            continue

        # 取出该块所有有效组的符号序列，形状为 [valid_count, position_count]。
        patterns = sign[valid_indices, :, block]
        assigned = np.zeros(valid_count, dtype=bool)
        class_id_per_valid = np.full(valid_count, -1, dtype=np.int64)

        best_local = -1
        best_rep = -1
        best_size = 0

        # 等价类按有效组顺序建立；已归类的组不再作为候选重复扫描。
        for local_index in range(valid_count):
            if assigned[local_index]:
                continue

            # 符号序列完全相同，包括 sign == 0，也属于同一个模式。
            matches = np.all(patterns == patterns[local_index], axis=1)
            class_id_per_valid[matches] = valid_indices[local_index]
            assigned[matches] = True
            class_size = int(np.count_nonzero(matches))

            # 并列时保留最早出现的候选组，因此只在严格更大时更新。
            if class_size > best_size:
                best_size = class_size
                best_local = local_index
                best_rep = valid_indices[local_index]

        # 保存当前块的主导模式统计量，用于最终判定和诊断。
        class_id[valid_indices, block] = class_id_per_valid
        best_candidate[block] = best_rep
        flip_count[block] = best_size
        sign_flip_ratio[block] = best_size / valid_count

        # 主导模式门槛严格使用 >；参数为 0 时表示禁用对应约束。
        count_pass = (
            params.min_match_groups == 0
            or best_size > params.min_match_groups
        )
        ratio_pass = (
            params.same_pattern_ratio == 0
            or sign_flip_ratio[block] > params.same_pattern_ratio
        )
        dominant_ok[block] = count_pass and ratio_pass
        if dominant_ok[block]:
            dominant_pattern[:, block] = patterns[best_local, :]
            dominant_members = class_id_per_valid == best_rep
            dominant_group_ok[valid_indices[dominant_members], block] = True

    # CV 门：对每个块中已经进入主导模式的组，按相位位置统计振幅稳定性。
    # mean_amp / pos_cv / cv_pos_valid 形状为 [position_count, block_count]。
    mean_amp = np.zeros((position_count, block_count), dtype=np.float64)
    pos_cv = np.full((position_count, block_count), np.nan, dtype=np.float64)
    cv_pos_valid = np.zeros((position_count, block_count), dtype=bool)
    max_pos_cv = np.full(block_count, np.nan, dtype=np.float64)
    mean_abs_diff = np.full(block_count, np.nan, dtype=np.float64)
    diff_cv = np.full(block_count, np.nan, dtype=np.float64)
    cv_ok = np.zeros(block_count, dtype=bool)

    for block in range(block_count):
        dominant_groups = np.flatnonzero(dominant_group_ok[:, block])
        if dominant_groups.size == 0:
            continue

        # 取该块主导模式的振幅，形状为 [dominant_group_count, position_count]。
        dominant_amp = amp[dominant_groups, :, block]
        mean_amp_p = np.mean(dominant_amp, axis=0)
        mean_amp[:, block] = mean_amp_p

        # 只有平均振幅超过 min_group_amp 的位置参与方差统计，
        # 避免平台位置和量化噪声主导 CV。
        pos_valid = mean_amp_p > min_group_amp
        cv_pos_valid[:, block] = pos_valid
        if not np.any(pos_valid):
            # CV 门禁用时，无有效位置也直接放行；
            # 否则保持不可判定，不产生 0 方差的假象。
            if params.max_group_amp_cv == 0:
                cv_ok[block] = dominant_ok[block]
            continue

        # CV 使用总体标准差：先求总体方差，再开平方得到 std。
        # 无有效位置时输出 NaN，表示“不可判定”，
        # 不能用 0 表示，因为 0 会被误解为“振幅完全没有方差”。
        std_amp_p = np.sqrt(
            np.var(dominant_amp, axis=0, ddof=0)
        )
        pos_cv_p = np.full(position_count, np.nan, dtype=np.float64)
        pos_cv_p[pos_valid] = std_amp_p[pos_valid] / mean_amp_p[pos_valid]
        pos_cv[:, block] = pos_cv_p
        max_pos_cv[block] = float(np.max(pos_cv_p[pos_valid]))
        mean_abs_diff[block] = float(np.mean(mean_amp_p[pos_valid]))
        diff_cv[block] = max_pos_cv[block]

        # max_group_amp_cv = 0 时禁用 CV 门；否则 max_pos_cv 超过阈值拒绝。
        if params.max_group_amp_cv == 0:
            cv_ok[block] = dominant_ok[block]
        else:
            cv_ok[block] = (
                dominant_ok[block]
                and np.isfinite(max_pos_cv[block])
                and max_pos_cv[block] <= params.max_group_amp_cv
            )

    # 返回本次组有效性判定的全部中间量和主导模式/CV 标记。
    return {
        "min_group_amp": min_group_amp,
        "k_value": k_value,
        "amp_pass_count": amp_pass_count,
        "amp_group_ok": amp_group_ok,
        "slope": slope,
        "slope_group_ok": slope_group_ok,
        "group_valid": group_valid,
        "valid_group_count": valid_group_count,
        "class_id": class_id,
        "best_candidate": best_candidate,
        "flip_count": flip_count,
        "sign_flip_ratio": sign_flip_ratio,
        "dominant_pattern": dominant_pattern,
        "dominant_group_ok": dominant_group_ok,
        "dominant_ok": dominant_ok,
        "mean_amp": mean_amp,
        "pos_cv": pos_cv,
        "cv_pos_valid": cv_pos_valid,
        "max_pos_cv": max_pos_cv,
        "mean_abs_diff": mean_abs_diff,
        "diff_cv": diff_cv,
        "cv_ok": cv_ok,
    }


# 块最终判定功能：汇总主导模式/CV 结果，并补全输出状态结构。
def finalize_block_detection(
    history: Sequence[np.ndarray] | np.ndarray,
    validity: dict,
    params: DetectorParameters,
) -> dict:
    """根据组有效性结果生成 block_flags、flicker_block_num 和诊断输出。

    history 的逻辑形状为 [frame, block]；validity 来自 evaluate_group_validity()。
    输出中的块级数组形状统一为 [block_count]。
    """
    values = np.asarray(history, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError("history must have shape [frame, block].")

    frame_count, history_block_count = values.shape
    block_flags = np.asarray(validity["cv_ok"], dtype=bool).astype(np.int8)
    if history_block_count != block_flags.size:
        raise ValueError(
            "history block_count does not match validity cv_ok size."
        )

    flicker_block_num = int(np.count_nonzero(block_flags))
    window_size = params.history
    ratio_snapshots: list[np.ndarray] = []
    delta_snapshots: list[np.ndarray] = []

    # 滑动窗口长度复用 history；窗口不足一个完整窗口时诊断量按 NaN 处理。
    if window_size > 0 and frame_count >= window_size:
        for start in range(frame_count - window_size + 1):
            window = values[start : start + window_size, :]
            max_brightness = np.max(window, axis=0)
            min_brightness = np.min(window, axis=0)

            # 最小亮度为 0 时，max/min 可能无意义，保留 inf/nan 供诊断。
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(
                    min_brightness > 0,
                    max_brightness / min_brightness,
                    np.where(max_brightness == 0, np.nan, np.inf),
                )
            ratio_snapshots.append(ratio)
            delta_snapshots.append(max_brightness - min_brightness)

    if ratio_snapshots:
        avg_ratio_diff = np.mean(np.stack(ratio_snapshots), axis=0)
        avg_delta_diff = np.mean(np.stack(delta_snapshots), axis=0)
    else:
        avg_ratio_diff = np.full(block_flags.size, np.nan, dtype=np.float64)
        avg_delta_diff = np.full(block_flags.size, np.nan, dtype=np.float64)

    # 返回最终输出状态；不可判定与无频闪都保持 block_flags = 0。
    return {
        "block_flags": block_flags,
        "flicker_block_num": flicker_block_num,
        "mean_abs_diff": np.asarray(validity["mean_abs_diff"]),
        "diff_cv": np.asarray(validity["diff_cv"]),
        "sign_flip_ratio": np.asarray(validity["sign_flip_ratio"]),
        "avg_ratio_diff": avg_ratio_diff,
        "avg_delta_diff": avg_delta_diff,
    }


# 主入口：预处理单帧 -> 写入固定容量缓存 -> 满足 min_frames 后调用分组。
def main() -> None:
    # 解析命令行参数。
    parser = argparse.ArgumentParser(description="Flicker detection pipeline")
    parser.add_argument("video", help="video path")
    parser.add_argument("--fps", type=int, default=30, help="video fps mode")
    parser.add_argument(
        "--frequency",
        type=int,
        default=50,
        help="target detection frequency in Hz",
    )
    parser.add_argument("--cols", type=int, default=None, help="block columns")
    parser.add_argument("--rows", type=int, default=None, help="block rows")
    parser.add_argument("--gamma", type=float, default=1.0, help="gamma value")
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="limit processed frames",
    )
    args = parser.parse_args()

    # 查表获取当前 fps 与检测频率对应的检测参数。
    params = lookup_detector_parameters(args.fps, args.frequency)
    if params.group_size <= 0:
        raise ValueError(
            f"group_size is not configured for fps={args.fps}, "
            f"detection_frequency={args.frequency}."
        )
    if params.min_frames <= 0:
        raise ValueError(
            f"min_frames is not configured for fps={args.fps}, "
            f"detection_frequency={args.frequency}."
        )

    # 预处理模块负责读取视频并返回元数据。
    metadata = inspect_video(
        args.video,
        args.cols or params.cols,
        args.rows or params.rows,
        args.gamma,
    )

    # 固定容量历史缓存和最近一次分组结果。
    # history 按逻辑顺序保存每帧块亮度，list 内每个元素是 [block_count]；
    # 转成 numpy 后整体形状为 [frame, block]。
    history: list[np.ndarray] = []
    frame_ids: list[int] = []
    last_result: dict | None = None
    # 逐帧读取预处理结果并更新缓存。
    for frame in iter_video_frames(
        args.video,
        metadata,
        max_frames=args.max_frames,
    ):
        # frame.blocks 是当前帧的 [block_count] 块亮度数组；
        # 累积后形成 [frame, block] 逻辑历史，但 list 形态不会直接显示第二维。
        history.append(frame.blocks)
        frame_ids.append(frame.frame_id)

        # 缓存只保留 min_frames 帧，超过后从最旧端丢弃。
        if len(history) > params.min_frames:
            history.pop(0)
            frame_ids.pop(0)

        # 满足最低缓存帧数后才开始分组检测。
        if len(history) >= params.min_frames:
            # np.asarray(history) 的形状为 [frame, block]；
            # group_frames 会同时分组所有 block，而不是在 main 里遍历 block。
            last_result = group_frames(
                np.asarray(history),
                params.group_size,
                np.asarray(frame_ids, dtype=np.int64),
            )

    # 视频帧数不足以启动检测时明确报错。
    if last_result is None:
        raise ValueError(
            f"Insufficient frames: need at least {params.min_frames}, "
            f"got {len(history)}."
        )

    # 对最后一份缓存的分组结果构建特征矩阵。
    # diff/amp/sign 的形状均为 [group_count, position_count, block_count]。
    features = build_features(last_result["groups"])

    # 输出当前缓存对应的分组信息，便于命令行验证。
    print(f"metadata: {metadata}")
    print(f"block_count: {params.block_count}")
    print(f"group_layout: {last_result['layout']}")
    print(f"groups: {len(last_result['groups'])}")
    if last_result["groups"]:
        print(f"first_group_frame_ids: {last_result['groups'][0]['frame_ids']}")
    print(
        "feature_shapes: "
        f"diff={features['diff'].shape} "
        f"amp={features['amp'].shape} "
        f"sign={features['sign'].shape}"
    )


if __name__ == "__main__":
    main()
