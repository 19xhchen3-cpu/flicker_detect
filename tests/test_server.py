from __future__ import annotations

"""频闪仿真 HTTP 服务的集成测试。"""

import json
import threading
import unittest
from http.client import HTTPConnection

from flicker_app.server import create_server


class FlickerServerTest(unittest.TestCase):
    """在临时端口启动服务，验证静态页面和仿真 API。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 使用端口 0 让系统自动分配可用端口。
        cls.server = create_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_index_page(self) -> None:
        """根路径应返回仿真页面 HTML。"""
        connection = HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertIn("Flicker Simulation", body)

    def test_simulate_api(self) -> None:
        """仿真 API 应返回帧强度和条纹数据。"""
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
        self.assertGreater(payload["result"]["max_min_ratio"], 9.0)

    def test_simulate_api_selects_stripe_frame(self) -> None:
        """stripe_frame 查询参数应控制返回的条纹帧。"""
        connection = HTTPConnection("127.0.0.1", self.port)
        connection.request(
            "GET",
            "/api/simulate?"
            "video_frame_rate=30&exposure_time=0.001&simulation_time=1"
            "&dc_amplitude=5&dc_frequency=0.25&dc_initial_phase=0"
            "&flicker_amplitude=1&flicker_frequency=50&ac_initial_phase=1"
            "&stripe_frame=29",
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["result"]["stripe_frame_index"], 29)


if __name__ == "__main__":
    unittest.main()
