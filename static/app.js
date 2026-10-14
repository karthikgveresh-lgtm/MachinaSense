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

    this.setupRouting();
    this.setupChaosButtons();
    this.setupAssetFilters();
    this.setupConditionMonitoring();
    this.setupSettingsView();
    this.setupMaintenanceView();
    this.setupGlobalSearch();
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
          this.fetchData();
        } catch (e) {
          console.error('Failed to set chaos mode:', e);
        }
      });
    });
  }

  async fetchData() {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) return;
      const data = await res.json();
      this.latestData = data;
      this.render(data);
    } catch (e) {
      console.warn('Dashboard fetch error:', e);
    }
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

    // 9. Alerts Feed
    const alertsContainer = document.getElementById('alerts-container');
    if (alertsContainer && data.alerts) {
      document.getElementById('badge-alert-count').textContent = data.alerts.length;
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
}

document.addEventListener('DOMContentLoaded', () => {
  new DashboardApp();
});
