const form = document.getElementById('uploadForm');
const fileInput = document.getElementById('videoFile');
const statusText = document.getElementById('statusText');
const submitBtn = document.getElementById('submitBtn');
const submitParallelBtn = document.getElementById('submitParallelBtn');
const canvas = document.getElementById('firstFrameCanvas');
const ctx = canvas.getContext('2d');

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
  if (!file) {
    return;
  }
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

  const formData = new FormData(form);
  if (!ensureValidVideoFile(formData)) {
    return;
  }

  setButtonsDisabled(true);
  statusText.textContent = 'Uploading and creating job...';

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      body: formData,
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Job creation failed');
    }

    window.location.href = `/jobs/${data.job_id}`;
  } catch (err) {
    statusText.textContent = `Error: ${String(err)}`;
    setButtonsDisabled(false);
  }
});

if (submitParallelBtn) {
  submitParallelBtn.addEventListener('click', async () => {
    const formData = new FormData(form);
    if (!ensureValidVideoFile(formData)) {
      return;
    }

    setButtonsDisabled(true);
    statusText.textContent = 'Uploading and starting batch + realtime...';

    try {
      const res = await fetch('/api/analysis/parallel', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Parallel analysis creation failed');
      }

      const batchUrl = `/jobs/${encodeURIComponent(data.job_id)}`;
      const realtimeUrl = `/realtime/${encodeURIComponent(data.session_id)}`;
      statusText.innerHTML = `Started both analyses. <a href="${batchUrl}">Open Batch</a> | <a href="${realtimeUrl}">Open Realtime</a>`;
      setButtonsDisabled(false);
    } catch (err) {
      statusText.textContent = `Error: ${String(err)}`;
      setButtonsDisabled(false);
    }
  });
}
