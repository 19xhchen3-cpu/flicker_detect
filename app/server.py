from __future__ import annotations

"""视频前置转换 Web 服务。

前端上传视频后，本服务调用 flicker_detect.preprocess 逐帧转换，
以 NDJSON 流式返回进度和每帧块亮度数据。
"""

import json
import base64
import math
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
    SIGN_ZERO_EPSILON,
    DetectorParameters,
    PARAMETER_TABLE,
    group_size_for,
    lookup_detector_parameters,
)
from flicker_detect.moudle1 import HistoryBuffer
from flicker_detect.preprocess import inspect_video, iter_video_frames

APP_DIR = Path(__file__).resolve().parent

# 默认使用 30fps、50Hz 参数表里的 cols/rows，当前填写值为 16x12。
DEFAULT_PARAMS = PARAMETER_TABLE[(30, 50)]
ENGINE = FlickerEngine()

# 最近一次转换视频的块亮度与元数据，供服务端重算和单帧详情使用。
# 检测计算全部在服务端 moudle1 完成，前端只负责调度与展示。
_CURRENT_BLOCKS: list[list[float]] = []
_CURRENT_VIDEO: dict[str, object] | None = None


def _window_layout(H: int, group_size: int) -> dict:
    """计算检测窗口的分组布局，语义与 moudle1 的 detect_window 一致。"""
    P = group_size - 1
    G = (H - 1) // P if H > 0 and P > 0 else 0
    used_frames = group_size + (G - 1) * P if G > 0 else 0
    drop_count = H - used_frames
    return {
        "group_size": group_size,
        "group_stride": P,
        "position_count": P,
        "group_count": G,
        "used_frames": used_frames,
        "drop_count": drop_count,
        "group_starts": [drop_count + g * P for g in range(G)],
    }


