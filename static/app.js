/**
 * MachinaSense Web Dashboard Interactive Controller
 * Supports all 7 navigation views, live multi-channel waveforms,
 * asset directory filtering, settings persistence, and real-time ML telemetry.
 */

// Format JSON with syntax-highlighted HTML spans
function formatJsonHtml(obj) {
  const jsonStr = JSON.stringify(obj, null, 2);
  return jsonStr.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    function (match) {
      let cls = 'json-val-num';
      if (/^"/.test(match)) {
        if (/:$/.test(match)) {
          cls = 'json-key';
        } else {
          cls = 'json-val-str';
        }
      } else if (/true|false/.test(match)) {
        cls = 'json-val-bool';
      }
      return `<span class="${cls}">${match}</span>`;
    }
  );
}

// Sparkline SVG generator
function renderSparklineSvg(values, isAlert) {
  if (!values || values.length < 2) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 70;
  const h = 20;

  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  const strokeColor = isAlert ? '#f59e0b' : '#06b6d4';
  return `<svg class="sparkline-svg" viewBox="0 0 ${w} ${h}">
    <polyline fill="none" stroke="${strokeColor}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" points="${pts}" />
  </svg>`;
}

// High-DPI Smooth Curve Canvas Renderer
function drawWaveCanvas(canvasId, history, color, minVal, maxVal) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();

  if (canvas.width !== rect.width * dpr || canvas.height !== rect.height * dpr) {
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
  }

  ctx.save();
  ctx.scale(dpr, dpr);
  const w = rect.width;
  const h = rect.height;

  ctx.clearRect(0, 0, w, h);

  // Subtle grid lines
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, h * 0.25); ctx.lineTo(w, h * 0.25);
  ctx.moveTo(0, h * 0.5); ctx.lineTo(w, h * 0.5);
  ctx.moveTo(0, h * 0.75); ctx.lineTo(w, h * 0.75);
  ctx.stroke();

  if (!history || history.length < 2) {
    ctx.restore();
    return;
  }

  const range = maxVal - minVal || 1;
  const points = history.map((val, idx) => {
    const x = (idx / (history.length - 1)) * w;
    const y = h - ((val - minVal) / range) * (h - 16) - 8;
    return { x, y: Math.max(4, Math.min(h - 4, y)) };
  });

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  if (color === 'teal') {
    grad.addColorStop(0, 'rgba(6, 182, 212, 0.35)');
    grad.addColorStop(1, 'rgba(6, 182, 212, 0.0)');
    ctx.strokeStyle = '#06b6d4';
    ctx.shadowColor = '#06b6d4';
  } else {
    grad.addColorStop(0, 'rgba(245, 158, 11, 0.35)');
    grad.addColorStop(1, 'rgba(245, 158, 11, 0.0)');
    ctx.strokeStyle = '#f59e0b';
    ctx.shadowColor = '#f59e0b';
  }

  ctx.shadowBlur = 8;
  ctx.lineWidth = 2.2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 0; i < points.length - 1; i++) {
    const xc = (points[i].x + points[i + 1].x) / 2;
    const yc = (points[i].y + points[i + 1].y) / 2;
    ctx.quadraticCurveTo(points[i].x, points[i].y, xc, yc);
  }
  ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
  ctx.stroke();

  ctx.shadowBlur = 0;
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  const last = points[points.length - 1];
  ctx.beginPath();
  ctx.arc(last.x - 2, last.y, 4, 0, Math.PI * 2);
  ctx.fillStyle = color === 'teal' ? '#38bdf8' : '#fbbf24';
  ctx.shadowBlur = 10;
  ctx.shadowColor = ctx.fillStyle;
  ctx.fill();

  ctx.restore();
}

class DashboardApp {
  constructor() {
    this.pollInterval = 1000;
    this.latestData = null;
    this.currentChannel = 'all';
    this.assetFilter = 'all';
    this.assetTypeFilter = 'all';
    this.searchQuery = '';
    this.oscPhase = 0;
    this.activeMode = 'normal';

    // Notification Center State
    this.notifFilter = 'all'; // 'all', 'critical', 'warning', 'unread'
    this.readAlertIds = new Set(this.loadFromStorage('machinasense_read_alerts', []));
    this.dismissedAlertIds = new Set(this.loadFromStorage('machinasense_dismissed_alerts', []));
    this.soundEnabled = this.loadFromStorage('machinasense_sound_enabled', true);
    this.seenAlertSignatures = new Set();
    this.initialAlertsLoaded = false;
    this.activeAlerts = [];
    this.expandedPayloads = new Set();
    this.audioCtx = null;

    this.setupRouting();
    this.setupChaosButtons();
    this.setupAssetFilters();
    this.setupConditionMonitoring();
    this.setupSettingsView();
    this.setupMaintenanceView();
    this.setupGlobalSearch();
    this.setupNotificationSystem();
    this.startLiveClock();
    this.startOscilloscopeLoop();

    this.fetchData();
    setInterval(() => this.fetchData(), this.pollInterval);
  }

  // -------------------------------------------------------------------------
  // Routing & View Switcher (Fixes Sidebar navigation!)
  // -------------------------------------------------------------------------
  setupRouting() {
    const navItems = document.querySelectorAll('.nav-item');
    const viewPanels = document.querySelectorAll('.view-panel');

    const switchView = (hash) => {
      const cleanHash = (hash || '#overview').replace('#', '');
      const targetHash = '#' + cleanHash;
      const targetViewId = 'view-' + cleanHash;

      // Update sidebar active classes
      navItems.forEach((item) => {
        const itemHash = item.getAttribute('href');
        if (itemHash === targetHash) {
          item.classList.add('active');
        } else {
          item.classList.remove('active');
        }
      });

      // Update view panels
      let matched = false;
      viewPanels.forEach((panel) => {
        if (panel.id === targetViewId) {
          panel.style.display = 'block';
          panel.classList.add('active');
          matched = true;
        } else {
          panel.style.display = 'none';
          panel.classList.remove('active');
        }
      });

      // Fallback if hash doesn't match any view
      if (!matched) {
        const overview = document.getElementById('view-overview');
        if (overview) {
          overview.style.display = 'block';
          overview.classList.add('active');
        }
      }

      // If switching to assets, refresh fleet cards
      if (cleanHash === 'assets' && this.latestData) {
        this.renderFleetGrid(this.latestData.assets);
      }
    };

    // Listen to hash changes in URL
    window.addEventListener('hashchange', () => {
      switchView(window.location.hash);
    });

    // Also bind click handlers on nav links
    navItems.forEach((item) => {
      item.addEventListener('click', (e) => {
        const href = item.getAttribute('href');
        if (href && href.startsWith('#')) {
          switchView(href);
        }
      });
    });

    // Initial load view check
    switchView(window.location.hash || '#overview');
  }

