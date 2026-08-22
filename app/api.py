from __future__ import annotations

"""API 接口层：上接任意前端，下接 moudle1 等核心模块。

纯 Python 标准库实现，只做四件事：
1. 路由：/api/v1/* 的简单 if 分支；
2. 会话管理：dict + 锁，按 session_id 保存视频块亮度和元数据；
3. 参数解析：查 detector_config 参数表，校验前端覆盖值；
4. 编排与序列化：调 moudle1 检测，NaN 转 null 后返回 JSON。

moudle1 只暴露纯函数（流式增量检测），API 层不包含检测算法本身。
"""

import itertools
import json
import math
import re
import tempfile
import threading
from dataclasses import fields
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from flicker_app.engine import FlickerEngine, FlickerParams
from flicker_detect.detector_config import (
    DETECTION_FREQUENCIES_HZ,
    SIGN_ZERO_EPSILON,
    DetectorParameters,
    PARAMETER_TABLE,
    group_size_for,
    lookup_detector_parameters,
)
from flicker_detect.moudle1 import (
    create_incremental_state,
    detect_window_incremental,
)
from flicker_detect.preprocess import inspect_video, iter_video_frames

ENGINE = FlickerEngine()
DEFAULT_PARAMS = PARAMETER_TABLE[(30, 50)]

# 会话存储：session_id -> {"id", "meta", "blocks"}
SESSIONS: dict[str, dict[str, Any]] = {}
SESSIONS_LOCK = threading.Lock()
_SESSION_IDS = itertools.count(1)


