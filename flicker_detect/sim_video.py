from __future__ import annotations

"""仿真视频生成模块：在导入的视频背景上叠加可调伪频闪光源。

按 docs/sim_video_generation_design.md 实现，输出 MP4 视频 + 同名 JSON 参考标签。

核心口径：
- 本底一律使用导入的视频（background_video），帧率/画面大小从视频读取，不可配置；
- 驱动频率 fd 手动可调，按 PLAN.md 频率定义构建虚拟光源：
  fd -> F=2*fd -> k=round(F/fps) -> fa=|F-k*fps| -> P=fps/fa（须为整数）；
- 光源波形（rectified_sin / square(duty_cycle)）在光强频率 F 上生成、按背景
  fps 帧中点采样，加法叠加到背景目标圆区域；
- 逐块真值用"二值掩码 -> 与 preprocess 同口径的 INTER_AREA 分块"精确计算。
"""

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from flicker_detect.detector_config import (
    Y_MAX,
    lookup_detector_parameters,
)

# 8bit -> 14bit 线性换算系数（gamma=1.0 时 preprocess 的 gamma 查找表为线性映射）。
K14: float = Y_MAX / 255.0

WAVEFORMS: tuple[str, ...] = ("rectified_sin", "square")
TRAJECTORY_TYPES: tuple[str, ...] = ("static", "line", "sine")


@dataclass
class SimVideoParams:
    """仿真视频生成参数。

    不含 fps / width / height：这三者从背景视频读取，不可配置。
    """

    background_video: str
    output_path: str | None = None
    frames: int | None = None
    flicker_frequency: float = 50.0
    flicker_phase: float = 0.0
    flicker_amplitude: float = 100.0
    flicker_dc: float = 64.0
    target_radius: int = 40
    trajectory_type: str = "line"
    start_x: float | None = None
    start_y: float | None = None
    velocity_x: float = 0.6
    velocity_y: float = 0.0
    sine_amplitude: float = 30.0
    sine_frequency: float = 0.2
    waveform: str = "rectified_sin"
    duty_cycle: float = 0.5
    cols: int = 16
    rows: int = 12

    def validate(self, meta: dict[str, Any] | None = None) -> None:
        """校验参数；提供背景元数据时再做视频相关的校验。"""
        if self.frames is not None and self.frames < 1:
            raise ValueError("frames must be at least 1.")
        if self.flicker_frequency <= 0:
            raise ValueError("flicker_frequency must be positive.")
        if self.flicker_dc < 0 or self.flicker_amplitude < 0:
            raise ValueError("flicker_dc and flicker_amplitude must be non-negative.")
        if self.target_radius < 0:
            raise ValueError("target_radius must be non-negative.")
        if self.waveform not in WAVEFORMS:
            raise ValueError(f"waveform must be one of {WAVEFORMS}.")
        if self.waveform == "square" and not 0 < self.duty_cycle <= 1:
            raise ValueError("duty_cycle must be in (0, 1] when waveform is 'square'.")
        if self.trajectory_type not in TRAJECTORY_TYPES:
            raise ValueError(f"trajectory_type must be one of {TRAJECTORY_TYPES}.")
        if self.trajectory_type == "line" and not (
            math.isfinite(self.velocity_x) and math.isfinite(self.velocity_y)
        ):
            raise ValueError("velocity_x/velocity_y must be finite.")
        if self.trajectory_type == "sine" and self.sine_amplitude < 0:
            raise ValueError("sine_amplitude must be non-negative.")

        if meta is not None:
            if self.cols > meta["width"] or self.rows > meta["height"]:
                raise ValueError(
                    f"block grid {self.cols}x{self.rows} exceeds video "
                    f"{meta['width']}x{meta['height']}."
                )
            try:
                det = lookup_detector_parameters(
                    int(round(meta["fps"])), int(round(self.flicker_frequency))
                )
            except KeyError as exc:
                raise ValueError(
                    f"(fps={meta['fps']}, fd={self.flicker_frequency}) has no "
                    f"configured detector parameters."
                ) from exc
            if det.group_size <= 0:
                raise ValueError(
                    f"(fps={meta['fps']}, fd={self.flicker_frequency}) has no "
                    "integer apparent period; not detectable."
                )


