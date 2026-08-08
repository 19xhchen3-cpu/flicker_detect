from __future__ import annotations

"""统一工作台 HTTP 服务的集成测试。"""

import json
import threading
import unittest
from http.client import HTTPConnection

from app.server import create_server


class AppServerTest(unittest.TestCase):
    """验证统一页面、检测参数接口和仿真 API。"""

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


if __name__ == "__main__":
    unittest.main()
