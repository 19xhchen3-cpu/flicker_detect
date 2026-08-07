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
    history: list[np.ndarray] = []
    frame_ids: list[int] = []
    last_result: dict | None = None
    # 逐帧读取预处理结果并更新缓存。
    for frame in iter_video_frames(
        args.video,
        metadata,
        max_frames=args.max_frames,
    ):
        history.append(frame.blocks)
        frame_ids.append(frame.frame_id)

        # 缓存只保留 min_frames 帧，超过后从最旧端丢弃。
        if len(history) > params.min_frames:
            history.pop(0)
            frame_ids.pop(0)

        # 满足最低缓存帧数后才开始分组检测。
        if len(history) >= params.min_frames:
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
