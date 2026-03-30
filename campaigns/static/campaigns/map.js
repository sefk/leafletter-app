/* Leafletter map.js — Leaflet interaction for campaign worker view */

(function () {
  'use strict';

  // ── Debug mode (append ?debug=1 to URL) ──────────────────────────────────
  const DEBUG_MODE = new URLSearchParams(location.search).get('debug') === '1';

  // ── State ────────────────────────────────────────────────────────────────
  const selectedIds = new Set();
  const selectionStack = [];        // undo stack: each entry is an id (click) or id[] (lasso batch)
  const layerById = new Map();      // id → Leaflet layer
  const layerToId = new Map();      // layer → id (for lasso reverse lookup)
  const nameById = new Map();       // id → street name (debug)
  let isPointerDown = false;
  let selectionMode = false;
  let spacebarPanning = false;
  let streetsLayer = null;
  let coverageMode = 'summary';   // 'detail' | 'summary' | 'hidden'
  let summaryLayer = null;
  let lasso = null;
  let map = null;

  // Per-trip coverage state
  const tripLayers = new Map();   // trip_id → Leaflet layer group
  const tripVisible = new Map();  // trip_id → boolean
  const tripMeta = new Map();     // trip_id → {worker_name, recorded_at, color}

  // Style helpers
  const STYLE_DEFAULT = { color: '#888', weight: 2, opacity: 0.7 };
  const STYLE_SELECTED = { color: '#1a6b3c', weight: 5, opacity: 1 };

  // Distinct colors for individual trips
  const TRIP_PALETTE = [
    '#e41a1c', '#377eb8', '#ff7f00', '#984ea3',
    '#4daf4a', '#a65628', '#f781bf', '#00bcd4',
    '#795548', '#607d8b',
  ];

  // ── Progress-tracked JSON fetch ───────────────────────────────────────────
  function fetchJSON(url, onProgress) {
    return fetch(url).then(r => {
      const total = parseInt(r.headers.get('Content-Length'), 10);
      if (!total || !r.body) return r.json();
      let loaded = 0;
      const chunks = [];
      const reader = r.body.getReader();
      onProgress(0);
      function pump() {
        return reader.read().then(({ done, value }) => {
          if (done) {
            const all = new Uint8Array(loaded);
            let pos = 0;
            for (const chunk of chunks) { all.set(chunk, pos); pos += chunk.length; }
            return JSON.parse(new TextDecoder().decode(all));
          }
          chunks.push(value);
          loaded += value.length;
          onProgress(Math.min(99, Math.round(loaded / total * 100)));
          return pump();
        });
      }
      return pump();
    });
  }

  // ── Pointer tracking (for drag-to-select) ────────────────────────────────
  document.addEventListener('mousedown', () => { isPointerDown = true; });
  document.addEventListener('mouseup', () => { isPointerDown = false; });
  document.addEventListener('touchstart', () => { isPointerDown = true; });
  document.addEventListener('touchend', () => { isPointerDown = false; });

  // ── Spacebar panning (hold Space to temporarily pan while in selection mode)
  document.addEventListener('keydown', e => {
    if (e.code === 'Space' && selectionMode && !spacebarPanning) {
      e.preventDefault();
      spacebarPanning = true;
      if (lasso) lasso.disable();
      map.dragging.enable();
      map.getContainer().style.cursor = 'grab';
    }
  });
  document.addEventListener('keyup', e => {
    if (e.code === 'Space' && spacebarPanning) {
      spacebarPanning = false;
      if (selectionMode) {
        map.dragging.disable();
        if (lasso) lasso.enable();
        map.getContainer().style.cursor = 'crosshair';
      }
    }
  });

  // ── Init map ─────────────────────────────────────────────────────────────
  // Use geo_limit bounds when available — it may be larger than the streets BBOX.
  let viewBounds = null;
  if (window.GEO_LIMIT) {
    viewBounds = L.geoJSON(window.GEO_LIMIT).getBounds();
  } else if (window.BBOX) {
    viewBounds = L.latLngBounds(window.BBOX[0], window.BBOX[1]);
  }

  const mapOptions = { maxBoundsViscosity: 1.0 };
  if (viewBounds) {
    const sw = viewBounds.getSouthWest(), ne = viewBounds.getNorthEast();
    const latPad = (ne.lat - sw.lat) * 0.25;
    const lonPad = (ne.lng - sw.lng) * 0.25;
    mapOptions.maxBounds = [
      [sw.lat - latPad, sw.lng - lonPad],
      [ne.lat + latPad, ne.lng + lonPad],
    ];
  }
  map = L.map('map', mapOptions);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© <a href="https://www.openstreetmap.org">OpenStreetMap</a> contributors',
    referrerPolicy: 'origin',
  }).addTo(map);

  if (viewBounds) {
    map.fitBounds(viewBounds, { padding: [20, 20], animate: false });
    map.setMinZoom(map.getBoundsZoom(map.options.maxBounds, false));
  }

  // ── Campaign boundary (red dashed line) ──────────────────────────────────
  if (window.GEO_LIMIT) {
    L.geoJSON(window.GEO_LIMIT, {
      style: { color: '#cc0000', weight: 2.5, fillOpacity: 0, dashArray: '8,5', opacity: 0.85 },
      interactive: false,
    }).addTo(map);
  }

  // ── Map-not-ready state: disable interaction, show overlay ───────────────
  if (window.MAP_READY === false) {
    // Disable all map interaction (scrolling/panning/zooming) but keep the
    // map visible so the user can see the tile background.
    map.dragging.disable();
    map.touchZoom.disable();
    map.doubleClickZoom.disable();
    map.scrollWheelZoom.disable();
    map.boxZoom.disable();
    map.keyboard.disable();
    if (map.tap) map.tap.disable();
    // The overlay HTML is only rendered server-side when the map isn't ready,
    // so no JS is needed to show it — it's visible as soon as it exists.
  }

  // ── Loading state for "Log a Trip" button ────────────────────────────────
  const btnLogTrip = document.getElementById('btn-log-trip');
  btnLogTrip.disabled = true;

  function setLoadingStatus(msg) {
    const el = document.getElementById('loading-status');
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.style.display = 'block';
    } else {
      el.textContent = '';
      el.style.display = 'none';
    }
  }

  // When map isn't ready, skip street/coverage loading entirely — the overlay
  // already explains the situation and disables interaction.
  if (window.MAP_READY === false) {
    return;
  }

  setLoadingStatus('Loading streets… 0%');

  // ── Load streets ─────────────────────────────────────────────────────────
  fetchJSON(window.STREETS_URL, pct => { setLoadingStatus(`Loading streets… ${pct}%`); })
    .then(geojson => {
      if (!geojson.features || geojson.features.length === 0) {
        map.setView([0, 0], 2);
        btnLogTrip.disabled = false;
        setLoadingStatus(null);
        return;
      }

      streetsLayer = L.geoJSON(geojson, {
        style: STYLE_DEFAULT,
        onEachFeature: (feature, layer) => {
          const id = feature.id;
          const name = feature.properties.name || 'Unnamed street';

          layerById.set(id, layer);
          layerToId.set(layer, id);
          nameById.set(id, name);

          layer.on('click', () => {
            if (!selectionMode) return;
            if (spacebarPanning) return;
            if (selectedIds.has(id)) {
              selectedIds.delete(id);
              // Remove from wherever it sits in the stack (may be inside a batch array)
              for (let i = selectionStack.length - 1; i >= 0; i--) {
                const entry = selectionStack[i];
                if (Array.isArray(entry)) {
                  const idx = entry.indexOf(id);
                  if (idx !== -1) {
                    entry.splice(idx, 1);
                    if (entry.length === 0) selectionStack.splice(i, 1);
                    break;
                  }
                } else if (entry === id) {
                  selectionStack.splice(i, 1);
                  break;
                }
              }
              layer.setStyle(STYLE_DEFAULT);
            } else {
              selectedIds.add(id);
              selectionStack.push(id);
              layer.setStyle(STYLE_SELECTED);
            }
            updateSelectionCount();
            updateUndoButton();
          });

          layer.on('mouseover', () => {
            if (!selectionMode || !isPointerDown || selectedIds.has(id) || spacebarPanning) return;
            selectedIds.add(id);
            selectionStack.push(id);
            layer.setStyle(STYLE_SELECTED);
            updateSelectionCount();
            updateUndoButton();
          });
        },
      }).addTo(map);

      // Disable pointer events on streets until selection mode is active
      setStreetsInteractive(false);

      // Initialize lasso if plugin is loaded
      if (typeof L.lasso === 'function') {
        lasso = L.lasso(map, { intersect: true });
        map.on('lasso.finished', event => {
          const batch = [];
          event.layers.forEach(layer => {
            const id = layerToId.get(layer);
            if (id !== undefined && !selectedIds.has(id)) {
              selectedIds.add(id);
              batch.push(id);
              layer.setStyle(STYLE_SELECTED);
            }
          });
          if (batch.length > 0) {
            selectionStack.push(batch);
            document.getElementById('lasso-warning').style.display = 'none';
          } else {
            document.getElementById('lasso-warning').style.display = 'block';
          }
          updateSelectionCount();
          updateUndoButton();
          if (selectionMode) {
            setTimeout(() => {
              lasso.enable();
            }, 0);
          }
        });
      }

      // Fit map to streets bounds only if no server-provided bbox
      if (!window.BBOX) {
        map.fitBounds(streetsLayer.getBounds(), { padding: [20, 20] });
      }

      // Load coverage by default
      setLoadingStatus('Loading coverage… 0%');
      loadCoverage();
    })
    .catch(err => {
      console.error('Failed to load streets:', err);
      btnLogTrip.disabled = false;
      setLoadingStatus(null);
    });

  // ── Coverage layer ────────────────────────────────────────────────────────
  function loadCoverage() {
    fetchJSON(window.COVERAGE_URL, pct => { setLoadingStatus(`Loading coverage… ${pct}%`); })
      .then(geojson => {
        // Remove existing layers
        tripLayers.forEach(layer => map.removeLayer(layer));
        tripLayers.clear();
        tripVisible.clear();
        tripMeta.clear();
        if (summaryLayer) { map.removeLayer(summaryLayer); summaryLayer = null; }

        const allFeatures = geojson.features || [];

        // ── Build summary layer (deduplicated streets, single color) ──────
        const seenStreets = new Set();
        const summaryFeatures = [];
        allFeatures.forEach(f => {
          const streetPk = f.id.split('_')[1];
          if (!seenStreets.has(streetPk)) {
            seenStreets.add(streetPk);
            summaryFeatures.push(f);
          }
        });
        summaryLayer = L.geoJSON({ type: 'FeatureCollection', features: summaryFeatures }, {
          style: { color: '#ff6f00', weight: 5, opacity: 0.8 },
        });

        // ── Build per-trip detail layers ──────────────────────────────────
        const byTrip = new Map();
        allFeatures.forEach(f => {
          const tid = f.properties.trip_id;
          if (!byTrip.has(tid)) byTrip.set(tid, []);
          byTrip.get(tid).push(f);
        });

        // Assign colors that are both stable and unique: sort trip IDs so the
        // assignment is deterministic across page loads, then assign each trip
        // the least-used palette color in order — guaranteeing no two trips
        // share a color as long as there are fewer trips than palette entries.
        const sortedTripIds = Array.from(byTrip.keys()).sort();
        const paletteUsage = new Array(TRIP_PALETTE.length).fill(0);
        const tripColorMap = new Map();
        sortedTripIds.forEach(tid => {
          let minUse = Infinity, bestIdx = 0;
          for (let i = 0; i < TRIP_PALETTE.length; i++) {
            if (paletteUsage[i] < minUse) { minUse = paletteUsage[i]; bestIdx = i; }
          }
          tripColorMap.set(tid, TRIP_PALETTE[bestIdx]);
          paletteUsage[bestIdx]++;
        });

        byTrip.forEach((features, tid) => {
          const first = features[0].properties;
          const color = tripColorMap.get(tid);
          tripMeta.set(tid, {
            worker_name: first.worker_name,
            recorded_at: first.recorded_at,
            color,
          });
          const layer = L.geoJSON({ type: 'FeatureCollection', features }, {
            style: { color, weight: 5, opacity: 0.85 },
          });
          tripLayers.set(tid, layer);
          tripVisible.set(tid, true);
        });

        applyCoverageMode();
        renderTripLegend();
        btnLogTrip.disabled = false;
        setLoadingStatus(null);
      })
      .catch(err => {
        console.error('Failed to load coverage:', err);
        btnLogTrip.disabled = false;
        setLoadingStatus(null);
      });
  }

  function applyCoverageMode() {
    // Remove all coverage layers first
    if (summaryLayer) map.removeLayer(summaryLayer);
    tripLayers.forEach(layer => map.removeLayer(layer));

    if (coverageMode === 'summary') {
      if (summaryLayer) summaryLayer.addTo(map);
    } else if (coverageMode === 'detail') {
      tripLayers.forEach((layer, tid) => {
        if (tripVisible.get(tid)) layer.addTo(map);
      });
    }
    // 'hidden' — nothing added
  }

  function renderTripLegend() {
    const legendEl = document.getElementById('trip-legend');
    const itemsEl = document.getElementById('trip-legend-items');
    if (!itemsEl) return;

    itemsEl.innerHTML = '';

    if (tripMeta.size === 0) {
      legendEl.style.display = 'none';
      return;
    }

    tripMeta.forEach((meta, tid) => {
      const item = document.createElement('label');
      item.className = 'trip-legend-item';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.addEventListener('change', () => {
        const layer = tripLayers.get(tid);
        if (!layer) return;
        if (cb.checked) {
          tripVisible.set(tid, true);
          if (coverageMode === 'detail') layer.addTo(map);
        } else {
          tripVisible.set(tid, false);
          map.removeLayer(layer);
        }
      });

      const swatch = document.createElement('span');
      swatch.className = 'trip-legend-swatch';
      swatch.style.background = meta.color;

      const labelText = document.createElement('span');
      labelText.className = 'trip-legend-label';
      labelText.textContent = meta.worker_name || 'Anonymous';

      const metaSpan = document.createElement('span');
      metaSpan.className = 'trip-legend-meta';
      const d = new Date(meta.recorded_at);
      const day = String(d.getDate()).padStart(2, '0');
      const mon = d.toLocaleString(undefined, { month: 'short' });
      const yr = String(d.getFullYear());
      const time = d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit', hour12: true });
      metaSpan.textContent = `${day}-${mon}-${yr} ${time}`;

      item.appendChild(cb);
      item.appendChild(swatch);
      item.appendChild(labelText);
      item.appendChild(metaSpan);
      itemsEl.appendChild(item);
    });

    legendEl.style.display = coverageMode === 'detail' ? 'block' : 'none';
  }

  function setStreetsInteractive(active) {
    if (!streetsLayer) return;
    streetsLayer.eachLayer(layer => {
      if (layer._path) layer._path.style.pointerEvents = active ? '' : 'none';
    });
  }

  // ── UI helpers ────────────────────────────────────────────────────────────
  function updateSelectionCount() {
    const el = document.getElementById('selection-count');
    if (selectedIds.size > 0) {
      el.textContent = `● ${selectedIds.size} block${selectedIds.size === 1 ? '' : 's'}`;
      el.style.display = 'inline-flex';
    } else {
      el.textContent = '';
      el.style.display = 'none';
    }
    updateDebugPanel();
  }

  function updateUndoButton() {
    const btn = document.getElementById('btn-undo');
    if (selectionMode) {
      btn.style.display = '';
      btn.disabled = selectionStack.length === 0;
    } else {
      btn.style.display = 'none';
    }
  }

  function setSelectionMode(active) {
    selectionMode = active;
    document.getElementById('btn-log-trip').style.display = active ? 'none' : '';
    document.getElementById('btn-done').style.display = active ? '' : 'none';
    document.getElementById('btn-cancel').style.display = active ? '' : 'none';
    document.getElementById('trip-form').style.display = active ? 'block' : 'none';
    if (streetsLayer) {
      map.getContainer().style.cursor = active ? 'crosshair' : '';
      setStreetsInteractive(active);
      streetsLayer.eachLayer(layer => {
        if (active) {
          const id = layerToId.get(layer);
          layer.bindTooltip(nameById.get(id) || 'Unnamed street', { sticky: true });
        } else {
          layer.unbindTooltip();
        }
      });
    }
    if (active) {
      map.dragging.disable();
    } else {
      map.dragging.enable();
      if (lasso) {
        lasso.disable();
      }
    }
    document.getElementById('drawing-instructions').style.display = active ? '' : 'none';
    if (!active) document.getElementById('lasso-warning').style.display = 'none';
    const mobileInstructions = document.querySelector('.mobile-map-instructions');
    if (mobileInstructions) mobileInstructions.style.display = active ? 'none' : '';
    updateUndoButton();
  }

  function resetSelection() {
    selectedIds.clear();
    selectionStack.length = 0;
    updateSelectionCount();
    updateUndoButton();
    if (streetsLayer) {
      streetsLayer.setStyle(STYLE_DEFAULT);
    }
  }

  function updateDebugPanel() {
    if (!DEBUG_MODE) return;
    const panel = document.getElementById('debug-panel');
    if (!panel) return;
    if (selectedIds.size === 0) {
      panel.innerHTML = '<em style="color:#888;">No segments selected.</em>';
      return;
    }
    const rows = Array.from(selectedIds).map(id => {
      const name = nameById.get(id) || 'Unnamed';
      return `<tr><td style="padding:2px 8px 2px 0; font-family:monospace; font-size:0.8rem;">${id}</td><td style="font-size:0.85rem;">${name}</td></tr>`;
    });
    panel.innerHTML = `<table>${rows.join('')}</table>`;
  }

  function showStatus(msg, type) {
    const el = document.getElementById('status-message');
    el.textContent = msg;
    el.className = type;
    el.style.display = 'block';
  }

  // ── Buttons ───────────────────────────────────────────────────────────────
  document.getElementById('btn-log-trip').addEventListener('click', () => {
    const ZOOM_THRESHOLD = 15;
    const TARGET_ZOOM = 16;

    function activateSelectionMode() {
      setSelectionMode(true);
      updateSelectionCount();
      if (DEBUG_MODE) {
        document.getElementById('debug-section').style.display = 'block';
        updateDebugPanel();
      }
      if (lasso) {
        lasso.enable();
      }
      document.getElementById('map').scrollIntoView({ behavior: 'smooth' });
    }

    if (map.getZoom() < ZOOM_THRESHOLD) {
      showStatus('Zooming in to your street — then draw a loop to select streets.', 'info');
      map.once('zoomend', () => {
        document.getElementById('status-message').style.display = 'none';
        activateSelectionMode();
      });
      map.flyTo(map.getCenter(), TARGET_ZOOM);
    } else {
      activateSelectionMode();
    }
  });

  document.getElementById('btn-cancel').addEventListener('click', () => {
    setSelectionMode(false);
    resetSelection();
    document.getElementById('worker-name').value = '';
    document.getElementById('notes').value = '';
    document.getElementById('status-message').style.display = 'none';
    document.getElementById('debug-section').style.display = 'none';
  });

  document.getElementById('btn-done').addEventListener('click', () => {
    if (selectedIds.size === 0) {
      alert('Please tap at least one street segment first.');
      return;
    }
    const workerName = document.getElementById('worker-name').value.trim();
    const notes = document.getElementById('notes').value.trim();
    const segmentIds = Array.from(selectedIds);

    document.getElementById('btn-done').disabled = true;

    fetch(window.TRIP_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ segment_ids: segmentIds, worker_name: workerName, notes }),
    })
      .then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t); });
        return r.json();
      })
      .then(data => {
        setSelectionMode(false);
        document.getElementById('debug-section').style.display = 'none';
        document.getElementById('worker-name').value = '';
        document.getElementById('notes').value = '';
        document.getElementById('btn-done').disabled = false;
        resetSelection();
        if (coverageMode !== 'hidden') loadCoverage();

        const tripId = data.trip_id;
        window._lastTripId = tripId;
        window._lastTripGetUrl = window.TRIP_URL + tripId + '/';
        window._lastTripEditUrl = window.TRIP_URL + tripId + '/edit/';

        const statusEl = document.getElementById('status-message');
        statusEl.innerHTML = 'Trip logged! Thank you. <a href="#" id="edit-trip-link" style="color:inherit;font-weight:600;">Edit this trip</a>';
        statusEl.className = 'success';
        statusEl.style.display = 'block';
        document.getElementById('edit-trip-link').addEventListener('click', e => {
          e.preventDefault();
          openEditTripPanel();
        });
        statusEl.scrollIntoView({ behavior: 'smooth' });
      })
      .catch(err => {
        showStatus('Error submitting trip: ' + err.message, 'error');
        document.getElementById('btn-done').disabled = false;
      });
  });

  document.getElementById('btn-undo').addEventListener('click', () => {
    if (selectionStack.length === 0) return;
    const entry = selectionStack.pop();
    const ids = Array.isArray(entry) ? entry : [entry];
    ids.forEach(id => {
      selectedIds.delete(id);
      const layer = layerById.get(id);
      if (layer) layer.setStyle(STYLE_DEFAULT);
    });
    updateSelectionCount();
    updateUndoButton();
  });

  document.getElementById('coverage-mode').addEventListener('change', function () {
    coverageMode = this.value;
    applyCoverageMode();
    const legendEl = document.getElementById('trip-legend');
    if (legendEl) legendEl.style.display = coverageMode === 'detail' && tripMeta.size > 0 ? 'block' : 'none';
  });

  // ── Edit last trip (session-scoped) ───────────────────────────────────────

  function openEditTripPanel() {
    fetch(window._lastTripGetUrl)
      .then(r => {
        if (!r.ok) throw new Error('Session expired — this trip can no longer be edited.');
        return r.json();
      })
      .then(data => {
        document.getElementById('edit-worker-name').value = data.worker_name || '';
        document.getElementById('edit-notes').value = data.notes || '';
        const panel = document.getElementById('edit-trip-panel');
        panel.style.display = 'block';
        panel.scrollIntoView({ behavior: 'smooth' });
      })
      .catch(err => showStatus(err.message, 'error'));
  }

  document.getElementById('btn-save-edit').addEventListener('click', () => {
    const workerName = document.getElementById('edit-worker-name').value.trim();
    const notes = document.getElementById('edit-notes').value.trim();
    const btn = document.getElementById('btn-save-edit');
    btn.disabled = true;
    fetch(window._lastTripEditUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ worker_name: workerName, notes }),
    })
      .then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t); });
        return r.json();
      })
      .then(() => {
        document.getElementById('edit-trip-panel').style.display = 'none';
        showStatus('Trip updated.', 'success');
        btn.disabled = false;
        if (coverageMode !== 'hidden') loadCoverage();
      })
      .catch(err => {
        const el = document.getElementById('edit-trip-status');
        el.textContent = 'Error: ' + err.message;
        el.style.display = 'block';
        btn.disabled = false;
      });
  });

  document.getElementById('btn-cancel-edit').addEventListener('click', () => {
    document.getElementById('edit-trip-panel').style.display = 'none';
  });


})();
