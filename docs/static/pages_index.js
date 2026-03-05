const form = document.getElementById('uploadForm');
const fileInput = document.getElementById('videoFile');
const statusText = document.getElementById('statusText');
const submitBtn = document.getElementById('submitBtn');
const canvas = document.getElementById('firstFrameCanvas');
const ctx = canvas.getContext('2d');
const apiBaseInput = document.getElementById('apiBaseInput');
const saveApiBaseBtn = document.getElementById('saveApiBaseBtn');
const apiBaseText = document.getElementById('apiBaseText');
const healthText = document.getElementById('healthText');

function renderApiBase() {
  const base = SG.getApiBase();
  apiBaseInput.value = base;
  apiBaseText.textContent = `API Base: ${base || '(not set)'}`;
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
    const res = await fetch(SG.apiUrl('/api/health'));
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'health check failed');
    healthText.textContent = `Backend health: ${data.status}`;
  } catch (err) {
    healthText.textContent = `Backend health error: ${String(err)}`;
  }
}

saveApiBaseBtn.addEventListener('click', async () => {
  SG.setApiBase(apiBaseInput.value);
  renderApiBase();
  await checkHealth();
});

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

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  const validationError = SG.validateApiBase();
  if (validationError) {
    statusText.textContent = validationError;
    return;
  }

  const formData = new FormData(form);
  const file = formData.get('file');
  if (!(file instanceof File)) {
    statusText.textContent = 'Choose an mp4 file.';
    return;
  }

  submitBtn.disabled = true;
  statusText.textContent = 'Uploading and creating job...';

  try {
    const res = await fetch(SG.apiUrl('/api/jobs'), {
      method: 'POST',
      body: formData,
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Job creation failed');
    }

    window.location.href = `./job.html?job_id=${encodeURIComponent(data.job_id)}`;
  } catch (err) {
    statusText.textContent = `Error: ${String(err)}`;
    submitBtn.disabled = false;
  }
});

renderApiBase();
checkHealth();
