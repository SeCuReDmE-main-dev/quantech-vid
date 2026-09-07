import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Studio } from '../src/Studio';
import { SyntheticAvatarStyle } from '../src/SyntheticAvatarStyle';
import { StudioAPI } from '../src/api/client';
import { edit, initialProject, undo, type History } from '../src/editor';
import type { Visual3D } from '../src/contracts';
import { health, job, plan, source } from './fixtures';

beforeEach(() => localStorage.clear());
afterEach(cleanup);

it('offers two exact fictional geometry presets and no real-person operation', () => {
  const onChange = vi.fn();
  const visual: Visual3D = { kind: 'synthetic-avatar', accent: '#000000', animation: 'none', lines: [] };
  render(<SyntheticAvatarStyle visual={visual} disabled={false} onChange={onChange} />);
  expect(screen.getByText(/No real-person likeness, identity, photo, or voice is created/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /Orbital guide/ }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith({ kind: 'synthetic-avatar', accent: '#14B8A6',
    animation: 'spin', lines: ['Fictional geometric guide', 'Human review required'] });
  expect(screen.queryByRole('button', { name: /upload|photo|identity|provider/i })).toBeNull();
});

it('is unavailable for non-avatar 3D representations', () => {
  const view = render(<SyntheticAvatarStyle visual={{ kind: 'diagram', accent: '#14B8A6',
    animation: 'none', lines: [] }} disabled={false} onChange={vi.fn()} />);
  expect(view.container.textContent).toBe('');
});

it('uses the existing bounded history for persistence and undo', () => {
  const document = initialProject(source);
  const visual: Visual3D = { kind: 'synthetic-avatar', accent: '#8B5CF6', animation: 'pulse',
    lines: ['Synthetic geometric host', 'Human review required'] };
  const styled = { ...document, scenes: [{ ...document.scenes[0], visual_3d: visual }] };
  const history: History = { past: [], present: document, future: [] };
  const changed = edit(history, styled);
  expect(changed.present.scenes[0].visual_3d).toEqual(styled.scenes[0].visual_3d);
  expect(undo(changed).present).toEqual(document);
});

it('saves the selected preset, invalidates approval on a later style change, and remains undoable', async () => {
  const api = new StudioAPI(vi.fn() as typeof fetch);
  vi.spyOn(api, 'health').mockResolvedValue(health);
  vi.spyOn(api, 'pair').mockResolvedValue({ actor_id: 'operator', expires_in_seconds: 900 });
  vi.spyOn(api, 'sample').mockResolvedValue(source);
  vi.spyOn(api, 'create').mockImplementation(async document => ({ project_id: 'prj_fixture', revision: 1,
    document_hash: 'a'.repeat(64), document, created_at: 'now' }));
  vi.spyOn(api, 'plan').mockResolvedValue(plan);
  vi.spyOn(api, 'approve').mockResolvedValue({ grant_id: 'grant', plan_id: plan.id, scope: 'render', status: 'approved' });
  vi.spyOn(api, 'run').mockResolvedValue(job);
  render(<Studio api={api} />);

  fireEvent.change(screen.getByLabelText('One-time operator code'), { target: { value: 'synthetic-operator-code' } });
  fireEvent.click(screen.getByRole('button', { name: 'Pair this studio' }));
  await screen.findByText(/Paired locally/);
  fireEvent.click(screen.getByRole('button', { name: 'Create a synthetic sample' }));
  await screen.findByDisplayValue('My first verified film');
  fireEvent.change(screen.getByLabelText('Scene representation'), { target: { value: 'synthetic-avatar' } });
  fireEvent.click(screen.getByRole('button', { name: /Orbital guide/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save project' }));
  await screen.findByText(/Saved revision 1/);
  const saved = vi.mocked(api.create).mock.calls[0][0];
  expect(saved.scenes[0].visual_3d).toMatchObject({ kind: 'synthetic-avatar', accent: '#14B8A6', animation: 'spin' });

  fireEvent.click(screen.getByRole('button', { name: 'Prepare render plan' }));
  await screen.findByText('Review this exact plan');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Approve this plan' }));
  await screen.findByText(/No render has started/);
  fireEvent.click(screen.getByRole('button', { name: /Signal mosaic/ }));
  expect(screen.queryByText('Review this exact plan')).toBeNull();
  expect(api.run).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: /Signal mosaic/ }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  await waitFor(() => expect(screen.getByRole('button', { name: /Orbital guide/ }).getAttribute('aria-pressed')).toBe('true'));
  expect(screen.queryByText('Review this exact plan')).toBeNull();
  expect(api.approve).toHaveBeenCalledOnce();
});
