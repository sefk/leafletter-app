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
  let streetsLayer = null;
  let coverageLayer = null;
  let coverageVisible = true;
  let lasso = null;
  let map = null;

  // Style helpers
  const STYLE_DEFAULT = { color: '#888', weight: 2, opacity: 0.7 };
  const STYLE_SELECTED = { color: '#1a6b3c', weight: 5, opacity: 1 };
  const STYLE_COVERAGE = { color: '#ff6f00', weight: 5, opacity: 0.8 };

  // ── Pointer tracking (for drag-to-select) ────────────────────────────────
  document.addEventListener('mousedown', () => { isPointerDown = true; });
  document.addEventListener('mouseup', () => { isPointerDown = false; });
  document.addEventListener('touchstart', () => { isPointerDown = true; });
  document.addEventListener('touchend', () => { isPointerDown = false; });

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

          layerById.set(id, layer);
          layerToId.set(layer, id);
          nameById.set(id, name);

          layer.on('click', () => {
            if (!selectionMode) return;
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
            if (!selectionMode || !isPointerDown || selectedIds.has(id)) return;
            selectedIds.add(id);
            selectionStack.push(id);
            layer.setStyle(STYLE_SELECTED);
            updateSelectionCount();
            updateUndoButton();
          });
        },
      }).addTo(map);

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
          if (batch.length > 0) selectionStack.push(batch);
          updateSelectionCount();
          updateUndoButton();
          if (selectionMode) {
            setTimeout(() => {
              lasso.enable();
              document.getElementById('btn-lasso').textContent = 'Drawing…';
            }, 0);
          }
        });
        // Show btn-lasso if already in selection mode (race condition guard)
        if (selectionMode) {
          document.getElementById('btn-lasso').style.display = '';
        }
      }

      // Fit map to streets bounds only if no server-provided bbox
      if (!window.BBOX) {
        map.fitBounds(streetsLayer.getBounds(), { padding: [20, 20] });
      }

      // Load coverage by default
      loadCoverage();
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
    document.getElementById('street-search-panel').style.display = active ? 'block' : 'none';
    if (!active) {
      document.getElementById('street-search-input').value = '';
      document.getElementById('street-search-results').style.display = 'none';
    }
    if (streetsLayer) {
      map.getContainer().style.cursor = active ? 'crosshair' : '';
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
        document.getElementById('btn-lasso').textContent = 'Select Area';
      }
    }
    if (lasso) {
      document.getElementById('btn-lasso').style.display = active ? '' : 'none';
    }
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

  // ── Street address search ─────────────────────────────────────────────────
  (function () {
    const searchInput = document.getElementById('street-search-input');
    const searchResults = document.getElementById('street-search-results');
    let searchTimer = null;

    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      const q = searchInput.value.trim();
      if (q.length < 2) {
        hideResults();
        return;
      }
      searchTimer = setTimeout(() => runSearch(q), 300);
    });

    searchInput.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        hideResults();
        searchInput.blur();
      }
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', e => {
      if (!document.getElementById('street-search-wrap').contains(e.target)) {
        hideResults();
      }
    });

    function hideResults() {
      searchResults.style.display = 'none';
      searchResults.innerHTML = '';
    }

    function runSearch(q) {
      const url = window.STREET_SEARCH_URL + '?q=' + encodeURIComponent(q);
      fetch(url)
        .then(r => r.json())
        .then(data => showResults(data.results || []))
        .catch(() => hideResults());
    }

    function showResults(results) {
      searchResults.innerHTML = '';
      if (results.length === 0) {
        searchResults.innerHTML = '<div class="sr-none">No matching streets found</div>';
        searchResults.style.display = 'block';
        return;
      }
      results.forEach(result => {
        const item = document.createElement('div');
        item.className = 'sr-item';
        if (result.subtitle) {
          item.innerHTML = `<span class="sr-name">${result.name}</span> <span class="sr-sub">${result.subtitle}</span>`;
        } else {
          item.textContent = result.name;
        }
        item.addEventListener('click', () => selectSearchResult(result));
        searchResults.appendChild(item);
      });
      searchResults.style.display = 'block';
    }

    function selectSearchResult(result) {
      hideResults();
      searchInput.value = '';

      const id = result.id;
      const layer = layerById.get(id);

      if (!layer) return;  // Street not loaded yet (shouldn't happen)

      // Add to selection (same as a click, skip if already selected)
      if (!selectedIds.has(id)) {
        selectedIds.add(id);
        selectionStack.push(id);
        layer.setStyle(STYLE_SELECTED);
        updateSelectionCount();
        updateUndoButton();
      }

      // Fit map to the selected block so user can confirm it's correct
      const bbox = result.bbox;  // [[sw_lat, sw_lon], [ne_lat, ne_lon]]
      map.fitBounds(bbox, { padding: [40, 40], maxZoom: 18, animate: true });
    }
  }());

  // ── Buttons ───────────────────────────────────────────────────────────────
  document.getElementById('btn-log-trip').addEventListener('click', () => {
    setSelectionMode(true);
    updateSelectionCount();
    if (DEBUG_MODE) {
      document.getElementById('debug-section').style.display = 'block';
      updateDebugPanel();
    }
    if (lasso) {
      lasso.enable();
      document.getElementById('btn-lasso').textContent = 'Drawing…';
    }
  });

  document.getElementById('btn-cancel').addEventListener('click', () => {
    setSelectionMode(false);
    resetSelection();
    document.getElementById('trip-form').style.display = 'none';
    document.getElementById('status-message').style.display = 'none';
    document.getElementById('debug-section').style.display = 'none';
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

  document.getElementById('btn-lasso').addEventListener('click', () => {
    if (lasso) {
      lasso.enable();
      document.getElementById('btn-lasso').textContent = 'Drawing…';
    }
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
        document.getElementById('debug-section').style.display = 'none';
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
