"""
Vercel Serverless Master API Router: /api/*
Routes incoming requests to status, simulate, settings, or report handlers.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Import generate_telemetry_state from api.status or define fallback
try:
    from api.status import generate_telemetry_state
except ImportError:
    import sys
    sys.path.append(str(Path(__file__).resolve().parent))
    from status import generate_telemetry_state

BASE_DIR = Path(__file__).resolve().parent.parent

class handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        if path.endswith("/status") or path.endswith("/telemetry") or path == "/api":
            mode = qs.get("mode", ["normal"])[0]
            data = generate_telemetry_state(mode=mode)
            payload = json.dumps(data).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path.endswith("/chaos_report.csv"):
            for candidate in [BASE_DIR / "public" / "chaos_report.csv", BASE_DIR / "chaos_report.csv"]:
                if candidate.is_file():
                    content = candidate.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/csv")
                    self.send_header("Content-Disposition", 'attachment; filename="chaos_report.csv"')
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return

        if path.endswith("/settings"):
            resp = json.dumps({
                "status": "ok",
                "sampleRate": 1000,
                "thresholdLgbm": 0.52,
                "thresholdIforest": 0.49,
                "persistenceK": 1,
                "webhookUrl": "https://plant-scada.internal/api/v1/machinasense/alerts"
            }).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        # Default info
        resp = json.dumps({"service": "MachinaSense API", "status": "online"}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"

        if path.endswith("/simulate") or path.endswith("/chaos"):
            try:
                payload = json.loads(body)
                mode = payload.get("mode", "normal")
                resp = json.dumps({"status": "ok", "mode": mode}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(HTTPStatus.BAD_REQUEST, str(e))
            return

        if path.endswith("/settings"):
            try:
                payload = json.loads(body)
                resp = json.dumps({"status": "ok", "message": "Settings saved successfully", "config": payload}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(HTTPStatus.BAD_REQUEST, str(e))
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