class V1Error(Exception):
    """带 HTTP 状态码和错误码的 API 异常。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _get_session(session_id: str) -> dict[str, Any] | None:
    with SESSIONS_LOCK:
        return SESSIONS.get(session_id)


def _require_session(session_id: str) -> dict[str, Any]:
    session = _get_session(session_id)
    if session is None:
        raise V1Error(404, "not_found", "Session not found.")
    return session


def _require_video(session: dict[str, Any]) -> dict[str, Any]:
    meta = session.get("meta")
    blocks = session.get("blocks")
    if not meta or not blocks:
        raise V1Error(404, "no_video", "Session has no video.")
    return {"meta": meta, "blocks": blocks}


def _new_session_id() -> str:
    with SESSIONS_LOCK:
        session_id = f"v1-{next(_SESSION_IDS)}"
        SESSIONS[session_id] = {"id": session_id, "meta": None, "blocks": None}
    return session_id


def _drop_session(session_id: str) -> None:
    with SESSIONS_LOCK:
        SESSIONS.pop(session_id, None)


# ---------- 参数解析（唯一事实来源是 detector_config 参数表） ----------


def resolve_params(raw: dict[str, Any], meta: dict[str, Any] | None) -> DetectorParameters:
    """把前端参数合并视频元数据，解析成 DetectorParameters。"""
    if not meta:
        raise V1Error(404, "no_video", "Session has no video.")

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
        raise V1Error(
            400,
            "invalid_params",
            f"No configured parameters for fps={fps}, "
            f"detection_frequency={frequency}.",
        ) from exc

    values: dict[str, Any] = {
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
            raise V1Error(400, "invalid_params", f"Invalid value for {key}.") from exc

    if int(values["group_size"]) < 2:
        raise V1Error(
            400,
            "invalid_params",
            f"group_size is not configured for fps={fps}, {frequency} Hz.",
        )
    if int(values["min_frames"]) < 1:
        raise V1Error(400, "invalid_params", "min_frames must be at least 1.")
    return DetectorParameters(**values)


def params_dict(params: DetectorParameters) -> dict[str, Any]:
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


def detector_options(fps: int) -> list[dict[str, Any]]:
    """返回指定 fps 下所有可查表检测频率及完整参数。"""
    options: list[dict[str, Any]] = []
    for detection_frequency in DETECTION_FREQUENCIES_HZ:
        group_size = group_size_for(fps, detection_frequency)
        if group_size is None:
            continue
        params = lookup_detector_parameters(fps, detection_frequency)
        values = params_dict(params)
        values["group_size"] = group_size
        options.append({"detection_frequency": detection_frequency, "params": values})
    return options


def detector_params(fps: int) -> dict[str, Any]:
    """返回指定 fps 下 50Hz 的默认检测参数。"""
    try:
        params = lookup_detector_parameters(round(fps), 50)
    except KeyError:
        params = DEFAULT_PARAMS
    return params_dict(params)


# ---------- 检测结果包装（不修改 moudle1） ----------


def window_layout(H: int, group_size: int) -> dict:
    """计算检测窗口的分组布局（历史全量语义，供元信息与旧布局复用）。"""
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


def group_view(
    blocks: list[list[float]],
    params: DetectorParameters,
    starts: list[int] | None = None,
) -> dict:
    """生成逐块逐组诊断，供前端悬停与曲线着色。"""
    H = len(blocks)
    layout = window_layout(H, params.group_size)
    P = layout["position_count"]
    G = layout["group_count"]
    B = params.block_count
    if starts is None:
        starts = layout["group_starts"]
    min_group_amp = params.y_max * params.min_group_amp_ratio
    amp_gate_enabled = params.min_amp_pos_ratio > 0
    k_value = math.ceil(P * params.min_amp_pos_ratio) if amp_gate_enabled else 0
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
                    blocks[start + params.group_stride][b] - blocks[start][b]
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
        count_pass = params.min_match_groups == 0 or best_size > params.min_match_groups
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


def compact_result(
    frame_id: int,
    result: dict,
    params: DetectorParameters,
    window_blocks: list[list[float]],
    group_starts: list[int] | None = None,
    first_frame_id: int | None = None,
) -> dict:
    """把一次满窗检测结果压缩成前端需要的紧凑结构。"""
    layout = window_layout(params.history, params.group_size)
    stride = params.group_stride
    if first_frame_id is None:
        first_frame_id = frame_id - (params.history - 1)
    if group_starts is None:
        group_starts = layout["group_starts"]
    groups = [
        {
            "group_index": group_index,
            "start_frame": first_frame_id + start,
            "end_frame": first_frame_id + start + stride,
        }
        for group_index, start in enumerate(group_starts)
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


def jsonable(value: Any) -> Any:
    """递归把 NaN/Inf 转成 None，保证浏览器 JSON.parse 可解析。"""
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


# ---------- HTTP 基础 ----------


def _send_json(handler, status: int, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _send_error(handler, status: int, code: str, message: str) -> None:
    _send_json(handler, status, {"error": {"code": code, "message": message}})


def _write_chunk(handler, data: bytes) -> None:
    handler.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
    handler.wfile.write(data)
    handler.wfile.write(b"\r\n")
    handler.wfile.flush()


def _write_line(handler, payload: dict[str, Any]) -> None:
    _write_chunk(
        handler,
        (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
    )


def _read_json_body(handler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        raw = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise V1Error(400, "invalid_params", "Invalid JSON body.") from exc
    if not isinstance(raw, dict):
        raise V1Error(400, "invalid_params", "JSON body must be an object.")
    return raw


# ---------- 接口实现 ----------


def _handle_options(handler, query: str) -> None:
    try:
        fps = int(parse_qs(query).get("fps", ["30"])[0])
    except (TypeError, ValueError) as exc:
        raise V1Error(400, "invalid_params", "Invalid fps.") from exc
    _send_json(handler, 200, {"fps": fps, "options": detector_options(fps)})


def _handle_create_session(handler) -> None:
    _send_json(handler, 200, {"session_id": _new_session_id()})


def _handle_delete_session(handler, session_id: str) -> None:
    _require_session(session_id)
    _drop_session(session_id)
    _send_json(handler, 200, {"status": "ok"})


def _handle_simulate(handler) -> None:
    raw = _read_json_body(handler)
    try:
        kwargs = {
            field.name: float(raw[field.name])
            for field in fields(FlickerParams)
            if field.name in raw
        }
        params = FlickerParams(**kwargs)
    except (TypeError, ValueError) as exc:
        raise V1Error(400, "invalid_params", str(exc)) from exc
    error = ENGINE.validate(params)
    if error:
        raise V1Error(400, "invalid_params", error)
    result = ENGINE.calculate(params)
    _send_json(
        handler,
        200,
        {"status": "ok", "result": result.to_dict()},
    )


def _handle_upload_video(handler, session_id: str, query: str) -> None:
    session = _require_session(session_id)
    raw = parse_qs(query)
    filename = raw.get("name", ["video.mp4"])[0]
    try:
        cols = int(raw.get("cols", [str(DEFAULT_PARAMS.cols)])[0])
        rows = int(raw.get("rows", [str(DEFAULT_PARAMS.rows)])[0])
        gamma = float(raw.get("gamma", ["1.0"])[0])
        length = int(handler.headers.get("Content-Length", "0"))
    except (TypeError, ValueError) as exc:
        raise V1Error(400, "invalid_params", "Invalid upload parameters.") from exc
    if length <= 0:
        raise V1Error(400, "invalid_params", "Empty video upload.")

    temp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=Path(filename).suffix or ".mp4",
    )
    try:
        remaining = length
        while remaining > 0:
            chunk = handler.rfile.read(min(65536, remaining))
            if not chunk:
                raise V1Error(400, "invalid_params", "Video upload interrupted.")
            temp.write(chunk)
            remaining -= len(chunk)
        temp.close()
        _stream_convert(handler, session, temp.name, cols, rows, gamma)
    finally:
        temp.close()
        Path(temp.name).unlink(missing_ok=True)


def _stream_convert(
    handler,
    session: dict[str, Any],
    video_path: str,
    cols: int,
    rows: int,
    gamma: float,
) -> None:
    """逐帧转换视频，NDJSON 流式返回并把块亮度存入会话。"""
    metadata = inspect_video(video_path, cols, rows, gamma)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.send_header("Connection", "close")
    handler.end_headers()

    _write_line(
        handler,
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
            "detector_params": detector_params(metadata.fps),
            "detector_options": detector_options(round(metadata.fps)),
        },
    )

    blocks: list[list[float]] = []
    try:
        for frame in iter_video_frames(video_path, metadata):
            blocks.append(frame.blocks.tolist())
            total = metadata.frame_count
            percent = (frame.frame_id / total * 100.0) if total else 0.0
            _write_line(
                handler,
                {
                    "type": "progress",
                    "frame_id": frame.frame_id,
                    "timestamp": frame.timestamp,
                    "percent": percent,
                    "min": frame.min_brightness,
                    "max": frame.max_brightness,
                    "mean": frame.mean_brightness,
                    "blocks": frame.blocks.tolist(),
                },
            )
    except BrokenPipeError:
        return
    except Exception as exc:
        _write_line(handler, {"type": "error", "error": str(exc)})
        _write_chunk(handler, b"")
        return

    session["meta"] = {
        "fps": metadata.fps,
        "fps_mode": round(metadata.fps),
        "cols": metadata.cols,
        "rows": metadata.rows,
        "frame_count": len(blocks),
        "block_count": metadata.cols * metadata.rows,
        "y_max": metadata.y_max,
    }
    session["blocks"] = blocks
    _write_line(handler, {"type": "done", "processed": len(blocks)})
    _write_chunk(handler, b"")


def _detect_all(session: dict[str, Any], body: dict[str, Any]) -> dict:
    """按参数对会话内全部帧重算，返回紧凑结果。"""
    video = _require_video(session)
    meta = video["meta"]
    blocks = video["blocks"]
    params_raw = dict(body.get("params") or {})
    if body.get("fps") is not None:
        params_raw["fps"] = body["fps"]
    params = resolve_params(params_raw, meta)

    state = create_incremental_state(params)
    results: list[dict | None] = []
    for frame_id, frame_blocks in enumerate(blocks):
        result = detect_window_incremental(state, frame_blocks, frame_id=frame_id)
        if result is None:
            results.append(None)
            continue
        # 相位锁定窗口：结论对应 [window_start, window_end]，组起点为
        # 窗口起点 + g*P（无 drop，与 detect_window_incremental 一致）。
        window_blocks = blocks[state["window_start"] : state["window_end"] + 1]
        group_starts = [g * state["P"] for g in range(state["G"])]
        results.append(
            compact_result(
                state["window_end"],
                result,
                params,
                window_blocks,
                group_starts=group_starts,
                first_frame_id=state["window_start"],
            )
        )

    layout = window_layout(params.history, params.group_size)
    return {
        "meta": {
            "frame_count": len(blocks),
            "block_count": params.block_count,
            "group_count": layout["group_count"],
            "position_count": layout["position_count"],
        },
        "results": jsonable(results),
    }


def _frame_detail(
    session: dict[str, Any],
    frame_id: int,
    query: str,
) -> dict:
    """返回单帧所在窗口的完整分组诊断。"""
    video = _require_video(session)
    meta = video["meta"]
    blocks = video["blocks"]
    raw = {key: values[-1] for key, values in parse_qs(query).items()}
    params = resolve_params(raw, meta)
    if frame_id < 0 or frame_id >= len(blocks):
        raise V1Error(404, "not_found", "Frame not available.")

    state = create_incremental_state(params)
    for index in range(frame_id + 1):
        detect_window_incremental(state, blocks[index], frame_id=index)
    result = state["result"]
    if result is None:
        return {"frame_id": frame_id, "ready": False}

    window_start = state["window_start"]
    window_end = state["window_end"]
    window_blocks = blocks[window_start : window_end + 1]
    layout = window_layout(params.history, params.group_size)
    group_starts = [g * state["P"] for g in range(state["G"])]
    result.update(group_view(window_blocks, params, starts=group_starts))
    stride = params.group_stride
    result["groups"] = [
        {
            "group_index": group_index,
            "start_frame": window_start + start,
            "end_frame": window_start + start + stride,
        }
        for group_index, start in enumerate(group_starts)
    ]
    result["group_count"] = layout["group_count"]
    result["position_count"] = layout["position_count"]
    result["frame_id"] = window_end
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
    return {"frame_id": frame_id, "ready": True, "result": jsonable(result)}


def _route(handler, path: str, query: str) -> None:
    method = handler.command
    if method == "GET" and path == "/api/v1/options":
        _handle_options(handler, query)
    elif method == "POST" and path == "/api/v1/sessions":
        _handle_create_session(handler)
    elif method == "POST" and path == "/api/v1/simulate":
        _handle_simulate(handler)
    elif method == "POST":
        match = re.fullmatch(r"/api/v1/sessions/([^/]+)/video", path)
        if match:
            _handle_upload_video(handler, match.group(1), query)
            return
        match = re.fullmatch(r"/api/v1/sessions/([^/]+)/detect", path)
        if match:
            _send_json(handler, 200, _detect_all(_require_session(match.group(1)), _read_json_body(handler)))
            return
        _send_error(handler, 404, "not_found", "Not found.")
    elif method == "GET":
        match = re.fullmatch(r"/api/v1/sessions/([^/]+)/frames/(\d+)", path)
        if match:
            _send_json(
                handler,
                200,
                _frame_detail(
                    _require_session(match.group(1)),
                    int(match.group(2)),
                    query,
                ),
            )
            return
        _send_error(handler, 404, "not_found", "Not found.")
    elif method == "DELETE":
        match = re.fullmatch(r"/api/v1/sessions/([^/]+)", path)
        if match:
            _handle_delete_session(handler, match.group(1))
            return
        _send_error(handler, 404, "not_found", "Not found.")
    else:
        _send_error(handler, 404, "not_found", "Not found.")


def handle_v1(handler, path: str, query: str) -> None:
    """处理 /api/v1/* 请求，统一错误格式。"""
    try:
        _route(handler, path, query)
    except V1Error as exc:
        _send_error(handler, exc.status, exc.code, exc.message)
    except (ValueError, KeyError) as exc:
        _send_error(handler, 400, "invalid_params", str(exc))
    except Exception:
        _send_error(handler, 500, "internal_error", "Internal error.")
