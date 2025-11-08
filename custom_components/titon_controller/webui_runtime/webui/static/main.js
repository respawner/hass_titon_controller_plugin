/* Titon WebUI client logic */

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Request failed ${resp.status}: ${text}`);
  }
  return resp.json();
}

function formatTimestamp(ts) {
  if (!ts) return '--';
  const date = new Date(ts);
  return date.toLocaleString();
}

// -----------------------
// Home page interactions
// -----------------------

function initHomePage() {
  let timerStart = null;
  const autoToggle = $('#autoToggle');
  const nightToggle = $('#nightToggle');
  const statusLine = $('#controlStatus');

  async function refreshStatus() {
    try {
      const data = await fetchJSON('/api/status');
      applyStatus(data);
      statusLine.textContent = data.auto_status?.reason || 'Ready.';
    } catch (err) {
      statusLine.textContent = `Error: ${err.message}`;
    }
  }

  function applyStatus(data) {
    const sensors = data.sensors || {};
    $('#cardIndoorTemp').textContent = sensors.indoor_temp != null ? `${sensors.indoor_temp}°C` : '--';
    $('#cardOutdoorTemp').textContent = sensors.outdoor_temp != null ? `${sensors.outdoor_temp}°C` : '--';
    $('#cardFreshTemp').textContent = sensors.fresh_temp != null ? `${sensors.fresh_temp}°C` : '--';
    $('#cardLocalHumidity').textContent = sensors.humidity != null ? `${sensors.humidity}%` : '--';
    $('#cardRuntime').textContent = sensors.runtime_hours != null ? `${(sensors.runtime_hours / 24).toFixed(1)} d` : '--';

    const metrics = data.metrics || {};
    $('#cardAvgHumidity').textContent = metrics.avg_humidity != null ? `${metrics.avg_humidity}%` : '--';
    $('#cardMaxDelta').textContent = metrics.max_delta != null ? `${metrics.max_delta >= 0 ? '+' : ''}${metrics.max_delta.toFixed(1)}%` : '--';
    $('#cardTimeInRange').textContent = metrics.time_in_range_pct != null ? `${metrics.time_in_range_pct}%` : '--';

    const autoStatus = data.auto_status || {};
    $('#autoStatusLabel').textContent = autoStatus.reason || 'Idle';

    autoToggle.checked = Boolean(data.auto_enabled);
    nightToggle.checked = Boolean(data.night_quiet_enabled);
    $('#boostInhibitLabel').textContent = data.boost_inhibit ? 'ON' : 'OFF';

    const currentLevel = data.current_level;
    const levelLabel = $('#currentLevelLabel');
    if (currentLevel) {
      levelLabel.textContent = `Level ${currentLevel}`;
    } else if (data.boost_active) {
      levelLabel.textContent = 'Boost';
    } else {
      levelLabel.textContent = 'Standby';
    }

    timerStart = data.level_start_time ? new Date(data.level_start_time) : null;

    // Update buttons state
    $$('.btn-level').forEach((btn) => {
      const level = parseInt(btn.dataset.level, 10);
      if (level && level === currentLevel) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    // Room humidity cards
    const roomValues = data.ha_humidity || {};
    const targets = data.settings?.humidity_targets || {};
    $$('.room-value').forEach((el) => {
      const entity = el.dataset.roomEntity;
      const value = roomValues[entity];
      el.textContent = value != null ? `${value.toFixed(1)}%` : '--%';
    });
    $$('.room-target span').forEach((el) => {
      const entity = el.dataset.roomTarget;
      if (entity && targets[entity] != null) {
        el.textContent = targets[entity];
      }
    });
  }

  function updateTimer() {
    const label = $('#activeTimer');
    if (!timerStart) {
      label.textContent = '--';
      return;
    }
    const diff = Math.max(0, Date.now() - timerStart.getTime());
    const minutes = Math.floor(diff / 60000);
    const seconds = Math.floor((diff % 60000) / 1000);
    label.textContent = `${minutes}m ${seconds.toString().padStart(2, '0')}s`;
  }

  setInterval(updateTimer, 1000);

  autoToggle?.addEventListener('change', async (ev) => {
    try {
      await fetchJSON('/api/auto/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: ev.target.checked }),
      });
      refreshStatus();
    } catch (err) {
      statusLine.textContent = `Auto toggle failed: ${err.message}`;
    }
  });

  nightToggle?.addEventListener('change', async (ev) => {
    try {
      await fetchJSON('/api/night-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: ev.target.checked }),
      });
      refreshStatus();
    } catch (err) {
      statusLine.textContent = `Night mode toggle failed: ${err.message}`;
    }
  });

  $('.control-buttons')?.addEventListener('click', async (ev) => {
    const button = ev.target.closest('button[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    try {
      if (action === 'level') {
        const level = parseInt(button.dataset.level, 10);
        statusLine.textContent = `Setting Level ${level}…`;
        await fetchJSON(`/api/level/${level}`, { method: 'POST' });
      } else if (action === 'off') {
        statusLine.textContent = 'Turning off all levels…';
        await fetchJSON('/api/off', { method: 'POST' });
      } else if (action === 'boost') {
        statusLine.textContent = 'Toggling boost…';
        await fetchJSON('/api/boost', { method: 'POST' });
      }
    } catch (err) {
      statusLine.textContent = `Command failed: ${err.message}`;
    } finally {
      refreshStatus();
    }
  });

  refreshStatus();
  setInterval(refreshStatus, 5000);
}

// -----------------------
// Logs page
// -----------------------

function initLogsPage() {
  const table = $('#logsTable');
  async function renderLogs() {
    try {
      const data = await fetchJSON('/api/logs?limit=200');
      const rows = data.logs.map((entry) => `
        <tr>
          <td>${formatTimestamp(entry.ts)}</td>
          <td>${entry.kind}</td>
          <td>${entry.message}</td>
        </tr>
      `).join('');
      table.innerHTML = rows || '<tr><td colspan="3">No log entries.</td></tr>';
    } catch (err) {
      table.innerHTML = `<tr><td colspan="3">Failed to load logs: ${err.message}</td></tr>`;
    }
  }
  $('#refreshLogs')?.addEventListener('click', renderLogs);
  renderLogs();
  setInterval(renderLogs, 10000);
}

// -----------------------
// Performance page
// -----------------------

function initPerformancePage() {
  const humidityCtx = $('#humidityChart');
  const levelCtx = $('#levelChart');
  let humidityChart;
  let levelChart;
  let historyWindow = 288;

  function buildCharts(history) {
    const labels = history.map((point) => new Date(point.ts));
    const avgHumidity = history.map((p) => p.avg_humidity ?? null);
    const maxHumidity = history.map((p) => p.max_humidity ?? null);
    const avgDelta = history.map((p) => p.avg_delta ?? null);
    const maxDelta = history.map((p) => p.max_delta ?? null);
    const levels = history.map((p) => p.level ?? 0);

    if (humidityChart) humidityChart.destroy();
    if (levelChart) levelChart.destroy();

    humidityChart = new Chart(humidityCtx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Average Humidity',
            data: avgHumidity,
            borderColor: '#60a5fa',
            backgroundColor: 'rgba(96,165,250,0.15)',
            tension: 0.3,
            spanGaps: true,
          },
          {
            label: 'Max Humidity',
            data: maxHumidity,
            borderColor: '#8b5cf6',
            backgroundColor: 'rgba(139,92,246,0.12)',
            tension: 0.3,
            spanGaps: true,
          },
          {
            label: 'Avg Δ Target',
            data: avgDelta,
            borderColor: '#22c55e',
            borderDash: [6, 6],
            tension: 0.3,
            yAxisID: 'delta',
            spanGaps: true,
          },
          {
            label: 'Max Δ Target',
            data: maxDelta,
            borderColor: '#ef4444',
            borderDash: [6, 6],
            tension: 0.3,
            yAxisID: 'delta',
            spanGaps: true,
          },
        ],
      },
      options: {
        responsive: true,
        scales: {
          x: {
            type: 'time',
            time: { tooltipFormat: 'MMM d HH:mm' },
            ticks: { color: '#94a3b8' },
            grid: { color: 'rgba(148, 163, 184, 0.1)' },
          },
          y: {
            beginAtZero: false,
            ticks: { color: '#bae6fd' },
            grid: { color: 'rgba(59, 130, 246, 0.08)' },
          },
          delta: {
            position: 'right',
            ticks: { color: '#fca5a5' },
            grid: { drawOnChartArea: false },
          },
        },
        plugins: {
          legend: { labels: { color: '#cbd5f5' } },
        },
      },
    });

    levelChart = new Chart(levelCtx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Level',
            data: levels,
            backgroundColor: 'rgba(59, 130, 246, 0.6)',
            borderRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        scales: {
          x: {
            type: 'time',
            time: { tooltipFormat: 'MMM d HH:mm' },
            ticks: { color: '#94a3b8' },
            grid: { color: 'rgba(148, 163, 184, 0.1)' },
          },
          y: {
            suggestedMin: 0,
            suggestedMax: 4.5,
            ticks: { stepSize: 1, color: '#bae6fd' },
            grid: { color: 'rgba(59, 130, 246, 0.08)' },
          },
        },
        plugins: {
          legend: { labels: { color: '#cbd5f5' } },
        },
      },
    });
  }

  async function refreshHistory() {
    try {
      const data = await fetchJSON(`/api/history?limit=${historyWindow}`);
      const history = data.history || [];
      buildCharts(history);
    } catch (err) {
      console.error('History fetch failed', err);
    }
  }

  $$('.chart-actions button')?.forEach((btn) => {
    btn.addEventListener('click', () => {
      historyWindow = parseInt(btn.dataset.historyWindow, 10);
      refreshHistory();
    });
  });

  refreshHistory();
  setInterval(refreshHistory, 60000);
}

// -----------------------
// Settings page
// -----------------------

function initSettingsPage() {
  const form = $('#settingsForm');
  const status = $('#settingsStatus');
  if (!form) return;

  function showStatus(message, success = true) {
    status.textContent = message;
    status.style.background = success ? 'rgba(22, 163, 74, 0.15)' : 'rgba(239, 68, 68, 0.15)';
    status.style.borderColor = success ? 'rgba(34, 197, 94, 0.4)' : 'rgba(248, 113, 113, 0.35)';
    status.style.color = success ? '#bbf7d0' : '#fecaca';
  }

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const payload = {
      humidity_targets: {},
      auto_mode: {
        override_minutes: parseInt($('#autoOverrideMinutes').value, 10),
        aggressiveness: $('#autoAggressiveness').value,
      },
      night_quiet: {
        enabled: $('#nightEnabled').checked,
        start: $('#nightStart').value,
        end: $('#nightEnd').value,
        max_level: parseInt($('#nightMaxLevel').value, 10),
      },
      ha: {
        url: $('#haUrl').value,
        token: $('#haToken').value,
        poll_seconds: parseInt($('#haPoll').value, 10),
      },
    };

    $$('[data-sensor-target]').forEach((input) => {
      const entity = input.dataset.sensorTarget;
      const value = parseFloat(input.value);
      if (!Number.isNaN(value)) {
        payload.humidity_targets[entity] = value;
      }
    });

    try {
      await fetchJSON('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      showStatus('Settings saved successfully.');
    } catch (err) {
      showStatus(`Save failed: ${err.message}`, false);
    }
  });
}

// -----------------------
// Boot
// -----------------------

document.addEventListener('DOMContentLoaded', () => {
  const page = document.body.dataset.page;
  switch (page) {
    case 'home':
      initHomePage();
      break;
    case 'logs':
      initLogsPage();
      break;
    case 'performance':
      initPerformancePage();
      break;
    case 'settings':
      initSettingsPage();
      break;
    default:
      break;
  }
});

