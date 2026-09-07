import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { VerifiedVideoPreview } from '../src/VerifiedVideoPreview';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('loads captions only after a click and releases their Blob URL on close', async () => {
  const create = vi.fn().mockReturnValue('blob:verified-vtt'), revoke = vi.fn();
  vi.stubGlobal('URL', {createObjectURL:create, revokeObjectURL:revoke});
  const load = vi.fn().mockResolvedValue(new Blob(['WEBVTT\n'], {type:'text/vtt'}));
  const {container, unmount} = render(<VerifiedVideoPreview url="blob:verified-video" loadCaptions={load} />);
  expect(load).not.toHaveBeenCalled(); expect(container.querySelector('track')).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole('button', {name:'Load verified captions'})); });
  await waitFor(() => expect(container.querySelector('track')?.getAttribute('src')).toBe('blob:verified-vtt'));
  expect(load).toHaveBeenCalledOnce();
  expect(container.querySelector('track')?.getAttribute('kind')).toBe('captions');
  const element = container.querySelector('track')!;
  Object.defineProperty(element, 'track', {value:{mode:'hidden',cues:[{},{}]}});
  fireEvent.load(element);
  await screen.findByText(/Browser loaded 2 caption cues/);
  expect(element.track.mode).toBe('showing');
  unmount(); expect(revoke).toHaveBeenCalledWith('blob:verified-vtt');
});
it('does not allocate a late caption URL after unmount or display raw provider errors', async () => {
  const create = vi.fn(); vi.stubGlobal('URL', {createObjectURL:create, revokeObjectURL:vi.fn()});
  let finish!: (blob:Blob)=>void;
  const load = vi.fn().mockReturnValue(new Promise<Blob>(resolve=>{finish=resolve;}));
  const {unmount} = render(<VerifiedVideoPreview url="blob:video" loadCaptions={load} />);
  fireEvent.click(screen.getByRole('button', {name:'Load verified captions'})); unmount();
  finish(new Blob(['WEBVTT'], {type:'text/vtt'})); await Promise.resolve();
  expect(create).not.toHaveBeenCalled();
  render(<VerifiedVideoPreview url="blob:other" loadCaptions={async()=>{throw new Error('private-provider-body');}} />);
  fireEvent.click(screen.getByRole('button', {name:'Load verified captions'}));
  await screen.findByText(/could not be verified/);
  expect(screen.queryByText(/private-provider-body/)).toBeNull();
});
