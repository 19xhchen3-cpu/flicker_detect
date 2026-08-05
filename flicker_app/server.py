from __future__ import annotations

"""频闪仿真 HTTP 服务，提供静态页面和仿真 API。"""

import json
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import DEFAULT_PARAMS, FlickerEngine, FlickerParams

STATIC_DIR = Path(__file__).resolve().parent / "static"
ENGINE = FlickerEngine()

# 静态资源后缀到 Content-Type 的映射。
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _params_from_query(query: str) -> FlickerParams:
    """从 URL 查询参数构造 FlickerParams，非法数值会抛出 ValueError。"""
    raw = parse_qs(query)
    kwargs: dict[str, float] = {}
    for field in fields(FlickerParams):
        if field.name not in raw:
            continue
        try:
            kwargs[field.name] = float(raw[field.name][-1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid value for {field.name}.") from exc
    return FlickerParams(**kwargs)


def _stripe_frame_from_query(query: str) -> int | None:
    """读取可选的 stripe_frame 查询参数，未提供时返回 None。"""
    raw = parse_qs(query)
    if "stripe_frame" not in raw:
        return None
    try:
        return int(raw["stripe_frame"][-1])
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid value for stripe_frame.") from exc


class FlickerRequestHandler(BaseHTTPRequestHandler):
    server_version = "FlickerSim/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        """关闭默认请求日志，避免终端输出被高频仿真请求刷屏。"""
        return

    def do_GET(self) -> None:
        """处理 GET 请求：API 路由或静态资源。"""
        parsed = urlparse(self.path)
        if parsed.path == "/api/simulate":
            self._handle_simulate(parsed.query)
            return

        if parsed.path in ("/", "/index.html"):
            self._send_static("index.html")
            return

        self._send_static(parsed.path.lstrip("/"))

    def _handle_simulate(self, query: str) -> None:
        """处理 /api/simulate，返回仿真结果或参数错误。"""
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

    def _send_static(self, relative: str) -> None:
        """发送 static 目录下的静态文件，并防止路径越界。"""
        root = STATIC_DIR.resolve()
        path = (root / relative).resolve()

        try:
            path.relative_to(root)
        except ValueError:
            self._send_json(404, {"status": "error", "error": "Not found"})
            return

        if path.is_dir():
            path = path / "index.html"

        if not path.is_file():
            self._send_json(404, {"status": "error", "error": "Not found"})
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
        """以 JSON 形式返回响应。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """创建线程化 HTTP 服务，默认监听本机 8765 端口。"""
    server = ThreadingHTTPServer((host, port), FlickerRequestHandler)
    server.daemon_threads = True
    return server
