import { beforeEach, describe, expect, it } from 'vitest';
import { documentSchema } from '../src/contracts';
import { DRAFT_KEY, edit, initialProject, moveScene, persistDraft, redo, restoreDraft, timeline, undo, verifiedCompletion } from '../src/editor';
import { source } from './fixtures';

beforeEach(() => localStorage.clear());
describe('local project history', () => {
  it('restores content without restoring credentials or an approval', () => {
    const document = initialProject(source);
    const draft = { projectId: 'project-one', baseRevision: 2, document, savedAt: 'now',
      session_token: 'not-a-real-token', approved: true };
    expect(persistDraft(localStorage, draft)).toBe(true);
    expect(localStorage.getItem(DRAFT_KEY)).not.toContain('session_token');
    expect(JSON.parse(localStorage.getItem(DRAFT_KEY)!)).not.toHaveProperty('approved');
    expect(restoreDraft(localStorage)?.document).toEqual(document);
  });
  it('rejects corrupt or excessive browser storage without throwing', () => {
    localStorage.setItem(DRAFT_KEY, '{broken'); expect(restoreDraft(localStorage)).toBeNull();
    localStorage.setItem(DRAFT_KEY, 'x'.repeat(250001)); expect(restoreDraft(localStorage)).toBeNull();
  });
  it('supports bounded undo/redo and a new edit discards only redo history', () => {
    const first = initialProject(source);
    let history = { past: [] as typeof first[], present: first, future: [] as typeof first[] };
    for (let i = 0; i < 70; i++) history = edit(history, { ...history.present, title: `Title ${i}` });
    expect(history.past).toHaveLength(50);
    const reverted = undo(history); expect(reverted.present.title).toBe('Title 68');
    expect(redo(reverted).present.title).toBe('Title 69');
    expect(edit(reverted, { ...reverted.present, title: 'Changed branch' }).future).toHaveLength(0);
  });
  it('keeps scene identity and computes fractional timeline boundaries', () => {
    const first = initialProject(source);
    first.scenes[0].duration = 1.25;
    const second = { ...first.scenes[0], id: 'scene-2', duration: 2.5 };
    const document = { ...first, scenes: [...first.scenes, second] };
    expect(timeline(document).map(s => [s.start, s.end])).toEqual([[0, 1.25], [1.25, 3.75]]);
    expect(moveScene(document, 0, 1).scenes.map(s => s.id)).toEqual(['scene-2', 'scene-1']);
    expect(moveScene(document, 0, -1)).toBe(document);
    expect(document.scenes[0].id).toBe('scene-1');
  });
  it('rejects orphan sources, duplicate scene IDs and non-finite duration', () => {
    const document = initialProject(source);
    expect(documentSchema.safeParse({ ...document, sources: ['other'] }).success).toBe(false);
    expect(documentSchema.safeParse({ ...document, scenes: [document.scenes[0], document.scenes[0]] }).success).toBe(false);
    expect(documentSchema.safeParse({ ...document, scenes: [{ ...document.scenes[0], duration: Infinity }] }).success).toBe(false);
  });
  it('does not infer a qualified result from status alone', () => {
    expect(verifiedCompletion({ status: 'complete', receipts: [] })).toBe(false);
    const receipts = ['video-mp4', 'video-webm', 'quality-report', 'provenance'].map(role => ({ role, size: 10 }));
    expect(verifiedCompletion({ status: 'running', receipts })).toBe(false);
    expect(verifiedCompletion({ status: 'complete', receipts })).toBe(true);
  });
});
