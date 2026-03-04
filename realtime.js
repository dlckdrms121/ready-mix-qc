const form = document.getElementById('rtUploadForm');
const fileInput = document.getElementById('rtVideoFile');
const localVideo = document.getElementById('rtLocalVideo');
const statusText = document.getElementById('rtStatusText');
const submitBtn = document.getElementById('rtSubmitBtn');

fileInput.addEventListener('change', () => {
  const file = fileInput.files?.[0];
  if (!file) {
    return;
  }
  const url = URL.createObjectURL(file);
  localVideo.src = url;
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
  statusText.textContent = 'Uploading and starting realtime session...';

  try {
    const res = await fetch('/api/realtime/sessions', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Realtime session creation failed');
    }
    window.location.href = data.viewer_url || `/realtime/${data.session_id}`;
  } catch (err) {
    statusText.textContent = `Error: ${String(err)}`;
    submitBtn.disabled = false;
  }
});
