import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AsrProposalPanel, type AsrContext } from '../src/AsrProposalPanel';
import { TranscriptEditor } from '../src/TranscriptEditor';
import { StudioAPI } from '../src/api/client';
import { asrProposalSchema, type AsrProposal } from '../src/api/asr-contracts';
import { source, revision, digest } from './fixtures';

const audio = { ...source, id: `src_${'a'.repeat(32)}`, provenance: { ...source.provenance,
  original: { name: 'synthetic.wav', media_type: 'audio/wav' as const, size: 64044,
    sha256: 'b'.repeat(64), transformation: 'pcm16-waveform-png-v1' as const } } };
const rev = { ...revision(), project_id: `prj_${'a'.repeat(32)}` };
rev.document.sources = [audio.id];
const proposal: AsrProposal = { schema_version: 'quantech.asr-proposal.v1', proposal_id: `asrp_${'c'.repeat(32)}`,
  effect: 'proposal_only', project_id: rev.project_id, revision: 1, project_hash: digest, locale: 'en',
  source_asset_id: audio.id, asset_sha256: digest, original_audio_sha256: 'b'.repeat(64),
  audio_duration_ms: 2000, resource_binding_sha256: 'd'.repeat(64),
  segments: [{ id: 'asr-one', start: 0, end: 2, text: '<script>untrusted text</script>',
    source_asset_id: audio.id, source_locator: 'machine proposal, 0-2000ms' }],
  limitations: { machine_proposal_only: true, human_review_required: true, speaker_identity_inferred: false,
    vad_performed: false, exact_zero_energy_rejected: true, independent_verification: false } };
const makeContext = (call = vi.fn().mockResolvedValue(proposal)): AsrContext => ({
  api: { asrProposal: call } as unknown as StudioAPI, revision: rev, sources: [audio],
  configured: true, disabled: false, onPendingChange: vi.fn() });
function select() {
  fireEvent.change(screen.getByLabelText('Audio source for transcription'), { target: { value: audio.id } });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Create local caption proposal' }));
}
afterEach(cleanup);

it('requires explicit selection and acknowledgement, then only offers an inert proposal', async () => {
  const ctx = makeContext(), review = vi.fn();
  const view = render(<AsrProposalPanel context={ctx} onReview={review} />);
  expect(ctx.api.asrProposal).not.toHaveBeenCalled();
  expect((screen.getByText('Create local caption proposal') as HTMLButtonElement).disabled).toBe(true);
  select(); await screen.findByText(proposal.segments[0].text, { exact: false });
  expect(view.container.querySelector('script')).toBeNull();
  expect(review).not.toHaveBeenCalled(); expect(ctx.api.asrProposal).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText('Discard machine proposal'));
  expect(review).not.toHaveBeenCalled();
});

it('requires review then Apply and permits cancellation without changing canonical captions', async () => {
  const changed = vi.fn();
  render(<TranscriptEditor segments={[]} duration={4} sourceIds={[audio.id]} disabled={false}
    onChange={changed} onEditingChange={vi.fn()} asr={makeContext()} />);
  select(); fireEvent.click(await screen.findByText('Review proposal in caption editor'));
  expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Cancel caption edit')); expect(changed).not.toHaveBeenCalled();
  select(); fireEvent.click(await screen.findByText('Review proposal in caption editor'));
  fireEvent.click(screen.getByText('Apply manual captions'));
  expect(changed).toHaveBeenCalledOnce(); expect(changed).toHaveBeenCalledWith(proposal.segments);
});

it('does not allow review of a stale source hash', async () => {
  render(<AsrProposalPanel context={makeContext(vi.fn().mockResolvedValue({ ...proposal,
    original_audio_sha256: 'e'.repeat(64) }))} onReview={vi.fn()} />);
  select(); const review = await screen.findByText('Review proposal in caption editor');
  expect((review as HTMLButtonElement).disabled).toBe(true);
});

it('aborts on unmount and never accepts a late response', async () => {
  let resolve!: (value: AsrProposal) => void;
  const call = vi.fn().mockImplementation(() => new Promise<AsrProposal>(done => { resolve = done; }));
  const ctx = makeContext(call), review = vi.fn();
  const view = render(<AsrProposalPanel context={ctx} onReview={review} />);
  select(); view.unmount(); expect(call.mock.calls[0][2].aborted).toBe(true);
  resolve(proposal); await waitFor(() => expect(ctx.onPendingChange).toHaveBeenLastCalledWith(false));
  expect(review).not.toHaveBeenCalled(); expect(call).toHaveBeenCalledOnce();
});

it('rejects authority fields and mismatched API context without retry or another mutation', async () => {
  expect(asrProposalSchema.safeParse({ ...proposal, approved: true }).success).toBe(false);
  const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
  const fetcher = vi.fn().mockResolvedValueOnce(response({ actor_id: 'operator', actor_type: 'human',
    session_token: 'test-session-credential-only', csrf_token: 'test-csrf-credential-only', expires_in_seconds: 900 }))
    .mockResolvedValueOnce(response({ ...proposal, revision: 2 }));
  const api = new StudioAPI(fetcher as typeof fetch); await api.pair('fixture');
  await expect(api.asrProposal(rev, audio)).rejects.toMatchObject({ code: 'LOCAL_ASR_PROPOSAL_INVALID' });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toBe(`/api/v2/sources/${audio.id}/asr-proposals`);
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({ acknowledge_machine_proposal_only: true });
});
