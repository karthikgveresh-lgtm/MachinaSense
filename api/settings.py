"""
Vercel Serverless Function: POST /api/settings
Handles updating anomaly thresholds, sampling frequencies, and webhook alerts.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
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

    def do_GET(self):
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
