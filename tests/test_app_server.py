from __future__ import annotations

"""统一工作台 HTTP 服务的集成测试。"""

import json
import threading
import unittest
from http.client import HTTPConnection

import app.server as server_module
from app.server import create_server
from flicker_detect.detector_config import lookup_detector_parameters


class AppServerTest(unittest.TestCase):
    """验证统一页面、检测参数接口和仿真 API。"""

    def setUp(self) -> None:
        server_module._CURRENT_BLOCKS = []
        server_module._CURRENT_VIDEO = None

    def tearDown(self) -> None:
        server_module._CURRENT_BLOCKS = []
        server_module._CURRENT_VIDEO = None

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

    def test_merged_index_page(self) -> None:
        """根路径应同时包含检测和仿真两个视图。"""
        connection = HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertIn("navDetect", body)
        self.assertIn("navSimulate", body)
        self.assertIn("simulateView", body)
        self.assertIn("parameterRows", body)

    def test_simulate_script_served(self) -> None:
        """统一页面应能加载仿真脚本。"""
        connection = HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", "/simulate.js")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertIn("redrawSimulation", body)

    def test_simulate_api(self) -> None:
        """统一服务应提供频闪仿真 API。"""
        connection = HTTPConnection("127.0.0.1", self.port)
        connection.request(
            "GET",
            "/api/simulate?"
            "video_frame_rate=30&exposure_time=0.001&simulation_time=1"
            "&dc_amplitude=5&dc_frequency=0.25&dc_initial_phase=0"
            "&flicker_amplitude=1&flicker_frequency=50&ac_initial_phase=1",
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["result"]["frame_strength"]), 30)
        self.assertEqual(len(payload["result"]["stripe_rows"]), 2160)

    def _seed_periodic_video(self) -> None:
        """写入 30 帧已知周期频闪块亮度，模拟一次转换后的服务端状态。"""
        params = lookup_detector_parameters(30, 50)
        levels = [6000.0, 9000.0, 5000.0]
        server_module._CURRENT_BLOCKS = [
            [levels[frame_id % 3]] * params.block_count
            for frame_id in range(params.history)
        ]
        server_module._CURRENT_VIDEO = {
            "fps": 30.0,
            "fps_mode": 30,
            "cols": params.cols,
            "rows": params.rows,
            "frame_count": len(server_module._CURRENT_BLOCKS),
            "block_count": params.block_count,
            "y_max": params.y_max,
        }

    def test_recompute_uses_moudle1(self) -> None:
        """重算接口应返回基于 moudle1 的逐帧紧凑检测结果。"""
        self._seed_periodic_video()
        connection = HTTPConnection("127.0.0.1", self.port)
        body = json.dumps({"fps": 30, "params": {"detection_frequency": 50}})
        connection.request(
            "POST",
            "/api/recompute",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(len(payload["results"]), 30)
        self.assertIsNone(payload["results"][0])
        last = payload["results"][-1]
        self.assertEqual(last["flicker_block_num"], 192)
        self.assertEqual(last["group_count"], 9)
        self.assertEqual(last["groups"][0]["start_frame"], 2)
        self.assertEqual(last["groups"][-1]["start_frame"], 26)
        self.assertEqual(last["block_flags"][0], 1)

    def test_frame_detect_returns_group_detail(self) -> None:
        """单帧详情接口应返回逐块逐组诊断矩阵。"""
        self._seed_periodic_video()
        connection = HTTPConnection("127.0.0.1", self.port)
        connection.request(
            "GET",
            "/api/detect?frame=29&fps=30&detection_frequency=50",
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ready"])
        result = payload["result"]
        self.assertEqual(len(result["groups"]), 9)
        self.assertEqual(len(result["group_dominant"]), 192)
        self.assertTrue(all(result["group_dominant"][b][0] for b in range(192)))
        self.assertEqual(result["group_sign"][0][0], "++-")
        self.assertEqual(result["group_amp_pass"][0][0], 3)

    def test_recompute_serializes_non_finite_as_null(self) -> None:
        """无频闪数据下诊断量中的 NaN 应序列化为 null。"""
        params = lookup_detector_parameters(30, 50)
        server_module._CURRENT_BLOCKS = [
            [8192.0] * params.block_count for _ in range(params.history)
        ]
        server_module._CURRENT_VIDEO = {
            "fps": 30.0,
            "fps_mode": 30,
            "cols": params.cols,
            "rows": params.rows,
            "frame_count": len(server_module._CURRENT_BLOCKS),
            "block_count": params.block_count,
            "y_max": params.y_max,
        }

        connection = HTTPConnection("127.0.0.1", self.port)
        body = json.dumps({"fps": 30, "params": {"detection_frequency": 50}})
        connection.request(
            "POST",
            "/api/recompute",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw_body = response.read().decode("utf-8")
        payload = json.loads(raw_body)
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertNotIn("NaN", raw_body)
        self.assertNotIn("Infinity", raw_body)
        self.assertEqual(payload["results"][-1]["mean_abs_diff"][0], None)
        self.assertEqual(payload["results"][-1]["diff_cv"][0], None)
        self.assertEqual(payload["results"][-1]["flicker_block_num"], 0)


if __name__ == "__main__":
    unittest.main()
