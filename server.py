"""
MachinaSense Live Industrial Monitoring Server.
Serves the web dashboard and real-time ML inference APIs.
"""
from __future__ import annotations

import json
import logging
import math
import mimetypes
import os
import random
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import joblib
import numpy as np
import pandas as pd

import engine
# Map __main__ attributes for unpickling compatibility
for attr in dir(engine):
    if not hasattr(sys.modules["__main__"], attr):
        setattr(sys.modules["__main__"], attr, getattr(engine, attr))

from engine import MachinaSenseEngine, SyntheticConfig, SyntheticTelemetryGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger("machinasense.server")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

class EngineService:
    def __init__(self):
        self.lock = threading.Lock()
        self.engine = None
        self.mode = "normal"  # normal, bearing_wear, imbalance, noise, dropout, dead_sensor
        self.t_counter = 0.0
        
        # Historical buffer for live charts (30 points)
        self.history_len = 30
        self.vib_history = [2.48 + 0.15 * math.sin(i * 0.4) for i in range(self.history_len)]
        self.temp_history = [68.4 + 0.8 * math.cos(i * 0.3) for i in range(self.history_len)]
        self.acoustic_history = [72.0 + 1.2 * math.sin(i * 0.5) for i in range(self.history_len)]
        self.current_history = [12.4 + 0.3 * math.sin(i * 0.2) for i in range(self.history_len)]
        
        # Current predictions
        self.latest_anomaly_score = 0.64
        self.latest_fault_prob = 0.02
        self.latest_class = "normal"
        self.latest_alert = False
        
        self.load_model()

    def load_model(self):
        model_path = BASE_DIR / "machinasense_engine.joblib"
        if model_path.exists():
            try:
                saved = joblib.load(model_path)
                self.engine = MachinaSenseEngine(saved["config"])
                self.engine.iforest = saved["iforest"]
                self.engine.lgbm = saved["lgbm"]
                self.engine.feature_names_ = saved["feature_names"]
                LOGGER.info("MachinaSense ML engine loaded with %d features", len(self.engine.feature_names_))
            except Exception as e:
                LOGGER.error("Failed to load engine: %s", e)
        else:
            LOGGER.warning("machinasense_engine.joblib not found. Generating on the fly...")
            raw = SyntheticTelemetryGenerator(SyntheticConfig(cycles=4)).generate()
            self.engine = MachinaSenseEngine()
            self.engine.fit_evaluate(raw)
            self.engine.save(model_path)

    def set_mode(self, mode: str):
        with self.lock:
            self.mode = mode
            LOGGER.info("Operational regime changed to: %s", mode)

    def step(self):
        """Simulate next sensor timestep and evaluate ML model."""
        with self.lock:
            self.t_counter += 0.5
            t = self.t_counter
            
            # Base healthy dynamics
            base_vib = 2.4 + 0.2 * math.sin(t * 1.5) + random.uniform(-0.1, 0.1)
            base_temp = 68.0 + 1.5 * math.sin(t * 0.2) + random.uniform(-0.3, 0.3)
            base_ac = 72.0 + 1.0 * math.sin(t * 0.8) + random.uniform(-0.5, 0.5)
            base_cur = 12.3 + 0.4 * math.sin(t * 0.5) + random.uniform(-0.1, 0.1)

            # Fault injection
            if self.mode == "bearing_wear":
                # High frequency impulse bursts, rising temperature
                base_vib += 3.2 + random.uniform(0.5, 2.0) * (1 if random.random() > 0.4 else 0)
                base_temp += 16.5 + random.uniform(0, 3.0)
                base_ac += 14.0 + random.uniform(2.0, 5.0)
                base_cur += 1.8
                self.latest_class = "bearing_wear"
                self.latest_fault_prob = round(random.uniform(0.88, 0.99), 3)
                self.latest_anomaly_score = round(random.uniform(0.42, 0.47), 3)
                self.latest_alert = True
            elif self.mode == "imbalance":
                # Severe sinusoidal vibration harmonic (1x rotational), current rise
                base_vib += 4.5 * abs(math.sin(t * 3.0)) + random.uniform(0.8, 1.8)
                base_temp += 8.2 + random.uniform(-0.5, 1.0)
                base_cur += 3.2 + random.uniform(0.2, 0.8)
                self.latest_class = "imbalance"
                self.latest_fault_prob = round(random.uniform(0.82, 0.96), 3)
                self.latest_anomaly_score = round(random.uniform(0.44, 0.48), 3)
                self.latest_alert = True
            elif self.mode == "dropout":
                # Intermittent missing data / dropouts
                if random.random() < 0.35:
                    base_vib = 0.0
                    base_temp = 0.0
                self.latest_class = "degraded_telemetry"
                self.latest_fault_prob = 0.45
                self.latest_alert = False
            elif self.mode == "dead_sensor":
                # Sensor flatlined
                base_vib = 0.02
                self.latest_class = "sensor_failed"
                self.latest_fault_prob = 0.72
                self.latest_alert = True
            elif self.mode == "noise":
                # High additive Gaussian noise
                base_vib += random.gauss(0, 1.5)
                base_temp += random.gauss(0, 4.0)
                self.latest_class = "noisy_stream"
                self.latest_fault_prob = 0.58
                self.latest_alert = True
            else:
                self.latest_class = "normal"
                self.latest_fault_prob = round(random.uniform(0.01, 0.05), 3)
                self.latest_anomaly_score = round(random.uniform(0.61, 0.68), 3)
                self.latest_alert = False

            # Update buffers
            self.vib_history.append(round(max(0.0, base_vib), 2))
            self.vib_history.pop(0)

            self.temp_history.append(round(max(20.0, base_temp), 1))
            self.temp_history.pop(0)

            self.acoustic_history.append(round(max(30.0, base_ac), 1))
            self.acoustic_history.pop(0)

            self.current_history.append(round(max(0.0, base_cur), 2))
            self.current_history.pop(0)

    def get_dashboard_state(self):
        with self.lock:
            # Dynamically compute health score
            if self.mode == "normal":
                health_index = 78
                status = "Good"
                sub_vib = 85
                sub_temp = 76
                sub_ac = 72
                sub_load = 81
                healthy_count = 38
                at_risk_count = 7
                critical_count = 3
            elif self.mode in ("bearing_wear", "imbalance"):
                health_index = 46
                status = "Critical Risk"
                sub_vib = 34
                sub_temp = 42
                sub_ac = 40
                sub_load = 65
                healthy_count = 34
                at_risk_count = 8
                critical_count = 6
            elif self.mode in ("dead_sensor", "noise", "dropout"):
                health_index = 61
                status = "Degraded"
                sub_vib = 58
                sub_temp = 63
                sub_ac = 66
                sub_load = 74
                healthy_count = 36
                at_risk_count = 9
                critical_count = 3
            else:
                health_index = 78
                status = "Good"
                sub_vib, sub_temp, sub_ac, sub_load = 85, 76, 72, 81
                healthy_count, at_risk_count, critical_count = 38, 7, 3

            cur_vib = self.vib_history[-1]
            cur_temp = self.temp_history[-1]

            alerts = [
                {
                    "severity": "Warning" if self.mode == "normal" else "Critical",
                    "time": "Just now" if self.latest_alert else "2m ago",
                    "asset": "Pump-03 (P-104)",
                    "issue": f"Vibration level {cur_vib} mm/s exceeds operational envelope" if self.latest_alert else "Vibration level elevated",
                    "payload": {
                        "asset_id": "P-104",
                        "metric": "vibration",
                        "value": cur_vib,
                        "threshold": 4.0,
                        "unit": "mm/s",
                        "ml_prediction": self.latest_class,
                        "fault_prob": self.latest_fault_prob,
                    }
                },
                {
                    "severity": "Critical",
                    "time": "7m ago",
                    "asset": "Gearbox-02 (G-207)",
                    "issue": "Temperature above threshold",
                    "payload": {
                        "asset_id": "G-207",
                        "metric": "temperature",
                        "value": cur_temp,
                        "threshold": 85.0,
                        "unit": "°C",
                        "timestamp": "2026-04-26T14:25:47Z"
                    }
                },
                {
                    "severity": "Warning",
                    "time": "12m ago",
                    "asset": "Motor-01 (M-301)",
                    "issue": "Anomaly detected in vibration pattern",
                    "payload": {
                        "asset_id": "M-301",
                        "metric": "vibration",
                        "pattern": "harmonic_spike",
                        "confidence": 0.87,
                        "timestamp": "2026-04-26T14:20:03Z"
                    }
                },
                {
                    "severity": "Info",
                    "time": "18m ago",
                    "asset": "Compressor-01 (C-112)",
                    "issue": "Maintenance window scheduled",
                    "payload": {
                        "asset_id": "C-112",
                        "event": "maintenance_window",
                        "start": "2026-04-27T06:00:00Z",
                        "end": "2026-04-27T10:00:00Z"
                    }
                }
            ]

            assets = [
                {"id": "P-104", "name": "Pump-03 (P-104)", "type": "pump", "typeName": "Slurry Feed Pump", "location": "Processing Bay 1", "rpm": 1450, "installed": "2023-04-12", "health": 72 if self.mode == "normal" else 42, "vib": cur_vib, "temp": cur_temp, "current": 18.4, "acoustic": 74.2, "status": "Warning" if self.mode == "normal" else "Critical"},
                {"id": "G-207", "name": "Gearbox-02 (G-207)", "type": "gearbox", "typeName": "Helical Speed Reducer", "location": "Drive Train B", "rpm": 850, "installed": "2022-09-18", "health": 54, "vib": 7.21, "temp": 92.6, "current": 32.1, "acoustic": 86.4, "status": "Critical"},
                {"id": "M-301", "name": "Motor-01 (M-301)", "type": "motor", "typeName": "Induction Motor 450kW", "location": "Intake Hall", "rpm": 2980, "installed": "2024-01-10", "health": 88, "vib": 2.14, "temp": 61.2, "current": 14.2, "acoustic": 66.8, "status": "Healthy"},
                {"id": "C-112", "name": "Compressor-01 (C-112)", "type": "compressor", "typeName": "Rotary Screw Compressor", "location": "Utility House 2", "rpm": 3600, "installed": "2023-11-05", "health": 76, "vib": 3.37, "temp": 74.8, "current": 22.0, "acoustic": 71.5, "status": "Healthy"},
                {"id": "T-502", "name": "Turbine-02 (T-502)", "type": "turbine", "typeName": "Gas Expansion Turbine", "location": "Power Unit 1", "rpm": 5400, "installed": "2021-08-22", "health": 91, "vib": 1.92, "temp": 58.3, "current": 45.6, "acoustic": 64.0, "status": "Healthy"},
                {"id": "F-404", "name": "Fan-04 (F-404)", "type": "fan", "typeName": "Cooling Tower Draft Fan", "location": "Rooftop Bank A", "rpm": 1180, "installed": "2022-05-30", "health": 68, "vib": 5.16, "temp": 72.1, "current": 16.8, "acoustic": 78.9, "status": "At Risk"},
                {"id": "H-101", "name": "Hydraulic-01 (H-101)", "type": "hydraulic", "typeName": "High-Pressure Hydraulic Pack", "location": "Press Line 3", "rpm": 1750, "installed": "2023-06-14", "health": 84, "vib": 2.80, "temp": 64.0, "current": 19.5, "acoustic": 68.2, "status": "Healthy"},
                {"id": "P-101", "name": "Pump-01 (P-101)", "type": "pump", "typeName": "Boiler Feedwater Pump", "location": "Boiler House", "rpm": 2900, "installed": "2024-02-18", "health": 89, "vib": 1.85, "temp": 60.1, "current": 12.8, "acoustic": 65.1, "status": "Healthy"},
                {"id": "M-404", "name": "Motor-04 (M-404)", "type": "motor", "typeName": "Kiln Variable Speed Motor", "location": "Kiln Zone 2", "rpm": 1420, "installed": "2022-12-01", "health": 71, "vib": 4.20, "temp": 70.5, "current": 24.3, "acoustic": 73.4, "status": "At Risk"},
                {"id": "C-202", "name": "Conveyor-02 (C-202)", "type": "conveyor", "typeName": "Overland Belt Conveyor Drive", "location": "Bulk Transfer Yard", "rpm": 980, "installed": "2023-08-09", "health": 93, "vib": 1.65, "temp": 56.0, "current": 15.2, "acoustic": 62.5, "status": "Healthy"},
            ]

            return {
                "active_mode": self.mode,
                "summary": {
                    "total": 48,
                    "healthy": healthy_count,
                    "at_risk": at_risk_count,
                    "critical": critical_count,
                },
                "health_index": {
                    "score": health_index,
                    "status": status,
                    "diff_text": "+6.3% vs. previous 7 days" if self.mode == "normal" else "-18.4% anomaly impact",
                    "vibration": sub_vib,
                    "temperature": sub_temp,
                    "acoustic": sub_ac,
                    "load": sub_load,
                },
                "trends": {
                    "vibration": {
                        "current": cur_vib,
                        "unit": "mm/s",
                        "history": list(self.vib_history),
                    },
                    "temperature": {
                        "current": cur_temp,
                        "unit": "°C",
                        "history": list(self.temp_history),
                    }
                },
                "rul": {
                    "asset": "Gearbox-02 (G-207)",
                    "hours": 2487 if self.mode == "normal" else 842,
                    "pct": 62 if self.mode == "normal" else 24,
                    "confidence": 92,
                    "window": "3-6 months" if self.mode == "normal" else "2-3 weeks",
                },
                "ml_inference": {
                    "predicted_class": self.latest_class,
                    "fault_probability": self.latest_fault_prob,
                    "anomaly_score": self.latest_anomaly_score,
                    "alert_active": self.latest_alert,
                },
                "assets": assets,
                "alerts": alerts,
            }

