from __future__ import annotations

"""API 接口层（/api/v1/*）的 HTTP 集成测试。"""

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

import app.api as api_module
from app.server import create_server
from flicker_detect.detector_config import lookup_detector_parameters


class ApiV1Test(unittest.TestCase):
    """验证会话生命周期、检测接口、参数解析和统一错误格式。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        with api_module.SESSIONS_LOCK:
            api_module.SESSIONS.clear()

    def _request(self, method: str, path: str, body: str | None = None) -> tuple[int, str]:
        connection = HTTPConnection("127.0.0.1", self.port)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, raw

    def _seed_periodic_session(self) -> str:
        """写入 30 帧已知周期频闪，模拟已上传视频的会话。"""
        params = lookup_detector_parameters(30, 50)
        levels = [6000.0, 9000.0, 5000.0]
        blocks = [
            [levels[frame_id % 3]] * params.block_count
            for frame_id in range(params.history)
        ]
        session_id = "test-periodic"
        with api_module.SESSIONS_LOCK:
            api_module.SESSIONS[session_id] = {
                "id": session_id,
                "meta": {
                    "fps": 30.0,
                    "fps_mode": 30,
                    "cols": params.cols,
                    "rows": params.rows,
                    "frame_count": len(blocks),
                    "block_count": params.block_count,
                    "y_max": params.y_max,
                },
                "blocks": blocks,
            }
        return session_id

    def test_options(self) -> None:
        """选项接口应返回 30fps 下可检测频率及完整参数。"""
        status, raw = self._request("GET", "/api/v1/options?fps=30")
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertTrue(
            any(item["detection_frequency"] == 50 for item in payload["options"])
        )
        params = next(
            item["params"]
            for item in payload["options"]
            if item["detection_frequency"] == 50
        )
        self.assertEqual(params["group_size"], 4)

    def test_session_lifecycle(self) -> None:
        """创建、删除、再删除应返回正确状态码。"""
        status, raw = self._request("POST", "/api/v1/sessions")
        self.assertEqual(status, 200)
        session_id = json.loads(raw)["session_id"]

        status, raw = self._request("DELETE", f"/api/v1/sessions/{session_id}")
        self.assertEqual(status, 200)

        status, raw = self._request("DELETE", f"/api/v1/sessions/{session_id}")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw)["error"]["code"], "not_found")

    def test_detect_periodic(self) -> None:
        """重算接口应返回基于 moudle1 的逐帧紧凑结果。"""
        session_id = self._seed_periodic_session()
        body = json.dumps({"fps": 30, "params": {"detection_frequency": 50}})
        status, raw = self._request(
            "POST", f"/api/v1/sessions/{session_id}/detect", body=body
        )
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["results"]), 30)
        self.assertIsNone(payload["results"][0])
        last = payload["results"][-1]
        self.assertEqual(last["flicker_block_num"], 192)
        self.assertEqual(last["group_count"], 9)
        # 相位锁定布局：窗口 [0,27]，组起点 0,3,...,24（无 drop）。
        self.assertEqual(last["groups"][0]["start_frame"], 0)
        self.assertEqual(last["groups"][-1]["start_frame"], 24)

    def test_frame_detail(self) -> None:
        """单帧详情接口应返回逐块逐组诊断矩阵。"""
        session_id = self._seed_periodic_session()
        status, raw = self._request(
            "GET",
            f"/api/v1/sessions/{session_id}/frames/29?fps=30&detection_frequency=50",
        )
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        result = payload["result"]
        self.assertEqual(len(result["groups"]), 9)
        self.assertEqual(len(result["group_dominant"]), 192)
        self.assertTrue(all(result["group_dominant"][b][0] for b in range(192)))
        self.assertEqual(result["group_sign"][0][0], "+-+")

    def test_unknown_session(self) -> None:
        """不存在的会话应返回 not_found。"""
        body = json.dumps({"fps": 30, "params": {"detection_frequency": 50}})
        status, raw = self._request("POST", "/api/v1/sessions/nope/detect", body=body)
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw)["error"]["code"], "not_found")

    def test_invalid_frequency(self) -> None:
        """参数表外的频率组合应返回 invalid_params。"""
        session_id = self._seed_periodic_session()
        body = json.dumps({"fps": 30, "params": {"detection_frequency": 999}})
        status, raw = self._request(
            "POST", f"/api/v1/sessions/{session_id}/detect", body=body
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], "invalid_params")

    def test_empty_upload_rejected(self) -> None:
        """空视频上传应返回 invalid_params。"""
        status, raw = self._request("POST", "/api/v1/sessions")
        session_id = json.loads(raw)["session_id"]
        status, raw = self._request(
            "POST", f"/api/v1/sessions/{session_id}/video"
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], "invalid_params")

    def test_simulate(self) -> None:
        """仿真接口应返回帧强度和条纹数据。"""
        body = json.dumps(
            {
                "video_frame_rate": 30,
                "exposure_time": 0.001,
                "simulation_time": 1,
            }
        )
        status, raw = self._request("POST", "/api/v1/simulate", body=body)
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["result"]["frame_strength"]), 30)

    def test_upload_then_detect(self) -> None:
        """上传真实视频后应能对会话重算检测。"""
        import cv2
        import numpy as np

        status, raw = self._request("POST", "/api/v1/sessions")
        session_id = json.loads(raw)["session_id"]

        video_path = Path(tempfile.mktemp(suffix=".avi"))
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"MJPG"),
            30.0,
            (64, 48),
        )
        if not writer.isOpened():
            self.skipTest("OpenCV MJPG writer not available.")
        try:
            levels = np.array([6000, 9000, 5000], dtype=np.uint16)
            for frame_id in range(30):
                value = int(levels[frame_id % 3]) // 256
                gray = np.full((48, 64), value, dtype=np.uint8)
                writer.write(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
            writer.release()
            data = video_path.read_bytes()
        finally:
            video_path.unlink(missing_ok=True)

        connection = HTTPConnection("127.0.0.1", self.port)
        connection.request(
            "POST",
            f"/api/v1/sessions/{session_id}/video"
            "?name=clip.avi&cols=16&rows=12",
            body=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        response = connection.getresponse()
        lines = response.read().decode("utf-8").strip().split("\n")
        connection.close()

        self.assertEqual(response.status, 200)
        line_types = [json.loads(line)["type"] for line in lines if line]
        self.assertIn("meta", line_types)
        self.assertEqual(line_types[-1], "done")

        body = json.dumps({"fps": 30, "params": {"detection_frequency": 50}})
        status, raw = self._request(
            "POST", f"/api/v1/sessions/{session_id}/detect", body=body
        )
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][-1]["flicker_block_num"], 192)


if __name__ == "__main__":
    unittest.main()
