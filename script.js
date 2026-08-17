/**
 * SensorsHub — Telemetry & IoT Dashboard Controller
 * Advanced Real-Time Monitoring, Live ESP32 Audio/Visual Notifications & SSE Ingestion
 */

// Initial Sensor Fleet (including ESP32 XiaoZhi Voice Node)
let sensors = [
  {
    id: 1,
    name: 'Living Room Climate',
    category: 'Climate',
    type: 'Temperature',
    location: 'Zone 1 - Main Floor',
    unit: '°C',
    minValue: 10,
    maxValue: 40,
    minNormal: 19,
    maxNormal: 26,
    currentValue: 22.4,
    status: 'online',
    accentColor: '#38bdf8',
    history: [21.8, 22.0, 22.1, 22.3, 22.2, 22.4, 22.5, 22.4],
    lastUpdate: new Date(),
  },
  {
    id: 2,
    name: 'Server Rack Ambient',
    category: 'Climate',
    type: 'Temperature',
    location: 'Server Room B',
    unit: '°C',
    minValue: 10,
    maxValue: 55,
    minNormal: 18,
    maxNormal: 32,
    currentValue: 27.8,
    status: 'online',
    accentColor: '#f43f5e',
    history: [26.5, 27.0, 27.2, 27.5, 27.6, 27.8, 27.7, 27.8],
    lastUpdate: new Date(),
  },
  {
    id: 3,
    name: 'Master Bedroom Humidity',
    category: 'Climate',
    type: 'Humidity',
    location: 'Zone 2 - Upper Floor',
    unit: '%',
    minValue: 10,
    maxValue: 90,
    minNormal: 35,
    maxNormal: 65,
    currentValue: 48.5,
    status: 'online',
    accentColor: '#38bdf8',
    history: [46.0, 46.5, 47.0, 47.5, 48.0, 48.2, 48.5, 48.5],
    lastUpdate: new Date(),
  },
  {
    id: 4,
    name: 'Main Lab Air Quality (CO₂)',
    category: 'Environment',
    type: 'Air Quality',
    location: 'Central Lab',
    unit: 'ppm',
    minValue: 300,
    maxValue: 2000,
    minNormal: 350,
    maxNormal: 800,
    currentValue: 420,
    status: 'online',
    accentColor: '#10b981',
    history: [410, 412, 415, 418, 422, 420, 419, 420],
    lastUpdate: new Date(),
  },
  {
    id: 5,
    name: 'Main Line Power Load',
    category: 'Power',
    type: 'Power',
    location: 'Main Distribution Unit',
    unit: 'W',
    minValue: 0,
    maxValue: 4000,
    minNormal: 100,
    maxNormal: 2800,
    currentValue: 640,
    status: 'online',
    accentColor: '#fbbf24',
    history: [610, 625, 630, 638, 645, 640, 635, 640],
    lastUpdate: new Date(),
  },
  {
    id: 6,
    name: 'Atmospheric Barometer',
    category: 'Environment',
    type: 'Pressure',
    location: 'Roof Weather Station',
    unit: 'hPa',
    minValue: 950,
    maxValue: 1050,
    minNormal: 980,
    maxNormal: 1030,
    currentValue: 1013.2,
    status: 'online',
    accentColor: '#818cf8',
    history: [1013.0, 1013.1, 1013.1, 1013.2, 1013.2, 1013.3, 1013.2],
    lastUpdate: new Date(),
  },
  {
    id: 7,
    name: 'ESP32 XiaoZhi Voice Assistant',
    category: 'Security',
    type: 'Voice',
    location: 'Central Gateway (mo-project-c3)',
    unit: 'Event',
    minValue: 0,
    maxValue: 100,
    minNormal: 0,
    maxNormal: 100,
    currentValue: 'Ready',
    status: 'online',
    accentColor: '#a855f7',
    history: [1, 1, 1, 1, 1, 1, 1, 1],
    lastUpdate: new Date(),
    lastTranscript: 'Waiting for voice transmission...',
  }
];

// App State
let isLive = true;
let currentFilter = 'all';
let currentSort = 'default';
let searchQuery = '';
let nextSensorId = 8;
let updateInterval = null;
let sseConnection = null;

// Icons dictionary based on sensor type
const ICONS = {
  Temperature: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/></svg>`,
  Humidity: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/></svg>`,
  'Air Quality': `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/></svg>`,
  Power: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
  Pressure: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  Motion: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  Voice: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="22"></line></svg>`,
  Light: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`,
};

