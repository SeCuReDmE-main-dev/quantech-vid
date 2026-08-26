const status = document.getElementById('status');
const capture = document.getElementById('capture');
const result = document.getElementById('result');

chrome.runtime.sendMessage({ type: 'studio_health' }, (response) => {
  const online = Boolean(response?.ok);
  status.textContent = online ? 'Studio local actif sur 127.0.0.1:7476' : 'Demarrez le studio local pour capturer.';
  capture.disabled = !online;
});

capture.addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.runtime.sendMessage({ type: 'capture_page', url: tab.url }, (response) => {
    result.hidden = false;
    result.textContent = response?.ok ? `Capture enregistree: ${response.capture.screenshot}` : response?.error || 'Capture impossible';
  });
});

document.getElementById('open').addEventListener('click', () => chrome.runtime.sendMessage({ type: 'open_studio' }));