def _light_wave(params: SimVideoParams, t: float) -> float:
    """归一化光源波形 w(t) in [0,1]。"""
    if params.waveform == "rectified_sin":
        return abs(
            math.sin(
                2.0 * math.pi * params.flicker_frequency * t + params.flicker_phase
            )
        )
    # square：每个光强周期 T_F 内 ON 的比例为 duty_cycle。
    x = 2.0 * params.flicker_frequency * t + params.flicker_phase / (2.0 * math.pi)
    return 1.0 if (x - math.floor(x)) < params.duty_cycle else 0.0


def light_at(params: SimVideoParams, frame_id: int, fps: float) -> float:
    """帧中点光源提升量（8 bit 空间，可能超过 255，渲染时裁切）。"""
    t = (frame_id + 0.5) / fps
    return params.flicker_dc + params.flicker_amplitude * _light_wave(params, t)


def _resolve_start(params: SimVideoParams, width: int, height: int) -> tuple[float, float]:
    """起点默认取画面中心偏左。"""
    sx = params.start_x if params.start_x is not None else width * 0.35
    sy = params.start_y if params.start_y is not None else height * 0.5
    return sx, sy


def trajectory_positions(
    params: SimVideoParams,
    fps: float,
    frames: int,
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    """逐帧目标中心坐标（像素）。"""
    if frames < 0:
        raise ValueError("frames must be non-negative.")
    sx, sy = _resolve_start(params, width, height)
    positions: list[tuple[int, int]] = []
    for frame_id in range(frames):
        if params.trajectory_type == "static":
            cx, cy = sx, sy
        elif params.trajectory_type == "line":
            cx = sx + params.velocity_x * frame_id
            cy = sy + params.velocity_y * frame_id
        else:  # sine
            oscillation = params.sine_amplitude * math.sin(
                2.0 * math.pi * params.sine_frequency * frame_id / fps
            )
            cx, cy = sx + oscillation, sy
        positions.append((int(round(cx)), int(round(cy))))
    return positions


def _in_bounds(
    cx: int, cy: int, width: int, height: int, radius: int
) -> bool:
    """目标圆是否完全在画面内。"""
    return (
        cx - radius >= 0
        and cy - radius >= 0
        and cx + radius < width
        and cy + radius < height
    )


def _circle_mask(
    width: int, height: int, cx: int, cy: int, radius: int
) -> np.ndarray:
    """目标圆二值掩码（float64，圆内 1、圆外 0）。"""
    yy, xx = np.ogrid[:height, :width]
    return ((xx - cx) ** 2 + (yy - cy) ** 2 <= radius * radius).astype(np.float64)


def block_overlap(
    params: SimVideoParams, cx: int, cy: int, width: int, height: int
) -> np.ndarray:
    """二值目标掩码 -> 块重叠度 [cols*rows]，与 preprocess 同一口径。

    使用与 preprocess._block_average 相同的 INTER_AREA 分块平均。
    """
    mask = _circle_mask(width, height, cx, cy, params.target_radius)
    return cv2.resize(
        mask, (params.cols, params.rows), interpolation=cv2.INTER_AREA
    ).reshape(-1)


def render_frame(
    params: SimVideoParams,
    cx: int,
    cy: int,
    frame_id: int,
    background_frame: np.ndarray,
    fps: float,
) -> np.ndarray:
    """叠加光源到背景帧，返回 BGR uint8（原生分辨率，不缩放）。"""
    boost = light_at(params, frame_id, fps)
    height, width = background_frame.shape[:2]
    mask = _circle_mask(width, height, cx, cy, params.target_radius)
    blended = background_frame.astype(np.float64) + boost * mask[..., None]
    return np.clip(blended, 0.0, 255.0).astype(np.uint8)


def open_background(params: SimVideoParams) -> tuple[cv2.VideoCapture, dict[str, Any]]:
    """打开背景视频并解析元数据（fps/分辨率/帧数，均不可配置）。"""
    path = Path(params.background_video)
    if not path.is_file():
        raise FileNotFoundError(f"Background video not found: {path}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open background video: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or width <= 0 or height <= 0:
        cap.release()
        raise ValueError(
            f"Invalid background video metadata: fps={fps}, size={width}x{height}."
        )
    return cap, {
        "path": str(path),
        "fps": fps,
        "width": width,
        "height": height,
        "frame_count": frame_count if frame_count > 0 else None,
    }


def _resolve_frames(params: SimVideoParams, meta: dict[str, Any]) -> int:
    """输出帧数：默认背景视频帧数，frames 作上限截断。"""
    total = meta["frame_count"]
    frames = params.frames if params.frames is not None else total
    if frames is None:
        raise ValueError("frames is required when background frame count is unknown.")
    if total is not None:
        frames = min(frames, total)
    if frames <= 0:
        raise ValueError("no frames to write; check --frames and background length.")
    return frames


def _derive(params: SimVideoParams, meta: dict[str, Any], frames: int) -> dict[str, Any]:
    """派生量：帧率/分辨率来源与 fd -> F -> fa -> P 关系。"""
    fps = meta["fps"]
    fd = params.flicker_frequency
    light_freq = 2.0 * fd
    fold = round(light_freq / fps)
    apparent_freq = abs(light_freq - fold * fps)
    det = lookup_detector_parameters(int(round(fps)), int(round(fd)))
    position_count = det.position_count
    k_value = (
        0
        if det.min_amp_pos_ratio <= 0
        else math.ceil(position_count * det.min_amp_pos_ratio)
    )
    return {
        "fps": fps,
        "frames": frames,
        "width": meta["width"],
        "height": meta["height"],
        "cols": params.cols,
        "rows": params.rows,
        "y_max": Y_MAX,
        "k14": round(K14, 6),
        "light_freq": round(light_freq, 3),
        "apparent_freq": round(apparent_freq, 3),
        "P": det.group_stride,
        "group_size": det.group_size,
        "min_group_amp": round(det.y_max * det.min_group_amp_ratio, 3),
        "k_value": k_value,
    }


def _frame_info(
    params: SimVideoParams, meta: dict[str, Any], positions: list[tuple[int, int]]
) -> tuple[list[dict[str, Any]], list[float], list[int]]:
    """逐帧信息 + 逐块统计（max_overlap / stay_frames）。"""
    fps = meta["fps"]
    width = meta["width"]
    height = meta["height"]
    block_count = params.cols * params.rows
    frames_list: list[dict[str, Any]] = []
    max_overlap = [0.0] * block_count
    stay_frames = [0] * block_count
    for frame_id, (cx, cy) in enumerate(positions):
        boost = light_at(params, frame_id, fps)
        overlap = block_overlap(params, cx, cy, width, height)
        for b in range(block_count):
            if overlap[b] > 0:
                stay_frames[b] += 1
            if overlap[b] > max_overlap[b]:
                max_overlap[b] = float(overlap[b])
        frames_list.append(
            {
                "frame_id": frame_id,
                "cx": int(cx),
                "cy": int(cy),
                "in_bounds": _in_bounds(
                    cx, cy, width, height, params.target_radius
                ),
                "light8": round(boost, 3),
                "light14": round(boost * K14, 3),
            }
        )
    return frames_list, [round(value, 3) for value in max_overlap], stay_frames


def build_reference(
    params: SimVideoParams, meta: dict[str, Any]
) -> dict[str, Any]:
    """构建参考标签（独立于视频写出，供测试断言）。"""
    params.validate(meta)
    frames = _resolve_frames(params, meta)
    positions = trajectory_positions(
        params, meta["fps"], frames, meta["width"], meta["height"]
    )
    frames_list, max_overlap, stay = _frame_info(params, meta, positions)
    return {
        "params": asdict(params),
        "derived": _derive(params, meta, frames),
        "frames": frames_list,
        "block_stats": {"max_overlap": max_overlap, "stay_frames": stay},
    }


def _open_writer(
    output_path: str, fps: float, width: int, height: int
) -> tuple[cv2.VideoWriter, str, str]:
    """探测编码器：优先浏览器可解码的 H.264（avc1），失败回退 mp4v/MJPG。"""
    primary = Path(output_path)
    candidates = [
        (primary, cv2.VideoWriter_fourcc(*"avc1"), "h264"),
        (primary, cv2.VideoWriter_fourcc(*"mp4v"), "mp4v"),
        (primary.with_suffix(".avi"), cv2.VideoWriter_fourcc(*"MJPG"), "MJPG"),
    ]
    for path, fourcc, codec in candidates:
        writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
        if writer.isOpened():
            return writer, str(path), codec
        writer.release()
    raise RuntimeError(f"No usable video codec for output: {output_path}")


def generate_video(params: SimVideoParams) -> dict[str, Any]:
    """生成频闪视频（MP4 + 同名 JSON 参考标签），返回输出元数据。"""
    if not params.output_path:
        raise ValueError("output_path is required.")
    cap, meta = open_background(params)
    try:
        params.validate(meta)
        fps = meta["fps"]
        width = meta["width"]
        height = meta["height"]
        frames = _resolve_frames(params, meta)
        positions = trajectory_positions(params, fps, frames, width, height)
        frames_list, max_overlap, stay = _frame_info(params, meta, positions)

        writer, used_path, codec = _open_writer(params.output_path, fps, width, height)
        written = 0
        try:
            for frame_id in range(frames):
                ok, bg_frame = cap.read()
                if not ok:
                    break
                cx, cy = positions[frame_id]
                out_frame = render_frame(params, cx, cy, frame_id, bg_frame, fps)
                writer.write(out_frame)
                written += 1
        finally:
            writer.release()

        reference = {
            "params": asdict(params),
            "derived": _derive(params, meta, frames),
            "frames": frames_list,
            "block_stats": {"max_overlap": max_overlap, "stay_frames": stay},
            "output": {
                "path": used_path,
                "codec": codec,
                "fps": fps,
                "width": width,
                "height": height,
                "frames": written,
            },
        }
        ref_path = Path(used_path).with_suffix(".json")
        with open(ref_path, "w", encoding="utf-8") as fh:
            json.dump(reference, fh, ensure_ascii=False, indent=2)
        return {
            "output_path": used_path,
            "reference_path": str(ref_path),
            "codec": codec,
            "fps": fps,
            "width": width,
            "height": height,
            "frames": written,
        }
    finally:
        cap.release()


def main(argv: list[str] | None = None) -> int:
    """CLI：在导入视频背景上叠加伪频闪光源生成频闪视频。"""
    parser = argparse.ArgumentParser(
        description="在导入视频背景上叠加伪频闪光源生成频闪视频"
    )
    parser.add_argument(
        "--background-video", required=True, help="背景视频路径（必填）"
    )
    parser.add_argument("--output", dest="output_path", required=True, help="输出 MP4 路径")
    parser.add_argument(
        "--frames", type=int, default=None, help="输出帧数上限（默认背景视频帧数）"
    )
    parser.add_argument(
        "--flicker-frequency",
        type=float,
        default=50.0,
        help="驱动频率 fd（Hz），手动可调",
    )
    parser.add_argument("--flicker-phase", type=float, default=0.0, help="AC 初相（rad）")
    parser.add_argument(
        "--flicker-amplitude", type=float, default=100.0, help="频闪强度（灰度幅值）"
    )
    parser.add_argument("--flicker-dc", type=float, default=64.0, help="光源 DC 提升量")
    parser.add_argument("--target-radius", type=int, default=40, help="目标半径（像素）")
    parser.add_argument("--trajectory-type", choices=TRAJECTORY_TYPES, default="line")
    parser.add_argument("--start-x", type=float, default=None, help="轨迹起点 x（默认画面中心偏左）")
    parser.add_argument("--start-y", type=float, default=None, help="轨迹起点 y（默认画面中心偏左）")
    parser.add_argument("--velocity-x", type=float, default=0.6, help="line 型速度 x（像素/帧）")
    parser.add_argument("--velocity-y", type=float, default=0.0, help="line 型速度 y（像素/帧）")
    parser.add_argument("--sine-amplitude", type=float, default=30.0, help="sine 型摆动幅度（像素）")
    parser.add_argument("--sine-frequency", type=float, default=0.2, help="sine 型摆动频率（Hz）")
    parser.add_argument("--waveform", choices=WAVEFORMS, default="rectified_sin")
    parser.add_argument("--duty-cycle", type=float, default=0.5, help="square 波形占空比 (0,1]")
    parser.add_argument("--cols", type=int, default=16, help="参考块网格列数")
    parser.add_argument("--rows", type=int, default=12, help="参考块网格行数")
    args = parser.parse_args(argv)

    params = SimVideoParams(**vars(args))
    result = generate_video(params)
    print(
        f"written: {result['frames']} frames -> {result['output_path']} "
        f"(codec={result['codec']})"
    )
    print(f"reference: {result['reference_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
