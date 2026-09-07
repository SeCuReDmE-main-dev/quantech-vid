import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SourcePreview } from '../src/SourcePreview';
import { StudioAPI } from '../src/api/client';
import { source } from './fixtures';
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('inspects only after a click and revokes the image when context unmounts', async () => {
  const api = new StudioAPI(vi.fn() as typeof fetch);
  vi.spyOn(api, 'sourcePreview').mockResolvedValue(new Blob(['fixture'], {type:'image/png'}));
  const create = vi.fn().mockReturnValue('blob:synthetic-preview'), revoke = vi.fn();
  vi.stubGlobal('URL', {createObjectURL:create, revokeObjectURL:revoke});
  const view = render(<SourcePreview source={source} api={api} disabled={false}/>);
  expect(api.sourcePreview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('img');
  expect(api.sourcePreview).toHaveBeenCalledOnce();
  expect(screen.getByRole('img').getAttribute('src')).toBe('blob:synthetic-preview');
  view.unmount(); expect(revoke).toHaveBeenCalledWith('blob:synthetic-preview');
});
it('does not attach an image when a pending request finishes after context changes', async () => {
  const api = new StudioAPI(vi.fn() as typeof fetch);
  let finish!: (blob: Blob) => void;
  vi.spyOn(api,'sourcePreview').mockReturnValue(new Promise(resolve => {finish=resolve;}));
  const create=vi.fn(); vi.stubGlobal('URL',{createObjectURL:create,revokeObjectURL:vi.fn()});
  const view=render(<SourcePreview source={source} api={api} disabled={false}/>);
  fireEvent.click(screen.getByRole('button')); view.unmount(); finish(new Blob(['fixture']));
  await waitFor(()=>expect(api.sourcePreview).toHaveBeenCalledOnce());
  expect(create).not.toHaveBeenCalled();
});
it('filters failures and leaves a retry action without starting production', async () => {
  const api=new StudioAPI(vi.fn() as typeof fetch);
  vi.spyOn(api,'sourcePreview').mockRejectedValue(new Error('PRIVATE_PATH'));
  render(<SourcePreview source={source} api={api} disabled={false}/>);
  fireEvent.click(screen.getByRole('button'));
  await screen.findByText(/could not be verified/);
  expect(screen.queryByText(/PRIVATE_PATH/)).toBeNull();
  expect(screen.queryByRole('img')).toBeNull();
});
