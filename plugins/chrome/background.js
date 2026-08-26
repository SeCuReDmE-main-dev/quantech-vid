import { API_BASE_URL, STUDIO_URL } from './config.js';

const MENU_ID = 'quantech-vid-capture';

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_ID, title: 'Envoyer la page a QuaNTecH-ViD', contexts: ['page'] });
  });
});

async function capturePage(url) {
  const response = await fetch(`${API_BASE_URL}/captures`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, width: 1440, height: 900, full_page: true, record_seconds: 0 }),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || 'Capture impossible');
  await chrome.storage.local.set({ lastCapture: { ...body, sourceUrl: url, createdAt: new Date().toISOString() } });
  return body;
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === MENU_ID && tab?.url) void capturePage(tab.url);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'studio_health') {
    fetch(`${API_BASE_URL}/health`).then((response) => sendResponse({ ok: response.ok })).catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (message.type === 'capture_page') {
    capturePage(message.url).then((capture) => sendResponse({ ok: true, capture })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === 'open_studio') {
    chrome.tabs.create({ url: STUDIO_URL });
    sendResponse({ ok: true });
    return false;
  }
  return false;
});
