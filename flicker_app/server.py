from __future__ import annotations

import json
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import DEFAULT_PARAMS, FlickerEngine, FlickerParams

STATIC_DIR = Path(__file__).resolve().parent / "static"
ENGINE = FlickerEngine()

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _params_from_query(query: str) -> FlickerParams:
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


class FlickerRequestHandler(BaseHTTPRequestHandler):
    server_version = "FlickerSim/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/simulate":
            self._handle_simulate(parsed.query)
            return

        if parsed.path in ("/", "/index.html"):
            self._send_static("index.html")
            return

        self._send_static(parsed.path.lstrip("/"))

    def _handle_simulate(self, query: str) -> None:
        try:
            params = _params_from_query(query)
        except ValueError as exc:
            self._send_json(400, {"status": "error", "error": str(exc)})
            return

        error = ENGINE.validate(params)
        if error:
            self._send_json(400, {"status": "error", "error": error})
            return

        result = ENGINE.calculate(params)
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
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), FlickerRequestHandler)
    server.daemon_threads = True
    return server
