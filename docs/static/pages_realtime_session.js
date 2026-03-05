const sessionId = SG.queryParam('session_id') || '';
document.getElementById('sessionIdText').textContent = sessionId || '(missing)';

const elStatus = document.getElementById('rtStatus');
const elProgress = document.getElementById('rtProgress');
const elMessage = document.getElementById('rtMessage');
const sourceVideo = document.getElementById('rtSourceVideo');
const liveFrame = document.getElementById('rtLiveFrame');
const statsTable = document.getElementById('rtStatsTable');
const overlayLink = document.getElementById('rtOverlayLink');
const traceLink = document.getElementById('rtTraceLink');

function renderStats(stats) {
  statsTable.innerHTML = '';
  const head = document.createElement('tr');
  head.innerHTML = '<th>Metric</th><th>Value</th>';
  statsTable.appendChild(head);

  Object.entries(stats || {}).forEach(([k, v]) => {
    const tr = document.createElement('tr');
    let text = '';
    if (typeof v === 'number') {
      text = Number.isInteger(v) ? String(v) : v.toFixed(4);
    } else {
      text = String(v);
    }
    tr.innerHTML = `<td>${k}</td><td>${text}</td>`;
    statsTable.appendChild(tr);
  });
}

async function pollRealtime() {
  const res = await fetch(SG.apiUrl(`/api/realtime/sessions/${sessionId}`));
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Failed to load realtime session');

  elStatus.textContent = data.status || '-';
  elProgress.textContent = data.progress ?? 0;
  elMessage.textContent = data.message || '';

  if (data.video_url && !sourceVideo.src) {
    sourceVideo.src = SG.apiUrl(data.video_url);
  }

  if (data.frame_jpeg_b64) {
    liveFrame.src = `data:image/jpeg;base64,${data.frame_jpeg_b64}`;
  }

  renderStats(data.stats || {});

  const artifacts = data.artifacts || {};
  overlayLink.href = SG.apiUrl(artifacts.overlay_video || '#');
  traceLink.href = SG.apiUrl(artifacts.trace_csv || '#');

  return data.status;
}

(async () => {
  if (!sessionId) {
    elMessage.textContent = 'Missing session_id in query string.';
    return;
  }

  const intervalMs = 300;
  while (true) {
    try {
      const status = await pollRealtime();
      if (status === 'done' || status === 'failed') break;
    } catch (err) {
      elMessage.textContent = `Polling error: ${String(err)}`;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
})();
