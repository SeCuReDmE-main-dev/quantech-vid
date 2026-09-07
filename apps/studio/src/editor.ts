import { z } from 'zod';
import { documentSchema, type ProjectDocument, type SourceAsset } from './contracts';

export const DRAFT_KEY = 'quantech.studio.local-draft.v2';
export type Draft = { projectId: string | null; baseRevision: number | null; document: ProjectDocument; savedAt: string };
const draftSchema = z.object({ projectId: z.string().nullable(), baseRevision: z.number().int().positive().nullable(),
  document: documentSchema, savedAt: z.string() });
export function initialProject(source: SourceAsset): ProjectDocument {
  return { schema_version: '2.0', slug: `local-film-${crypto.randomUUID().slice(0, 8)}`,
    title: 'My first verified film', disclosure: 'Synthetic local demonstration. Not a claim about a person or a learning outcome.',
    sources: [source.id], scenes: [{ id: 'scene-1', duration: 4, source_asset_id: source.id,
      title: { en: 'From an idea to a visible result' }, body: { en: 'A small, human-approved local production.' }, fit: 'contain' }],
    tracks: [{ locale: 'en', title: 'English', narration: 'A small, human-approved local production.' }],
    output_profiles: [{ name: 'landscape', width: 1280, height: 720, fps: 30 },
      { name: 'portrait', width: 720, height: 1280, fps: 30 }],
  };
}
export function restoreDraft(storage: Storage): Draft | null {
  try {
    const raw = storage.getItem(DRAFT_KEY);
    if (!raw || raw.length > 250_000) return null;
    const parsed = draftSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch { return null; }
}
export function persistDraft(storage: Storage, draft: Draft): boolean {
  try { storage.setItem(DRAFT_KEY, JSON.stringify(draftSchema.parse(draft))); return true; } catch { return false; }
}
export type History = { past: ProjectDocument[]; present: ProjectDocument; future: ProjectDocument[] };
export function edit(history: History, next: ProjectDocument): History {
  const validated = documentSchema.parse(next);
  if (JSON.stringify(history.present) === JSON.stringify(validated)) return history;
  return { past: [...history.past.slice(-49), history.present], present: validated, future: [] };
}
export function undo(history: History): History {
  if (!history.past.length) return history;
  return { past: history.past.slice(0, -1), present: history.past.at(-1)!, future: [history.present, ...history.future] };
}
export function redo(history: History): History {
  if (!history.future.length) return history;
  return { past: [...history.past, history.present], present: history.future[0], future: history.future.slice(1) };
}
export function moveScene(document: ProjectDocument, from: number, delta: number): ProjectDocument {
  const to = from + delta;
  if (to < 0 || to >= document.scenes.length) return document;
  const scenes = [...document.scenes];
  [scenes[from], scenes[to]] = [scenes[to], scenes[from]];
  return { ...document, scenes };
}
export function timeline(document: ProjectDocument) {
  let start = 0;
  return document.scenes.map(scene => { const item = { ...scene, start, end: start + scene.duration }; start = item.end; return item; });
}
export function verifiedCompletion(job: { status: string; receipts: { role: string; size: number }[] }): boolean {
  return job.status === 'complete' && ['video-mp4', 'video-webm', 'quality-report', 'provenance'].every(
    role => job.receipts.some(r => r.size > 0 && r.role === role));
}