/** Initialize Application */
function initApp() {
  bindEvents();
  renderSensors();
  updateTopMetrics();
  updateClock();
  logEvent('System initialized. 7 telemetry nodes active.', 'info');

  // Request browser notification permission
  requestNotificationPermission();

  // Connect to live ESP32 real-time notification stream
  connectToEsp32Sse();

  // Master telemetry tick: Every 1.5s
  updateInterval = setInterval(() => {
    if (isLive) {
      simulateTelemetryTick();
      updateDiagnostics();
    }
    updateClock();
  }, 1500);

  // Set copyright year
  const yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();
}

/** Request Desktop Browser Notification Permission */
function requestNotificationPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    // Show prompt on first user gesture or after 2s
    setTimeout(() => {
      Notification.requestPermission();
    }, 2000);
  }
}

/** Connect to FastAPI Live SSE Notification Stream */
function connectToEsp32Sse() {
  const host = window.location.hostname || '104.197.63.204';
  const sseUrl = `http://${host}:8000/api/v1/events/stream`;

  try {
    sseConnection = new EventSource(sseUrl);

    sseConnection.onopen = () => {
      console.log('Connected to ESP32 Live Telemetry Notification Gateway');
      logEvent('Real-time SSE notification link established with ESP32 Gateway (port 8000).', 'info');
    };

    sseConnection.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.type === 'esp32_data') {
          handleIncomingEsp32Notification(payload);
        }
      } catch (err) {
        console.error('Error parsing SSE event:', err);
      }
    };

    sseConnection.onerror = () => {
      // Reconnection handled automatically by EventSource
    };
  } catch (e) {
    console.warn('SSE connection skipped:', e);
  }
}

/** Handle Incoming Live ESP32 Telemetry Notification */
function handleIncomingEsp32Notification(event) {
  // 1. Play audio chime notification
  playNotificationChime();

  // 2. Show Glowing Toast Notification
  const toastMsg = `🎙️ [ESP32 ${event.device_id}] (${event.category}): "${event.data}"`;
  showToast(toastMsg, event.category === 'alert' ? 'danger' : 'info');

  // 3. Fire Desktop System Notification
  if ('Notification' in window && Notification.permission === 'granted') {
    new Notification(`ESP32 Voice Assistant [${event.device_id}]`, {
      body: `Category: ${event.category}\nData: ${event.data}`,
      icon: 'favicon.ico',
    });
  }

  // 4. Record to live Event Stream Log
  logEvent(`ESP32 INGEST [${event.device_id}] [${event.category}]: ${event.data}`, event.category === 'alert' ? 'danger' : 'info');

  // 5. Update ESP32 Sensor card in UI
  const voiceNode = sensors.find((s) => s.type === 'Voice');
  if (voiceNode) {
    voiceNode.currentValue = event.category.toUpperCase();
    voiceNode.lastTranscript = event.data;
    voiceNode.lastUpdate = new Date();
    voiceNode.history.push(voiceNode.history.length + 1);
    if (voiceNode.history.length > 20) voiceNode.history.shift();
    renderSensors();
  }
}

/** Synthesize a subtle, pleasant audio chime using Web Audio API */
function playNotificationChime() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = 'sine';
    // Frequency melody: C5 to G5
    osc.frequency.setValueAtTime(523.25, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(783.99, ctx.currentTime + 0.12);

    gain.gain.setValueAtTime(0.12, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.35);
  } catch (e) {
    // Audio context might be restricted before first user interaction
  }
}

/** Bind Interactive DOM Listeners */
function bindEvents() {
  // Pause / Resume Feed
  const btnPause = document.getElementById('btnPauseResume');
  btnPause.addEventListener('click', toggleLiveFeed);

  // Filter Tabs
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      e.target.classList.add('active');
      currentFilter = e.target.dataset.filter;
      renderSensors();
    });
  });

  // Search Input
  const searchInput = document.getElementById('sensorSearchInput');
  searchInput.addEventListener('input', (e) => {
    searchQuery = e.target.value.toLowerCase().trim();
    renderSensors();
  });

  // Sort Select
  const sortSelect = document.getElementById('sensorSortSelect');
  sortSelect.addEventListener('change', (e) => {
    currentSort = e.target.value;
    renderSensors();
  });

  // Export Data JSON
  document.getElementById('btnExportData').addEventListener('click', exportTelemetrySnapshot);

  // Simulate Anomaly Spike
  document.getElementById('btnTriggerAlert').addEventListener('click', triggerSimulatedSpike);

  // Clear Event Logs
  document.getElementById('btnClearLogs').addEventListener('click', () => {
    document.getElementById('eventStreamContainer').innerHTML = '';
    logEvent('Event log buffer cleared by administrator.', 'info');
  });

  // Modal Controls
  const modal = document.getElementById('addSensorModal');
  document.getElementById('btnAddSensorBtn').addEventListener('click', () => modal.classList.add('open'));
  document.getElementById('btnCloseModal').addEventListener('click', () => modal.classList.remove('open'));
  document.getElementById('btnCancelModal').addEventListener('click', () => modal.classList.remove('open'));

  // Add Sensor Form Submit
  document.getElementById('addSensorForm').addEventListener('submit', handleAddSensorSubmit);
}