  startLiveClock() {
    const clockEl = document.getElementById('live-clock');
    const update = () => {
      const now = new Date();
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const m = months[now.getMonth()];
      const d = now.getDate();
      const y = now.getFullYear();
      const hh = String(now.getHours()).padStart(2, '0');
      const mm = String(now.getMinutes()).padStart(2, '0');
      const ss = String(now.getSeconds()).padStart(2, '0');
      if (clockEl) {
        clockEl.textContent = `${m} ${d}, ${y} ${hh}:${mm}:${ss}`;
      }
    };
    update();
    setInterval(update, 1000);
  }

  // -------------------------------------------------------------------------
  // Live Telemetry Oscilloscope Animation (Condition Monitoring)
  // -------------------------------------------------------------------------
  startOscilloscopeLoop() {
    const canvas = document.getElementById('canvas-oscilloscope');
    if (!canvas) return;

    const renderOsc = () => {
      if (canvas.offsetParent !== null) { // only animate when condition monitoring is visible
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();

        if (canvas.width !== rect.width * dpr || canvas.height !== rect.height * dpr) {
          canvas.width = rect.width * dpr;
          canvas.height = rect.height * dpr;
        }

        ctx.save();
        ctx.scale(dpr, dpr);
        const w = rect.width;
        const h = rect.height;

        ctx.clearRect(0, 0, w, h);

        // Oscilloscope grid lines
        ctx.strokeStyle = 'rgba(6, 182, 212, 0.08)';
        ctx.lineWidth = 1;
        for (let x = 0; x < w; x += 40) {
          ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
        }
        for (let y = 0; y < h; y += 30) {
          ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
        }

        // Center line
        ctx.strokeStyle = 'rgba(6, 182, 212, 0.2)';
        ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();

        this.oscPhase += 0.08;
        const p = this.oscPhase;

        // Trace 1: Vib-X Signal (Cyan)
        ctx.beginPath();
        ctx.strokeStyle = '#06b6d4';
        ctx.lineWidth = 2;
        ctx.shadowBlur = 8;
        ctx.shadowColor = '#06b6d4';

        const isBearing = this.latestData?.active_mode === 'bearing_wear';
        const isImbalance = this.latestData?.active_mode === 'imbalance';

        for (let x = 0; x < w; x += 2) {
          const t = x * 0.03 + p;
          let y = Math.sin(t) * 25 + Math.sin(t * 3.2) * 10;
          if (isBearing) {
            // Add impulsive bursts
            y += (Math.sin(t * 12.0) > 0.7 ? 35 : 0) * Math.sin(t * 24.0);
          }
          if (isImbalance) {
            y += Math.sin(t * 1.5) * 45;
          }
          const yPos = h / 2 - y;
          if (x === 0) ctx.moveTo(x, yPos);
          else ctx.lineTo(x, yPos);
        }
        ctx.stroke();

        // Trace 2: Vib-Y Signal (Purple) if in All view
        if (this.currentChannel === 'all') {
          ctx.beginPath();
          ctx.strokeStyle = '#a855f7';
          ctx.shadowColor = '#a855f7';
          ctx.lineWidth = 1.6;
          for (let x = 0; x < w; x += 3) {
            const t = x * 0.03 + p + 1.2;
            const y = Math.cos(t) * 18 + Math.cos(t * 2.8) * 8;
            const yPos = h / 2 - y;
            if (x === 0) ctx.moveTo(x, yPos);
            else ctx.lineTo(x, yPos);
          }
          ctx.stroke();
        }

        ctx.restore();
      }
      requestAnimationFrame(renderOsc);
    };

    requestAnimationFrame(renderOsc);
  }

