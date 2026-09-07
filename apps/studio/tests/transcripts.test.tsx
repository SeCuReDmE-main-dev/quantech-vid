import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { TranscriptEditor } from '../src/TranscriptEditor';
import { captionMilliseconds, documentSchema, transcriptSegmentSchema, type TranscriptSegment } from '../src/contracts';
import { initialProject, edit, undo, type History } from '../src/editor';
import { source } from './fixtures';

const sourceId = `src_${'a'.repeat(32)}`;
const segment: TranscriptSegment = { id: 'caption-one', start: 0, end: 2,
  text: 'A manually reviewed source.', source_asset_id: sourceId, source_locator: 'paragraph 1' };
afterEach(cleanup);

it('preserves historical omission and rejects unknown keys, invalid times and disappearing cues', () => {
  const doc = initialProject({ ...source, id: sourceId });
  expect(documentSchema.parse(doc)).toEqual(doc);
  expect(documentSchema.parse(doc).tracks[0]).not.toHaveProperty('segments');
  for (const invalid of [{ verified: true }, { start: NaN }, { end: Infinity }, { start: -1 },
    { start: 2, end: 2 }, { start: .0001, end: .0002 }, { start: '0.5' }, { end: true },
    { text: '  \n\t' }, { text: '\u0000\u200b' }, { source_locator: '\u0001 ' }])
    expect(transcriptSegmentSchema.safeParse({ ...segment, ...invalid }).success).toBe(false);
});

it('matches decimal half-up precision including ties and exponent notation', () => {
  expect([.0005, .0015, 1.0005, 1.0025, 8.0005, 5e-7, 5e-4].map(captionMilliseconds))
    .toEqual([1, 2, 1001, 1003, 8001, 0, 1]);
});

it('rejects duplicate, unadmitted, out-of-film, overlapping and unordered segments', () => {
  const doc = initialProject({ ...source, id: sourceId });
  const valid = [{ ...segment }, { ...segment, id: 'two', start: 2, end: 4 }];
  doc.tracks[0].segments = valid;
  expect(documentSchema.safeParse(doc).success).toBe(true);
  for (const invalid of [[segment, segment], [{ ...segment, source_asset_id: `src_${'b'.repeat(32)}` }],
    [{ ...segment, end: 4.1 }], [segment, { ...segment, id: 'two', start: 1.5 }], [...valid].reverse()]) {
    doc.tracks[0].segments = invalid;
    expect(documentSchema.safeParse(doc).success).toBe(false);
  }
});

it('preserves canonical text and supports undo without a network or inference call', () => {
  const doc = initialProject({ ...source, id: sourceId });
  const text = '<script>ignored</script>\n\n00:00:01 --> 00:00:02';
  const history: History = { past: [], present: doc, future: [] };
  const next = edit(history, { ...doc, tracks: [{ ...doc.tracks[0], segments: [{ ...segment, text }] }] });
  expect(next.present.tracks[0].segments![0].text).toBe(text);
  expect(undo(next).present).toEqual(doc);
});

it('requires apply, permits cancellation and rejects an invalid local caption draft', () => {
  const changed = vi.fn(), editing = vi.fn();
  render(<TranscriptEditor segments={[segment]} duration={4} sourceIds={[sourceId]} disabled={false}
    onChange={changed} onEditingChange={editing} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit manual captions' }));
  expect(editing).toHaveBeenLastCalledWith(true);
  fireEvent.change(screen.getByLabelText('End 1 (seconds)'), { target: { value: '5' } });
  fireEvent.submit(screen.getByRole('form', { name: 'Edit manual captions' }));
  expect(screen.getByRole('alert')).toBeTruthy(); expect(changed).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('End 1 (seconds)'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel caption edit' }));
  expect(changed).not.toHaveBeenCalled(); expect(editing).toHaveBeenLastCalledWith(false);
  fireEvent.click(screen.getByRole('button', { name: 'Edit manual captions' }));
  expect((screen.getByLabelText('End 1 (seconds)') as HTMLInputElement).value).toBe('2');
  fireEvent.change(screen.getByLabelText('Caption text 1'), { target: { value: 'A corrected source-linked caption.' } });
  expect(changed).not.toHaveBeenCalled();
  fireEvent.submit(screen.getByRole('form', { name: 'Edit manual captions' }));
  expect(changed).toHaveBeenCalledWith([expect.objectContaining({ text: 'A corrected source-linked caption.' })]);
});
