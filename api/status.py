"""
Vercel Serverless Function: GET /api/status
Provides live ML telemetry, sensor streams, health metrics, and asset status.
Zero external dependencies (uses standard library only) for instant Vercel cold starts.
"""
from __future__ import annotations

import json
import math
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

def generate_telemetry_state(mode: str = "normal", t: float | None = None) -> dict:
    if t is None:
        t = time.time()

    history_len = 30
    vib_history = []
    temp_history = []

    pred_class = "normal"
    fault_prob = 0.03
    anomaly_score = 0.64
    is_alert = False

    for i in range(history_len):
        tau = t - (history_len - 1 - i) * 1.0
        # Base healthy dynamics
        base_vib = 2.4 + 0.2 * math.sin(tau * 1.5) + math.sin(tau * 4.3) * 0.08
        base_temp = 68.0 + 1.5 * math.sin(tau * 0.2) + math.sin(tau * 0.9) * 0.2

        if mode == "bearing_wear":
            is_burst = math.sin(tau * 12.0) > 0.65
            base_vib += 3.2 + (1.8 if is_burst else 0.4)
            base_temp += 16.5 + math.sin(tau * 0.1) * 1.5
            pred_class = "bearing_wear"
            fault_prob = 0.94
            anomaly_score = 0.44
            is_alert = True
        elif mode == "imbalance":
            base_vib += 4.5 * abs(math.sin(tau * 3.0)) + 1.2
            base_temp += 8.2 + math.sin(tau * 0.15) * 0.8
            pred_class = "imbalance"
            fault_prob = 0.91
            anomaly_score = 0.46
            is_alert = True
        elif mode == "dropout":
            if math.sin(tau * 3.7) > 0.5:
                base_vib = 0.0
                base_temp = 0.0
            pred_class = "degraded_telemetry"
            fault_prob = 0.45
            anomaly_score = 0.55
            is_alert = False
        elif mode == "dead_sensor":
            base_vib = 0.02
            pred_class = "sensor_failed"
            fault_prob = 0.72
            anomaly_score = 0.38
            is_alert = True
        elif mode == "noise":
            base_vib += math.sin(tau * 17.3) * 1.4
            base_temp += math.cos(tau * 11.2) * 3.5
            pred_class = "noisy_stream"
            fault_prob = 0.58
            anomaly_score = 0.49
            is_alert = True
        else:
            pred_class = "normal"
            fault_prob = 0.03
            anomaly_score = 0.64
            is_alert = False

        vib_history.append(round(max(0.0, base_vib), 2))
        temp_history.append(round(max(20.0, base_temp), 1))

    cur_vib = vib_history[-1]
    cur_temp = temp_history[-1]

    if mode == "normal":
        health_index = 78
        status = "Good"
        diff_text = "+6.3% vs. previous 7 days"
        sub_vib, sub_temp, sub_ac, sub_load = 85, 76, 72, 81
        healthy_count, at_risk_count, critical_count = 38, 7, 3
    elif mode in ("bearing_wear", "imbalance"):
        health_index = 46
        status = "Critical Risk"
        diff_text = "-18.4% anomaly impact"
        sub_vib, sub_temp, sub_ac, sub_load = 34, 42, 40, 65
        healthy_count, at_risk_count, critical_count = 34, 8, 6
    elif mode in ("dead_sensor", "noise", "dropout"):
        health_index = 61
        status = "Degraded"
        diff_text = "-18.4% anomaly impact"
        sub_vib, sub_temp, sub_ac, sub_load = 58, 63, 66, 74
        healthy_count, at_risk_count, critical_count = 36, 9, 3
    else:
        health_index = 78
        status = "Good"
        diff_text = "+6.3% vs. previous 7 days"
        sub_vib, sub_temp, sub_ac, sub_load = 85, 76, 72, 81
        healthy_count, at_risk_count, critical_count = 38, 7, 3

    assets = [
        {"id": "P-104", "name": "Pump-03 (P-104)", "type": "pump", "typeName": "Slurry Feed Pump", "location": "Processing Bay 1", "rpm": 1450, "installed": "2023-04-12", "health": 72 if mode == "normal" else 42, "vib": cur_vib, "temp": cur_temp, "current": 18.4, "acoustic": 74.2, "status": "Warning" if mode == "normal" else "Critical"},
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

    alerts = [
        {
            "severity": "Warning" if mode == "normal" else "Critical",
            "time": "Just now" if is_alert else "2m ago",
            "asset": "Pump-03 (P-104)",
            "issue": f"Vibration level {cur_vib} mm/s exceeds operational envelope" if is_alert else "Vibration level elevated",
            "payload": {
                "asset_id": "P-104",
                "metric": "vibration",
                "value": cur_vib,
                "threshold": 4.0,
                "unit": "mm/s",
                "ml_prediction": pred_class,
                "fault_prob": fault_prob,
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

    return {
        "active_mode": mode,
        "summary": {
            "total": 48,
            "healthy": healthy_count,
            "at_risk": at_risk_count,
            "critical": critical_count,
        },
        "health_index": {
            "score": health_index,
            "status": status,
            "diff_text": diff_text,
            "vibration": sub_vib,
            "temperature": sub_temp,
            "acoustic": sub_ac,
            "load": sub_load,
        },
        "trends": {
            "vibration": {
                "current": cur_vib,
                "unit": "mm/s",
                "history": vib_history,
            },
            "temperature": {
                "current": cur_temp,
                "unit": "°C",
                "history": temp_history,
            }
        },
        "rul": {
            "asset": "Gearbox-02 (G-207)",
            "hours": 2487 if mode == "normal" else 842,
            "pct": 62 if mode == "normal" else 24,
            "confidence": 92,
            "window": "3-6 months" if mode == "normal" else "2-3 weeks",
        },
        "ml_inference": {
            "predicted_class": pred_class,
            "fault_probability": fault_prob,
            "anomaly_score": anomaly_score,
            "alert_active": is_alert,
        },
        "assets": assets,
        "alerts": alerts,
    }


class handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        mode = qs.get("mode", ["normal"])[0]

        data = generate_telemetry_state(mode=mode)
        payload = json.dumps(data).encode("utf-8")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        self.do_GET()