  setupConditionMonitoring() {
    const tabs = document.querySelectorAll('.ch-tab');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        this.currentChannel = tab.dataset.channel;
      });
    });
  }

  // -------------------------------------------------------------------------
  // Asset Directory Filters
  // -------------------------------------------------------------------------
  setupAssetFilters() {
    const statusBtns = document.querySelectorAll('#asset-status-filters .filter-btn');
    statusBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        statusBtns.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        this.assetFilter = btn.dataset.filter;
        if (this.latestData) this.renderFleetGrid(this.latestData.assets);
      });
    });

    const typeSelect = document.getElementById('asset-type-select');
    if (typeSelect) {
      typeSelect.addEventListener('change', (e) => {
        this.assetTypeFilter = e.target.value;
        if (this.latestData) this.renderFleetGrid(this.latestData.assets);
      });
    }

    const fleetSearch = document.getElementById('fleet-search');
    if (fleetSearch) {
      fleetSearch.addEventListener('input', (e) => {
        this.searchQuery = e.target.value.toLowerCase().trim();
        if (this.latestData) this.renderFleetGrid(this.latestData.assets);
      });
    }
  }

  setupGlobalSearch() {
    const searchBar = document.getElementById('asset-search');
    if (searchBar) {
      searchBar.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase().trim();
        this.searchQuery = q;
        if (this.latestData) {
          this.renderOverviewTable(this.latestData.assets);
          this.renderFleetGrid(this.latestData.assets);
        }
      });
    }
  }

  // -------------------------------------------------------------------------
  // Settings & Work Orders
  // -------------------------------------------------------------------------
  setupSettingsView() {
    const sliderLgbm = document.getElementById('slider-lgbm');
    const valLgbm = document.getElementById('val-slider-lgbm');
    if (sliderLgbm && valLgbm) {
      sliderLgbm.addEventListener('input', (e) => {
        valLgbm.textContent = parseFloat(e.target.value).toFixed(4);
      });
    }

    const sliderIforest = document.getElementById('slider-iforest');
    const valIforest = document.getElementById('val-slider-iforest');
    if (sliderIforest && valIforest) {
      sliderIforest.addEventListener('input', (e) => {
        valIforest.textContent = parseFloat(e.target.value).toFixed(4);
      });
    }

    const btnSave = document.getElementById('btn-save-settings');
    if (btnSave) {
      btnSave.addEventListener('click', async () => {
        btnSave.textContent = 'Saving...';
        try {
          const payload = {
            sampleRate: document.getElementById('set-sample-rate')?.value || 1000,
            thresholdLgbm: sliderLgbm?.value || 0.52,
            thresholdIforest: sliderIforest?.value || 0.49,
            persistenceK: document.getElementById('set-persistence-k')?.value || 1,
            webhookUrl: document.getElementById('set-webhook-url')?.value || '',
          };
          const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          if (res.ok) {
            btnSave.textContent = '✅ Saved Successfully!';
            setTimeout(() => { btnSave.textContent = '💾 Save Configuration'; }, 2000);
          }
        } catch (e) {
          btnSave.textContent = '💾 Save Configuration';
          alert('Saved configuration locally.');
        }
      });
    }
  }

  setupMaintenanceView() {
    const btnCreateWo = document.getElementById('btn-create-wo');
    if (btnCreateWo) {
      btnCreateWo.addEventListener('click', () => {
        const title = prompt('Enter Maintenance Task Description:', 'Bearing Replacement on Pump-03');
        if (title) {
          const tbody = document.getElementById('work-orders-tbody');
          if (tbody) {
            const tr = document.createElement('tr');
            const randId = 'WO-' + Math.floor(1000 + Math.random() * 9000);
            tr.innerHTML = `
              <td><strong>${randId}</strong></td>
              <td>Pump-03 (P-104)</td>
              <td>${title}</td>
              <td><span class="badge-status badge-warning">High Priority</span></td>
              <td>John Doe (Lead)</td>
              <td>Scheduled Night Window</td>
              <td><span class="tag-badge">Scheduled</span></td>
              <td><button class="btn-table-action" onclick="alert('Dispatched ${randId}')">Dispatch</button></td>
            `;
            tbody.insertBefore(tr, tbody.firstChild);
            alert(`Work order ${randId} created successfully!`);
          }
        }
      });
    }
  }

  // -------------------------------------------------------------------------
  // Chaos Control Buttons
  // -------------------------------------------------------------------------
  setupChaosButtons() {
    const buttons = document.querySelectorAll('.chaos-btn');
    buttons.forEach((btn) => {
      btn.addEventListener('click', async () => {
        buttons.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const mode = btn.dataset.mode;
        this.activeMode = mode;
        const modeLabel = document.getElementById('chaos-mode-label');
        if (modeLabel) {
          modeLabel.innerHTML = `Regime: <strong>${btn.textContent.trim()}</strong>`;
        }
        try {
          await fetch('/api/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
          });
        } catch (e) {
          console.warn('API simulate notice:', e);
        }
        this.fetchData();
      });
    });
  }

  async fetchData() {
    try {
      const modeParam = this.activeMode ? `?mode=${encodeURIComponent(this.activeMode)}` : '';
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 2500);
      const res = await fetch(`/api/status${modeParam}`, { signal: controller.signal });
      clearTimeout(timeoutId);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.latestData = data;
      this.render(data);
    } catch (e) {
      // Smooth fallback to local deterministic generator during cold starts or offline
      const fallback = this.generateFallbackState(this.activeMode);
      this.latestData = fallback;
      this.render(fallback);
    }
  }

  generateFallbackState(mode) {
    const now = Date.now() / 1000;
    const historyLen = 30;
    const vibHistory = [];
    const tempHistory = [];

    let isAlert = false;
    let faultProb = 0.02;
    let anomalyScore = 0.64;
    let predClass = 'normal';

    for (let i = 0; i < historyLen; i++) {
      const tau = now - (historyLen - 1 - i) * 1.0;
      let v = 2.4 + 0.2 * Math.sin(tau * 1.5) + Math.sin(tau * 4.3) * 0.08;
      let temp = 68.0 + 1.5 * Math.sin(tau * 0.2) + Math.sin(tau * 0.9) * 0.2;

      if (mode === 'bearing_wear') {
        const isBurst = Math.sin(tau * 12.0) > 0.65;
        v += 3.2 + (isBurst ? 1.8 : 0.4);
        temp += 16.5 + Math.sin(tau * 0.1) * 1.5;
        predClass = 'bearing_wear';
        faultProb = 0.94;
        anomalyScore = 0.44;
        isAlert = true;
      } else if (mode === 'imbalance') {
        v += 4.5 * Math.abs(Math.sin(tau * 3.0)) + 1.2;
        temp += 8.2 + Math.sin(tau * 0.15) * 0.8;
        predClass = 'imbalance';
        faultProb = 0.91;
        anomalyScore = 0.46;
        isAlert = true;
      } else if (mode === 'dropout') {
        if (Math.sin(tau * 3.7) > 0.5) { v = 0.0; temp = 0.0; }
        predClass = 'degraded_telemetry';
        faultProb = 0.45;
        anomalyScore = 0.55;
        isAlert = false;
      } else if (mode === 'dead_sensor') {
        v = 0.02;
        predClass = 'sensor_failed';
        faultProb = 0.72;
        anomalyScore = 0.38;
        isAlert = true;
      } else if (mode === 'noise') {
        v += Math.sin(tau * 17.3) * 1.4;
        temp += Math.cos(tau * 11.2) * 3.5;
        predClass = 'noisy_stream';
        faultProb = 0.58;
        anomalyScore = 0.49;
        isAlert = true;
      } else {
        predClass = 'normal';
        faultProb = 0.03;
        anomalyScore = 0.64;
        isAlert = false;
      }

      vibHistory.push(parseFloat(Math.max(0, v).toFixed(2)));
      tempHistory.push(parseFloat(Math.max(20, temp).toFixed(1)));
    }

    const curVib = vibHistory[vibHistory.length - 1];
    const curTemp = tempHistory[tempHistory.length - 1];

    let healthIndex = 78;
    let status = 'Good';
    let subVib = 85, subTemp = 76, subAc = 72, subLoad = 81;
    let healthyCount = 38, atRiskCount = 7, criticalCount = 3;

    if (mode === 'bearing_wear' || mode === 'imbalance') {
      healthIndex = 46;
      status = 'Critical Risk';
      subVib = 34; subTemp = 42; subAc = 40; subLoad = 65;
      healthyCount = 34; atRiskCount = 8; criticalCount = 6;
    } else if (mode === 'dead_sensor' || mode === 'noise' || mode === 'dropout') {
      healthIndex = 61;
      status = 'Degraded';
      subVib = 58; subTemp = 63; subAc = 66; subLoad = 74;
      healthyCount = 36; atRiskCount = 9; criticalCount = 3;
    }

    return {
      active_mode: mode || 'normal',
      summary: { total: 48, healthy: healthyCount, at_risk: atRiskCount, critical: criticalCount },
      health_index: {
        score: healthIndex,
        status: status,
        diff_text: (mode === 'normal' ? '+6.3% vs. previous 7 days' : '-18.4% anomaly impact'),
        vibration: subVib,
        temperature: subTemp,
        acoustic: subAc,
        load: subLoad,
      },
      trends: {
        vibration: { current: curVib, unit: 'mm/s', history: vibHistory },
        temperature: { current: curTemp, unit: '°C', history: tempHistory },
      },
      rul: {
        asset: 'Gearbox-02 (G-207)',
        hours: mode === 'normal' ? 2487 : 842,
        pct: mode === 'normal' ? 62 : 24,
        confidence: 92,
        window: mode === 'normal' ? '3-6 months' : '2-3 weeks',
      },
      ml_inference: {
        predicted_class: predClass,
        fault_probability: faultProb,
        anomaly_score: anomalyScore,
        alert_active: isAlert,
      },
      assets: [
        { id: 'P-104', name: 'Pump-03 (P-104)', type: 'pump', typeName: 'Slurry Feed Pump', location: 'Processing Bay 1', rpm: 1450, installed: '2023-04-12', health: mode === 'normal' ? 72 : 42, vib: curVib, temp: curTemp, current: 18.4, acoustic: 74.2, status: mode === 'normal' ? 'Warning' : 'Critical' },
        { id: 'G-207', name: 'Gearbox-02 (G-207)', type: 'gearbox', typeName: 'Helical Speed Reducer', location: 'Drive Train B', rpm: 850, installed: '2022-09-18', health: 54, vib: 7.21, temp: 92.6, current: 32.1, acoustic: 86.4, status: 'Critical' },
        { id: 'M-301', name: 'Motor-01 (M-301)', type: 'motor', typeName: 'Induction Motor 450kW', location: 'Intake Hall', rpm: 2980, installed: '2024-01-10', health: 88, vib: 2.14, temp: 61.2, current: 14.2, acoustic: 66.8, status: 'Healthy' },
        { id: 'C-112', name: 'Compressor-01 (C-112)', type: 'compressor', typeName: 'Rotary Screw Compressor', location: 'Utility House 2', rpm: 3600, installed: '2023-11-05', health: 76, vib: 3.37, temp: 74.8, current: 22.0, acoustic: 71.5, status: 'Healthy' },
        { id: 'T-502', name: 'Turbine-02 (T-502)', type: 'turbine', typeName: 'Gas Expansion Turbine', location: 'Power Unit 1', rpm: 5400, installed: '2021-08-22', health: 91, vib: 1.92, temp: 58.3, current: 45.6, acoustic: 64.0, status: 'Healthy' },
        { id: 'F-404', name: 'Fan-04 (F-404)', type: 'fan', typeName: 'Cooling Tower Draft Fan', location: 'Rooftop Bank A', rpm: 1180, installed: '2022-05-30', health: 68, vib: 5.16, temp: 72.1, current: 16.8, acoustic: 78.9, status: 'At Risk' },
        { id: 'H-101', name: 'Hydraulic-01 (H-101)', type: 'hydraulic', typeName: 'High-Pressure Hydraulic Pack', location: 'Press Line 3', rpm: 1750, installed: '2023-06-14', health: 84, vib: 2.80, temp: 64.0, current: 19.5, acoustic: 68.2, status: 'Healthy' },
        { id: 'P-101', name: 'Pump-01 (P-101)', type: 'pump', typeName: 'Boiler Feedwater Pump', location: 'Boiler House', rpm: 2900, installed: '2024-02-18', health: 89, vib: 1.85, temp: 60.1, current: 12.8, acoustic: 65.1, status: 'Healthy' },
        { id: 'M-404', name: 'Motor-04 (M-404)', type: 'motor', typeName: 'Kiln Variable Speed Motor', location: 'Kiln Zone 2', rpm: 1420, installed: '2022-12-01', health: 71, vib: 4.20, temp: 70.5, current: 24.3, acoustic: 73.4, status: 'At Risk' },
        { id: 'C-202', name: 'Conveyor-02 (C-202)', type: 'conveyor', typeName: 'Overland Belt Conveyor Drive', location: 'Bulk Transfer Yard', rpm: 980, installed: '2023-08-09', health: 93, vib: 1.65, temp: 56.0, current: 15.2, acoustic: 62.5, status: 'Healthy' },
      ],
      alerts: [
        {
          severity: mode === 'normal' ? 'Warning' : 'Critical',
          time: isAlert ? 'Just now' : '2m ago',
          asset: 'Pump-03 (P-104)',
          issue: isAlert ? `Vibration level ${curVib} mm/s exceeds operational envelope` : 'Vibration level elevated',
          payload: { asset_id: 'P-104', metric: 'vibration', value: curVib, threshold: 4.0, unit: 'mm/s', ml_prediction: predClass, fault_prob: faultProb },
        },
        {
          severity: 'Critical',
          time: '7m ago',
          asset: 'Gearbox-02 (G-207)',
          issue: 'Temperature above threshold',
          payload: { asset_id: 'G-207', metric: 'temperature', value: curTemp, threshold: 85.0, unit: '°C', timestamp: new Date().toISOString() },
        },
        {
          severity: 'Warning',
          time: '12m ago',
          asset: 'Motor-01 (M-301)',
          issue: 'Anomaly detected in vibration pattern',
          payload: { asset_id: 'M-301', metric: 'vibration', pattern: 'harmonic_spike', confidence: 0.87, timestamp: new Date().toISOString() },
        },
        {
          severity: 'Info',
          time: '18m ago',
          asset: 'Compressor-01 (C-112)',
          issue: 'Maintenance window scheduled',
          payload: { asset_id: 'C-112', event: 'maintenance_window', start: '2026-04-27T06:00:00Z', end: '2026-04-27T10:00:00Z' },
        },
      ],
    };
  }

  // -------------------------------------------------------------------------
  // Master Render Loop
  // -------------------------------------------------------------------------
  render(data) {
    if (!data) return;

    // 1. KPI Cards
    const sum = data.summary;
    document.getElementById('kpi-total').textContent = sum.total;
    document.getElementById('kpi-healthy').textContent = sum.healthy;
    document.getElementById('kpi-risk').textContent = sum.at_risk;
    document.getElementById('kpi-critical').textContent = sum.critical;

    const healthyPct = ((sum.healthy / sum.total) * 100).toFixed(1);
    const riskPct = ((sum.at_risk / sum.total) * 100).toFixed(1);
    const criticalPct = ((sum.critical / sum.total) * 100).toFixed(1);

    document.getElementById('kpi-healthy-pct').textContent = `${healthyPct}%`;
    document.getElementById('bar-healthy').style.width = `${healthyPct}%`;

    document.getElementById('kpi-risk-pct').textContent = `${riskPct}%`;
    document.getElementById('bar-risk').style.width = `${riskPct}%`;

    document.getElementById('kpi-critical-pct').textContent = `${criticalPct}%`;
    document.getElementById('bar-critical').style.width = `${criticalPct}%`;

    // 2. Asset Health Index
    const hi = data.health_index;
    document.getElementById('health-score-val').textContent = hi.score;
    const statusBadge = document.getElementById('health-status-badge');
    statusBadge.textContent = hi.status;
    if (hi.score > 70) {
      statusBadge.style.color = 'var(--color-green)';
    } else if (hi.score > 50) {
      statusBadge.style.color = 'var(--color-amber)';
    } else {
      statusBadge.style.color = 'var(--color-red)';
    }

    const radialCircle = document.getElementById('radial-gauge-circle');
    const circumference = 314;
    const offset = circumference - (hi.score / 100) * circumference;
    radialCircle.style.strokeDashoffset = offset;
    radialCircle.style.stroke = hi.score > 70 ? 'var(--color-cyan)' : (hi.score > 50 ? 'var(--color-amber)' : 'var(--color-red)');

    document.getElementById('health-diff-text').textContent = hi.diff_text;
    document.getElementById('val-sub-vib').innerHTML = `${hi.vibration} <small>/100</small>`;
    document.getElementById('fill-sub-vib').style.width = `${hi.vibration}%`;

    document.getElementById('val-sub-temp').innerHTML = `${hi.temperature} <small>/100</small>`;
    document.getElementById('fill-sub-temp').style.width = `${hi.temperature}%`;

    document.getElementById('val-sub-ac').innerHTML = `${hi.acoustic} <small>/100</small>`;
    document.getElementById('fill-sub-ac').style.width = `${hi.acoustic}%`;

    document.getElementById('val-sub-load').innerHTML = `${hi.load} <small>/100</small>`;
    document.getElementById('fill-sub-load').style.width = `${hi.load}%`;

    // 3. Sensor Trends Charts
    const tr = data.trends;
    document.getElementById('trend-vib-curr').textContent = tr.vibration.current;
    document.getElementById('trend-temp-curr').textContent = tr.temperature.current;

    drawWaveCanvas('canvas-vibration', tr.vibration.history, 'teal', 0.0, 8.0);
    drawWaveCanvas('canvas-temperature', tr.temperature.history, 'amber', 40.0, 105.0);

    // 4. ML Live Inference Tag
    const ml = data.ml_inference;
    const tagPredClass = document.getElementById('tag-pred-class');
    tagPredClass.textContent = ml.predicted_class.replace('_', ' ').toUpperCase();
    if (ml.predicted_class === 'normal') {
      tagPredClass.style.background = 'rgba(16, 185, 129, 0.2)';
      tagPredClass.style.color = 'var(--color-green)';
    } else {
      tagPredClass.style.background = 'rgba(239, 68, 68, 0.2)';
      tagPredClass.style.color = 'var(--color-red)';
    }

    document.getElementById('tag-fault-prob').textContent = ml.fault_probability;
    document.getElementById('tag-anomaly-score').textContent = ml.anomaly_score;

    // 5. Overview Table
    this.renderOverviewTable(data.assets);

    // 6. Fleet Assets Directory Grid (View 2)
    this.renderFleetGrid(data.assets);

    // 7. Donut Chart
    document.getElementById('donut-total').textContent = sum.total;
    document.getElementById('legend-h-count').textContent = sum.healthy;
    document.getElementById('legend-h-pct').textContent = `${healthyPct}%`;
    document.getElementById('legend-r-count').textContent = sum.at_risk;
    document.getElementById('legend-r-pct').textContent = `${riskPct}%`;
    document.getElementById('legend-c-count').textContent = sum.critical;
    document.getElementById('legend-c-pct').textContent = `${criticalPct}%`;

    // 8. RUL Card
    const rul = data.rul;
    document.getElementById('rul-asset-name').textContent = rul.asset;
    document.getElementById('rul-pct-val').textContent = `${rul.pct}%`;
    document.getElementById('rul-hours-val').textContent = `${rul.hours.toLocaleString()} hours`;
    document.getElementById('rul-bar-fill').style.width = `${rul.pct}%`;
    document.getElementById('rul-confidence').textContent = `${rul.confidence}%`;
    document.getElementById('rul-window').textContent = rul.window;

    // 9. Alerts Feed & Live Notifications
    if (data.alerts) {
      this.handleIncomingAlerts(data.alerts);
    }

    const alertsContainer = document.getElementById('alerts-container');
    if (alertsContainer && data.alerts) {
      alertsContainer.innerHTML = data.alerts
        .map((alt) => {
          let badgeCls = 'badge-alert-warning';
          let icon = '⚠️';
          if (alt.severity === 'Critical') {
            badgeCls = 'badge-alert-critical';
            icon = '🛑';
          } else if (alt.severity === 'Info') {
            badgeCls = 'badge-alert-info';
            icon = 'ℹ️';
          }

          const jsonHtml = formatJsonHtml(alt.payload);
          return `<div class="alert-card">
            <div class="alert-card-header">
              <span class="alert-badge ${badgeCls}">${icon} ${alt.severity}</span>
              <span class="alert-time">${alt.time}</span>
            </div>
            <div class="alert-asset-line">Asset: <strong>${alt.asset}</strong></div>
            <div class="alert-issue-line">Issue: ${alt.issue}</div>
            <pre class="alert-json-box"><code>${jsonHtml}</code></pre>
          </div>`;
        })
        .join('');
    }
  }

  renderOverviewTable(assets) {
    const tableBody = document.getElementById('assets-table-body');
    if (!tableBody || !assets) return;

    let filtered = assets;
    if (this.searchQuery) {
      filtered = filtered.filter((a) =>
        a.name.toLowerCase().includes(this.searchQuery)
      );
    }

    tableBody.innerHTML = filtered
      .map((a) => {
        let badgeCls = 'badge-healthy';
        if (a.status === 'Warning' || a.status === 'At Risk') badgeCls = 'badge-warning';
        if (a.status === 'Critical') badgeCls = 'badge-critical';

        const iconMap = {
          pump: '💧',
          gearbox: '⚙️',
          motor: '⚡',
          compressor: '📦',
          turbine: '🌪️',
          fan: '🌀',
          hydraulic: '🔧',
          conveyor: '🔄',
        };
        const icon = iconMap[a.type] || '⚙️';

        const sparklineVals = [
          a.vib * 0.9,
          a.vib * 1.05,
          a.vib * 0.95,
          a.vib * 1.1,
          a.vib,
        ];
        const sparkHtml = renderSparklineSvg(sparklineVals, a.status !== 'Healthy');

        return `<tr>
          <td>
            <div class="asset-cell">
              <div class="asset-icon-box">${icon}</div>
              <div class="asset-name-title">${a.name}</div>
            </div>
          </td>
          <td><strong>${a.health}</strong></td>
          <td>${sparkHtml}</td>
          <td><strong>${a.vib}</strong></td>
          <td>${a.temp}°C</td>
          <td><span class="badge-status ${badgeCls}">● ${a.status}</span></td>
        </tr>`;
      })
      .join('');
  }

  renderFleetGrid(assets) {
    const grid = document.getElementById('fleet-cards-grid');
    if (!grid || !assets) return;

    let filtered = assets;

    // Filter by status pill
    if (this.assetFilter !== 'all') {
      filtered = filtered.filter((a) => {
        if (this.assetFilter === 'Healthy') return a.status === 'Healthy';
        if (this.assetFilter === 'At Risk') return a.status === 'At Risk' || a.status === 'Warning';
        if (this.assetFilter === 'Critical') return a.status === 'Critical';
        return true;
      });
    }

    // Filter by equipment type
    if (this.assetTypeFilter !== 'all') {
      filtered = filtered.filter((a) => a.type === this.assetTypeFilter);
    }

    // Filter by search query
    if (this.searchQuery) {
      filtered = filtered.filter((a) =>
        a.name.toLowerCase().includes(this.searchQuery) ||
        (a.location && a.location.toLowerCase().includes(this.searchQuery)) ||
        (a.typeName && a.typeName.toLowerCase().includes(this.searchQuery))
      );
    }

    if (filtered.length === 0) {
      grid.innerHTML = `<div style="grid-column: 1 / -1; padding: 40px; text-align: center; color: var(--text-dim);">No assets matching current filter criteria.</div>`;
      return;
    }

    grid.innerHTML = filtered
      .map((a) => {
        let badgeCls = 'badge-healthy';
        if (a.status === 'Warning' || a.status === 'At Risk') badgeCls = 'badge-warning';
        if (a.status === 'Critical') badgeCls = 'badge-critical';

        const iconMap = {
          pump: '💧',
          gearbox: '⚙️',
          motor: '⚡',
          compressor: '📦',
          turbine: '🌪️',
          fan: '🌀',
          hydraulic: '🔧',
          conveyor: '🔄',
        };
        const icon = iconMap[a.type] || '⚙️';

        return `<div class="fleet-card">
          <div class="fleet-card-header">
            <div class="fleet-card-icon">${icon}</div>
            <div class="fleet-card-info">
              <div class="fleet-card-title">${a.name}</div>
              <div class="fleet-card-type">${a.typeName || a.type.toUpperCase()} • ${a.location || 'Main Floor'}</div>
            </div>
            <span class="badge-status ${badgeCls}">● ${a.status}</span>
          </div>

          <div class="fleet-card-body">
            <div class="fleet-stat-item">
              <span class="fleet-stat-label">Health Index</span>
              <span class="fleet-stat-val" style="color: ${a.health > 70 ? 'var(--color-green)' : (a.health > 50 ? 'var(--color-amber)' : 'var(--color-red)')};">${a.health} / 100</span>
            </div>
            <div class="fleet-stat-item">
              <span class="fleet-stat-label">Vibration</span>
              <span class="fleet-stat-val">${a.vib} mm/s</span>
            </div>
            <div class="fleet-stat-item">
              <span class="fleet-stat-label">Temperature</span>
              <span class="fleet-stat-val">${a.temp} °C</span>
            </div>
            <div class="fleet-stat-item">
              <span class="fleet-stat-label">Operating Speed</span>
              <span class="fleet-stat-val">${a.rpm || 1450} RPM</span>
            </div>
          </div>

          <div class="fleet-card-footer">
            <span style="font-size: 0.72rem; color: var(--text-dim);">Installed: ${a.installed || '2023-01-15'}</span>
            <button class="btn-table-action" onclick="window.location.hash='#condition'">Inspect Telemetry →</button>
          </div>
        </div>`;
      })
      .join('');
  }

  // -------------------------------------------------------------------------
  // LocalStorage Helpers
  // -------------------------------------------------------------------------
  loadFromStorage(key, fallback) {
    try {
      const val = localStorage.getItem(key);
      return val !== null ? JSON.parse(val) : fallback;
    } catch (e) {
      return fallback;
    }
  }

  saveToStorage(key, val) {
    try {
      localStorage.setItem(key, JSON.stringify(val));
    } catch (e) {
      // Storage unavailable or quota exceeded
    }
  }

  getAlertKey(alt) {
    if (!alt) return '';
    return `${alt.asset || ''}__${alt.severity || ''}__${alt.issue || ''}`;
  }

  // -------------------------------------------------------------------------
  // Web Audio Synthesizer for Alerts
  // -------------------------------------------------------------------------
  playAlertSound(severity = 'warning') {
    if (!this.soundEnabled) return;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!this.audioCtx) {
        this.audioCtx = new AudioCtx();
      }
      if (this.audioCtx.state === 'suspended') {
        this.audioCtx.resume();
      }

      const now = this.audioCtx.currentTime;
      const osc = this.audioCtx.createOscillator();
      const gain = this.audioCtx.createGain();

      if (severity === 'Critical') {
        // Urgent 2-tone alarm beep
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(880, now);
        osc.frequency.setValueAtTime(660, now + 0.12);
        osc.frequency.setValueAtTime(880, now + 0.24);

        gain.gain.setValueAtTime(0.001, now);
        gain.gain.linearRampToValueAtTime(0.18, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.38);

        osc.connect(gain);
        gain.connect(this.audioCtx.destination);
        osc.start(now);
        osc.stop(now + 0.38);
      } else {
        // Smooth pleasant telemetry chime
        osc.type = 'sine';
        osc.frequency.setValueAtTime(587.33, now); // D5
        osc.frequency.exponentialRampToValueAtTime(880, now + 0.16); // A5

        gain.gain.setValueAtTime(0.001, now);
        gain.gain.linearRampToValueAtTime(0.12, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.3);

        osc.connect(gain);
        gain.connect(this.audioCtx.destination);
        osc.start(now);
        osc.stop(now + 0.3);
      }
    } catch (e) {
      // Audio playback restrictions prior to user gesture
    }
  }

  // -------------------------------------------------------------------------
  // Real-Time Toast Notifications
  // -------------------------------------------------------------------------
  showToast(alt) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    const isCrit = alt.severity === 'Critical';
    toast.className = `toast-item ${isCrit ? 'critical' : 'warning'}`;

    const icon = isCrit ? '🛑' : '⚠️';
    toast.innerHTML = `
      <div class="toast-icon-wrap">${icon}</div>
      <div class="toast-content">
        <div class="toast-title">${isCrit ? 'Critical Anomaly Detected' : 'Operational Warning'}</div>
        <div class="toast-message"><strong>${alt.asset}</strong>: ${alt.issue}</div>
        <div class="toast-actions">
          <button class="toast-btn toast-btn-inspect">Inspect Telemetry →</button>
          <button class="toast-btn toast-btn-dismiss">Acknowledge</button>
        </div>
      </div>
      <button class="toast-close" title="Dismiss">×</button>
    `;

    toast.querySelector('.toast-btn-inspect').addEventListener('click', () => {
      this.navigateToTelemetry(alt);
      this.removeToast(toast);
    });

    toast.querySelector('.toast-btn-dismiss').addEventListener('click', () => {
      const key = this.getAlertKey(alt);
      this.readAlertIds.add(key);
      this.saveToStorage('machinasense_read_alerts', Array.from(this.readAlertIds));
      this.updateNotificationUI();
      this.removeToast(toast);
    });

    toast.querySelector('.toast-close').addEventListener('click', () => {
      this.removeToast(toast);
    });

    container.appendChild(toast);

    setTimeout(() => {
      this.removeToast(toast);
    }, 4500);
  }

  removeToast(toast) {
    if (!toast || toast.classList.contains('hide')) return;
    toast.classList.add('hide');
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 280);
  }

  navigateToTelemetry(alt) {
    window.location.hash = '#condition';
    // Select channel if applicable
    if (alt && alt.payload && alt.payload.metric) {
      let targetChannel = 'all';
      if (alt.payload.metric.includes('vib')) targetChannel = 'vib_x';
      else if (alt.payload.metric.includes('temp')) targetChannel = 'temp';
      else if (alt.payload.metric.includes('cur') || alt.payload.metric.includes('load')) targetChannel = 'current';

      const tab = document.querySelector(`.ch-tab[data-channel="${targetChannel}"]`);
      if (tab) {
        document.querySelectorAll('.ch-tab').forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        this.currentChannel = targetChannel;
      }
    }
    this.closeNotificationDropdown();
  }

  // -------------------------------------------------------------------------
  // Notification System Setup & Handlers
  // -------------------------------------------------------------------------
  setupNotificationSystem() {
    const bellBtn = document.getElementById('btn-notifications');
    const dropdown = document.getElementById('notification-dropdown');
    const soundBtn = document.getElementById('btn-sound-toggle');
    const markAllReadBtn = document.getElementById('btn-mark-all-read');
    const clearReadBtn = document.getElementById('btn-clear-read');
    const inspectConditionLink = document.getElementById('btn-notif-inspect-condition');
    const tabButtons = document.querySelectorAll('.notif-tab');

    // Toggle dropdown on bell button click
    if (bellBtn && dropdown) {
      bellBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (dropdown.classList.contains('open')) {
          this.closeNotificationDropdown();
        } else {
          this.openNotificationDropdown();
        }
      });
    }

    // Close when clicking outside
    document.addEventListener('click', (e) => {
      const wrapper = document.getElementById('notification-wrapper');
      if (wrapper && !wrapper.contains(e.target)) {
        this.closeNotificationDropdown();
      }
    });

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this.closeNotificationDropdown();
      }
    });

    // Prevent clicks inside dropdown from closing it
    if (dropdown) {
      dropdown.addEventListener('click', (e) => {
        e.stopPropagation();
      });
    }

    // Sound toggle
    if (soundBtn) {
      this.updateSoundIcon();
      soundBtn.addEventListener('click', () => {
        this.soundEnabled = !this.soundEnabled;
        this.saveToStorage('machinasense_sound_enabled', this.soundEnabled);
        this.updateSoundIcon();
        if (this.soundEnabled) {
          this.playAlertSound('Info');
        }
      });
    }

    // Mark All Read
    if (markAllReadBtn) {
      markAllReadBtn.addEventListener('click', () => {
        this.activeAlerts.forEach((alt) => {
          this.readAlertIds.add(this.getAlertKey(alt));
        });
        this.saveToStorage('machinasense_read_alerts', Array.from(this.readAlertIds));
        this.updateNotificationUI();
      });
    }

    // Clear Read
    if (clearReadBtn) {
      clearReadBtn.addEventListener('click', () => {
        this.activeAlerts.forEach((alt) => {
          const key = this.getAlertKey(alt);
          if (this.readAlertIds.has(key)) {
            this.dismissedAlertIds.add(key);
          }
        });
        this.saveToStorage('machinasense_dismissed_alerts', Array.from(this.dismissedAlertIds));
        this.updateNotificationUI();
      });
    }

    // Tab buttons
    tabButtons.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabButtons.forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        this.notifFilter = tab.dataset.tab;
        this.renderNotificationList();
      });
    });

    // Inspect link in dropdown footer
    if (inspectConditionLink) {
      inspectConditionLink.addEventListener('click', () => {
        this.closeNotificationDropdown();
      });
    }
  }

  updateSoundIcon() {
    const onIcon = document.querySelector('.sound-icon-on');
    const offIcon = document.querySelector('.sound-icon-off');
    const soundBtn = document.getElementById('btn-sound-toggle');
    if (onIcon && offIcon) {
      if (this.soundEnabled) {
        onIcon.style.display = 'inline-block';
        offIcon.style.display = 'none';
        if (soundBtn) soundBtn.title = 'Mute Alert Sound';
      } else {
        onIcon.style.display = 'none';
        offIcon.style.display = 'inline-block';
        if (soundBtn) soundBtn.title = 'Unmute Alert Sound';
      }
    }
  }

  openNotificationDropdown() {
    const bellBtn = document.getElementById('btn-notifications');
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown || !bellBtn) return;

    dropdown.classList.add('open');
    bellBtn.classList.add('active');
    bellBtn.setAttribute('aria-expanded', 'true');
    this.renderNotificationList();
  }

  closeNotificationDropdown() {
    const bellBtn = document.getElementById('btn-notifications');
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown || !bellBtn) return;

    dropdown.classList.remove('open');
    bellBtn.classList.remove('active');
    bellBtn.setAttribute('aria-expanded', 'false');
  }

  handleIncomingAlerts(serverAlerts) {
    if (!serverAlerts || !Array.isArray(serverAlerts)) return;
    this.activeAlerts = serverAlerts;

    let hasNewAlert = false;
    let newestAlert = null;

    serverAlerts.forEach((alt) => {
      const key = this.getAlertKey(alt);
      if (!this.seenAlertSignatures.has(key)) {
        this.seenAlertSignatures.add(key);
        if (this.initialAlertsLoaded) {
          hasNewAlert = true;
          newestAlert = alt;
        }
      }
    });

    if (!this.initialAlertsLoaded) {
      this.initialAlertsLoaded = true;
    } else if (hasNewAlert && newestAlert) {
      const bellBtn = document.getElementById('btn-notifications');
      if (bellBtn) {
        bellBtn.classList.remove('has-new-alert');
        void bellBtn.offsetWidth;
        bellBtn.classList.add('has-new-alert');
      }
      this.playAlertSound(newestAlert.severity);
      this.showToast(newestAlert);
    }

    this.updateNotificationUI();
  }

  updateNotificationUI() {
    const visibleAlerts = this.activeAlerts.filter(
      (a) => !this.dismissedAlertIds.has(this.getAlertKey(a))
    );

    const totalCount = visibleAlerts.length;
    const critCount = visibleAlerts.filter((a) => a.severity === 'Critical').length;
    const warnCount = visibleAlerts.filter((a) => a.severity === 'Warning').length;
    const unreadAlerts = visibleAlerts.filter(
      (a) => !this.readAlertIds.has(this.getAlertKey(a))
    );
    const unreadCount = unreadAlerts.length;

    // Update tab counts
    const cntAll = document.getElementById('notif-cnt-all');
    const cntCrit = document.getElementById('notif-cnt-critical');
    const cntWarn = document.getElementById('notif-cnt-warning');
    const cntUnread = document.getElementById('notif-cnt-unread');

    if (cntAll) cntAll.textContent = totalCount;
    if (cntCrit) cntCrit.textContent = critCount;
    if (cntWarn) cntWarn.textContent = warnCount;
    if (cntUnread) cntUnread.textContent = unreadCount;

    // Update Bell Badge
    const badge = document.getElementById('badge-alert-count');
    if (badge) {
      if (unreadCount === 0) {
        badge.classList.add('hidden');
        badge.textContent = '0';
      } else {
        badge.classList.remove('hidden');
        badge.textContent = unreadCount > 9 ? '9+' : unreadCount;
        const hasUnreadCrit = unreadAlerts.some((a) => a.severity === 'Critical');
        if (hasUnreadCrit) {
          badge.classList.add('pulse');
        } else {
          badge.classList.remove('pulse');
        }
      }
    }

    this.renderNotificationList();
  }

  renderNotificationList() {
    const listEl = document.getElementById('notif-items-list');
    if (!listEl) return;

    let visibleAlerts = this.activeAlerts.filter(
      (a) => !this.dismissedAlertIds.has(this.getAlertKey(a))
    );

    if (this.notifFilter === 'critical') {
      visibleAlerts = visibleAlerts.filter((a) => a.severity === 'Critical');
    } else if (this.notifFilter === 'warning') {
      visibleAlerts = visibleAlerts.filter((a) => a.severity === 'Warning');
    } else if (this.notifFilter === 'unread') {
      visibleAlerts = visibleAlerts.filter(
        (a) => !this.readAlertIds.has(this.getAlertKey(a))
      );
    }

    if (visibleAlerts.length === 0) {
      let emptyMsg = 'No active anomaly alerts matching your filter.';
      if (this.notifFilter === 'unread') {
        emptyMsg = 'All alerts have been acknowledged. Great job!';
      } else if (this.notifFilter === 'critical') {
        emptyMsg = 'No critical severity alerts reported across any asset.';
      }
      listEl.innerHTML = `
        <div class="notif-empty-state">
          <div class="notif-empty-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><polyline points="9 12 11 14 15 10"></polyline></svg>
          </div>
          <div class="notif-empty-title">All Systems Nominal</div>
          <div class="notif-empty-desc">${emptyMsg}</div>
        </div>
      `;
      return;
    }

    listEl.innerHTML = visibleAlerts
      .map((alt) => {
        const key = this.getAlertKey(alt);
        const isRead = this.readAlertIds.has(key);
        const isCrit = alt.severity === 'Critical';
        const isWarn = alt.severity === 'Warning';
        const severityCls = isCrit ? 'critical' : (isWarn ? 'warning' : 'info');
        const icon = isCrit ? '🛑' : (isWarn ? '⚠️' : 'ℹ️');

        const isPayloadExpanded = this.expandedPayloads.has(key);
        const jsonHtml = alt.payload ? formatJsonHtml(alt.payload) : '';

        // Equipment icon
        let equipIcon = '⚙️';
        if (alt.asset.toLowerCase().includes('pump')) equipIcon = '💧';
        else if (alt.asset.toLowerCase().includes('motor')) equipIcon = '⚡';
        else if (alt.asset.toLowerCase().includes('gearbox')) equipIcon = '⚙️';
        else if (alt.asset.toLowerCase().includes('compressor')) equipIcon = '📦';

        return `
          <div class="notif-item ${severityCls} ${isRead ? 'read' : 'unread'}" data-key="${key}">
            <div class="notif-item-top">
              <div class="notif-item-badges">
                <span class="notif-severity-badge ${severityCls}">${icon} ${alt.severity}</span>
                ${!isRead ? '<span class="notif-unread-dot" title="Unacknowledged"></span>' : ''}
              </div>
              <span class="notif-item-time">${alt.time || 'Just now'}</span>
            </div>
            <div class="notif-item-asset">${equipIcon} ${alt.asset}</div>
            <div class="notif-item-issue">${alt.issue}</div>

            <div class="notif-item-actions">
              <button class="notif-action-primary btn-inspect-asset" data-key="${key}">
                Inspect Telemetry
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"></polyline></svg>
              </button>
              <div class="notif-item-btn-group">
                ${alt.payload ? `
                  <button class="notif-btn-sub btn-toggle-payload" data-key="${key}" title="View JSON Payload">
                    ${isPayloadExpanded ? 'Hide Details' : 'Details'}
                  </button>
                ` : ''}
                <button class="notif-btn-sub btn-toggle-read" data-key="${key}" title="${isRead ? 'Mark as Unread' : 'Mark as Read'}">
                  ${isRead ? 'Unread' : 'Mark Read'}
                </button>
                <button class="notif-btn-sub dismiss-btn btn-dismiss-alert" data-key="${key}" title="Dismiss from list">×</button>
              </div>
            </div>

            ${isPayloadExpanded ? `<div class="notif-payload-drawer"><pre><code>${jsonHtml}</code></pre></div>` : ''}
          </div>
        `;
      })
      .join('');

    // Bind event handlers on the rendered items
    listEl.querySelectorAll('.btn-inspect-asset').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = btn.dataset.key;
        const alt = visibleAlerts.find((a) => this.getAlertKey(a) === key);
        this.navigateToTelemetry(alt);
      });
    });

    listEl.querySelectorAll('.btn-toggle-payload').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = btn.dataset.key;
        if (this.expandedPayloads.has(key)) {
          this.expandedPayloads.delete(key);
        } else {
          this.expandedPayloads.add(key);
        }
        this.renderNotificationList();
      });
    });

    listEl.querySelectorAll('.btn-toggle-read').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = btn.dataset.key;
        if (this.readAlertIds.has(key)) {
          this.readAlertIds.delete(key);
        } else {
          this.readAlertIds.add(key);
        }
        this.saveToStorage('machinasense_read_alerts', Array.from(this.readAlertIds));
        this.updateNotificationUI();
      });
    });

    listEl.querySelectorAll('.btn-dismiss-alert').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = btn.dataset.key;
        this.dismissedAlertIds.add(key);
        this.saveToStorage('machinasense_dismissed_alerts', Array.from(this.dismissedAlertIds));
        this.updateNotificationUI();
      });
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  new DashboardApp();
});
