export const STUDIO_URL = 'http://127.0.0.1:7476';
export const API_BASE_URL = `${STUDIO_URL}/api/v1`;

export async function getConfig() {
  return { STUDIO_URL, API_BASE_URL };
}
