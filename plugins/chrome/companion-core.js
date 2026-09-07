import { MAX_SELECTION_CHARACTERS, STUDIO_URL } from './config.js';

export const MENU_ID = 'quantech-vid-selected-text';
export const MESSAGE_TYPES = Object.freeze({
  status: 'companion_status', clear: 'clear_selected_text', open: 'open_studio',
});

export function boundedSelection(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > MAX_SELECTION_CHARACTERS) return null;
  return value;
}

export function publicSelection(value) {
  return value === null ? { selectedText: null, characterCount: 0 }
    : { selectedText: value, characterCount: value.length };
}

export function isKnownMessage(message) {
  return Boolean(message && typeof message === 'object' && !Array.isArray(message)
    && Object.keys(message).length === 1 && Object.values(MESSAGE_TYPES).includes(message.type));
}

export function renderSelectedText(target, value) { target.textContent = value; }
export function makeTextBlob(value) { return new Blob([value], { type: 'text/plain;charset=utf-8' }); }
export function studioTabProperties() { return { url: STUDIO_URL, active: true }; }
