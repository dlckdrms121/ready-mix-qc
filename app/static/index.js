const form = document.getElementById('uploadForm');
const fileInput = document.getElementById('videoFile');
const statusText = document.getElementById('statusText');
const submitBtn = document.getElementById('submitBtn');
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

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  const formData = new FormData(form);
  const file = formData.get('file');
  if (!(file instanceof File)) {
    statusText.textContent = 'Choose an mp4 file.';
    return;
  }

  submitBtn.disabled = true;
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
    submitBtn.disabled = false;
  }
});
