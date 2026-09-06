import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { visual3DSchema, sceneSchema } from '../src/contracts';
import { Scene3DLabels } from '../src/Scene3DLabels';
import { initialProject } from '../src/editor';
import { source } from './fixtures';

afterEach(cleanup);
const visual = { kind: 'diagram', lines: ['Source', 'Review'], accent: '#14B8A6', animation: 'spin' } as const;
it('uses a closed, bounded representation while leaving historical scenes unchanged', () => {
  const scene = initialProject(source).scenes[0];
  expect(sceneSchema.parse(scene)).not.toHaveProperty('visual_3d');
  expect(visual3DSchema.parse(visual)).toEqual(visual);
  for (const change of [{ url: 'https://example.invalid/model.glb' }, { lines: Array(9).fill('label') },
    { lines: ['x'.repeat(201)] }, { animation: 'run-script' }, { accent: 'javascript:1' }])
    expect(visual3DSchema.safeParse({ ...visual, ...change }).success).toBe(false);
});
it('requires Apply, validates labels, and reports the pending edit to the production guard', () => {
  const onChange = vi.fn(), editing = vi.fn();
  render(<Scene3DLabels visual={{ ...visual, lines: [...visual.lines] }} disabled={false} onChange={onChange} onEditingChange={editing} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit 3D labels and accent' }));
  expect(editing).toHaveBeenLastCalledWith(true);
  fireEvent.change(screen.getByLabelText('3D labels, one per line'), { target: { value: 'x'.repeat(201) } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply 3D labels' }));
  expect(screen.getByRole('alert')).toBeTruthy(); expect(onChange).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('3D labels, one per line'), { target: { value: ' First\n\nSecond ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply 3D labels' }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith({ ...visual, lines: ['First', 'Second'] });
  expect(editing).toHaveBeenLastCalledWith(false);
});
it('cancels draft labels without modifying the scene', () => {
  const onChange = vi.fn(), editing = vi.fn();
  render(<Scene3DLabels visual={{ ...visual, lines: [...visual.lines] }} disabled={false} onChange={onChange} onEditingChange={editing} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit 3D labels and accent' }));
  fireEvent.change(screen.getByLabelText('3D labels, one per line'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel 3D labels' }));
  expect(onChange).not.toHaveBeenCalled(); expect(editing).toHaveBeenLastCalledWith(false);
});
