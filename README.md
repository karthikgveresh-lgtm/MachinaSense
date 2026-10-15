# 🏭 MachinaSense | Industrial Asset Intelligence & Condition Monitoring

[![MachinaSense Live Monitor](https://img.shields.io/badge/Status-Live%20Streaming-06b6d4?style=for-the-badge&logo=prometheus)](http://127.0.0.1:8000)
[![ML Engine](https://img.shields.io/badge/ML%20Engine-LightGBM%20%2B%20IsolationForest-10b981?style=for-the-badge&logo=scikitlearn)](http://127.0.0.1:8000)
[![Inference Accuracy](https://img.shields.io/badge/Telemetry%20Accuracy-99.85%25-38bdf8?style=for-the-badge)](http://127.0.0.1:8000)
[![Architecture](https://img.shields.io/badge/Architecture-Zero%20Dependency%20Frontend-f59e0b?style=for-the-badge)](http://127.0.0.1:8000)

**MachinaSense** is a next-generation industrial asset monitoring and predictive maintenance platform designed to eliminate unplanned downtime in mission-critical manufacturing, energy, and heavy machinery plants. 

The platform combines real-time streaming telemetry, dual-model machine learning anomaly detection, causal signal imputation, Remaining Useful Life (RUL) prognostics, and an interactive **Chaos Engineering Console** with a state-of-the-art dark glassmorphic interface.

---

## 📸 Executive Visual Preview & Graphs

MachinaSense renders high-frequency industrial telemetry with zero lag using custom HTML5 Canvas oscillograms and dynamic SVGs.

```
+--------------------------------------------------------------------------------------------------+
| 🟢 LIVE TESTING CONSOLE | Normal Steady State | Bearing Wear | Imbalance | Dropouts | Dead Sensor    |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
| [MachinaSense]            Overview • Assets • Condition • Predictive • Maintenance • Reports    |
|                                                                                                  |
| +--------------------------------------------------------------------+ +-----------------------+ |
| | KPI Fleet Summary:  48 Total  |  38 Healthy  |  7 At Risk  | 3 Crit| | Asset Distribution    | |
| +--------------------------------------------------------------------+ |  (SVG Donut Chart)    | |
|                                                                        |  38 Healthy (79.2%)   | |
| +----------------------------------+ +-------------------------------+ |  7 At Risk  (14.6%)   | |
| | Health Index Gauge (Radial SVG)  | | Real-Time Sensor Trends       | |  3 Critical (6.3%)    | |
| |          78/100 Good             | | - Vibration Oscillogram (Teal)| +-----------------------+ |
| | • Vib: 85  • Temp: 76  • Load: 81| | - Temp Oscillogram (Amber)    | | Remaining Useful Life | |
| +----------------------------------+ | - ML Live Inference Tag       | |  Gearbox-02 (G-207)   | |
|                                      +-------------------------------+ |  62% RUL | 2,487 hrs   | |
| +--------------------------------------------------------------------+ +-----------------------+ |
| | Asset Performance Table (Wide Fleet Telemetry with Sparklines)     | | Active Alerts Feed    | |
| | Asset    | Health | Trend       | Vib (mm/s) | Temp (°C) | Status  | |  [WARNING] M-301      | |
| | Pump-03  | 72     | ~~~~~~~/~~~ | 2.24       | 68.6°C    | Warning | |  [CRITICAL] G-207     | |
| | Gear-02  | 54     | ~~/~~\~~~~~ | 7.21       | 92.6°C    | Critical| |  {JSON Diagnostics}   | |
| +--------------------------------------------------------------------+ +-----------------------+ |
+--------------------------------------------------------------------------------------------------+
```

---

## ⚡ Card Interactive Vibration & Haptic Hover Animation

One of the signature visual interactions in MachinaSense is its **tactile industrial haptic vibration**. When you point (hover your cursor) toward any metric card, fleet card, or panel, the interface responds with a realistic mechanical vibration animation:

### How the Vibration Hover Interaction Works
- **Physical Elevation:** The card instantly lifts `-3px` above the dashboard plane using an ultra-smooth cubic bezier curve (`cubic-bezier(0.16, 1, 0.3, 1)`).
- **Mechanical Micro-Vibration:** Rather than a static flat hover, an animated `@keyframes cardVibrate` sequence rapidly oscillates the card along its rotational axis (`-0.35deg` to `+0.35deg`) across 5 rapid harmonic cycles (350ms duration). This mimics the micro-vibration sensations felt by engineers touching running industrial equipment.
- **Cyan Ion Glow:** The border illuminates with an intense cyber-cyan glow (`rgba(6, 182, 212, 0.45)`), accompanied by a deep multi-layered drop shadow (`box-shadow: 0 10px 28px -6px rgba(0, 0, 0, 0.5), 0 0 16px rgba(6, 182, 212, 0.16)`).
- **Haptic Feedback Association:** The card vibration visually alerts the operator's eye to inspect telemetry details, giving a tangible feeling of live machine resonance beneath the cursor.

```css
/* Card Interactive Vibration & Elevation Haptic Animation */
@keyframes cardVibrate {
  0%   { transform: translateY(0)      rotate(0deg); }
  20%  { transform: translateY(-2px)   rotate(-0.35deg); }
  40%  { transform: translateY(-3px)   rotate(0.35deg); }
  60%  { transform: translateY(-2.5px) rotate(-0.2deg); }
  80%  { transform: translateY(-3px)   rotate(0.2deg); }
  100% { transform: translateY(-3px)   rotate(0deg); }
}

.panel-card:hover,
.kpi-card:hover,
.alert-card:hover,
.fleet-card:hover {
  transform: translateY(-3px);
  border-color: rgba(6, 182, 212, 0.4);
  box-shadow: 0 10px 28px -6px rgba(0, 0, 0, 0.5), 0 0 16px rgba(6, 182, 212, 0.16);
  animation: cardVibrate 0.35s ease-in-out;
}
```

---

## 📊 Complete Graph & Visual Designs Breakdown

MachinaSense integrates 7 distinct graph and data visualization paradigms engineered specifically for condition monitoring:

### 1. High-Frequency Real-Time Waveform Oscillograms (`<canvas>`)
- **Technology:** Hardware-accelerated HTML5 2D Canvas rendering engine with automatic `devicePixelRatio` scaling for Retina sharpness.
- **Teal Oscillogram (Vibration):** Plots root-mean-square (RMS) and peak vibration velocity in mm/s over a rolling 30-sample time buffer.
- **Amber Oscillogram (Temperature):** Tracks thermal fluctuations in °C with adaptive ceiling scales (40°C to 105°C).
- **Time Controls:** Interactive time filter pills (`1H`, `6H`, `24H`, `7D`) to toggle historical inspection windows.
- **Embedded ML Inference Ribbon:** Live readout displaying the active model (`LightGBM`), predicted fault classification, anomaly score (0.00 to 1.00), and fault probability.

### 2. Radial Health Index Circular Gauge (SVG)
- **Visual Design:** 120px circular vector gauge featuring a background guide track (`#1e293b`) and a dynamic cyan/amber/red indicator ring.
- **Smooth Geometry:** Transition governed by calculated SVG `stroke-dashoffset` (0 to 314) representing fleet operational health (0 to 100).
- **Sub-Metric Progress Bars:** Four channel-specific progress meters breaking down health into individual physical components:
  - ⚡ **Vibration Health** (85/100)
  - 🌡️ **Thermal Health** (76/100)
  - 🔊 **Acoustic Integrity** (72/100)
  - ⚙️ **Mechanical Load** (81/100)

### 3. Multi-Segment Asset Distribution Donut Chart (SVG)
- **Visual Design:** Circular tri-color donut chart with 16px stroke width and centered asset counter (`48 Total Assets`).
- **Calibrated Offsets:** Precisely offset segments for:
  - 🔵 **Healthy Assets:** 79.2% (Cyan: `#06b6d4`)
  - 🟡 **At Risk Assets:** 14.6% (Amber: `#f59e0b`)
  - 🔴 **Critical Assets:** 6.2% (Red: `#ef4444`)
- **Interactive Breakdown Legend:** Real-time unit counters and percentages with matching indicator bullets.

### 4. Remaining Useful Life (RUL) Prognostics Gauge
- **Predictive Prognosis:** Displays the estimated operating hours remaining before catastrophic failure (e.g., `2,487 hours`).
- **Gradient Lifespan Bar:** Dual-color cyan-to-emerald gradient fill tracking percentage of useful life left (e.g., `62% RUL`).
- **Model Confidence & Window:** Displays LightGBM inference confidence (`92%`) and predicted maintenance window (`3-6 months`).

### 5. Fleet Performance Table with Inline Sparklines
- **Table Data:** 6 comprehensive telemetry columns: Asset Name/ID, Health Index, Trend Sparkline, Vibration (mm/s), Temperature (°C), and Health Status.
- **Inline Sparkline Waves:** Dynamic SVG polylines illustrating individual machine trend trajectories over time.
- **Status Tags:** Glowing pills (`Healthy`, `Warning`, `Critical`, `At Risk`) with subtle pulse indicators.

### 6. Chronological Diagnostic Alerts Feed with JSON Payloads
- **Real-Time Stream:** Ranked alerts displaying severity badge, relative timestamp (e.g., `12m ago`), and target asset.
- **Formatted Telemetry Payloads:** Monospace syntax-highlighted JSON diagnostic payloads containing real-time sensor values, anomaly pattern classification, confidence ratings, and ISO-8601 timestamps.
- **Smooth Internal Scrollbar:** Custom webkit scrollbars ensuring the feed never breaks outer layout flow.

### 7. Feature Importance & Confusion Matrix Graphs
- **Condition & Predictive Studios:** Visualizes LightGBM feature weights (e.g., `vib_x_skewness`, `acoustic_kurtosis`, `temp_rolling_std`) and multi-class confusion matrix matches vs. false positives.

---

## 🛠️ Technology Stack

| Layer | Technologies Used | Key Purpose |
|---|---|---|
| **Frontend Framework** | **Pure Vanilla ES6+ & HTML5** | Zero framework bloat; ultra-fast initial load (<100ms) and 60 FPS UI rendering |
| **Styling & Design System**| **Custom Vanilla CSS3 (HSL Variables)** | Dark glassmorphism, responsive CSS Grid, haptic vibration keyframes, custom scrollbars |
| **Typography** | **Google Fonts (Plus Jakarta Sans & JetBrains Mono)** | Crisp executive readability paired with monospace industrial telemetry formatting |
| **Graphics Engines** | **HTML5 Canvas 2D & Scalable Vector Graphics (SVG)** | High-frequency oscillogram rendering, dynamic radial gauges, and donut charts |
| **Machine Learning Backend** | **Scikit-Learn (IsolationForest)** | Unsupervised multivariate anomaly detection & novelty scoring |
| **Supervised Classifier** | **LightGBM (Gradient Boosted Trees)** | Ultra-fast multi-class fault classification (Normal, Bearing Wear, Imbalance, Noise, etc.) |
| **Signal Processing** | **NumPy & Pandas** | Trailing causal median smoothing, FFT frequency band extraction, rolling feature transforms |
| **Model Persistence** | **Joblib** | Serialized engine pipeline with feature maps, scalers, and decision thresholds |
| **Backend Web Server** | **Python `http.server.ThreadingHTTPServer`** | Multi-threaded asynchronous REST API server handling live telemetry streaming |

---

## 🎮 Interactive Live Testing & Chaos Engineering

MachinaSense includes an onboard **Chaos Testing Console** at the top of the interface that allows operators to inject real-world mechanical faults and sensor degradation regimes into the live ML pipeline:

| Chaos Mode | Description | Expected ML Pipeline Reaction |
|---|---|---|
| 🟢 **Normal Steady State** | Clean industrial baseline operating conditions | Anomaly score ~0.65 (Normal), Fault prob < 0.05 |
| ⚠️ **Bearing Wear** | High-frequency acceleration bursts and acoustic friction | Vibration jumps to ~5.2 mm/s; LightGBM detects `bearing_wear` with >90% confidence |
| ⚠️ **Imbalance Fault** | 1x rotational harmonic spike | Sinusoidal oscillation amplitude increases; triggers Warning status |
| ⚡ **10% Sensor Dropouts** | Simulated packet loss and intermittent wireless disconnects | Causal median imputation pipeline fills missing data; prevents NaN crashes |
| 🔇 **Dead Sensor** | Simulated hardware freeze / zero-reading flatline | Novelty score drops below threshold; IsolationForest flags sensor fault |
| 💥 **Gaussian Noise** | 1.0σ high-amplitude electrical jitter | Multi-sensor cross-correlation isolates signal from noise |

---

## 🔔 Quiet Notification Center & Bell Popover

- **Quiet Silent Delivery:** Notifications deliver quietly in the background without noisy beeps, intrusive audio chimes, or screen-blocking popups.
- **Bell Icon Badge:** The notification bell icon in the top header features a live unread counter badge and subtle alert indicator.
- **On-Demand Inspection:** Clicking the bell icon opens the glassmorphic **System Alerts Popover** to review active anomalies, filter by `All`, `Critical`, `Warning`, or `Unread`, inspect telemetry, or acknowledge incidents with one click.
- **Optional Sound Toggle:** An optional mute/unmute toggle in the dropdown header allows operators to enable sound chimes only when desired.

---

## 🚀 Quickstart & Installation

### 1. Prerequisites
- **Python 3.10+** (Tested on Python 3.10, 3.11, 3.12, 3.14)
- Modern web browser (Chrome, Edge, Firefox, Safari)

### 2. Clone and Setup Environment
```bash
git clone https://github.com/karthikgveresh-lgtm/MachinaSense.git
cd MachinaSense

# Optional: Create virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install numpy pandas scikit-learn lightgbm joblib
```

### 4. Verify Machine Learning Inference Engine
Run the included verification suite to benchmark rolling window telemetry scoring:
```bash
python test_inference.py
```
*Expected Result:*
```
[5] Overall Telemetry Stream Accuracy: 1325/1327 (99.85%)
================================================================================
 STEP 3 PASSED: Inference engine is verified and ready for live dashboard!
================================================================================
```

### 5. Launch the Server
```bash
python server.py
```
Open your browser and navigate to:
```
http://127.0.0.1:8000
```

---

## 📂 Project Structure

```
MachinaSense/
├── server.py              # Multi-threaded HTTP & REST API server
├── engine.py              # ML Engine, Feature Extractor & Telemetry Generator
├── test_inference.py      # Automated model accuracy and pipeline verification
├── machinasense_engine.joblib # Serialized LightGBM & IsolationForest models
├── public/                # Live frontend assets (served by server.py)
│   ├── index.html         # Complete 7-view single-page application
│   ├── style.css          # Design system, layout grid, and haptic vibrations
│   └── app.js             # Telemetry loop, canvas visualizers & state engine
├── static/                # Source static mirrors
├── api/                   # Vercel serverless API handlers
├── vercel.json            # Deployment configuration
└── README.md              # Project documentation
```

---

## 🛡️ License & Acknowledgements

Developed with precision for modern reliability engineering and predictive maintenance teams. Built to demonstrate high-performance web engineering combined with real-time industrial artificial intelligence.
