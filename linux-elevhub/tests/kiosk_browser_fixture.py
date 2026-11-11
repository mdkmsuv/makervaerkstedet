"""Loopback fixture server for the real Chromium kiosk page regression."""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "ansible" / "roles" / "kiosk" / "templates"
ASSETS = ROOT / "server" / "elevhub" / "web" / "assets"


def render() -> bytes:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=False)
    env.filters["to_json"] = lambda value: json.dumps(value, ensure_ascii=False)
    template = env.get_template("start.html.j2")
    return template.render(
        kiosk_server="school.example.test",
        kiosk_protokol="http",
        kiosk_hub_port=PORT,
        inventory_hostname="browser-fixture",
        kiosk_enhed={"ui_token": "fixture-token"},
        kiosk_theme="red",
        kiosk_school_name="Testskolen",
        kiosk_lokale_app_urler={"projekter": f"http://127.0.0.1:{PORT}/projects/"},
        kiosk_internet_start="https://internet.example.test/",
    ).encode("utf-8")


def apps(mode: str) -> list[dict]:
    values = [
        ("laser", False), ("scratch", True), ("internet", False),
    ] if mode == "closed" else [
        ("laser", True), ("scratch", True), ("internet", True),
    ]
    return [{"id": name, "aaben": opened, "klar": True, "type": "vaerksted", "navn": name}
            for name, opened in values]


class Handler(BaseHTTPRequestHandler):
    mode = "down"

    def log_message(self, *_args):
        pass

    def send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Elevhub-Computer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/__set":
            mode = parse_qs(parsed.query).get("mode", [""])[0]
            if mode not in {"down", "closed", "open"}:
                return self.send(400, b"bad mode")
            self.__class__.mode = mode
            return self.send(204, b"")
        if parsed.path in {"/", "/start.html"}:
            return self.send(200, render(), "text/html; charset=utf-8")
        if parsed.path.startswith("/assets/"):
            relative = parsed.path.removeprefix("/assets/")
            candidate = (ASSETS / relative).resolve()
            if not candidate.is_file() or not candidate.is_relative_to(ASSETS):
                return self.send(404, b"missing")
            return self.send(200, candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        return self.send(404, b"missing")

    def do_OPTIONS(self):
        if urlparse(self.path).path == "/api/status":
            return self.send(204, b"")
        return self.send(404, b"missing")

    def do_POST(self):
        if urlparse(self.path).path != "/api/status":
            return self.send(404, b"missing")
        if self.__class__.mode == "down":
            return self.send(503, b"offline")
        body = json.dumps({"version": 1, "apps": apps(self.__class__.mode)}).encode("utf-8")
        return self.send(200, body, "application/json")


def main() -> None:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    PORT = server.server_address[1]
    print(PORT, flush=True)
    server.serve_forever()


PORT = 0
if __name__ == "__main__":
    main()
