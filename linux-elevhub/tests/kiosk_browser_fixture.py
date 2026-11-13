"""Loopback fixture server for the real Chromium kiosk page regression."""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "administration"))
from template_activation import APP_URLS
TEMPLATE_DIR = ROOT / "ansible" / "roles" / "kiosk" / "templates"
ASSETS = ROOT / "server" / "elevhub" / "web" / "assets"
# Synthetic school colours only; never a real school's values.
COLOURS = {"none": ("", ""), "custom": ("#1f5a8c", "#143c5e"), "invalid": ("#1F5A8C", "blue")}


def render(colours: str = "none") -> bytes:
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
        kiosk_brand=COLOURS[colours][0],
        kiosk_brand_dark=COLOURS[colours][1],
        kiosk_school_name="Testskolen",
        kiosk_lokale_app_urler={**APP_URLS, "projekter": f"http://127.0.0.1:{PORT}/projects/"},
        kiosk_internet_start="https://internet.example.test/",
    ).encode("utf-8")


def apps(mode: str) -> list[dict]:
    values = [
        ("laser", False), ("scratch", True), ("microbit", False), ("internet", False),
    ] if mode == "closed" else [
        ("laser", True), ("scratch", True), ("microbit", True), ("internet", True),
    ]
    # Types as in server/elevhub/konfiguration.json: laser is hosted on the
    # school server (with a local package on the device), Scratch is local.
    types = {"internet": "browser", "microbit": "web", "scratch": "lokal", "laser": "vaerksted"}
    return [{"id": name, "aaben": opened, "klar": True, "type": types[name], "navn": name,
             **({"start_url": "https://internet.example.test/"} if name == "internet" else {"start_url": "https://makecode.microbit.org/"} if name == "microbit"
                else {"port": 8771} if name == "laser" else {"port": 8792})}
            for name, opened in values]


def manifests(mode: str) -> list[dict]:
    if mode == "empty":
        return []
    entries = [("3dprint", 8798, "model3d.html"), ("laser", 8795, ""), ("folie", 8796, ""),
               ("microbit", 8797, ""), ("scratch", 8792, "")]
    return [{"schema": 1, "id": name, "navn": name, "version": "synthetic-test", "start": f"http://127.0.0.1:{port}/{start}",
             "offline": True, "aflevering": None, "kraever_internet": name == "microbit", "licens": "synthetic"}
            for name, port, start in entries if not (mode == "removed" and name == "scratch")
            and not (mode == "microbit-removed" and name == "microbit")
            and not (mode == "laser-removed" and name == "laser")]


class Handler(BaseHTTPRequestHandler):
    mode = "down"
    catalog = "installed"
    colours = "none"

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
        if parsed.path == "/__stop":
            self.send(204, b"")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if parsed.path == "/__set":
            query = parse_qs(parsed.query)
            mode = query.get("mode", [self.__class__.mode])[0]
            catalog = query.get("catalog", [self.__class__.catalog])[0]
            colours = query.get("colours", [self.__class__.colours])[0]
            if colours not in COLOURS:
                return self.send(400, b"bad colours")
            self.__class__.colours = colours
            if mode not in {"down", "closed", "open", "forbidden"} or catalog not in {"installed", "removed", "microbit-removed", "laser-removed", "empty", "broken"}:
                return self.send(400, b"bad mode")
            self.__class__.mode = mode
            self.__class__.catalog = catalog
            return self.send(204, b"")
        if parsed.path == "/apps":
            if self.headers.get("Authorization") != "Bearer fixture-token" or self.headers.get("X-Elevhub-Computer") != "browser-fixture":
                return self.send(403, b"forbidden")
            if self.__class__.catalog == "broken":
                return self.send(503, b'{"error":"synthetic unreadable manifest"}', "application/json")
            return self.send(200, json.dumps({"apps": manifests(self.__class__.catalog)}).encode(), "application/json")
        if parsed.path in {"/", "/start.html"}:
            return self.send(200, render(self.__class__.colours), "text/html; charset=utf-8")
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
        if self.__class__.mode == "forbidden":
            return self.send(403, b"forbidden")
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
