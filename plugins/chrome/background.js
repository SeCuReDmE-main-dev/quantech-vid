import { boundedSelection, isKnownMessage, MENU_ID, MESSAGE_TYPES, publicSelection,
  studioTabProperties } from './companion-core.js';
import { browserAPI } from './browser-api.js';

function ignoreRejectedPromise(value) {
  if (value && typeof value.catch === 'function') value.catch(() => undefined);
}

function notifySelectionChanged(api) {
  try { ignoreRejectedPromise(api.runtime.sendMessage({ type: 'selection_changed' })); }
  catch { /* No companion view is currently open. */ }
}

export function installBackground(api) {
  let selectedText = null;

  api.runtime.onInstalled.addListener(() => {
    const removed = api.contextMenus.removeAll(() => {
      void api.runtime.lastError;
      api.contextMenus.create({
        id: MENU_ID,
        title: 'Prepare selected text for QuaNTecH-ViD',
        contexts: ['selection'],
      });
    });
    ignoreRejectedPromise(removed);
  });

  api.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId !== MENU_ID) return;
    selectedText = boundedSelection(info.selectionText);
    try {
      if (api.sidePanel?.open && Number.isInteger(tab?.windowId)) {
        ignoreRejectedPromise(api.sidePanel.open({ windowId: tab.windowId }));
      } else if (api.sidebarAction?.open) {
        ignoreRejectedPromise(api.sidebarAction.open());
      }
    } catch { /* The toolbar popup remains an explicit fallback. */ }
    notifySelectionChanged(api);
  });

  api.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender?.id !== api.runtime.id) return false;
    if (!isKnownMessage(message)) return false;
    if (message.type === MESSAGE_TYPES.status) {
      sendResponse({ ok: true, ...publicSelection(selectedText), engines: 'not_connected', toolCount: 8 });
      return false;
    }
    if (message.type === MESSAGE_TYPES.clear) {
      selectedText = null;
      sendResponse({ ok: true, ...publicSelection(selectedText) });
      notifySelectionChanged(api);
      return false;
    }
    if (message.type === MESSAGE_TYPES.open) {
      api.tabs.create(studioTabProperties(), openedTab => {
        const error = api.runtime.lastError;
        sendResponse(error || !Number.isInteger(openedTab?.id) ? { ok: false } : { ok: true });
      });
      return true;
    }
    return false;
  });

  return { snapshot: () => selectedText };
}

if (globalThis.chrome ?? globalThis.browser) installBackground(browserAPI());
