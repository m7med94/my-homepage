// Sensors Hub - Real-time Monitoring Dashboard

// Mock sensor data
const sensors = [
  {
    id: 1,
    name: 'Temperature Sensor - Room A',
    type: 'Temperature',
    unit: '°C',
    minValue: -10,
    maxValue: 50,
    minNormal: 18,
    maxNormal: 28,
    currentValue: 22.5,
    status: 'online',
    lastUpdate: new Date(),
    humidityValue: 45,
  },
  {
    id: 2,
    name: 'Humidity Sensor - Room A',
    type: 'Humidity',
    unit: '%',
    minValue: 0,
    maxValue: 100,
    minNormal: 30,
    maxNormal: 70,
    currentValue: 52,
    status: 'online',
    lastUpdate: new Date(),
    temperatureValue: 22.5,
  },
  {
    id: 3,
    name: 'Temperature Sensor - Room B',
    type: 'Temperature',
    unit: '°C',
    minValue: -10,
    maxValue: 50,
    minNormal: 18,
    maxNormal: 28,
    currentValue: 20.1,
    status: 'online',
    lastUpdate: new Date(),
    humidityValue: 48,
  },
  {
    id: 4,
    name: 'Humidity Sensor - Room B',
    type: 'Humidity',
    unit: '%',
    minValue: 0,
    maxValue: 100,
    minNormal: 30,
    maxNormal: 70,
    currentValue: 55,
    status: 'online',
    lastUpdate: new Date(),
    temperatureValue: 20.1,
  },
  {
    id: 5,
    name: 'Pressure Sensor - Main',
    type: 'Pressure',
    unit: 'kPa',
    minValue: 80,
    maxValue: 120,
    minNormal: 95,
    maxNormal: 105,
    currentValue: 101.3,
    status: 'online',
    lastUpdate: new Date(),
    altitudeValue: 500,
  },
  {
    id: 6,
    name: 'Air Quality Sensor',
    type: 'Air Quality',
    unit: 'ppm',
    minValue: 0,
    maxValue: 1000,
    minNormal: 0,
    maxNormal: 400,
    currentValue: 380,
    status: 'online',
    lastUpdate: new Date(),
    co2Level: 380,
  },
];

// Initialize dashboard
function initDashboard() {
  updateTime();
  renderSensors();
  updateStatistics();

  // Update every second
  setInterval(() => {
    updateTime();
    updateSensorValues();
    updateStatistics();
  }, 1000);

  // Set footer year
  document.getElementById('year').textContent = new Date().getFullYear();
}

// Update time display
function updateTime() {
  const now = new Date();
  const timeString = now.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  document.getElementById('timeDisplay').textContent = timeString;
}

// Simulate sensor value changes
function updateSensorValues() {
  sensors.forEach((sensor) => {
    // Add some random fluctuation to sensor values
    const fluctuation = (Math.random() - 0.5) * (sensor.maxValue - sensor.minValue) * 0.02;
    sensor.currentValue = Math.max(
      sensor.minValue,
      Math.min(sensor.maxValue, sensor.currentValue + fluctuation)
    );
    sensor.lastUpdate = new Date();

    // Randomly set one sensor to offline occasionally
    if (Math.random() > 0.995) {
      sensor.status = sensor.status === 'online' ? 'offline' : 'online';
    }
  });

  // Update displayed values
  document.querySelectorAll('.sensor-card').forEach((card, index) => {
    const sensor = sensors[index];
    if (sensor) {
      card.querySelector('.sensor-value-number').textContent = sensor.currentValue.toFixed(1);
      updateSensorBar(card, sensor);
      updateSensorStatus(card, sensor);
    }
  });
}

// Render sensor cards
function renderSensors() {
  const sensorsGrid = document.getElementById('sensorsGrid');
  sensorsGrid.innerHTML = '';

  sensors.forEach((sensor) => {
    const card = createSensorCard(sensor);
    sensorsGrid.appendChild(card);
  });
}

