"""频闪检测与仿真统一 Web 服务入口。"""
from __future__ import annotations

import argparse
import webbrowser

from app.server import create_server


def main() -> None:
    """解析命令行参数并启动统一工作台 HTTP 服务。"""
    parser = argparse.ArgumentParser(description="Flicker detection web app")
    # 服务监听地址，默认仅允许本机访问。
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    # 服务端口，默认 8766。
    parser.add_argument("--port", type=int, default=8766, help="bind port")
    # 可选：启动成功后自动打开默认浏览器。
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="open the default browser after startup",
    )
    args = parser.parse_args()

    # 创建 HTTP 服务，并输出访问地址。
    server = create_server(args.host, args.port)
    url = f"http://{args.host}:{args.port}"
    print(f"Flicker detection app running at {url}")

    # 用户请求时打开浏览器，便于直接查看仿真页面。
    if args.open_browser:
        webbrowser.open(url)

    # 持续提供服务，直到用户按 Ctrl+C 停止。
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Flicker detection app.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