/** Clock Update */
function updateClock() {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  document.getElementById('timeDisplay').textContent = timeStr;
}

/** Toggle Live Telemetry Stream */
function toggleLiveFeed() {
  isLive = !isLive;
  const pulse = document.querySelector('.pulse-dot');
  const text = document.getElementById('feedStatusText');

  if (isLive) {
    pulse.classList.remove('paused');
    text.textContent = 'LIVE';
    showToast('Live stream resumed', 'info');
    logEvent('Live telemetry resumed.', 'info');
  } else {
    pulse.classList.add('paused');
    text.textContent = 'PAUSED';
    showToast('Telemetry stream paused', 'warning');
    logEvent('Live telemetry stream paused.', 'warn');
  }
}

/** Telemetry Tick Engine */
function simulateTelemetryTick() {
  sensors.forEach((s) => {
    if (s.type === 'Voice') return; // Handled dynamically via SSE from real ESP32

    if (s.status === 'offline') {
      if (Math.random() > 0.85) {
        s.status = 'online';
        logEvent(`Sensor [${s.name}] re-established link.`, 'info');
      }
      return;
    }

    if (Math.random() > 0.995) {
      s.status = 'offline';
      logEvent(`Connection lost with [${s.name}]!`, 'danger');
      return;
    }

    // Continuous variation
    const range = s.maxValue - s.minValue;
    const noise = (Math.random() - 0.49) * (range * 0.035);
    let nextVal = s.currentValue + noise;
    nextVal = Math.max(s.minValue, Math.min(s.maxValue, nextVal));
    s.currentValue = Number(nextVal.toFixed(s.type === 'Pressure' || s.type === 'Temperature' ? 1 : 0));

    // Append to history for sparklines
    s.history.push(s.currentValue);
    if (s.history.length > 20) s.history.shift();
    s.lastUpdate = new Date();
  });

  renderSensors();
  updateTopMetrics();
}

/** Render Sensor Cards into Grid */
function renderSensors() {
  const grid = document.getElementById('sensorsGrid');
  if (!grid) return;
  
  let list = sensors.filter((s) => {
    const matchesCat = currentFilter === 'all' || s.category.toLowerCase() === currentFilter.toLowerCase();
    const matchesSearch =
      s.name.toLowerCase().includes(searchQuery) ||
      s.location.toLowerCase().includes(searchQuery) ||
      s.type.toLowerCase().includes(searchQuery);
    return matchesCat && matchesSearch;
  });

  if (currentSort === 'name') {
    list.sort((a, b) => a.name.localeCompare(b.name));
  } else if (currentSort === 'value') {
    list.sort((a, b) => (typeof b.currentValue === 'number' ? b.currentValue : 0) - (typeof a.currentValue === 'number' ? a.currentValue : 0));
  } else if (currentSort === 'status') {
    list.sort((a, b) => (a.status === 'online' ? -1 : 1));
  }

  grid.innerHTML = '';

  list.forEach((sensor) => {
    const card = document.createElement('div');
    card.className = 'sensor-card';
    card.style.setProperty('--sensor-accent', sensor.accentColor || 'var(--primary)');

    const isAlert = typeof sensor.currentValue === 'number' && (sensor.currentValue > sensor.maxNormal || sensor.currentValue < sensor.minNormal);
    const statusText = sensor.status === 'offline' ? 'Offline' : isAlert ? 'Alert' : 'Online';
    const statusClass = sensor.status === 'offline' ? 'offline' : isAlert ? 'alert' : 'online';

    const pct = typeof sensor.currentValue === 'number'
      ? Math.min(100, Math.max(0, ((sensor.currentValue - sensor.minValue) / (sensor.maxValue - sensor.minValue)) * 100))
      : 100;
    
    const iconSvg = ICONS[sensor.type] || ICONS.Temperature;
    const displayVal = sensor.type === 'Voice' ? sensor.currentValue : sensor.currentValue;
    const displayUnit = sensor.type === 'Voice' ? '' : sensor.unit;
    const subSubtitle = sensor.type === 'Voice' && sensor.lastTranscript ? `"${sensor.lastTranscript}"` : sensor.location;

    card.innerHTML = `
      <div class="sensor-card-top">
        <div class="sensor-title-group">
          <div class="sensor-avatar">${iconSvg}</div>
          <div>
            <div class="sensor-name">${sensor.name}</div>
            <div class="sensor-loc" style="max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${subSubtitle}</div>
          </div>
        </div>
        <span class="status-tag ${statusClass}">${statusText}</span>
      </div>

      <div class="sensor-data-row">
        <div class="sensor-main-val">
          <span class="sensor-num" style="${sensor.type === 'Voice' ? 'font-size: 1.5rem;' : ''}">${displayVal}</span>
          <span class="sensor-unit">${displayUnit}</span>
        </div>
        <div class="sparkline-box">
          <canvas class="sparkline-canvas" id="spark-${sensor.id}" width="100" height="38"></canvas>
        </div>
      </div>

      <div class="sensor-bar-wrap">
        <div class="sensor-bar-progress ${isAlert ? 'danger' : ''}" style="width: ${pct}%"></div>
      </div>

      <div class="sensor-meta-grid">
        <div class="sensor-meta-item">
          <span class="lbl">Category</span>
          <span class="val">${sensor.category}</span>
        </div>
        <div class="sensor-meta-item">
          <span class="lbl">Type</span>
          <span class="val">${sensor.type}</span>
        </div>
        <div class="sensor-meta-item">
          <span class="lbl">Updated</span>
          <span class="val">${timeAgo(sensor.lastUpdate)}</span>
        </div>
      </div>
    `;

    grid.appendChild(card);
    drawSparkline(`spark-${sensor.id}`, sensor.history, sensor.accentColor || '#38bdf8');
  });
}

