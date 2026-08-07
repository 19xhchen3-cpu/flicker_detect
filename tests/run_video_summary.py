from __future__ import annotations

"""批量测试 D:\\照片\\x5 下可读视频的当前检测模块效果。"""

import json
import time
from pathlib import Path

import cv2
import numpy as np

from flicker_detect.detector_config import lookup_detector_parameters
from flicker_detect.fedetect_moudle import (
    build_features,
    evaluate_group_validity,
    group_frames,
)
from flicker_detect.preprocess import inspect_video, iter_video_frames


VIDEO_ROOT = Path(r"D:\照片\x5")
OUTPUT_MARKDOWN = Path(r"D:\CODING\project\flicker_detect\video_test_summary.md")
OUTPUT_JSON = Path(r"D:\CODING\project\flicker_detect\video_test_results.json")
OUTPUT_CYCLES_JSON = Path(
    r"D:\CODING\project\flicker_detect\video_test_cycles.json"
)
DETECTION_FREQUENCY_HZ = 50
MAX_FRAMES_PER_VIDEO: int | None = None

VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".3gp",
    ".insv",
    ".insp",
    ".m4v",
}


def probe_video(path: Path) -> dict:
    """返回 OpenCV 是否可读及基础视频元数据。"""
    info = {
        "name": path.name,
        "size": path.stat().st_size,
        "ok": False,
    }
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return info

    info.update(
        {
            "ok": True,
            "fps": float(cap.get(cv2.CAP_PROP_FPS)),
            "frame_count": float(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        }
    )
    cap.release()
    return info


def summarize_frame(validity: dict, block_count: int) -> dict:
    """把单个缓存窗口的判定结果压缩成便于汇总的标量。"""
    dominant_ok = np.asarray(validity["dominant_ok"], dtype=bool)
    dominant_block_count = int(np.count_nonzero(dominant_ok))
    dominant_blocks = dominant_ok.nonzero()[0]
    if dominant_blocks.size > 0:
        mean_flip_count = float(np.mean(validity["flip_count"][dominant_blocks]))
        mean_sign_flip_ratio = float(
            np.mean(validity["sign_flip_ratio"][dominant_blocks])
        )
    else:
        mean_flip_count = 0.0
        mean_sign_flip_ratio = 0.0

    return {
        "dominant_block_count": dominant_block_count,
        "dominant_block_ratio": dominant_block_count / block_count,
        "mean_valid_group_count": float(np.mean(validity["valid_group_count"])),
        "mean_flip_count": mean_flip_count,
        "mean_sign_flip_ratio": mean_sign_flip_ratio,
        "dominant_blocks": [int(index) for index in dominant_blocks],
    }


def summarize_cycle_changes(frame_summaries: list[dict], block_count: int) -> dict:
    """统计相邻检测周期之间主导块的新增、消失和变化。"""
    added: list[int] = []
    lost: list[int] = []
    changed: list[int] = []
    previous: set[int] | None = None

    for item in frame_summaries:
        current = set(item["dominant_blocks"])
        if previous is None:
            previous = current
            continue
        added.append(len(current - previous))
        lost.append(len(previous - current))
        changed.append(len(current ^ previous))
        previous = current

    if not changed:
        avg_added = 0.0
        avg_lost = 0.0
        avg_changed = 0.0
    else:
        avg_added = float(np.mean(added))
        avg_lost = float(np.mean(lost))
        avg_changed = float(np.mean(changed))

    return {
        "avg_added_blocks_per_cycle": avg_added,
        "avg_lost_blocks_per_cycle": avg_lost,
        "avg_changed_blocks_per_cycle": avg_changed,
        "block_change_rate": avg_changed / block_count,
    }


def evaluate_video(path: Path, max_frames: int | None) -> dict:
    """对单个视频执行预处理、分组、特征构建和组有效性判定。"""
    cap = cv2.VideoCapture(str(path))
    try:
        fps = round(float(cap.get(cv2.CAP_PROP_FPS)))
    except Exception:
        fps = 0
    finally:
        cap.release()

    try:
        params = lookup_detector_parameters(fps, DETECTION_FREQUENCY_HZ)
    except KeyError:
        return {
            "status": "unsupported_fps",
            "message": f"no parameter table for fps={fps}, 50Hz",
        }

    if params.group_size <= 0 or params.min_frames <= 0:
        return {
            "status": "not_configured",
            "message": "parameter cell exists but group_size/min_frames is 0",
        }

    metadata = inspect_video(
        str(path),
        params.cols,
        params.rows,
        gamma=1.0,
    )
    frame_count = metadata.frame_count
    if frame_count is not None and frame_count < params.min_frames:
        return {
            "status": "insufficient_frames",
            "message": f"frame_count={frame_count}, min_frames={params.min_frames}",
        }

    history: list[np.ndarray] = []
    frame_ids: list[int] = []
    frame_summaries: list[dict] = []
    last_validity: dict | None = None
    processed_frames = 0

    for frame in iter_video_frames(
        str(path),
        metadata,
        max_frames=max_frames,
    ):
        history.append(frame.blocks)
        frame_ids.append(frame.frame_id)
        processed_frames += 1
        if processed_frames % 500 == 0:
            print(f"  {path.name}: processed {processed_frames} frames", flush=True)

        if len(history) > params.min_frames:
            history.pop(0)
            frame_ids.pop(0)

        if len(history) >= params.min_frames:
            grouped = group_frames(
                np.asarray(history),
                params.group_size,
                np.asarray(frame_ids, dtype=np.int64),
            )
            features = build_features(grouped["groups"])
            validity = evaluate_group_validity(grouped, features, params)
            frame_summaries.append(
                {
                    "frame_id": frame.frame_id,
                    **summarize_frame(validity, params.block_count),
                }
            )
            last_validity = validity

    if not frame_summaries:
        return {
            "status": "no_result",
            "message": "processed frames but no valid detection window",
        }

    dominant_counts = np.asarray(
        [item["dominant_block_count"] for item in frame_summaries],
        dtype=np.float64,
    )
    dominant_ratios = np.asarray(
        [item["dominant_block_ratio"] for item in frame_summaries],
        dtype=np.float64,
    )
    positive_frames = int(np.count_nonzero(dominant_counts > 0))

    return {
        "status": "ok",
        "fps": metadata.fps,
        "width": metadata.width,
        "height": metadata.height,
        "source_frame_count": metadata.frame_count,
        "processed_frames": processed_frames,
        "group_size": params.group_size,
        "min_frames": params.min_frames,
        "min_match_groups": params.min_match_groups,
        "same_pattern_ratio": params.same_pattern_ratio,
        "block_count": params.block_count,
        "result_frames": len(frame_summaries),
        "positive_frames": positive_frames,
        "positive_frame_ratio": positive_frames / len(frame_summaries),
        "avg_dominant_block_count": float(np.mean(dominant_counts)),
        "max_dominant_block_count": int(np.max(dominant_counts)),
        "avg_dominant_block_ratio": float(np.mean(dominant_ratios)),
        "max_dominant_block_ratio": float(np.max(dominant_ratios)),
        "first_dominant_frame_id": next(
            (
                item["frame_id"]
                for item in frame_summaries
                if item["dominant_block_count"] > 0
            ),
            None,
        ),
        "avg_mean_valid_group_count": float(
            np.mean([item["mean_valid_group_count"] for item in frame_summaries])
        ),
        "avg_mean_flip_count": float(
            np.mean([item["mean_flip_count"] for item in frame_summaries])
        ),
        "avg_mean_sign_flip_ratio": float(
            np.mean([item["mean_sign_flip_ratio"] for item in frame_summaries])
        ),
        "cycles": frame_summaries,
        **summarize_cycle_changes(frame_summaries, params.block_count),
    }


def format_size(size: int) -> str:
    """把文件大小格式化为易读文本。"""
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{size / 1_000:.1f} KB"


def write_summary(rows: list[dict], elapsed: float) -> None:
    """生成 Markdown 总结文档和 JSON 明细文件。"""
    ok_rows = [row for row in rows if row["status"] == "ok"]
    configured = [row for row in rows if row["status"] in {"ok", "insufficient_frames"}]

    lines = [
        "# 视频批量检测总结",
        "",
        f"- 测试目录：`{VIDEO_ROOT}`",
        f"- 目标检测频率：`{DETECTION_FREQUENCY_HZ} Hz` 驱动频率",
        f"- 每个视频处理帧数："
        f"`{'全部帧' if MAX_FRAMES_PER_VIDEO is None else MAX_FRAMES_PER_VIDEO}`",
        f"- 可读视频数：`{len(rows)}`",
        f"- 有可用检测参数的视频数：`{len(configured)}`",
        f"- 实际完成检测的视频数：`{len(ok_rows)}`",
        f"- 总耗时：`{elapsed:.1f} s`",
        "",
        "## 检测结果",
        "",
        "| 视频 | 格式 | 帧率 | 分辨率 | 结果帧数 | 阳性帧比例 | 平均主导块数 | 最大主导块数 | 平均主导块比例 | 状态 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]

    for row in rows:
        name = row["name"]
        if row["status"] == "ok":
            fps = f"{row['fps']:.2f}"
            resolution = f"{row['width']}x{row['height']}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        name,
                        Path(name).suffix,
                        fps,
                        resolution,
                        str(row["result_frames"]),
                        f"{row['positive_frame_ratio']:.2f}",
                        f"{row['avg_dominant_block_count']:.2f}",
                        str(row["max_dominant_block_count"]),
                        f"{row['avg_dominant_block_ratio']:.2f}",
                        row["status"],
                    ]
                )
                + " |"
            )
        else:
            lines.append(
                "| "
                + " | ".join(
                    [
                        name,
                        Path(name).suffix,
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        f"{row['status']}: {row.get('message', '')}",
                    ]
                )
                + " |"
            )

    lines += [
        "",
        "## 周期稳定性",
        "",
        "| 视频 | 结果帧数 | 平均新增块数 | 平均消失块数 | 平均变化块数 | 变化率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        if row["status"] == "ok":
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["name"],
                        str(row["result_frames"]),
                        f"{row['avg_added_blocks_per_cycle']:.2f}",
                        f"{row['avg_lost_blocks_per_cycle']:.2f}",
                        f"{row['avg_changed_blocks_per_cycle']:.2f}",
                        f"{row['block_change_rate']:.2f}",
                    ]
                )
                + " |"
            )
        else:
            lines.append(
                "| "
                + " | ".join(
                    [row["name"], "-", "-", "-", "-", "-"]
                )
                + " |"
            )

    lines += [
        "",
        "## 初步观察",
        "",
    ]
    if ok_rows:
        avg_dominants = [row["avg_dominant_block_count"] for row in ok_rows]
        positive_ratios = [row["positive_frame_ratio"] for row in ok_rows]
        min_match_groups = sorted(
            {row["min_match_groups"] for row in ok_rows}
        )
        same_pattern_ratios = sorted(
            {row["same_pattern_ratio"] for row in ok_rows}
        )
        lines += [
            f"- 完成检测的 `{len(ok_rows)}` 个视频中，阳性帧比例区间为 "
            f"`{min(positive_ratios):.2f} - {max(positive_ratios):.2f}`。",
            f"- 平均主导块数区间为 "
            f"`{min(avg_dominants):.2f} - {max(avg_dominants):.2f}`，"
            f"对应 192 块中约 "
            f"`{min(avg_dominants) / 192:.0%} - {max(avg_dominants) / 192:.0%}`。",
            f"- 当前配置使用 `min_match_groups = "
            f"{', '.join(str(value) for value in min_match_groups)}`、"
            f"`same_pattern_ratio = "
            f"{', '.join(f'{value:.2f}' for value in same_pattern_ratios)}`；"
            f"该结果仍只是组有效性阶段，不能直接当作最终频闪判定。",
        ]
    lines += [
        "",
        "## 说明",
        "",
        "- 当前输出的是组有效性阶段的 `dominant_ok`，尚未实现最终的 `block_flags`。",
        "- 阳性帧指该帧至少有一个块进入主导模式。",
        "- 每个检测周期对应的 `dominant_blocks` 明细见 `video_test_cycles.json`。",
        "- 没有可用参数的视频不会进入完整预处理，避免无意义读取超大文件。",
    ]

    OUTPUT_MARKDOWN.write_text("\n".join(lines), encoding="utf-8")
    summary_rows = [
        {key: value for key, value in row.items() if key != "cycles"}
        for row in rows
    ]
    cycle_rows = [
        {
            "name": row["name"],
            "cycles": row.get("cycles", []),
        }
        for row in rows
    ]
    OUTPUT_JSON.write_text(
        json.dumps(
            {
                "elapsed_seconds": elapsed,
                "rows": summary_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    OUTPUT_CYCLES_JSON.write_text(
        json.dumps(
            {
                "elapsed_seconds": elapsed,
                "rows": cycle_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    start = time.perf_counter()
    rows: list[dict] = []
    candidates = [
        path
        for path in sorted(VIDEO_ROOT.iterdir())
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]

    for path in candidates:
        probe = probe_video(path)
        if not probe["ok"]:
            rows.append(
                {
                    "name": path.name,
                    "status": "invalid",
                    "message": "OpenCV cannot open file",
                }
            )
            continue

        print(f"processing: {path.name}", flush=True)
        try:
            result = evaluate_video(path, MAX_FRAMES_PER_VIDEO)
        except Exception as exc:  # 单个视频失败不应中断整个批量测试。
            result = {
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}",
            }
        result["name"] = path.name
        result["size"] = probe.get("size", 0)
        result["size_text"] = format_size(result["size"])
        rows.append(result)

    elapsed = time.perf_counter() - start
    write_summary(rows, elapsed)
    print(f"done: {OUTPUT_MARKDOWN}")
    print(f"rows={len(rows)} ok={sum(1 for row in rows if row['status'] == 'ok')}")


if __name__ == "__main__":
    main()
