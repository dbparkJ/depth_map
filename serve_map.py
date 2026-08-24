from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def make_handler(directory: Path, key: str, domain: str):
    class MapHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/viewer/")
                self.end_headers()
                return
            if path == "/viewer/runtime-config.js":
                config = {
                    "vworldApiKey": key,
                    "vworldDomain": domain,
                    "dataBase": "../data",
                    "preferredEngine": "vworld",
                }
                payload = (
                    "window.RGBD_MAP_CONFIG = "
                    + json.dumps(config, ensure_ascii=False)
                    + ";\n"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return
            super().do_GET()

    return MapHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a generated RGB-D map viewer")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--vworld-key", default=None)
    parser.add_argument("--vworld-domain", default=None)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if not (output / "viewer" / "index.html").is_file():
        raise SystemExit(f"Generated viewer not found: {output / 'viewer' / 'index.html'}")
    env_file = load_dotenv(PROJECT_ROOT / ".env")
    key = args.vworld_key or os.environ.get("VWORLD_API_KEY") or env_file.get("VWORLD_API_KEY", "")
    domain = (
        args.vworld_domain
        or os.environ.get("VWORLD_DOMAIN")
        or env_file.get("VWORLD_DOMAIN", "")
    )
    handler = make_handler(output, key, domain)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/viewer/"
    print(f"Serving {output}")
    print(url)
    if not key:
        print("VWORLD_API_KEY is empty; the viewer will use the Cesium/OSM fallback.")
    if args.open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

