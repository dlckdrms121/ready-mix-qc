const form = document.getElementById('uploadForm');
const fileInput = document.getElementById('videoFile');
const statusText = document.getElementById('statusText');
const submitBtn = document.getElementById('submitBtn');
const submitParallelBtn = document.getElementById('submitParallelBtn');
const canvas = document.getElementById('firstFrameCanvas');
const ctx = canvas.getContext('2d');
const apiBaseInput = document.getElementById('apiBaseInput');
const saveApiBaseBtn = document.getElementById('saveApiBaseBtn');
const resetApiBaseBtn = document.getElementById('resetApiBaseBtn');
const apiBaseText = document.getElementById('apiBaseText');
const healthText = document.getElementById('healthText');
const buildText = document.getElementById('buildText');
const UI_BUILD = '20260306-8';

function renderApiBase() {
  const base = SG.getApiBase();
  apiBaseInput.value = base;
  apiBaseText.textContent = `API Base: ${base || '(not set)'}`;
  if (buildText) {
    buildText.textContent = `UI build: ${UI_BUILD}`;
  }
}

async function checkHealth() {
  const base = SG.getApiBase();
  const validationError = SG.validateApiBase(base);
  if (validationError) {
    healthText.textContent = validationError;
    return;
  }

  healthText.textContent = 'Checking backend health...';
  try {
    const result = await SG.fetchJson('/api/health');
    if (!result.ok) {
      const detail = result.data?.detail || result.text || `health check failed (status ${result.status})`;
      throw new Error(String(detail));
    }
    if (!result.data || typeof result.data !== 'object') {
      throw new Error(result.text || 'Invalid non-JSON response from backend health API');
    }
    const data = result.data;
    healthText.textContent = `Backend health: ${data.status}`;
  } catch (err) {
    const healthUrl = SG.apiUrl('/api/health');
    healthText.textContent = `Backend health error: ${String(err)} (url: ${healthUrl})`;
  }
}

saveApiBaseBtn.addEventListener('click', async () => {
  const candidate = String(apiBaseInput.value || '').trim();
  const validationError = SG.validateApiBase(candidate);
  if (validationError) {
    healthText.textContent = validationError;
    return;
  }
  SG.setApiBase(candidate);
  renderApiBase();
  await checkHealth();
});

if (resetApiBaseBtn) {
  resetApiBaseBtn.addEventListener('click', async () => {
    SG.clearApiBase();
    apiBaseInput.value = '';
    renderApiBase();
    await checkHealth();
  });
}

async function renderFirstFrame(file) {
  const url = URL.createObjectURL(file);
  const video = document.createElement('video');
  video.src = url;
  video.muted = true;
  video.playsInline = true;

  await new Promise((resolve, reject) => {
    video.onloadeddata = () => resolve();
    video.onerror = () => reject(new Error('Unable to read video file'));
  });

  const w = video.videoWidth || 640;
  const h = video.videoHeight || 360;
  canvas.width = w;
  canvas.height = h;
  ctx.drawImage(video, 0, 0, w, h);

  URL.revokeObjectURL(url);
}

fileInput.addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  try {
    await renderFirstFrame(file);
  } catch (err) {
    statusText.textContent = String(err);
  }
});

function setButtonsDisabled(disabled) {
  submitBtn.disabled = disabled;
  if (submitParallelBtn) {
    submitParallelBtn.disabled = disabled;
  }
}

function ensureValidVideoFile(formData) {
  const file = formData.get('file');
  if (!(file instanceof File)) {
    statusText.textContent = 'Choose an mp4 file.';
    return false;
  }
  return true;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  const validationError = SG.validateApiBase();
  if (validationError) {
    statusText.textContent = validationError;
    return;
  }

  const formData = new FormData(form);
  if (!ensureValidVideoFile(formData)) {
    return;
  }

  setButtonsDisabled(true);
  statusText.textContent = 'Uploading and creating job...';

  try {
    const result = await SG.fetchJson('/api/jobs', {
      method: 'POST',
      body: formData,
    });
    if (!result.ok) {
      const detail = result.data?.detail || result.text || `Job creation failed (status ${result.status})`;
      throw new Error(String(detail));
    }
    const data = result.data || {};

    window.location.href = `./job.html?job_id=${encodeURIComponent(data.job_id)}`;
  } catch (err) {
    statusText.textContent = `Error: ${String(err)}`;
    setButtonsDisabled(false);
  }
});

if (submitParallelBtn) {
  submitParallelBtn.addEventListener('click', async () => {
    const validationError = SG.validateApiBase();
    if (validationError) {
      statusText.textContent = validationError;
      return;
    }

    const formData = new FormData(form);
    if (!ensureValidVideoFile(formData)) {
      return;
    }

    setButtonsDisabled(true);
    statusText.textContent = 'Uploading and starting batch + realtime...';

    try {
      const result = await SG.fetchJson('/api/analysis/parallel', {
        method: 'POST',
        body: formData,
      });
      if (!result.ok) {
        const detail = result.data?.detail || result.text || `Parallel analysis creation failed (status ${result.status})`;
        throw new Error(String(detail));
      }
      const data = result.data || {};
      const batchUrl = `./job.html?job_id=${encodeURIComponent(data.job_id)}`;
      const realtimeUrl = `./realtime_session.html?session_id=${encodeURIComponent(data.session_id)}`;
      statusText.innerHTML = `Started both analyses. <a href="${batchUrl}">Open Batch</a> | <a href="${realtimeUrl}">Open Realtime</a>`;
      setButtonsDisabled(false);
    } catch (err) {
      statusText.textContent = `Error: ${String(err)}`;
      setButtonsDisabled(false);
    }
  });
}

renderApiBase();
checkHealth();
