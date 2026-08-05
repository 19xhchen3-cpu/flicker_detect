from __future__ import annotations

"""视频前置转换模块的单元测试。"""

import unittest
from pathlib import Path

import cv2
import numpy as np

from flicker_detect.preprocess import (
    _block_average,
    inspect_video,
    iter_video_frames,
)


def _write_synthetic_video(path: Path, frames: list[np.ndarray]) -> None:
    """把灰度帧写成临时 mp4 视频，用于测试读取流程。"""
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        30.0,
        (width, height),
    )
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()


class PreprocessTest(unittest.TestCase):
    def test_invalid_video_path_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            inspect_video("not_exist.mp4", cols=4, rows=3)

    def test_synthetic_video_metadata_and_frames(self) -> None:
        import tempfile

        frames = [
            np.full((48, 64, 3), value, dtype=np.uint8)
            for value in (10, 40, 80, 120)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.mp4"
            _write_synthetic_video(path, frames)

            metadata = inspect_video(str(path), cols=4, rows=3, gamma=1.0)
            processed = list(iter_video_frames(str(path), metadata))

            self.assertEqual(metadata.width, 64)
            self.assertEqual(metadata.height, 48)
            self.assertEqual(metadata.cols, 4)
            self.assertEqual(metadata.rows, 3)
            self.assertEqual(len(processed), 4)
            self.assertEqual([frame.frame_id for frame in processed], [0, 1, 2, 3])
            self.assertEqual(processed[0].blocks.shape, (12,))
            self.assertLess(processed[0].mean_brightness, processed[3].mean_brightness)

    def test_gamma_less_than_one_brightens(self) -> None:
        import tempfile

        frames = [np.full((32, 32, 3), 100, dtype=np.uint8)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gamma.mp4"
            _write_synthetic_video(path, frames)

            metadata_flat = inspect_video(str(path), cols=2, rows=2, gamma=1.0)
            metadata_bright = inspect_video(str(path), cols=2, rows=2, gamma=0.5)

            flat = next(iter(iter_video_frames(str(path), metadata_flat)))
            bright = next(iter(iter_video_frames(str(path), metadata_bright)))

            self.assertGreater(bright.mean_brightness, flat.mean_brightness)

    def test_block_average_handles_non_divisible_grid(self) -> None:
        image = np.arange(48 * 64, dtype=np.float64).reshape(48, 64)
        blocks = _block_average(image, rows=5, cols=7)

        self.assertEqual(blocks.shape, (35,))
        self.assertTrue(np.all(np.isfinite(blocks)))


if __name__ == "__main__":
    unittest.main()
