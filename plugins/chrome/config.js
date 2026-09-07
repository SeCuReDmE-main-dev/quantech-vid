export const STUDIO_URL = 'http://127.0.0.1:7476/';
export const MAX_SELECTION_CHARACTERS = 200_000;
export const SELECTION_FILENAME = 'quantech-selected-text.txt';

export function getConfig() {
  return { STUDIO_URL, MAX_SELECTION_CHARACTERS, SELECTION_FILENAME };
}
