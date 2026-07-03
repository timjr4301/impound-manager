// Shared webcam device persistence — remembers which camera (e.g. the IPEVO V4K
// document camera) was last selected so office staff don't have to re-pick it
// every time a browser camera prompt resets on a machine with multiple cameras.
const BJ_CAMERA_STORAGE_KEY = 'bjCameraDeviceId';

async function bjPopulateCameraSelect(selectEl) {
  if (!selectEl || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const videoInputs = devices.filter(d => d.kind === 'videoinput');
    if (videoInputs.length <= 1) {
      selectEl.style.display = 'none';
      return;
    }
    const saved = localStorage.getItem(BJ_CAMERA_STORAGE_KEY);
    selectEl.innerHTML = videoInputs.map((d, i) =>
      `<option value="${d.deviceId}">${d.label || ('Camera ' + (i + 1))}</option>`
    ).join('');
    if (saved && videoInputs.some(d => d.deviceId === saved)) {
      selectEl.value = saved;
    } else {
      localStorage.setItem(BJ_CAMERA_STORAGE_KEY, selectEl.value);
    }
    selectEl.style.display = 'inline-block';
  } catch (e) {
    // enumerateDevices unsupported or blocked — fall back to default camera silently
  }
}

function bjOnCameraDeviceChange(selectEl) {
  if (!selectEl || !selectEl.value) return;
  localStorage.setItem(BJ_CAMERA_STORAGE_KEY, selectEl.value);
}

function bjPreferredVideoConstraints(selectEl, extra) {
  const constraints = Object.assign({}, extra);
  const preferred = (selectEl && selectEl.value) || localStorage.getItem(BJ_CAMERA_STORAGE_KEY);
  if (preferred) {
    constraints.deviceId = { exact: preferred };
  } else {
    constraints.facingMode = 'environment';
  }
  return constraints;
}
