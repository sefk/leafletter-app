/* Leafletter map.js — Leaflet interaction for campaign worker view */

(function () {
  'use strict';

  // ── State ────────────────────────────────────────────────────────────────
  const selectedIds = new Set();
  let selectionMode = false;
  let streetsLayer = null;
  let coverageLayer = null;
  let coverageVisible = false;
  let map = null;

  // Style helpers
  const STYLE_DEFAULT = { color: '#888', weight: 2, opacity: 0.7 };
  const STYLE_SELECTED = { color: '#1a6b3c', weight: 5, opacity: 1 };
  const STYLE_COVERAGE = { color: '#ff6f00', weight: 5, opacity: 0.8 };

  // ── Init map ─────────────────────────────────────────────────────────────
  const mapOptions = { maxBoundsViscosity: 1.0 };
  if (window.BBOX) {
    const sw = window.BBOX[0], ne = window.BBOX[1];
    const latPad = (ne[0] - sw[0]) * 0.25;
    const lonPad = (ne[1] - sw[1]) * 0.25;
    mapOptions.maxBounds = [[sw[0] - latPad, sw[1] - lonPad], [ne[0] + latPad, ne[1] + lonPad]];
  }
  map = L.map('map', mapOptions);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap contributors',
  }).addTo(map);

  if (window.BBOX) {
    map.fitBounds(window.BBOX, { padding: [20, 20], animate: false });
    map.setMinZoom(map.getBoundsZoom(map.options.maxBounds, true));
  }

  // ── Load streets ─────────────────────────────────────────────────────────
  fetch(window.STREETS_URL)
    .then(r => r.json())
    .then(geojson => {
      if (!geojson.features || geojson.features.length === 0) {
        map.setView([0, 0], 2);
        return;
      }

      streetsLayer = L.geoJSON(geojson, {
        style: STYLE_DEFAULT,
        onEachFeature: (feature, layer) => {
          const id = feature.id;
          const name = feature.properties.name || 'Unnamed street';

          layer.bindTooltip(name, { sticky: true });

          layer.on('click', () => {
            if (!selectionMode) return;
            if (selectedIds.has(id)) {
              selectedIds.delete(id);
              layer.setStyle(STYLE_DEFAULT);
            } else {
              selectedIds.add(id);
              layer.setStyle(STYLE_SELECTED);
            }
            updateSelectionCount();
          });
        },
      }).addTo(map);

      // Fit map to streets bounds only if no server-provided bbox
      if (!window.BBOX) {
        map.fitBounds(streetsLayer.getBounds(), { padding: [20, 20] });
      }
    })
    .catch(err => console.error('Failed to load streets:', err));

  // ── Coverage layer ────────────────────────────────────────────────────────
  function loadCoverage() {
    fetch(window.COVERAGE_URL)
      .then(r => r.json())
      .then(geojson => {
        if (coverageLayer) {
          map.removeLayer(coverageLayer);
        }
        coverageLayer = L.geoJSON(geojson, {
          style: STYLE_COVERAGE,
        });
        if (coverageVisible) {
          coverageLayer.addTo(map);
        }
      })
      .catch(err => console.error('Failed to load coverage:', err));
  }

  // ── UI helpers ────────────────────────────────────────────────────────────
  function updateSelectionCount() {
    const el = document.getElementById('selection-count');
    el.textContent = selectedIds.size > 0
      ? `${selectedIds.size} segment${selectedIds.size === 1 ? '' : 's'} selected`
      : '';
  }

  function setSelectionMode(active) {
    selectionMode = active;
    document.getElementById('btn-log-trip').style.display = active ? 'none' : '';
    document.getElementById('btn-done').style.display = active ? '' : 'none';
    document.getElementById('btn-cancel').style.display = active ? '' : 'none';
    if (streetsLayer) {
      map.getContainer().style.cursor = active ? 'crosshair' : '';
    }
  }

  function resetSelection() {
    selectedIds.clear();
    updateSelectionCount();
    if (streetsLayer) {
      streetsLayer.setStyle(STYLE_DEFAULT);
    }
  }

  function showStatus(msg, type) {
    const el = document.getElementById('status-message');
    el.textContent = msg;
    el.className = type;
    el.style.display = 'block';
  }

  // ── Buttons ───────────────────────────────────────────────────────────────
  document.getElementById('btn-log-trip').addEventListener('click', () => {
    setSelectionMode(true);
    updateSelectionCount();
  });

  document.getElementById('btn-cancel').addEventListener('click', () => {
    setSelectionMode(false);
    resetSelection();
    document.getElementById('trip-form').style.display = 'none';
    document.getElementById('status-message').style.display = 'none';
  });

  document.getElementById('btn-done').addEventListener('click', () => {
    if (selectedIds.size === 0) {
      alert('Please tap at least one street segment first.');
      return;
    }
    setSelectionMode(false);
    document.getElementById('btn-submit').disabled = false;
    document.getElementById('trip-form').style.display = 'block';
    document.getElementById('trip-form').scrollIntoView({ behavior: 'smooth' });
  });

  document.getElementById('btn-toggle-coverage').addEventListener('click', () => {
    const btn = document.getElementById('btn-toggle-coverage');
    if (!coverageVisible) {
      coverageVisible = true;
      btn.textContent = 'Hide Coverage';
      loadCoverage();
    } else {
      coverageVisible = false;
      btn.textContent = 'Show Coverage';
      if (coverageLayer) {
        map.removeLayer(coverageLayer);
      }
    }
  });

  document.getElementById('btn-submit').addEventListener('click', () => {
    const workerName = document.getElementById('worker-name').value.trim();
    const notes = document.getElementById('notes').value.trim();
    const segmentIds = Array.from(selectedIds);

    document.getElementById('btn-submit').disabled = true;

    fetch(window.TRIP_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ segment_ids: segmentIds, worker_name: workerName, notes }),
    })
      .then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t); });
        return r.json();
      })
      .then(() => {
        document.getElementById('trip-form').style.display = 'none';
        showStatus('Trip logged! Thank you for your work.', 'success');
        resetSelection();
        // Reload coverage if visible
        if (coverageVisible) loadCoverage();
        // Scroll to message
        document.getElementById('status-message').scrollIntoView({ behavior: 'smooth' });
      })
      .catch(err => {
        showStatus('Error submitting trip: ' + err.message, 'error');
        document.getElementById('btn-submit').disabled = false;
      });
  });

})();
