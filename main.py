from __future__ import annotations

import argparse
import webbrowser

from flicker_app.server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Flicker simulation web app")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="open the default browser after startup",
    )
    args = parser.parse_args()

    server = create_server(args.host, args.port)
    url = f"http://{args.host}:{args.port}"
    print(f"Flicker Simulation running at {url}")

    if args.open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Flicker Simulation.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
