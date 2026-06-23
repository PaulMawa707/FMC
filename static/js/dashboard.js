function pad(n) {
  return String(n).padStart(2, '0');
}

function toDatetimeLocal(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function initDatetimeDefaults() {
  const now = new Date();
  const from = new Date(now);
  from.setHours(4, 0, 0, 0);
  const end = new Date(now);
  end.setHours(18, 0, 0, 0);
  document.querySelectorAll('.route-from').forEach(function (el) {
    if (!el.value) el.value = toDatetimeLocal(from);
  });
  document.querySelectorAll('.route-end').forEach(function (el) {
    if (!el.value) el.value = toDatetimeLocal(end);
  });
}

function showTab(tabName) {
  document.querySelectorAll('.tab-panel').forEach(function (panel) {
    panel.classList.toggle('active', panel.id === `tab-${tabName}`);
  });
  document.querySelectorAll('.nav-item[data-tab]').forEach(function (item) {
    item.classList.toggle('active', item.dataset.tab === tabName);
  });
  document.querySelectorAll('.mobile-tab-item[data-tab]').forEach(function (item) {
    item.classList.toggle('active', item.dataset.tab === tabName);
  });
  if (tabName === 'fleet') {
    loadFleet();
  }
}

function setAlert(el, type, message, link) {
  if (!el) return;
  el.classList.remove('hidden', 'success', 'error', 'info', 'warning');
  el.classList.add(type || 'info');
  let html = message || '';
  if (link) {
    html += ` <a href="${link}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:underline;">Open in Logistics</a>`;
  }
  el.innerHTML = html;
}

function renderTable(tbody, rows, columns) {
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!rows || !rows.length) {
    tbody.innerHTML = '<tr><td colspan="99">No data available.</td></tr>';
    return;
  }
  rows.forEach(function (row) {
    const tr = document.createElement('tr');
    columns.forEach(function (col) {
      const td = document.createElement('td');
      const value = row[col];
      td.textContent = value === null || value === undefined ? '' : value;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

let fleetCache = [];
let routesCache = [];

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || 'Request failed.');
  }
  return data;
}

async function loadRoutes() {
  const alertEl = document.getElementById('routes-alert');
  const tbody = document.querySelector('#routes-table tbody');
  const routeSelect = document.getElementById('route-select');
  const statRoutes = document.getElementById('stat-routes');
  const statWorkbook = document.getElementById('stat-workbook');

  try {
    const data = await fetchJson('/api/routes');
    routesCache = data.routes || [];
    if (statRoutes) statRoutes.textContent = String(routesCache.length);
    if (statWorkbook) statWorkbook.textContent = data.workbook || '—';

    renderTable(
      tbody,
      routesCache,
      ['Route Name', 'Sheet', 'Format', 'Base Stops', 'Expanded Stops', 'Total Amount', 'Total Tonnage']
    );

    if (routeSelect) {
      routeSelect.innerHTML = '';
      (data.route_names || []).forEach(function (name) {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        routeSelect.appendChild(option);
      });
      if (routeSelect.options.length) {
        await loadRoutePreview(routeSelect.value);
      }
    }

    if (data.parse_errors && Object.keys(data.parse_errors).length) {
      setAlert(alertEl, 'warning', `Some sheets were skipped: ${JSON.stringify(data.parse_errors)}`);
    } else {
      setAlert(alertEl, 'info', `Loaded ${routesCache.length} routes from ${data.workbook}.`);
    }
  } catch (err) {
    setAlert(alertEl, 'error', err.message);
  }
}

async function loadRoutePreview(routeName) {
  const previewAlert = document.getElementById('preview-alert');
  const dispatchBadge = document.getElementById('dispatch-badge');
  const vehicleSelect = document.getElementById('vehicle-select');

  if (!routeName) return;

  try {
    const data = await fetchJson(`/api/routes/${encodeURIComponent(routeName)}/preview`);

    renderTable(
      document.querySelector('#base-stops-table tbody'),
      data.base_stops,
      ['PRIORITY', 'CUSTOMER NAME', 'LOCATION', 'LAT', 'LONG', 'TONNAGE', 'AMOUNT']
    );
    renderTable(
      document.querySelector('#expanded-stops-table tbody'),
      data.expanded_stops,
      ['SEQUENCE', 'STOP TYPE', 'DISPLAY NAME', 'LOCATION', 'LAT', 'LONG']
    );

    if (dispatchBadge) {
      dispatchBadge.textContent = data.dispatch_ready ? 'Ready' : 'Blocked';
      dispatchBadge.className = `badge ${data.dispatch_ready ? 'ok' : 'fail'}`;
    }

    setAlert(
      previewAlert,
      data.dispatch_ready ? 'success' : 'error',
      data.dispatch_summary
    );

    if (vehicleSelect && fleetCache.length) {
      const defaultVehicle = data.default_vehicle;
      if (defaultVehicle) {
        const match = Array.from(vehicleSelect.options).find(function (opt) {
          return opt.value === defaultVehicle;
        });
        if (match) vehicleSelect.value = defaultVehicle;
      }
    }
  } catch (err) {
    setAlert(previewAlert, 'error', err.message);
  }
}

async function loadFleet() {
  const alertEl = document.getElementById('fleet-alert');
  const tbody = document.querySelector('#fleet-table tbody');
  const vehicleSelect = document.getElementById('vehicle-select');
  const statFleet = document.getElementById('stat-fleet');

  try {
    const data = await fetchJson('/api/fleet');
    fleetCache = data.fleet || [];
    if (statFleet) statFleet.textContent = String(fleetCache.length);

    renderTable(tbody, fleetCache, ['asset_name', 'itemid']);

    if (vehicleSelect) {
      const current = vehicleSelect.value;
      vehicleSelect.innerHTML = '';
      fleetCache.forEach(function (asset) {
        const option = document.createElement('option');
        option.value = asset.asset_name;
        option.dataset.unitId = asset.itemid;
        option.textContent = asset.asset_name;
        vehicleSelect.appendChild(option);
      });
      if (current) vehicleSelect.value = current;
    }

    if (data.message) {
      setAlert(alertEl, 'warning', data.message);
    } else {
      setAlert(alertEl, 'info', `Loaded ${fleetCache.length} vehicles from ${data.workbook}.`);
    }
  } catch (err) {
    setAlert(alertEl, 'error', err.message);
  }
}

async function dispatchRoute() {
  const alertEl = document.getElementById('dispatch-alert');
  const btn = document.getElementById('dispatch-btn');
  const routeSelect = document.getElementById('route-select');
  const vehicleSelect = document.getElementById('vehicle-select');
  const routeFrom = document.getElementById('route-from');
  const routeEnd = document.getElementById('route-end');
  const warehouse = document.getElementById('warehouse-select');

  const selectedOption = vehicleSelect?.selectedOptions?.[0];
  const unitId = selectedOption?.dataset?.unitId;

  if (!routeSelect?.value || !vehicleSelect?.value || !unitId) {
    setAlert(alertEl, 'error', 'Select a route and vehicle before dispatching.');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Dispatching…';

  try {
    const data = await fetchJson('/api/dispatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        route_name: routeSelect.value,
        vehicle_name: vehicleSelect.value,
        unit_id: unitId,
        route_from: routeFrom.value,
        route_end: routeEnd.value,
        warehouse: warehouse.value,
      }),
    });
    setAlert(alertEl, 'success', data.message, data.planning_url);
  } catch (err) {
    setAlert(alertEl, 'error', err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Dispatch Selected Route';
  }
}

function initClock() {
  const clockEl = document.getElementById('clock-time');
  if (!clockEl) return;
  function tick() {
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString('en-GB', { hour12: false });
  }
  tick();
  setInterval(tick, 1000);
}

document.addEventListener('DOMContentLoaded', function () {
  initDatetimeDefaults();
  initClock();

  const activeTab = document.body.dataset.activeTab || 'dispatch';
  showTab(activeTab);

  const routeSelect = document.getElementById('route-select');
  if (routeSelect) {
    routeSelect.addEventListener('change', function () {
      loadRoutePreview(routeSelect.value);
    });
  }

  const dispatchBtn = document.getElementById('dispatch-btn');
  if (dispatchBtn) {
    dispatchBtn.addEventListener('click', dispatchRoute);
  }

  const refreshBtn = document.getElementById('refresh-routes-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', loadRoutes);
  }

  loadFleet().then(loadRoutes);
});