/** Render HTML5 Canvas Sparkline Graph */
function drawSparkline(canvasId, points, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !points || points.length < 2) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);

  const numPoints = points.map((p) => (typeof p === 'number' ? p : 1));
  const min = Math.min(...numPoints);
  const max = Math.max(...numPoints);
  const diff = max - min === 0 ? 1 : max - min;

  ctx.beginPath();
  numPoints.forEach((val, i) => {
    const x = (i / (numPoints.length - 1)) * (w - 6) + 3;
    const y = h - 6 - ((val - min) / diff) * (h - 12);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.stroke();

  ctx.lineTo(w - 3, h);
  ctx.lineTo(3, h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, hexToRgba(color, 0.3));
  grad.addColorStop(1, hexToRgba(color, 0.0));
  ctx.fillStyle = grad;
  ctx.fill();
}

/** Top Metrics Calculations */
function updateTopMetrics() {
  const onlineCount = sensors.filter((s) => s.status === 'online').length;
  document.getElementById('statOnlineSensors').textContent = onlineCount;
  document.getElementById('statTotalSensors').textContent = `/ ${sensors.length} total`;

  const temps = sensors.filter((s) => s.type === 'Temperature' && s.status === 'online').map((s) => s.currentValue);
  const avgTemp = temps.length ? (temps.reduce((a, b) => a + b, 0) / temps.length).toFixed(1) : '--';
  document.getElementById('statAvgTemp').textContent = avgTemp;

  const hums = sensors.filter((s) => s.type === 'Humidity' && s.status === 'online').map((s) => s.currentValue);
  const avgHum = hums.length ? (hums.reduce((a, b) => a + b, 0) / hums.length).toFixed(0) : '--';
  document.getElementById('statAvgHumidity').textContent = avgHum;

  const healthEl = document.getElementById('statSystemHealth');
  if (onlineCount === sensors.length) {
    healthEl.textContent = 'Optimal';
    healthEl.className = 'metric-num text-success';
  } else if (onlineCount >= sensors.length * 0.7) {
    healthEl.textContent = 'Degraded';
    healthEl.className = 'metric-num';
    healthEl.style.color = 'var(--warning)';
  } else {
    healthEl.textContent = 'Critical';
    healthEl.className = 'metric-num';
    healthEl.style.color = 'var(--danger)';
  }
}

/** Host Diagnostics Gauges Update */
function updateDiagnostics() {
  const cpu = Math.floor(25 + Math.random() * 30);
  const ram = (4.1 + Math.random() * 0.4).toFixed(1);
  const ping = Math.floor(14 + Math.random() * 10);

  const cpuGauge = document.getElementById('cpuGauge');
  if (cpuGauge) {
    cpuGauge.style.setProperty('--val', cpu);
    document.getElementById('cpuValText').textContent = `${cpu}%`;
  }

  const memGauge = document.getElementById('memGauge');
  if (memGauge) {
    const memPct = Math.round((ram / 8.0) * 100);
    memGauge.style.setProperty('--val', memPct);
    document.getElementById('memValText').textContent = `${memPct}%`;
    document.getElementById('memUsageText').textContent = `${ram} / 8.0 GB`;
  }

  const netGauge = document.getElementById('netGauge');
  if (netGauge) {
    netGauge.style.setProperty('--val', Math.min(100, ping * 2));
    document.getElementById('netValText').textContent = `${ping}ms`;
  }
}

/** Trigger Simulated Spikes */
function triggerSimulatedSpike() {
  const target = sensors[Math.floor(Math.random() * (sensors.length - 1))];
  if (!target) return;
  target.currentValue = Number((target.maxNormal * 1.35).toFixed(1));
  target.history.push(target.currentValue);
  logEvent(`ANOMALY SPIKE: ${target.name} surged to ${target.currentValue}${target.unit}!`, 'danger');
  showToast(`Warning: Spike detected on ${target.name}!`, 'danger');
  playNotificationChime();
  renderSensors();
}

/** Add Sensor Form Submit Handler */
function handleAddSensorSubmit(e) {
  e.preventDefault();
  const name = document.getElementById('sensorName').value;
  const type = document.getElementById('sensorType').value;
  const location = document.getElementById('sensorLocation').value;
  const minNormal = Number(document.getElementById('sensorMin').value);
  const maxNormal = Number(document.getElementById('sensorMax').value);

  const initialVal = Number(((minNormal + maxNormal) / 2).toFixed(1));

  const newSensor = {
    id: nextSensorId++,
    name,
    category: 'Climate',
    type,
    location,
    unit: type === 'Temperature' ? '°C' : type === 'Humidity' ? '%' : 'Units',
    minValue: 0,
    maxValue: 100,
    minNormal,
    maxNormal,
    currentValue: initialVal,
    status: 'online',
    accentColor: '#38bdf8',
    history: [initialVal, initialVal, initialVal],
    lastUpdate: new Date(),
  };

  sensors.push(newSensor);
  document.getElementById('addSensorModal').classList.remove('open');
  document.getElementById('addSensorForm').reset();

  renderSensors();
  updateTopMetrics();
  logEvent(`Registered new sensor node: [${name}]`, 'info');
  showToast(`Added sensor: ${name}`, 'info');
}

/** Export Current Telemetry Snapshot as JSON */
function exportTelemetrySnapshot() {
  const snapshot = {
    timestamp: new Date().toISOString(),
    nodeCount: sensors.length,
    activeNodes: sensors.filter((s) => s.status === 'online').length,
    sensors: sensors.map((s) => ({
      id: s.id,
      name: s.name,
      type: s.type,
      location: s.location,
      value: s.currentValue,
      unit: s.unit,
      status: s.status,
      lastUpdate: s.lastUpdate,
    })),
  };

  const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(snapshot, null, 2));
  const downloadAnchor = document.createElement('a');
  downloadAnchor.setAttribute('href', dataStr);
  downloadAnchor.setAttribute('download', `sensors_telemetry_${Date.now()}.json`);
  document.body.appendChild(downloadAnchor);
  downloadAnchor.click();
  downloadAnchor.remove();
  showToast('Telemetry JSON snapshot downloaded', 'info');
}

