import { Blob as NodeBlob } from 'node:buffer';
import { webcrypto } from 'node:crypto';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AsrProposalPanel, type AsrContext } from '../src/AsrProposalPanel';
import { SourceAudioPreview } from '../src/SourceAudioPreview';
import { StudioAPI } from '../src/api/client';
import type { SourceAsset } from '../src/contracts';
import { digest, revision, source } from './fixtures';

const session = { actor_type: 'human', actor_id: 'operator', session_token: 'audio-session-token',
  csrf_token: 'audio-csrf-token', expires_in_seconds: 900 };
const audioSource = (sha256 = 'b'.repeat(64), size = 64_044): SourceAsset => ({
  ...source, id: `src_${'a'.repeat(32)}`, provenance: { ...source.provenance, original: {
    name: 'review.wav', media_type: 'audio/wav', size, sha256, transformation: 'pcm16-waveform-png-v1',
  } },
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('streams and hashes the original WAV with a header credential and an opaque URL only', async () => {
  vi.stubGlobal('crypto', webcrypto); vi.stubGlobal('Blob', NodeBlob);
  const bytes = new TextEncoder().encode('synthetic WAV bytes verified by the server '.repeat(2));
  const sha256 = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex');
  const audio = audioSource(sha256, bytes.length);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(session), { headers: { 'content-type': 'application/json' } }))
    .mockResolvedValueOnce(new Response(bytes, { headers: {
      'content-type': 'audio/wav', 'content-length': String(bytes.length),
    } }));
  const api = new StudioAPI(fetcher as typeof fetch); await api.pair('synthetic-code');
  const blob = await api.sourceAudioPreview(audio);
  expect(blob.type).toBe('audio/wav'); expect(blob.size).toBe(bytes.length);
  expect(fetcher.mock.calls[1][0]).toBe(`/api/v2/sources/${audio.id}/audio-preview`);
  expect(fetcher.mock.calls[1][0]).not.toContain(session.session_token);
  expect(fetcher.mock.calls[1][1]).toMatchObject({ credentials: 'omit', cache: 'no-store', redirect: 'error' });
  expect(fetcher.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${session.session_token}`);
  expect(await api.sourceAudioPreview(audio)).toBe(blob);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('rejects mismatched bytes, content metadata and unqualified source metadata', async () => {
  vi.stubGlobal('crypto', webcrypto); vi.stubGlobal('Blob', NodeBlob);
  const bytes = new TextEncoder().encode('synthetic fixture bytes '.repeat(3));
  const response = (type = 'audio/wav', length = bytes.length) => new Response(bytes, { headers: {
    'content-type': type, 'content-length': String(length),
  } });
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(session), { headers: { 'content-type': 'application/json' } }))
    .mockResolvedValueOnce(response());
  const api = new StudioAPI(fetcher as typeof fetch); await api.pair('synthetic-code');
  await expect(api.sourceAudioPreview(audioSource('0'.repeat(64), bytes.length)))
    .rejects.toMatchObject({ code: 'AUDIO_PREVIEW_INTEGRITY_FAILED' });
  for (const mismatched of [response('text/html'), response('audio/wav', bytes.length + 1)]) {
    fetcher.mockResolvedValueOnce(mismatched);
    await expect(api.sourceAudioPreview(audioSource('0'.repeat(64), bytes.length)))
      .rejects.toMatchObject({ code: 'AUDIO_PREVIEW_INTEGRITY_FAILED' });
  }
  await expect(api.sourceAudioPreview({ ...audioSource(), allowed_operations: ['render'] }))
    .rejects.toMatchObject({ code: 'AUDIO_PREVIEW_UNAVAILABLE' });
  await expect(api.sourceAudioPreview(audioSource('b'.repeat(64), 9_600_046)))
    .rejects.toMatchObject({ code: 'AUDIO_PREVIEW_UNAVAILABLE' });
});

it('loads only after a click, never autoplays, and revokes on close and decode error', async () => {
  const api = { sourceAudioPreview: vi.fn().mockResolvedValue(new Blob(['fixture'], { type: 'audio/wav' })) } as unknown as StudioAPI;
  const create = vi.fn().mockReturnValueOnce('blob:audio-one').mockReturnValueOnce('blob:audio-two');
  const revoke = vi.fn(); vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
  const view = render(<SourceAudioPreview source={audioSource()} api={api} disabled={false} />);
  expect(api.sourceAudioPreview).not.toHaveBeenCalled(); expect(view.container.querySelector('audio')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Load original audio for review' }));
  await waitFor(() => expect(view.container.querySelector('audio')).not.toBeNull());
  const player = view.container.querySelector('audio')!;
  expect(api.sourceAudioPreview).toHaveBeenCalledOnce(); expect(player.controls).toBe(true);
  expect(player.preload).toBe('none'); expect(player.autoplay).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Close original audio' }));
  expect(revoke).toHaveBeenCalledWith('blob:audio-one'); expect(view.container.querySelector('audio')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Load original audio for review' }));
  await waitFor(() => expect(view.container.querySelector('audio')).not.toBeNull());
  fireEvent.error(view.container.querySelector('audio')!);
  expect(revoke).toHaveBeenCalledWith('blob:audio-two');
  expect(await screen.findByText(/could not decode this WAV/)).toBeTruthy();
});

it('ignores a late response after unmount and exposes review only for the selected ASR source', async () => {
  let finish!: (blob: Blob) => void;
  const sourceAudioPreview = vi.fn().mockImplementation(() => new Promise<Blob>(resolve => { finish = resolve; }));
  const api = { sourceAudioPreview, asrProposal: vi.fn() } as unknown as StudioAPI;
  const create = vi.fn(); vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() });
  const view = render(<SourceAudioPreview source={audioSource()} api={api} disabled={false} />);
  fireEvent.click(screen.getByRole('button')); view.unmount(); finish(new Blob(['late'], { type: 'audio/wav' }));
  await waitFor(() => expect(sourceAudioPreview).toHaveBeenCalledOnce()); expect(create).not.toHaveBeenCalled();

  const rev = { ...revision(), project_id: `prj_${'c'.repeat(32)}`, document_hash: digest };
  rev.document.sources = [audioSource().id];
  const context: AsrContext = { api, revision: rev, sources: [audioSource()], configured: true,
    disabled: false, onPendingChange: vi.fn() };
  const panel = render(<AsrProposalPanel context={context} onReview={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Load original audio for review' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Audio source for transcription'), { target: { value: audioSource().id } });
  expect((screen.getByRole('button', { name: 'Load original audio for review' }) as HTMLButtonElement).disabled).toBe(false);
  panel.rerender(<AsrProposalPanel context={{ ...context, disabled: true }} onReview={vi.fn()} />);
  expect((screen.getByRole('button', { name: 'Load original audio for review' }) as HTMLButtonElement).disabled).toBe(true);
});