// Create individual sensor card
function createSensorCard(sensor) {
  const card = document.createElement('div');
  card.className = 'sensor-card';

  const statusClass = `status-${sensor.status}`;
  const fillPercentage = ((sensor.currentValue - sensor.minValue) / (sensor.maxValue - sensor.minValue)) * 100;
  const fillClass = getFillClass(sensor);

  card.innerHTML = `
    <div class="sensor-header">
      <div>
        <p class="sensor-name">${sensor.name}</p>
        <p class="sensor-type">${sensor.type}</p>
      </div>
      <span class="sensor-status ${statusClass}">${sensor.status.toUpperCase()}</span>
    </div>

    <div class="sensor-value">
      <span class="sensor-value-number">${sensor.currentValue.toFixed(1)}</span>
      <span class="sensor-value-unit">${sensor.unit}</span>
    </div>

    <div class="sensor-bar">
      <div class="sensor-bar-fill ${fillClass}" style="width: ${fillPercentage}%"></div>
    </div>

    <div class="sensor-info">
      <div class="sensor-info-item">
        <span class="sensor-info-label">Min</span>
        <span class="sensor-info-value">${sensor.minNormal}${sensor.unit}</span>
      </div>
      <div class="sensor-info-item">
        <span class="sensor-info-label">Max</span>
        <span class="sensor-info-value">${sensor.maxNormal}${sensor.unit}</span>
      </div>
      <div class="sensor-info-item">
        <span class="sensor-info-label">Status</span>
        <span class="sensor-info-value">${sensor.status === 'online' ? '✓ Active' : '✗ Inactive'}</span>
      </div>
      <div class="sensor-info-item">
        <span class="sensor-info-label">Updated</span>
        <span class="sensor-info-value">${getTimeSince(sensor.lastUpdate)}</span>
      </div>
    </div>
  `;

  return card;
}

// Determine bar fill color based on value
function getFillClass(sensor) {
  if (sensor.currentValue >= sensor.minNormal && sensor.currentValue <= sensor.maxNormal) {
    return '';
  } else if (sensor.currentValue > sensor.maxNormal || sensor.currentValue < sensor.minNormal) {
    const distFromNormal = Math.min(
      Math.abs(sensor.currentValue - sensor.maxNormal),
      Math.abs(sensor.currentValue - sensor.minNormal)
    );
    const maxDistFromNormal = Math.max(
      Math.abs(sensor.maxValue - sensor.maxNormal),
      Math.abs(sensor.minValue - sensor.minNormal)
    );
    if (distFromNormal > maxDistFromNormal * 0.5) {
      return 'danger';
    }
    return 'warning';
  }
  return '';
}

// Update sensor bar visualization
function updateSensorBar(card, sensor) {
  const fillPercentage = ((sensor.currentValue - sensor.minValue) / (sensor.maxValue - sensor.minValue)) * 100;
  const bar = card.querySelector('.sensor-bar-fill');
  bar.style.width = fillPercentage + '%';
  bar.className = 'sensor-bar-fill ' + getFillClass(sensor);
}

// Update sensor status badge
function updateSensorStatus(card, sensor) {
  const statusBadge = card.querySelector('.sensor-status');
  statusBadge.className = `sensor-status status-${sensor.status}`;
  statusBadge.textContent = sensor.status.toUpperCase();
}

// Update dashboard statistics
function updateStatistics() {
  const onlineSensors = sensors.filter((s) => s.status === 'online').length;
  const temperatures = sensors
    .filter((s) => s.type === 'Temperature')
    .map((s) => s.currentValue);
  const avgTemp = temperatures.length > 0 ? (temperatures.reduce((a, b) => a + b, 0) / temperatures.length).toFixed(1) : 0;

  document.getElementById('activeSensors').textContent = onlineSensors;
  document.getElementById('avgTemp').textContent = `${avgTemp}°C`;

  // Determine system health
  const allOnline = onlineSensors === sensors.length;
  const allNormal = sensors.every((s) => s.currentValue >= s.minNormal && s.currentValue <= s.maxNormal);

  const healthElement = document.getElementById('systemHealth');
  if (allOnline && allNormal) {
    healthElement.textContent = 'Optimal';
    healthElement.className = 'stat-value good';
  } else if (allOnline) {
    healthElement.textContent = 'Good';
    healthElement.className = 'stat-value';
  } else {
    healthElement.textContent = 'Warning';
    healthElement.className = 'stat-value warning';
  }
}

// Format time since last update
function getTimeSince(date) {
  const seconds = Math.floor((new Date() - date) / 1000);
  if (seconds < 60) return 'Just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return 'Offline';
}

// Start the dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', initDashboard);
