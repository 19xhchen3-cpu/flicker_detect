from __future__ import annotations

"""视频前置转换模块。

职责：读取视频 -> 灰度化 -> gamma 提亮 -> 按 cols x rows 分块平均，
输出供检测算法使用的逐帧块亮度数据。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np

# 可修改：检测算法使用的 14 bit 亮度满量程。
DEFAULT_Y_MAX: int = 16383


@dataclass(frozen=True)
class VideoMetadata:
    """视频源与转换参数元数据。"""

    source_path: str
    fps: float
    frame_count: int | None
    width: int
    height: int
    source_dtype: str
    cols: int
    rows: int
    gamma: float
    y_max: int


@dataclass(frozen=True)
class ProcessedFrame:
    """转换后的一帧块亮度数据。"""

    frame_id: int
    timestamp: float
    blocks: np.ndarray
    min_brightness: float
    max_brightness: float
    mean_brightness: float
    source_gray: np.ndarray | None = None


FrameCallback = Callable[[ProcessedFrame, VideoMetadata], None]


def inspect_video(
    video_path: str,
    cols: int,
    rows: int,
    gamma: float = 1.0,
    y_max: int = DEFAULT_Y_MAX,
) -> VideoMetadata:
    """读取视频元数据和首帧，校验转换参数。"""
    _validate_params(cols, rows, gamma, y_max)

    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {path}")

    try:
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Failed to read first frame: {path}")

        gray = _to_gray(frame)
        if cols > gray.shape[1] or rows > gray.shape[0]:
            raise ValueError(
                f"Block grid {cols}x{rows} exceeds frame {gray.shape[1]}x{gray.shape[0]}."
            )

        frame_count = _safe_frame_count(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        return VideoMetadata(
            source_path=str(path),
            fps=float(cap.get(cv2.CAP_PROP_FPS)),
            frame_count=frame_count,
            width=gray.shape[1],
            height=gray.shape[0],
            source_dtype=str(gray.dtype),
            cols=cols,
            rows=rows,
            gamma=gamma,
            y_max=y_max,
        )
    finally:
        cap.release()


def iter_video_frames(
    video_path: str,
    metadata: VideoMetadata,
    max_frames: int | None = None,
    on_frame: FrameCallback | None = None,
    include_source_gray: bool = False,
) -> Iterator[ProcessedFrame]:
    """逐帧执行灰度化、gamma 提亮和分块平均。

    frame_id 从 0 开始自动递增，作为检测算法使用的帧索引。
    """
    if max_frames is not None and max_frames < 0:
        raise ValueError("max_frames must be non-negative or None.")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    try:
        frame_id = 0
        while max_frames is None or frame_id < max_frames:
            ok, frame = cap.read()
            if not ok:
                break

            gray = _to_gray(frame)
            bright = _apply_gamma_to_gray(
                gray,
                metadata.gamma,
                metadata.y_max,
            )
            blocks = _block_average(bright, metadata.rows, metadata.cols)

            # 时间戳默认按帧号 / fps 计算；后续接入真实 PTS 时可替换这里。
            timestamp = frame_id / metadata.fps if metadata.fps > 0 else float("nan")
            processed = ProcessedFrame(
                frame_id=frame_id,
                timestamp=timestamp,
                blocks=blocks,
                min_brightness=float(np.min(blocks)),
                max_brightness=float(np.max(blocks)),
                mean_brightness=float(np.mean(blocks)),
                source_gray=gray if include_source_gray else None,
            )
            yield processed

            if on_frame is not None:
                on_frame(processed, metadata)

            frame_id += 1
    finally:
        cap.release()


def _validate_params(cols: int, rows: int, gamma: float, y_max: int) -> None:
    """校验可修改的转换参数。"""
    if cols <= 0 or rows <= 0:
        raise ValueError("cols and rows must be positive.")
    if gamma <= 0:
        raise ValueError("gamma must be positive.")
    if y_max <= 0:
        raise ValueError("y_max must be positive.")


def _to_gray(frame: np.ndarray) -> np.ndarray:
    """把 BGR 或灰度帧统一转成灰度图。"""
    if frame.ndim == 2:
        return frame
    if frame.ndim == 3 and frame.shape[2] >= 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"Unsupported frame shape: {frame.shape}")


def _to_unit_gray(gray: np.ndarray) -> np.ndarray:
    """按位深把灰度图归一化到 [0, 1]。

    浮点输入按数值范围自动识别为 0-1、0-255 或 0-65535。
    """
    if gray.dtype == np.uint8:
        return gray.astype(np.float64) / 255.0
    if gray.dtype == np.uint16:
        return gray.astype(np.float64) / 65535.0
    if gray.dtype in (np.float32, np.float64):
        arr = gray.astype(np.float64)
        maximum = float(np.max(arr))
        if maximum <= 1.0:
            return arr
        if maximum <= 255.0:
            return arr / 255.0
        if maximum <= 65535.0:
            return arr / 65535.0
        raise ValueError(f"Unsupported float range: {maximum}")
    raise ValueError(f"Unsupported gray dtype: {gray.dtype}")


def _apply_gamma(gray01: np.ndarray, gamma: float, y_max: int) -> np.ndarray:
    """执行 gamma 提亮，gamma < 1 时提亮，gamma = 1 时保持原亮度。"""
    return y_max * np.power(np.clip(gray01, 0.0, 1.0), gamma)


def _apply_gamma_to_gray(
    gray: np.ndarray,
    gamma: float,
    y_max: int,
) -> np.ndarray:
    """对灰度图执行 gamma，8bit 输入使用查找表加速。"""
    if gray.dtype == np.uint8:
        lookup = np.array(
            [
                y_max * ((value / 255.0) ** gamma)
                for value in range(256)
            ],
            dtype=np.float64,
        )
        return lookup[gray]
    return _apply_gamma(_to_unit_gray(gray), gamma, y_max)


def _block_average(
    image: np.ndarray,
    rows: int,
    cols: int,
) -> np.ndarray:
    """把图像按 rows x cols 网格区域压缩，返回行优先的一维数组。

    使用 INTER_AREA 做区域平均；对整除网格等价于分块平均，
    非整除网格采用像素面积加权近似，以兼顾 4K 视频的处理速度。
    """
    return cv2.resize(
        image,
        (cols, rows),
        interpolation=cv2.INTER_AREA,
    ).reshape(-1)


def _safe_frame_count(value: float) -> int | None:
    """帧数不可靠或为 0 时返回 None。"""
    count = int(round(value))
    return count if count > 0 else None
