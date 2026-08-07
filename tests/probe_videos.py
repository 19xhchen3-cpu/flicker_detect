from __future__ import annotations

"""批量探测测试视频目录，筛选 OpenCV 可以正常读取的文件。"""

import json
from pathlib import Path

import cv2


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
    """读取单个候选文件的视频元数据，失败时只保留文件名信息。"""
    info = {
        "name": path.name,
        "path": str(path),
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


def main() -> None:
    root = Path(r"D:\照片\x5")
    rows = [
        probe_video(path)
        for path in sorted(root.iterdir())
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]
    for info in rows:
        print(json.dumps(info, ensure_ascii=False))
    print(f"TOTAL {len(rows)} OK {sum(1 for info in rows if info['ok'])}")


if __name__ == "__main__":
    main()