/** Event Logging Stream */
function logEvent(message, type = 'info') {
  const container = document.getElementById('eventStreamContainer');
  if (!container) return;

  const now = new Date();
  const time = now.toLocaleTimeString('en-US', { hour12: false });

  const entry = document.createElement('div');
  entry.className = `event-entry ${type}`;
  entry.innerHTML = `
    <span class="event-time">[${time}]</span>
    <span class="event-msg">${message}</span>
  `;

  container.prepend(entry);
  if (container.children.length > 50) {
    container.removeChild(container.lastChild);
  }
}

/** Toast Notifications */
function showToast(message, type = 'info') {
  const stack = document.getElementById('toastStack');
  if (!stack) return;

  const toast = document.createElement('div');
  toast.className = `toast-item ${type}`;
  toast.textContent = message;

  stack.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

/** Helper: Time Ago */
function timeAgo(date) {
  const sec = Math.floor((new Date() - date) / 1000);
  if (sec < 5) return 'Just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  return `${min}m ago`;
}

/** Helper: Hex to RGBA */
function hexToRgba(hex, alpha) {
  let c = hex.replace('#', '');
  if (c.length === 3) c = c.split('').map((char) => char + char).join('');
  const num = parseInt(c, 16);
  return `rgba(${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}, ${alpha})`;
}

// Start application
document.addEventListener('DOMContentLoaded', initApp);
