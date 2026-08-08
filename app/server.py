from __future__ import annotations

"""视频前置转换 Web 服务。

前端上传视频后，本服务调用 flicker_detect.preprocess 逐帧转换，
以 NDJSON 流式返回进度和每帧块亮度数据。
"""

import json
import base64
import tempfile
import threading
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

from flicker_app.engine import FlickerEngine, FlickerParams
from flicker_app.server import _params_from_query, _stripe_frame_from_query
from flicker_detect.detector_config import (
    DETECTION_FREQUENCIES_HZ,
    PARAMETER_TABLE,
    group_size_for,
    lookup_detector_parameters,
)
from flicker_detect.preprocess import inspect_video, iter_video_frames

APP_DIR = Path(__file__).resolve().parent

# 默认使用 30fps、50Hz 参数表里的 cols/rows，当前填写值为 16x12。
DEFAULT_PARAMS = PARAMETER_TABLE[(30, 50)]
ENGINE = FlickerEngine()

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class ConvertRequestHandler(BaseHTTPRequestHandler):
    server_version = "FlickerPreprocess/1.0"
    # 使用 HTTP/1.1，浏览器才能正确解码 chunked 流式响应。
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        """提供前端静态页面。"""
        parsed = urlparse(self.path)
        if parsed.path == "/api/options":
            self._handle_detector_options(parsed.query)
            return
        if parsed.path == "/api/simulate":
            self._handle_simulate(parsed.query)
            return
        relative = parsed.path.lstrip("/") or "index.html"
        self._send_static(relative)

    def _handle_detector_options(self, query: str) -> None:
        """返回指定 fps 下可检测频率及完整参数。"""
        try:
            fps = float(parse_qs(query).get("fps", ["30"])[0])
        except (TypeError, ValueError):
            self._send_json(400, {"error": "Invalid fps."})
            return
        self._send_json(200, {"options": self._detector_options(fps)})

    def _handle_simulate(self, query: str) -> None:
        """处理频闪仿真 API，返回曲线、帧强度和卷帘条纹。"""
        try:
            params = _params_from_query(query)
            stripe_frame_index = _stripe_frame_from_query(query)
        except ValueError as exc:
            self._send_json(400, {"status": "error", "error": str(exc)})
            return

        error = ENGINE.validate(params)
        if error:
            self._send_json(400, {"status": "error", "error": error})
            return

        result = ENGINE.calculate(params, stripe_frame_index=stripe_frame_index)
        self._send_json(
            200,
            {
                "status": "ok",
                "result": result.to_dict(),
                "params": {
                    field.name: getattr(params, field.name)
                    for field in fields(FlickerParams)
                },
            },
        )

    def do_POST(self) -> None:
        """接收视频文件并流式执行前置转换。"""
        parsed = urlparse(self.path)
        if parsed.path != "/api/convert":
            self._send_json(404, {"type": "error", "error": "Not found"})
            return

        try:
            query = parse_qs(parsed.query)
            filename = query.get("name", ["video.mp4"])[0]
            cols = int(query.get("cols", [str(DEFAULT_PARAMS.cols)])[0])
            rows = int(query.get("rows", [str(DEFAULT_PARAMS.rows)])[0])
            gamma = float(query.get("gamma", ["1.0"])[0])
            length = int(self.headers.get("Content-Length", "0"))

            if length <= 0:
                raise ValueError("Empty video upload.")

            # 可修改：上传文件保存到临时目录，转换完成后立即删除。
            temp = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=Path(filename).suffix or ".mp4",
            )
            try:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError("Video upload interrupted.")
                    temp.write(chunk)
                    remaining -= len(chunk)
                temp.close()

                self._stream_convert(temp.name, cols, rows, gamma)
            finally:
                temp.close()
                Path(temp.name).unlink(missing_ok=True)
        except Exception as exc:
            self._send_json(500, {"type": "error", "error": str(exc)})

    def _stream_convert(self, video_path: str, cols: int, rows: int, gamma: float) -> None:
        """逐帧转换并以 chunked NDJSON 返回。"""
        metadata = inspect_video(video_path, cols, rows, gamma)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        self._write_line(
            {
                "type": "meta",
                "fps": metadata.fps,
                "frame_count": metadata.frame_count,
                "width": metadata.width,
                "height": metadata.height,
                "cols": metadata.cols,
                "rows": metadata.rows,
                "gamma": metadata.gamma,
                "y_max": metadata.y_max,
                "detector_params": self._detector_params(metadata.fps),
                "detector_options": self._detector_options(metadata.fps),
            }
        )

        processed_count = 0
        try:
            for frame in iter_video_frames(
                video_path,
                metadata,
                include_source_gray=True,
            ):
                total = metadata.frame_count
                percent = (frame.frame_id / total * 100.0) if total else 0.0
                original_b64 = self._encode_gray_thumbnail(frame.source_gray)
                self._write_line(
                    {
                        "type": "progress",
                        "frame_id": frame.frame_id,
                        "timestamp": frame.timestamp,
                        "percent": percent,
                        "min": frame.min_brightness,
                        "max": frame.max_brightness,
                        "mean": frame.mean_brightness,
                        "blocks": frame.blocks.tolist(),
                        "original": original_b64,
                    }
                )
                processed_count += 1
        except BrokenPipeError:
            return
        except Exception as exc:
            self._write_line({"type": "error", "error": str(exc)})
            self._write_chunk(b"")
            return

        self._write_line({"type": "done", "processed": processed_count})
        self._write_chunk(b"")

    def _encode_gray_thumbnail(self, gray) -> str | None:
        """把原灰度帧压缩成 JPEG base64，供前端叠加显示。"""
        if gray is None:
            return None
        height, width = gray.shape
        thumb_width = 640
        thumb_height = max(1, round(thumb_width * height / width))
        thumb = cv2.resize(
            gray,
            (thumb_width, thumb_height),
            interpolation=cv2.INTER_AREA,
        )
        ok, encoded = cv2.imencode(
            ".jpg",
            thumb,
            [int(cv2.IMWRITE_JPEG_QUALITY), 70],
        )
        if not ok:
            return None
        return base64.b64encode(encoded).decode("ascii")

    def _detector_params(self, fps: float) -> dict[str, object]:
        """返回当前帧率下 50Hz 检测参数，供前端初始化参数编辑器。"""
        try:
            params = lookup_detector_parameters(round(fps), 50)
        except KeyError:
            params = PARAMETER_TABLE[(30, 50)]
        return self._params_dict(params)

    def _detector_options(self, fps: float) -> list[dict[str, object]]:
        """返回当前帧率下所有可查表检测频率及完整参数。"""
        fps_mode = round(fps)
        options: list[dict[str, object]] = []
        for detection_frequency in DETECTION_FREQUENCIES_HZ:
            group_size = group_size_for(fps_mode, detection_frequency)
            if group_size is None:
                continue
            params = lookup_detector_parameters(fps_mode, detection_frequency)
            values = self._params_dict(params)
            values["group_size"] = group_size
            options.append(
                {
                    "detection_frequency": detection_frequency,
                    "params": values,
                }
            )
        return options

    def _params_dict(self, params) -> dict[str, object]:
        """把 DetectorParameters 转成前端可用的参数字典。"""
        return {
            "fps": params.fps,
            "detection_frequency": params.detection_frequency,
            "group_size": params.group_size,
            "min_frames": params.min_frames,
            "min_match_groups": params.min_match_groups,
            "min_group_amp_ratio": params.min_group_amp_ratio,
            "min_amp_pos_ratio": params.min_amp_pos_ratio,
            "same_pattern_ratio": params.same_pattern_ratio,
            "slope_max": params.slope_max,
            "max_group_amp_cv": params.max_group_amp_cv,
            "y_max": params.y_max,
            "y_max_bit": params.y_max_bit,
            "cols": params.cols,
            "rows": params.rows,
        }

    def _write_line(self, payload: dict[str, object]) -> None:
        """把一行 JSON 写入 chunked 流。"""
        self._write_chunk(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )

    def _write_chunk(self, data: bytes) -> None:
        """按 HTTP chunked 编码写数据并刷新。"""
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _send_static(self, relative: str) -> None:
        root = APP_DIR.resolve()
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            self._send_json(404, {"type": "error", "error": "Not found"})
            return

        if not path.is_file():
            self._send_json(404, {"type": "error", "error": "Not found"})
            return

        data = path.read_bytes()
        content_type = MIME_TYPES.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(host: str = "127.0.0.1", port: int = 8766) -> ThreadingHTTPServer:
    """创建前置转换页面服务。"""
    server = ThreadingHTTPServer((host, port), ConvertRequestHandler)
    server.daemon_threads = True
    return server


def main() -> None:
    server = create_server()
    url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    print(f"Video preprocess app running at {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping video preprocess app.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