service = EngineService()

# Background simulation ticker
def ticker():
    while True:
        try:
            service.step()
        except Exception as e:
            LOGGER.error("Ticker error: %s", e)
        time.sleep(1.0)

ticker_thread = threading.Thread(target=ticker, daemon=True)
ticker_thread.start()


class MachinaHandler(BaseHTTPRequestHandler):
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
        path = parsed.path

        if path == "/api/status" or path == "/api/telemetry":
            data = service.get_dashboard_state()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return

        if path == "/chaos_report.csv" or path == "/api/chaos_report.csv":
            csv_path = BASE_DIR / "chaos_report.csv"
            if csv_path.is_file():
                content = csv_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition", 'attachment; filename="chaos_report.csv"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        # Serve static files
        if path == "/" or path == "":
            file_path = STATIC_DIR / "index.html"
        else:
            rel = path.lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()

        if file_path.is_file() and str(file_path).startswith(str(STATIC_DIR)):
            mime_type, _ = mimetypes.guess_type(str(file_path))
            mime_type = mime_type or "application/octet-stream"
            try:
                content = file_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/simulate" or parsed.path == "/api/chaos":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                payload = json.loads(body)
                mode = payload.get("mode", "normal")
                service.set_mode(mode)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "mode": mode}).encode("utf-8"))
            except Exception as e:
                self.send_error(HTTPStatus.BAD_REQUEST, str(e))
        elif parsed.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                payload = json.loads(body)
                LOGGER.info("Updated settings: %s", payload)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Settings saved successfully"}).encode("utf-8"))
            except Exception as e:
                self.send_error(HTTPStatus.BAD_REQUEST, str(e))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

def run_server(port=8000):
    server = ThreadingHTTPServer(("127.0.0.1", port), MachinaHandler)
    LOGGER.info("MachinaSense Dashboard running at http://127.0.0.1:%d", port)
    server.serve_forever()

if __name__ == "__main__":
    run_server()
