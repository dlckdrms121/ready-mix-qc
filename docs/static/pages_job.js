const jobId = SG.queryParam('job_id') || '';

document.getElementById('jobIdText').textContent = jobId || '(missing)';

const elStatus = document.getElementById('status');
const elProgress = document.getElementById('progress');
const elMessage = document.getElementById('message');
const resultCard = document.getElementById('resultCard');
const gradeEl = document.getElementById('grade');
const reasonsEl = document.getElementById('reasons');
const metricsTable = document.getElementById('metricsTable');
const reportLink = document.getElementById('reportLink');
const overlayLink = document.getElementById('overlayLink');
const yoloTraceLink = document.getElementById('yoloTraceLink');
const yoloHeatmapLink = document.getElementById('yoloHeatmapLink');
const jsonLink = document.getElementById('jsonLink');
const speedPlot = document.getElementById('speedPlot');
const yoloProcessPlot = document.getElementById('yoloProcessPlot');
const yoloHeatmapPlot = document.getElementById('yoloHeatmapPlot');
const debugJson = document.getElementById('debugJson');

function renderMetrics(metrics) {
  metricsTable.innerHTML = '';
  const head = document.createElement('tr');
  head.innerHTML = '<th>Metric</th><th>Value</th>';
  metricsTable.appendChild(head);

  Object.entries(metrics || {}).forEach(([k, v]) => {
    const tr = document.createElement('tr');
    const val = typeof v === 'number' ? v.toFixed(6) : String(v);
    tr.innerHTML = `<td>${k}</td><td>${val}</td>`;
    metricsTable.appendChild(tr);
  });
}

function renderReasons(reasons) {
  reasonsEl.innerHTML = '';
  (reasons || []).forEach((r) => {
    const li = document.createElement('li');
    li.textContent = r;
    reasonsEl.appendChild(li);
  });
}

function artifactUrl(pathOrUrl) {
  return SG.apiUrl(pathOrUrl || '#');
}

async function fetchResult() {
  const result = await SG.fetchJson(`/api/jobs/${jobId}/result`);
  if (!result.ok) {
    const detail = result.data?.detail || result.text || `Failed to load result (status ${result.status})`;
    throw new Error(String(detail));
  }
  if (!result.data || typeof result.data !== 'object') {
    throw new Error(result.text || 'Invalid non-JSON response from result API');
  }
  return result.data;
}

async function pollStatus() {
  const result = await SG.fetchJson(`/api/jobs/${jobId}`);
  if (!result.ok) {
    const detail = result.data?.detail || result.text || `Failed to load status (status ${result.status})`;
    throw new Error(String(detail));
  }
  if (!result.data || typeof result.data !== 'object') {
    throw new Error(result.text || 'Invalid non-JSON response from status API');
  }
  const data = result.data;

  elStatus.textContent = data.status;
  elProgress.textContent = data.progress;
  elMessage.textContent = data.message;

  if (data.status === 'done') {
    const result = await fetchResult();
    resultCard.style.display = 'block';

    gradeEl.textContent = result.quality_grade;
    renderReasons(result.reasons);
    renderMetrics(result.metrics);

    reportLink.href = SG.apiUrl(`/api/jobs/${jobId}/report`);
    overlayLink.href = SG.apiUrl(`/api/jobs/${jobId}/overlay_video`);
    yoloTraceLink.href = artifactUrl(result.artifacts?.yolo_trace_csv);
    yoloHeatmapLink.href = artifactUrl(result.artifacts?.yolo_conf_heatmap);
    jsonLink.href = artifactUrl(result.artifacts?.result_json);
    speedPlot.src = artifactUrl(result.artifacts?.speed_plot);
    yoloProcessPlot.src = artifactUrl(result.artifacts?.yolo_process_plot);
    yoloHeatmapPlot.src = artifactUrl(result.artifacts?.yolo_conf_heatmap);
    debugJson.textContent = JSON.stringify(result, null, 2);
    return true;
  }

  if (data.status === 'failed') {
    resultCard.style.display = 'block';
    gradeEl.textContent = 'FAILED';
    reasonsEl.innerHTML = `<li>${data.message}</li>`;
    debugJson.textContent = JSON.stringify(data, null, 2);
    return true;
  }

  return false;
}

(async () => {
  if (!jobId) {
    elMessage.textContent = 'Missing job_id in query string.';
    return;
  }

  const validationError = SG.validateApiBase();
  if (validationError) {
    elMessage.textContent = validationError;
    return;
  }

  const interval = 2000;
  while (true) {
    try {
      const stop = await pollStatus();
      if (stop) break;
    } catch (err) {
      elMessage.textContent = `Polling error: ${String(err)}`;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
})();
