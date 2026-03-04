const jobId = window.__JOB_ID__;

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

async function fetchResult() {
  const res = await fetch(`/api/jobs/${jobId}/result`);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to load result');
  }
  return data;
}

async function pollStatus() {
  const res = await fetch(`/api/jobs/${jobId}`);
  const data = await res.json();

  elStatus.textContent = data.status;
  elProgress.textContent = data.progress;
  elMessage.textContent = data.message;

  if (data.status === 'done') {
    const result = await fetchResult();
    resultCard.style.display = 'block';

    gradeEl.textContent = result.quality_grade;
    renderReasons(result.reasons);
    renderMetrics(result.metrics);

    reportLink.href = `/api/jobs/${jobId}/report`;
    overlayLink.href = `/api/jobs/${jobId}/overlay_video`;
    yoloTraceLink.href = result.artifacts?.yolo_trace_csv || '#';
    yoloHeatmapLink.href = result.artifacts?.yolo_conf_heatmap || '#';
    jsonLink.href = result.artifacts?.result_json || '#';
    speedPlot.src = result.artifacts?.speed_plot || '';
    yoloProcessPlot.src = result.artifacts?.yolo_process_plot || '';
    yoloHeatmapPlot.src = result.artifacts?.yolo_conf_heatmap || '';
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
  const interval = 2000;
  while (true) {
    try {
      const stop = await pollStatus();
      if (stop) {
        break;
      }
    } catch (err) {
      elMessage.textContent = `Polling error: ${String(err)}`;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
})();
