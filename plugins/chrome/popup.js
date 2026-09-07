import { makeTextBlob, MESSAGE_TYPES, renderSelectedText } from './companion-core.js';
import { browserAPI } from './browser-api.js';
import { SELECTION_FILENAME } from './config.js';

const api = browserAPI();
const status = document.getElementById('status');
const selection = document.getElementById('selection');
const count = document.getElementById('count');
const selectedText = document.getElementById('selected-text');
const save = document.getElementById('save');
let currentText = null;

function send(message) {
  return new Promise(resolve => {
    api.runtime.sendMessage(message, response => {
      void api.runtime.lastError;
      resolve(response);
    });
  });
}

function render(response) {
  currentText = typeof response?.selectedText === 'string' ? response.selectedText : null;
  selection.hidden = currentText === null;
  status.textContent = currentText === null ? 'No selected text is held.' : 'Selected text is held only in ephemeral extension memory.';
  count.textContent = currentText === null ? '' : `${response.characterCount} characters`;
  renderSelectedText(selectedText, currentText ?? '');
  save.disabled = currentText === null;
}

async function refresh() { render(await send({ type: MESSAGE_TYPES.status })); }

save.addEventListener('click', () => {
  if (currentText === null) return;
  const url = URL.createObjectURL(makeTextBlob(currentText));
  const link = document.createElement('a');
  link.href = url;
  link.download = SELECTION_FILENAME;
  link.rel = 'noopener';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
});
document.getElementById('clear').addEventListener('click', async () => render(await send({ type: MESSAGE_TYPES.clear })));
document.getElementById('open').addEventListener('click', async () => {
  const result = await send({ type: MESSAGE_TYPES.open });
  status.textContent = result?.ok ? 'Studio tab opened. Server availability and pairing are checked there.'
    : 'The browser did not confirm a new studio tab. Open http://127.0.0.1:7476/ yourself; no import was started.';
});
api.runtime.onMessage.addListener(message => {
  if (message?.type === 'selection_changed') void refresh();
  return false;
});
void refresh();