def _group_view(blocks: list[list[float]], params: DetectorParameters) -> dict:
    """生成供前端悬停与曲线着色的逐块逐组诊断（不改动 moudle1）。"""
    H = len(blocks)
    layout = _window_layout(H, params.group_size)
    P = layout["position_count"]
    G = layout["group_count"]
    B = params.block_count
    starts = layout["group_starts"]
    min_group_amp = params.y_max * params.min_group_amp_ratio
    amp_gate_enabled = params.min_amp_pos_ratio > 0
    k_value = (
        math.ceil(P * params.min_amp_pos_ratio) if amp_gate_enabled else 0
    )
    effective_amp = 0.0 if k_value == 0 else min_group_amp

    group_amp_pass = [[0] * G for _ in range(B)]
    group_slope = [[float("nan")] * G for _ in range(B)]
    group_valid = [[False] * G for _ in range(B)]
    group_dominant = [[False] * G for _ in range(B)]
    group_sign = [[""] * G for _ in range(B)]
    valid_group_count = [0] * B
    valid_codes: list[list[int]] = [[] for _ in range(B)]
    valid_groups: list[list[int]] = [[] for _ in range(B)]

    for g in range(G):
        start = starts[g]
        for b in range(B):
            pass_count = 0
            for p in range(P):
                diff = blocks[start + p + 1][b] - blocks[start + p][b]
                if abs(diff) > effective_amp:
                    pass_count += 1
            if k_value == 0:
                pass_count = P
            group_amp_pass[b][g] = pass_count

            amp_ok = k_value == 0 or pass_count >= k_value
            slope_value = float("nan")
            slope_ok = amp_ok
            if params.slope_max > 0 and amp_ok:
                slope_value = (
                    blocks[start + params.group_stride][b]
                    - blocks[start][b]
                ) / params.group_stride
                slope_ok = abs(slope_value) <= params.slope_max
            group_slope[b][g] = slope_value
            group_valid[b][g] = slope_ok

            sign_parts: list[str] = []
            code = 0
            for p in range(P):
                diff = blocks[start + p + 1][b] - blocks[start + p][b]
                sign_parts.append(
                    "+" if diff > SIGN_ZERO_EPSILON
                    else ("-" if diff < -SIGN_ZERO_EPSILON else "0")
                )
                digit = (
                    2 if diff > SIGN_ZERO_EPSILON
                    else (0 if diff < -SIGN_ZERO_EPSILON else 1)
                )
                code = code * 3 + digit
            group_sign[b][g] = "".join(sign_parts)
            if slope_ok:
                valid_codes[b].append(code)
                valid_groups[b].append(g)

    # 主导模式与 moudle1 一致：严格大于更新，先达到最大计数的模式胜出。
    for b in range(B):
        valid_count = len(valid_groups[b])
        valid_group_count[b] = valid_count
        if valid_count == 0:
            continue
        counts: dict[int, int] = {}
        best_size = 0
        best_code = -1
        for code in valid_codes[b]:
            counts[code] = counts.get(code, 0) + 1
            if counts[code] > best_size:
                best_size = counts[code]
                best_code = code
        if best_code < 0:
            continue
        sign_flip_ratio = best_size / valid_count
        count_pass = (
            params.min_match_groups == 0
            or best_size > params.min_match_groups
        )
        ratio_pass = (
            params.same_pattern_ratio == 0
            or sign_flip_ratio > params.same_pattern_ratio
        )
        if not (count_pass and ratio_pass):
            continue
        for local_index, g in enumerate(valid_groups[b]):
            if valid_codes[b][local_index] == best_code:
                group_dominant[b][g] = True

    return {
        "valid_group_count": valid_group_count,
        "group_amp_pass": group_amp_pass,
        "group_slope": group_slope,
        "group_valid": group_valid,
        "group_dominant": group_dominant,
        "group_sign": group_sign,
    }


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
        if parsed.path == "/api/detect":
            self._handle_frame_detect(parsed.query)
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
        if parsed.path == "/api/recompute":
            self._handle_recompute()
            return
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
        global _CURRENT_BLOCKS, _CURRENT_VIDEO
        _CURRENT_BLOCKS = []
        _CURRENT_VIDEO = None
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
                _CURRENT_BLOCKS.append(frame.blocks.tolist())
                processed_count += 1
        except BrokenPipeError:
            return
        except Exception as exc:
            self._write_line({"type": "error", "error": str(exc)})
            self._write_chunk(b"")
            return

        _CURRENT_VIDEO = {
            "fps": metadata.fps,
            "fps_mode": round(metadata.fps),
            "cols": metadata.cols,
            "rows": metadata.rows,
            "frame_count": processed_count,
            "block_count": metadata.cols * metadata.rows,
            "y_max": metadata.y_max,
        }
        self._write_line({"type": "done", "processed": processed_count})
        self._write_chunk(b"")

    def _handle_recompute(self) -> None:
        """按前端参数用 moudle1 重算全部帧，返回紧凑检测结果。"""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b"{}"
            raw = json.loads(body.decode("utf-8") or "{}")
            params_raw = dict(raw.get("params") or {})
            if raw.get("fps") is not None:
                params_raw["fps"] = raw["fps"]
            params = self._request_params(params_raw)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        if not _CURRENT_BLOCKS:
            self._send_json(404, {"error": "No video data available."})
            return

        buffer = HistoryBuffer(params.block_count, params.history)
        results: list[dict | None] = []
        for frame_id, blocks in enumerate(_CURRENT_BLOCKS):
            buffer.push_frame(blocks, frame_id=frame_id)
            if buffer.valid_count < params.history:
                results.append(None)
                continue
            result = buffer.detect(params)
            if result is None:
                results.append(None)
                continue
            window_blocks = _CURRENT_BLOCKS[
                frame_id - params.history + 1 : frame_id + 1
            ]
            results.append(
                self._compact_result(frame_id, result, params, window_blocks)
            )

        layout = _window_layout(params.history, params.group_size)
        self._send_json(
            200,
            {
                "meta": {
                    "frame_count": len(_CURRENT_BLOCKS),
                    "block_count": params.block_count,
                    "group_count": layout["group_count"],
                    "position_count": layout["position_count"],
                },
                "results": self._jsonable(results),
            },
        )

    def _handle_frame_detect(self, query: str) -> None:
        """返回指定帧所在检测窗口的完整分组诊断，供悬停与曲线着色。"""
        raw = {key: values[-1] for key, values in parse_qs(query).items()}
        try:
            frame_id = int(raw.get("frame", str(len(_CURRENT_BLOCKS) - 1)))
            params = self._request_params(raw)
        except (ValueError, KeyError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        if not _CURRENT_BLOCKS or frame_id < 0 or frame_id >= len(_CURRENT_BLOCKS):
            self._send_json(404, {"error": "Frame not available."})
            return
        if frame_id < params.history - 1:
            self._send_json(200, {"frame_id": frame_id, "ready": False})
            return

        buffer = HistoryBuffer(params.block_count, params.history)
        for index in range(frame_id + 1):
            buffer.push_frame(_CURRENT_BLOCKS[index], frame_id=index)
        result = buffer.detect(params)
        if result is None:
            self._send_json(200, {"frame_id": frame_id, "ready": False})
            return

        window_blocks = _CURRENT_BLOCKS[
            frame_id - params.history + 1 : frame_id + 1
        ]
        layout = _window_layout(params.history, params.group_size)
        result.update(_group_view(window_blocks, params))
        stride = params.group_stride
        first_frame_id = frame_id - (params.history - 1)
        result["groups"] = [
            {
                "group_index": group_index,
                "start_frame": first_frame_id + start,
                "end_frame": first_frame_id + start + stride,
            }
            for group_index, start in enumerate(layout["group_starts"])
        ]
        result["group_count"] = layout["group_count"]
        result["position_count"] = layout["position_count"]
        result["frame_id"] = frame_id
        result["min_group_amp"] = params.y_max * params.min_group_amp_ratio
        result["k_value"] = (
            0
            if params.min_amp_pos_ratio <= 0
            else math.ceil(params.position_count * params.min_amp_pos_ratio)
        )
        result["slope_max"] = params.slope_max
        result["min_match_groups"] = params.min_match_groups
        result["same_pattern_ratio"] = params.same_pattern_ratio
        result["max_group_amp_cv"] = params.max_group_amp_cv
        self._send_json(
            200,
            {"frame_id": frame_id, "ready": True, "result": self._jsonable(result)},
        )

    def _request_params(self, raw: dict[str, object]) -> DetectorParameters:
        """把前端参数合并最近一次视频元数据，构造成 DetectorParameters。"""
        if not _CURRENT_VIDEO:
            raise ValueError("No video data available.")
        meta = _CURRENT_VIDEO

        def to_int(key: str, fallback: int) -> int:
            value = raw.get(key)
            if value is None or value == "":
                return fallback
            return int(value)

        fps = to_int("fps", int(meta.get("fps_mode") or 30))
        frequency = to_int("detection_frequency", 50)
        try:
            table_params = lookup_detector_parameters(fps, frequency)
        except KeyError as exc:
            raise ValueError(
                f"No configured parameters for fps={fps}, "
                f"detection_frequency={frequency}."
            ) from exc

        values: dict[str, object] = {
            "fps": fps,
            "detection_frequency": frequency,
            "group_size": table_params.group_size,
            "min_frames": table_params.min_frames,
            "min_match_groups": table_params.min_match_groups,
            "min_group_amp_ratio": table_params.min_group_amp_ratio,
            "min_amp_pos_ratio": table_params.min_amp_pos_ratio,
            "same_pattern_ratio": table_params.same_pattern_ratio,
            "slope_max": table_params.slope_max,
            "max_group_amp_cv": table_params.max_group_amp_cv,
            "y_max_bit": table_params.y_max_bit,
            "cols": int(meta["cols"]),
            "rows": int(meta["rows"]),
        }

        for key, cast in (
            ("group_size", int),
            ("min_frames", int),
            ("min_match_groups", int),
            ("min_group_amp_ratio", float),
            ("min_amp_pos_ratio", float),
            ("same_pattern_ratio", float),
            ("slope_max", float),
            ("max_group_amp_cv", float),
            ("y_max_bit", int),
        ):
            value = raw.get(key)
            if value is None or value == "":
                continue
            try:
                values[key] = cast(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid value for {key}.") from exc

        if int(values["group_size"]) < 2:
            raise ValueError(f"group_size is not configured for fps={fps}, {frequency} Hz.")
        if int(values["min_frames"]) < 1:
            raise ValueError("min_frames must be at least 1.")
        return DetectorParameters(**values)

    def _compact_result(
        self,
        frame_id: int,
        result: dict,
        params: DetectorParameters,
        window_blocks: list[list[float]],
    ) -> dict:
        """把一次满窗检测结果压缩成前端需要的紧凑结构。"""
        layout = _window_layout(params.history, params.group_size)
        stride = params.group_stride
        first_frame_id = frame_id - (params.history - 1)
        groups = [
            {
                "group_index": group_index,
                "start_frame": first_frame_id + start,
                "end_frame": first_frame_id + start + stride,
            }
            for group_index, start in enumerate(layout["group_starts"])
        ]
        block_count = params.block_count
        avg_ratio_diff = [float("nan")] * block_count
        avg_delta_diff = [float("nan")] * block_count
        for b in range(block_count):
            min_value = min(frame[b] for frame in window_blocks)
            max_value = max(frame[b] for frame in window_blocks)
            avg_delta_diff[b] = max_value - min_value
            if min_value > 0:
                avg_ratio_diff[b] = max_value / min_value
            elif max_value == 0:
                avg_ratio_diff[b] = float("nan")
            else:
                avg_ratio_diff[b] = float("inf")

        return {
            "frame_id": frame_id,
            "flicker_block_num": result["flicker_block_num"],
            "block_flags": result["block_flags"],
            "sign_flip_ratio": result["sign_flip_ratio"],
            "mean_abs_diff": result["mean_abs_diff"],
            "diff_cv": result["diff_cv"],
            "flip_count": result["flip_count"],
            "avg_ratio_diff": avg_ratio_diff,
            "avg_delta_diff": avg_delta_diff,
            "group_count": layout["group_count"],
            "position_count": layout["position_count"],
            "groups": groups,
            "min_group_amp": params.y_max * params.min_group_amp_ratio,
            "k_value": (
                0
                if params.min_amp_pos_ratio <= 0
                else math.ceil(params.position_count * params.min_amp_pos_ratio)
            ),
            "slope_max": params.slope_max,
            "min_match_groups": params.min_match_groups,
            "same_pattern_ratio": params.same_pattern_ratio,
            "max_group_amp_cv": params.max_group_amp_cv,
        }

    @staticmethod
    def _jsonable(value: object) -> object:
        """递归把 NaN/Inf 转成 None，保证浏览器 JSON.parse 可解析。"""
        if isinstance(value, float):
            return None if not math.isfinite(value) else value
        if isinstance(value, dict):
            return {
                key: ConvertRequestHandler._jsonable(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [ConvertRequestHandler._jsonable(item) for item in value]
        return value

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
